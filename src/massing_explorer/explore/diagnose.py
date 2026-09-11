"""
DIAGNOSE: when COVER+REPAIR finds zero legal schemes, or a low legal yield
with a large recoverable frontier.

Do not proceed to LEARN / REFINE on an empty legal set. Classify why the
archive is empty of legal cells, run at most one targeted COVER batch if
search looks thin, then name the smallest conflicting brief clauses and
offer relaxation probes. Never apply a probe — the architect decides.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from . import archive as archive_mod
from .cover import (
    CoverSample,
    apply_cover_sample,
    build_cover_plan,
    story_pattern_library,
)
from .performance import measure
from .strategy import grouping_is_required

TARGETED_BUDGET = 15
MIN_ATTEMPTS_FOR_CONFLICT = 8
DOMINANT_FRAC = 0.55
LEGAL_YIELD_LOW = 3
FRONTIER_DIAGNOSE_MIN = 5
LEGAL_YIELD_SKIP = 8


def should_diagnose(archive: dict[str, Any]) -> bool:
    """True when DIAGNOSE can still help after COVER + REPAIR."""
    n_legal = len(archive_mod.legal_cells(archive))
    n_front = len(archive.get("frontier") or archive_mod.frontier_entries(archive))
    if n_legal >= LEGAL_YIELD_SKIP:
        return False
    if n_legal == 0:
        return True
    return n_legal < LEGAL_YIELD_LOW and n_front >= FRONTIER_DIAGNOSE_MIN


def diagnose(
    session: Any,
    archive: dict[str, Any],
    *,
    evaluate: Any | None = None,
) -> dict[str, Any]:
    """
    Full DIAGNOSE pass after a zero-legal COVER.

    May run one targeted COVER batch when the failure class is search.
    Returns a report suitable for the explore store / UI. Never mutates
    requirements permanently.
    """
    legal = archive_mod.legal_cells(archive)
    if not should_diagnose(archive):
        return {
            "ran": False,
            "skipped": True,
            "reason": "enough legal cells" if legal else "no recoverable frontier",
            "class": None,
            "note": "",
        }

    origin = archive_mod.capture(session)
    report: dict[str, Any] = {
        "ran": True,
        "class": None,
        "patterns": failure_patterns(archive),
        "targeted": {"ran": False},
        "conflict": None,
        "probes": [],
        "knowledge": [],
        "note": "",
    }

    kind = classify_failure(session, archive, report["patterns"])
    report["class"] = kind
    report["knowledge"] = _knowledge_from_patterns(report["patterns"], kind)

    if kind == "search" and evaluate is not None:
        targeted = run_targeted_cover(
            session,
            archive,
            evaluate=evaluate,
            patterns=report["patterns"],
            budget=TARGETED_BUDGET,
        )
        report["targeted"] = targeted
        archive_mod.restore_snapshot(session, origin)
        legal = archive_mod.legal_cells(archive)
        if legal:
            report["class"] = "resolved"
            report["note"] = (
                f"DIAGNOSE: targeted COVER found {len(legal)} legal cell(s) "
                f"after a {kind} reading. Proceed."
            )
            archive["diagnose"] = report
            return report
        # Re-classify patterns after the extra batch.
        report["patterns"] = failure_patterns(archive)
        report["knowledge"] = _knowledge_from_patterns(report["patterns"], "conflict")
        kind = "conflict"
        report["class"] = kind

    if kind == "model":
        report["probes"] = []
        report["conflict"] = {
            "kind": "model",
            "clauses": [],
            "note": (
                "The brief needs a form the engine cannot draw yet "
                "(courtyard / podium / perpendicular wings). That is a model "
                "limit, not a brief arithmetic conflict."
            ),
        }
        report["note"] = (
            "DIAGNOSE: model limitation. Unsupported topology — not a coverage failure. "
            "No auto-relax. Architect must change the brief or wait for drawing support."
        )
        archive["diagnose"] = report
        archive_mod.restore_snapshot(session, origin)
        return report

    conflict = find_minimal_conflict(session, archive)
    report["conflict"] = conflict
    report["probes"] = relaxation_probes(session, conflict, report["patterns"])
    report["note"] = _note_for(kind, report)
    archive["diagnose"] = report
    archive_mod.restore_snapshot(session, origin)
    # Keep stated / least-bad drawing for the UI.
    return report


def classify_failure(
    session: Any,
    archive: dict[str, Any],
    patterns: dict[str, Any] | None = None,
) -> str:
    """
    Return one of: search | conflict | model.

    Model: brief wants an unsupported drawable form.
    Search: thin coverage or open axes barely touched.
    Conflict: enough samples, same hard fails dominate.
    """
    patterns = patterns or failure_patterns(archive)
    if _brief_needs_unsupported(session):
        return "model"

    attempts = int(archive.get("attempts") or 0)
    cells = archive.get("cells") or {}
    cover = archive.get("cover") or {}
    planned = int(cover.get("samples_planned") or 0)
    used = int(cover.get("samples_used") or 0)

    if attempts < MIN_ATTEMPTS_FOR_CONFLICT:
        return "search"
    if planned and used < max(MIN_ATTEMPTS_FOR_CONFLICT, int(0.4 * planned)):
        return "search"
    if not grouping_is_required(session):
        parts = {e.get("partition") for e in cells.values() if e.get("partition")}
        if len(parts) <= 1 and attempts < 40:
            return "search"

    dominant = patterns.get("dominant_kinds") or []
    share = float(patterns.get("dominant_share") or 0.0)
    if dominant and share >= DOMINANT_FRAC and attempts >= MIN_ATTEMPTS_FOR_CONFLICT:
        return "conflict"
    if attempts >= MIN_ATTEMPTS_FOR_CONFLICT and cells:
        return "conflict"
    return "search"


def failure_patterns(archive: dict[str, Any]) -> dict[str, Any]:
    """Aggregate failed_kinds / messages across illegal archive cells."""
    cells = list((archive.get("cells") or {}).values())
    illegal = [e for e in cells if not e.get("fits_limitations")]
    kinds: Counter[str] = Counter()
    for entry in illegal:
        perf = entry.get("performance") or {}
        for k in perf.get("failed_kinds") or []:
            kinds[str(k).split(":")[0]] += 1
        # Fall back to failed_checks count signal.
        if not perf.get("failed_kinds") and int(perf.get("limit_fails") or 0) > 0:
            kinds["site_limit"] += 1

    total = max(1, len(illegal))
    ranked = kinds.most_common()
    dominant = [k for k, _ in ranked[:3]]
    top_n = ranked[0][1] if ranked else 0
    return {
        "illegal": len(illegal),
        "attempts": int(archive.get("attempts") or 0),
        "kind_counts": dict(ranked),
        "dominant_kinds": dominant,
        "dominant_share": top_n / total if illegal else 0.0,
        "focus": _focus_from_kinds(dominant),
    }


def run_targeted_cover(
    session: Any,
    archive: dict[str, Any],
    *,
    evaluate: Any,
    patterns: dict[str, Any],
    budget: int = TARGETED_BUDGET,
) -> dict[str, Any]:
    """One steered COVER batch. Does not expand adaptively."""
    focus = str(patterns.get("focus") or "stories")
    origin = archive_mod.capture(session)
    plan = build_cover_plan(session, pool_size=max(budget * 2, 30))
    samples = list(_targeted_samples(session, plan, focus=focus, budget=budget))
    search_cache: dict[str, Any] = {}
    before_legal = len(archive_mod.legal_cells(archive))
    before_attempts = int(archive.get("attempts") or 0)
    ran = 0
    for sample in samples:
        reason = apply_cover_sample(
            session, plan, sample, origin=origin, search_cache=search_cache
        )
        evaluate(session, archive, f"DIAGNOSE targeted ({focus}): {reason}")
        ran += 1
    archive_mod.restore_snapshot(session, origin)
    after_legal = len(archive_mod.legal_cells(archive))
    return {
        "ran": True,
        "focus": focus,
        "requested": budget,
        "ran_n": ran,
        "new_legal": after_legal - before_legal,
        "attempts_delta": int(archive.get("attempts") or 0) - before_attempts,
        "note": (
            f"Targeted COVER on {focus}: {ran} sample(s), "
            f"{after_legal - before_legal} new legal."
        ),
    }


def find_minimal_conflict(session: Any, archive: dict[str, Any]) -> dict[str, Any]:
    """
    Drop hard clauses one at a time (then pairs) on the stated drawing.
    Returns the smallest set whose removal makes the drawing fit.
    """
    from ..solver import solve_massing_study

    candidates = _hard_clause_candidates(session)
    if not candidates:
        return {
            "kind": "unknown",
            "clauses": [],
            "size": 0,
            "note": "No hard numeric clauses to probe.",
        }

    origin = archive_mod.capture(session)
    base = measure(solve_massing_study(session), session, archive=archive)
    if base.get("fits_limitations"):
        archive_mod.restore_snapshot(session, origin)
        return {
            "kind": "none",
            "clauses": [],
            "size": 0,
            "note": "Stated drawing already fits; archive may be stale.",
        }

    # Singles first.
    for clause in candidates:
        _apply_drop(session, clause)
        perf = measure(solve_massing_study(session), session, archive=archive)
        archive_mod.restore_snapshot(session, origin)
        if perf.get("fits_limitations"):
            return {
                "kind": "conflict",
                "clauses": [clause],
                "size": 1,
                "note": f"Removing «{clause['label']}» alone makes the stated drawing fit.",
            }

    # Pairs.
    for i, a in enumerate(candidates):
        for b in candidates[i + 1 :]:
            _apply_drop(session, a)
            _apply_drop(session, b)
            perf = measure(solve_massing_study(session), session, archive=archive)
            archive_mod.restore_snapshot(session, origin)
            if perf.get("fits_limitations"):
                return {
                    "kind": "conflict",
                    "clauses": [a, b],
                    "size": 2,
                    "note": (
                        f"Smallest conflict pair: «{a['label']}» + «{b['label']}»."
                    ),
                }

    archive_mod.restore_snapshot(session, origin)
    # Fall back: report the top pattern drivers as suspected conflict.
    patterns = failure_patterns(archive)
    suspected = [
        c
        for c in candidates
        if c.get("family") in set(patterns.get("dominant_kinds") or [])
        or c.get("lever") in {"max_edge", "max_length", "max_stories", "exact_width"}
    ][:3]
    return {
        "kind": "suspected",
        "clauses": suspected or candidates[:3],
        "size": len(suspected or candidates[:3]),
        "note": (
            "No single or pair drop cleared the stated drawing. "
            "Suspected drivers listed from failure patterns."
        ),
    }


def relaxation_probes(
    session: Any,
    conflict: dict[str, Any] | None,
    patterns: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Minimal alternatives for the architect. Never applied by the engine.
    """
    probes: list[dict[str, Any]] = []
    seen: set[str] = set()
    patterns = patterns or {}

    def add(probe: dict[str, Any]) -> None:
        key = probe.get("id") or probe.get("label") or ""
        if key in seen:
            return
        seen.add(str(key))
        probes.append(probe)

    for clause in (conflict or {}).get("clauses") or []:
        lever = str(clause.get("lever") or "")
        value = clause.get("value")
        if lever in {"max_stories", "max_height"} and value is not None:
            nxt = int(value) + 1
            add(
                {
                    "id": f"stories_{value}_to_{nxt}",
                    "kind": clause.get("kind") or "limitation",
                    "lever": "max_stories",
                    "label": f"Raise story cap {int(value)} → {nxt}",
                    "from": int(value),
                    "to": nxt,
                    "unlocks": "Shorter plan edges for the same GSF.",
                    "apply": False,
                }
            )
        elif lever in {"max_edge", "max_length"} and value is not None:
            feet = float(value)
            bumped = round(feet * 1.1, 1)
            add(
                {
                    "id": f"edge_{feet:g}_to_{bumped:g}",
                    "kind": "limitation",
                    "lever": "max_edge",
                    "label": f"Relax every-edge cap {feet:g} → {bumped:g} ft",
                    "from": feet,
                    "to": bumped,
                    "unit": "ft",
                    "unlocks": "Allows longer or wider bars without changing stories.",
                    "apply": False,
                }
            )
        elif lever in {"site_length", "max_total_length"} and value is not None:
            feet = float(value)
            bumped = round(feet + max(15.0, feet * 0.06), 0)
            add(
                {
                    "id": f"frontage_{feet:g}_to_{bumped:g}",
                    "kind": "limitation",
                    "lever": "site_length",
                    "label": f"Relax site frontage {feet:g} → {bumped:g} ft",
                    "from": feet,
                    "to": bumped,
                    "unit": "ft",
                    "unlocks": "More combined length for independent bars.",
                    "apply": False,
                }
            )
        elif lever in {"exact_width", "dept_width", "max_width"} and value is not None:
            feet = float(value)
            soft = round(feet * 0.9, 1)
            depts = list(clause.get("departments") or [])
            who = ", ".join(depts) if depts else "bar"
            add(
                {
                    "id": f"width_{who}_{feet:g}_to_{soft:g}",
                    "kind": clause.get("kind") or "requirement",
                    "lever": "exact_width",
                    "departments": depts,
                    "label": f"Soften {who} width {feet:g} → {soft:g} ft",
                    "from": feet,
                    "to": soft,
                    "unit": "ft",
                    "unlocks": "Lets the long edge clear an all-edge length cap.",
                    "apply": False,
                }
            )
        elif lever in {"mass_count", "same_mass", "keep_together"}:
            depts = list(clause.get("departments") or [])
            if depts:
                add(
                    {
                        "id": f"split_{'_'.join(depts[:2])}",
                        "kind": "requirement",
                        "lever": "split_mass",
                        "departments": depts,
                        "label": f"Allow splitting {', '.join(depts)} into another mass",
                        "unlocks": "Smaller plates; easier edge / gym fit.",
                        "apply": False,
                    }
                )

    # Pattern-driven fallbacks when conflict list is thin.
    focus = str((patterns or {}).get("focus") or "")
    stories = session.constraints.get("max_stories")
    if focus in {"edge", "stories", "frontage"} and stories is not None:
        add(
            {
                "id": f"stories_fallback_{int(stories)}",
                "kind": "limitation",
                "lever": "max_stories",
                "label": f"Raise story cap {int(stories)} → {int(stories) + 1}",
                "from": int(stories),
                "to": int(stories) + 1,
                "unlocks": "Failure pattern points at long / wide edges.",
                "apply": False,
            }
        )
    edge = session.constraints.get("max_edge_ft") or session.constraints.get(
        "max_building_length_ft"
    )
    if focus == "edge" and edge is not None:
        feet = float(edge)
        bumped = round(feet * 1.1, 1)
        add(
            {
                "id": f"edge_fallback_{feet:g}",
                "kind": "limitation",
                "lever": "max_edge",
                "label": f"Relax every-edge cap {feet:g} → {bumped:g} ft",
                "from": feet,
                "to": bumped,
                "unit": "ft",
                "unlocks": "Dominant failures are per-mass edge caps.",
                "apply": False,
            }
        )
    frontage = session.constraints.get("max_total_length_ft")
    if focus == "frontage" and frontage is not None:
        feet = float(frontage)
        bumped = round(feet + max(15.0, feet * 0.06), 0)
        add(
            {
                "id": f"frontage_fallback_{feet:g}",
                "kind": "limitation",
                "lever": "site_length",
                "label": f"Relax site frontage {feet:g} → {bumped:g} ft",
                "from": feet,
                "to": bumped,
                "unit": "ft",
                "unlocks": "Dominant failures are combined site length.",
                "apply": False,
            }
        )

    # Gym / anchor fit → suggest separating athletics.
    if focus == "anchor" or "layout_dims" in (patterns.get("dominant_kinds") or []):
        add(
            {
                "id": "split_athletics",
                "kind": "requirement",
                "lever": "split_mass",
                "departments": ["HEALTH & PHYSICAL EDUCATION"],
                "label": "Separate athletics into its own mass",
                "unlocks": "Gym clear dims stop fighting academic plate depth.",
                "apply": False,
            }
        )

    for p in probes:
        p["apply"] = False
    return probes[:8]


# --- internals ----------------------------------------------------------------


def _brief_needs_unsupported(session: Any) -> bool:
    text = " ".join(
        [
            str(session.constraints.get("brief_text") or ""),
            str((session.constraints.get("explore") or {}).get("note") or ""),
        ]
    ).lower()
    briefing = session.constraints.get("briefing") or {}
    for bucket in ("requirements", "limitations", "preferences"):
        for clause in briefing.get(bucket) or []:
            text += " " + str(clause.get("text") or "") + " " + str(clause.get("lever") or "")
    return any(
        token in text
        for token in ("courtyard", "podium", "perpendicular", "cloister")
    )


def _focus_from_kinds(kinds: list[str]) -> str:
    joined = " ".join(kinds)
    if "site_total" in joined or "site_total_length" in joined:
        return "frontage"
    if "site_length" in joined or "site_width" in joined or "site_limit" in joined:
        return "edge"
    if "layout_dims" in joined or "anchor" in joined:
        return "anchor"
    if "gsf_fit" in joined:
        return "stories"
    return "stories"


def _targeted_samples(
    session: Any,
    plan: Any,
    *,
    focus: str,
    budget: int,
) -> list[CoverSample]:
    n = len(session.masses)
    cap = max(1, int(session.constraints.get("max_stories") or 4))
    locks = dict(session.constraints.get("story_lock") or {})
    mass_ids = [m.id for m in session.masses]
    high = tuple(
        int(locks[mid]) if mid in locks else cap for mid in mass_ids
    ) or (cap,) * max(1, n)
    mid = tuple(
        int(locks[mid]) if mid in locks else min(2, cap) for mid in mass_ids
    ) or (min(2, cap),) * max(1, n)

    # Prefer alternate partitions when grouping was open and focus is program fit.
    part_indices = list(range(len(plan.partitions)))
    if focus == "anchor" and len(part_indices) > 1:
        part_order = part_indices[1:] + [0]
    else:
        part_order = part_indices

    envelopes = list(plan.envelopes)
    if focus in {"edge", "frontage"}:
        # Compact / low-rise (elongated) first.
        preferred = [e for e in ("compact", "elongated", "balanced") if e in envelopes]
        envelopes = preferred + [e for e in envelopes if e not in preferred]
    loadings = list(plan.loadings)
    if focus == "edge" and "single" in loadings:
        loadings = ["single"] + [x for x in loadings if x != "single"]

    topologies = list(plan.topologies)
    samples: list[CoverSample] = []
    seen: set[tuple] = set()

    def push(i_p: int, stories: tuple[int, ...], topo: str, loading: str, env: str) -> None:
        key = (i_p, stories, topo, loading, env)
        if key in seen or len(samples) >= budget:
            return
        seen.add(key)
        samples.append(
            CoverSample(
                partition_index=i_p,
                stories=stories,
                topology=topo,
                loading=loading,
                envelope=env,
                label=f"target:{focus}:P{i_p}+{stories}+{topo}+{loading}+{env}",
            )
        )

    story_opts = [high, mid]
    if focus == "stories":
        story_opts = [high] + [
            p for p in story_pattern_library(n, cap, locks=locks, mass_ids=mass_ids) if p != high
        ]

    for i_p in part_order:
        for stories in story_opts:
            for topo in topologies:
                for loading in loadings:
                    for env in envelopes:
                        push(i_p, stories, topo, loading, env)
                        if len(samples) >= budget:
                            return samples
    return samples


def _hard_clause_candidates(session: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    briefing = session.constraints.get("briefing") or {}
    for bucket in ("requirements", "limitations"):
        for clause in briefing.get(bucket) or []:
            lever = str(clause.get("lever") or "")
            if lever not in {
                "max_stories",
                "max_height",
                "max_edge",
                "max_length",
                "site_length",
                "max_total_length",
                "exact_width",
                "max_width",
                "dept_width",
                "mass_count",
                "same_mass",
                "keep_together",
            }:
                continue
            label = _clause_label(clause)
            family = {
                "max_edge": "site_length",
                "max_length": "site_length",
                "site_length": "site_total_length",
                "max_total_length": "site_total_length",
                "exact_width": "site_width",
                "max_width": "site_width",
                "max_stories": "gsf_fit",
            }.get(lever, lever)
            out.append(
                {
                    "kind": clause.get("kind") or bucket[:-1],
                    "lever": lever,
                    "value": clause.get("value"),
                    "unit": clause.get("unit"),
                    "departments": list(clause.get("departments") or []),
                    "label": label,
                    "family": family,
                    "source": "briefing",
                }
            )

    # Constraints that may not be mirrored as briefing rows.
    c = session.constraints
    if c.get("max_stories") is not None and not any(
        x["lever"] == "max_stories" for x in out
    ):
        out.append(
            {
                "kind": "limitation",
                "lever": "max_stories",
                "value": int(c["max_stories"]),
                "label": f"max stories {int(c['max_stories'])}",
                "family": "gsf_fit",
                "source": "constraints",
                "departments": [],
            }
        )
    edge = c.get("max_edge_ft") or c.get("max_building_length_ft")
    if edge is not None and not any(x["lever"] in {"max_edge", "max_length"} for x in out):
        out.append(
            {
                "kind": "limitation",
                "lever": "max_edge",
                "value": float(edge),
                "unit": "ft",
                "label": f"every mass edge ≤ {float(edge):g} ft",
                "family": "site_length",
                "source": "constraints",
                "departments": [],
            }
        )
    if c.get("max_total_length_ft") is not None and not any(
        x["lever"] in {"site_length", "max_total_length"} for x in out
    ):
        out.append(
            {
                "kind": "limitation",
                "lever": "site_length",
                "value": float(c["max_total_length_ft"]),
                "unit": "ft",
                "label": f"site frontage ≤ {float(c['max_total_length_ft']):g} ft",
                "family": "site_total_length",
                "source": "constraints",
                "departments": [],
            }
        )
    return out


def _clause_label(clause: dict[str, Any]) -> str:
    lever = str(clause.get("lever") or "clause").replace("_", " ")
    depts = clause.get("departments") or []
    value = clause.get("value")
    unit = clause.get("unit") or ""
    parts = [lever]
    if depts:
        parts.append(", ".join(str(d) for d in depts))
    if value is not None and value != "":
        parts.append(f"{value}{(' ' + unit) if unit else ''}")
    text = (clause.get("text") or "").strip()
    if text and text not in parts:
        parts.append(text)
    return " · ".join(parts)


def _apply_drop(session: Any, clause: dict[str, Any]) -> None:
    """Temporarily remove one hard clause from the live session."""
    lever = str(clause.get("lever") or "")
    c = session.constraints
    if lever in {"max_stories", "max_height"}:
        c.pop("max_stories", None)
        # Unlock story locks so the solver can grow.
        c.pop("story_lock", None)
    elif lever in {"max_edge", "max_length"}:
        for key in (
            "max_edge_ft",
            "max_building_length_ft",
            "max_building_width_ft",
            "length_limit_is_cap",
        ):
            c.pop(key, None)
        depts = clause.get("departments") or []
        edges = dict(c.get("department_max_edge_ft") or {})
        for d in depts:
            edges.pop(str(d), None)
        if edges:
            c["department_max_edge_ft"] = edges
        else:
            c.pop("department_max_edge_ft", None)
        for mass in session.masses or []:
            c.pop(f"{mass.id}_max_edge_ft", None)
    elif lever in {"site_length", "max_total_length"}:
        c.pop("max_total_length_ft", None)
    elif lever in {"exact_width", "dept_width", "max_width"}:
        depts = set(clause.get("departments") or [])
        widths = dict(c.get("department_widths") or {})
        for d in list(widths):
            if not depts or d in depts:
                widths.pop(d, None)
        if widths:
            c["department_widths"] = widths
        else:
            c.pop("department_widths", None)
        for mass in session.masses or []:
            if depts and not depts.intersection(mass.departments or []):
                continue
            c.pop(f"{mass.id}_width_ft", None)
        c.pop("academic_width_ft", None)
        if lever == "max_width" and not depts:
            c.pop("max_building_width_ft", None)
    elif lever in {"mass_count", "same_mass", "keep_together"}:
        # Soften: clear brief lock so regrouping could help — but we only
        # drop the lock flag for the probe; we do not regroup here.
        session.brief_locked = False
        c.pop("story_lock", None)


def _knowledge_from_patterns(patterns: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for k, n in (patterns.get("kind_counts") or {}).items():
        out.append(
            {
                "region": k,
                "impossible_because": f"{n} illegal cell(s) failed {k}",
                "class": kind,
            }
        )
    if not out and kind:
        out.append(
            {
                "region": "archive",
                "impossible_because": f"COVER produced no legal cells ({kind})",
                "class": kind,
            }
        )
    return out[:12]


def _note_for(kind: str, report: dict[str, Any]) -> str:
    probes = report.get("probes") or []
    conflict = report.get("conflict") or {}
    bits = [f"DIAGNOSE: {kind}."]
    if conflict.get("note"):
        bits.append(str(conflict["note"]))
    if probes:
        bits.append(
            f"{len(probes)} relaxation probe(s) for the architect — none applied."
        )
    else:
        bits.append("No automatic relaxation. Change the brief, then COVER again.")
    targeted = report.get("targeted") or {}
    if targeted.get("ran"):
        bits.append(str(targeted.get("note") or "Targeted COVER ran."))
    return " ".join(bits)
