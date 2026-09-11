"""
Search controller: COVER / LEARN / REFINE over the archive.

Default after a brief: adaptive multi-axis COVER (start ~40, expand while
new regions or feature encodings appear, cap ~120), then planner / MCTS
from several COVER elites / sequential BO / REFINE local neighbors / LEARN pair.

Zero legal COVER cells → DIAGNOSE (not LEARN). LEARN / REFINE only run once
a feasible design space exists.
"""

from __future__ import annotations

from typing import Any

from ..solver import solve_massing_study
from ..tools import solve_dimensions
from . import archive as archive_mod
from .cover import run_cover
from .diagnose import diagnose
from .partitions import apply_partition, enumerate_partitions
from .performance import measure
from .preference import next_pair, schemes_from_archive, taste_weight
from .saturate import REFINE_CAP, REFINE_MIN, Saturation, encodings_from_archive, feature_is_novel, read_explore_budget
from .strategy import grouping_is_required, partition_id, read_strategy

def run_search(session: Any, mode: str = "cover", client: Any = None, plan: Any = None) -> dict[str, Any]:
    mode = (mode or "cover").lower()
    if mode not in {"cover", "learn", "refine"}:
        mode = "cover"

    store = dict(session.constraints.get("explore") or {})
    archive = archive_mod.load_archive(session)
    archive["mode"] = mode
    strategy = read_strategy(session)
    refine_report: dict[str, Any] = {"ran": False}
    learning = dict(store.get("learning") or {})
    weights = dict(learning.get("weights") or {})
    plan_report: dict[str, Any] = {"ran": False}
    mcts_report: dict[str, Any] = {"ran": False}
    bayes_report: dict[str, Any] = {"ran": False}
    diagnose_report: dict[str, Any] = {"ran": False}

    if mode == "cover":
        _cover(session, archive)
        if not archive_mod.legal_cells(archive):
            diagnose_report = diagnose(
                session,
                archive,
                evaluate=_evaluate,
            )
            # Still none → stop. Do not invent a feasible space via LEARN.
            if not archive_mod.legal_cells(archive):
                learning = _prepare_learn(archive, learning)
            else:
                plan_report = _run_planner(session, archive, client, plan)
                mcts_report = _run_mcts(session, archive, client, plan, weights)
                bayes_report = _run_bayes(session, archive, weights)
                refine_report = _refine(session, archive)
                learning = _prepare_learn(archive, learning)
        else:
            plan_report = _run_planner(session, archive, client, plan)
            mcts_report = _run_mcts(session, archive, client, plan, weights)
            bayes_report = _run_bayes(session, archive, weights)
            refine_report = _refine(session, archive)
            learning = _prepare_learn(archive, learning)
    elif mode == "learn":
        if not archive.get("attempts"):
            _cover(session, archive)
        if not archive_mod.legal_cells(archive):
            diagnose_report = diagnose(session, archive, evaluate=_evaluate)
        learning = _prepare_learn(archive, learning)
    else:
        if not archive.get("attempts"):
            _cover(session, archive)
        if archive_mod.legal_cells(archive):
            refine_report = _refine(session, archive)
        else:
            diagnose_report = diagnose(session, archive, evaluate=_evaluate)
        learning = _prepare_learn(archive, learning)

    locked = grouping_is_required(session)
    note = archive_mod.explain(archive, locked)
    if diagnose_report.get("ran"):
        note += " " + str(diagnose_report.get("note") or "")
    if plan_report.get("ran"):
        note += " " + str(plan_report.get("note") or "")
    if mcts_report.get("ran"):
        note += " " + str(mcts_report.get("note") or "")
    if bayes_report.get("ran"):
        note += " " + str(bayes_report.get("note") or "")
    if refine_report.get("ran"):
        note += " " + str(refine_report.get("reason") or "")
    if learning.get("pending_pair"):
        pair = learning["pending_pair"]
        note += (
            f" LEARN: compare A={pair['a']} or B={pair['b']} "
            f"({pair.get('kind') or 'pair'}). A written brief is not a choice."
        )
    elif mode == "learn":
        note += " LEARN: not enough legal cells for a pair."
    elif diagnose_report.get("ran") and not archive_mod.legal_cells(archive):
        note += " LEARN/REFINE skipped — no legal COVER cells yet."

    from .csp import describe_csp
    from .explain import empty_cells
    from .robustness import probe_strategy
    from .topology import describe_topology

    explain_report = empty_cells(session, archive)
    note += " " + str(explain_report.get("note") or "")
    csp_report = describe_csp(session)
    note += " " + str(csp_report.get("note") or "")
    topo_report = describe_topology(session)
    note += " " + str(topo_report.get("note") or "")
    archive["note"] = note

    weights = (learning.get("weights") or {}) if learning else {}
    kept = _pick_kept(archive, weights, archive.get("stated_partition"))
    if kept:
        archive_mod.restore_entry(session, kept)
    robust_report = {"ran": False}
    if session.masses:
        robust_report = probe_strategy(session)
        note += " " + str(robust_report.get("note") or "")
        archive["note"] = note
        if kept and robust_report.get("ran") and robust_report.get("score") is not None:
            score = float(robust_report["score"])
            perf = dict(kept.get("performance") or {})
            perf["_robustness_probe"] = score
            from .performance import eval_composites

            perf.update(eval_composites(perf, session))
            kept["performance"] = perf
            cells = archive.get("cells") or {}
            cell_id = kept.get("cell")
            if cell_id and cell_id in cells:
                cells[cell_id]["performance"] = perf
    solved = solve_dimensions(session)

    store["archive"] = archive
    store["strategy"] = strategy
    store["mode"] = mode
    store["kept_cell"] = kept.get("cell") if kept else None
    store["learning"] = learning
    store["refine"] = refine_report
    store["diagnose"] = {
        "ran": diagnose_report.get("ran"),
        "class": diagnose_report.get("class"),
        "note": diagnose_report.get("note"),
        "patterns": diagnose_report.get("patterns") or {},
        "targeted": diagnose_report.get("targeted") or {},
        "conflict": diagnose_report.get("conflict"),
        "probes": diagnose_report.get("probes") or [],
        "knowledge": diagnose_report.get("knowledge") or [],
    }
    store["planner"] = plan_report
    store["mcts"] = {
        "ran": mcts_report.get("ran"),
        "simulations": mcts_report.get("simulations"),
        "depth": mcts_report.get("depth"),
        "roots": mcts_report.get("roots") or [],
        "saturated": mcts_report.get("saturated"),
        "applied": mcts_report.get("applied"),
        "illegal": mcts_report.get("illegal"),
        "unsupported": mcts_report.get("unsupported"),
        "prior": mcts_report.get("prior") or [],
        "best_path": mcts_report.get("best_path") or [],
        "nodes": mcts_report.get("nodes") or [],
        "baseline": mcts_report.get("baseline"),
        "note": mcts_report.get("note"),
    }
    store["bayes"] = {
        "ran": bayes_report.get("ran"),
        "spent": bayes_report.get("spent"),
        "budget": bayes_report.get("budget"),
        "saturated": bayes_report.get("saturated"),
        "observed": bayes_report.get("observed"),
        "candidates": bayes_report.get("candidates") or [],
        "picked": bayes_report.get("picked") or [],
        "next": bayes_report.get("next"),
        "note": bayes_report.get("note"),
    }
    store["explain"] = {
        "note": explain_report.get("note"),
        "sentences": (explain_report.get("sentences") or [])[:12],
        "items": [
            {"kind": i.get("kind"), "sentence": i.get("sentence")}
            for i in (explain_report.get("items") or [])
        ][:12],
        "unsupported": explain_report.get("unsupported"),
        "infeasible": explain_report.get("infeasible"),
        "locked": explain_report.get("locked"),
        "unsampled": explain_report.get("unsampled"),
    }
    store["robustness"] = {
        "ran": robust_report.get("ran"),
        "survived": robust_report.get("survived"),
        "collapsed": robust_report.get("collapsed"),
        "score": robust_report.get("score"),
        "note": robust_report.get("note"),
        "probes": robust_report.get("probes") or [],
        "departments": robust_report.get("departments") or [],
    }
    store["csp"] = {
        "ran": csp_report.get("ran"),
        "locked": csp_report.get("locked"),
        "feasible": csp_report.get("feasible_count"),
        "shown": csp_report.get("shown"),
        "note": csp_report.get("note"),
        "rejected": csp_report.get("rejected") or [],
        "atoms": csp_report.get("atoms") or [],
        "apart": csp_report.get("apart") or [],
        "chosen": [
            {"reason": c.get("reason"), "source": c.get("source")}
            for c in (csp_report.get("chosen") or [])
        ],
    }
    store["topology"] = {
        "ran": topo_report.get("ran"),
        "locked": topo_report.get("locked"),
        "current": topo_report.get("current"),
        "drawable": topo_report.get("drawable"),
        "unsupported": topo_report.get("unsupported"),
        "site": topo_report.get("site"),
        "note": topo_report.get("note"),
    }
    store["note"] = note
    store["partitions"] = sorted(
        {e.get("partition") for e in (archive.get("cells") or {}).values() if e.get("partition")}
    )
    session.constraints["explore"] = store
    if hasattr(session, "save"):
        session.save()

    return {
        "ok": True,
        "mode": mode,
        "strategy": strategy,
        "archive": {
            "attempts": archive.get("attempts"),
            "legal": len(archive_mod.legal_cells(archive)),
            "cells": sorted((archive.get("cells") or {}).keys()),
            "unsupported": archive.get("unsupported"),
            "note": archive.get("note"),
        },
        "learning": {
            "pending_pair": learning.get("pending_pair"),
            "comparisons": len(learning.get("comparisons") or []),
            "note": learning.get("note"),
        },
        "refine": refine_report,
        "diagnose": store.get("diagnose") or diagnose_report,
        "planner": {
            "ran": plan_report.get("ran"),
            "applied": plan_report.get("applied"),
            "illegal": plan_report.get("illegal"),
            "unsupported": plan_report.get("unsupported"),
            "note": plan_report.get("note"),
        },
        "mcts": {
            "ran": mcts_report.get("ran"),
            "simulations": mcts_report.get("simulations"),
            "applied": mcts_report.get("applied"),
            "illegal": mcts_report.get("illegal"),
            "unsupported": mcts_report.get("unsupported"),
            "note": mcts_report.get("note"),
        },
        "bayes": {
            "ran": bayes_report.get("ran"),
            "spent": bayes_report.get("spent"),
            "budget": bayes_report.get("budget"),
            "next": bayes_report.get("next"),
            "note": bayes_report.get("note"),
        },
        "explain": store["explain"],
        "robustness": {
            "ran": robust_report.get("ran"),
            "survived": robust_report.get("survived"),
            "collapsed": robust_report.get("collapsed"),
            "score": robust_report.get("score"),
            "note": robust_report.get("note"),
        },
        "csp": {
            "ran": csp_report.get("ran"),
            "locked": csp_report.get("locked"),
            "feasible": csp_report.get("feasible_count"),
            "shown": csp_report.get("shown"),
            "note": csp_report.get("note"),
        },
        "topology": {
            "ran": topo_report.get("ran"),
            "locked": topo_report.get("locked"),
            "current": topo_report.get("current"),
            "note": topo_report.get("note"),
        },
        "kept": kept.get("cell") if kept else None,
        "partitions": sorted(
            {e.get("partition") for e in (archive.get("cells") or {}).values() if e.get("partition")}
        ),
        "solved": solved,
        "note": note,
    }


def _run_planner(session: Any, archive: dict[str, Any], client: Any, plan: Any) -> dict[str, Any]:
    from .planner import run_planner

    def evaluate(current: Any, reason: str) -> None:
        _evaluate(current, archive, reason)

    return run_planner(session, client=client, plan=plan, archive=archive, evaluate=evaluate)


def _run_mcts(
    session: Any,
    archive: dict[str, Any],
    client: Any,
    plan: Any,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    from .mcts import run_mcts

    return run_mcts(session, archive=archive, client=client, plan=plan, weights=weights)


def _run_bayes(
    session: Any,
    archive: dict[str, Any],
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    from .bayes import run_bayes

    return run_bayes(session, archive=archive, weights=weights)


def _cover(session: Any, archive: dict[str, Any]) -> None:
    """Adaptive multi-axis COVER: P × stories × T × loading × envelope."""

    def evaluate(current: Any, store: dict[str, Any], reason: str) -> None:
        _evaluate(current, store, reason)

    cfg = dict(session.constraints.get("cover_budget") or {})
    run_cover(
        session,
        archive,
        evaluate=evaluate,
        start=int(cfg.get("start", 40)),
        step_small=int(cfg.get("step_small", 10)),
        step_large=int(cfg.get("step_large", 20)),
        max_attempts=int(cfg.get("max", 120)),
    )


def _cover_topology(session: Any, archive: dict[str, Any]) -> None:
    """Legacy helper retained for tests that call it directly."""
    from ..tools import pair_masses
    from .topology import pairing_proposals, stated_frontage_ft, topology_is_required

    if topology_is_required(session):
        return
    frontage = stated_frontage_ft(session)
    if frontage is None or len(session.masses or []) < 2:
        return
    held = archive_mod.capture(session)
    for ids in pairing_proposals(session):
        out = pair_masses(session, ids, float(frontage), length_is_cap=True)
        if not out.get("ok"):
            archive_mod.restore_snapshot(session, held)
            continue
        _evaluate(
            session,
            archive,
            f"open T: paired bars {' + '.join(ids)} under {frontage:g} ft (cap)",
        )
        archive_mod.restore_snapshot(session, held)


def partition_signature_of(session: Any) -> frozenset:
    from .partitions import partition_signature

    return partition_signature([list(m.departments) for m in session.masses])


def partition_signature_of_groups(groups: list[dict[str, Any]]) -> frozenset:
    from .partitions import partition_signature

    return partition_signature(groups)


def _cover_geometry(session: Any, archive: dict[str, Any]) -> None:
    """Legacy single-axis story sweep — kept for direct unit tests."""
    _evaluate(session, archive, "stated strategy")
    lock = session.constraints.get("story_lock") or {}
    cap = max(1, int(session.constraints.get("max_stories") or 4))
    baseline = {m.id: int(m.story_count) for m in session.masses}
    for mass in list(session.masses):
        if mass.id in lock:
            continue
        for stories in range(1, cap + 1):
            if stories == baseline.get(mass.id):
                continue
            mass.story_count = stories
            _evaluate(session, archive, f"cover stories on {mass.name}: {stories}")
        mass.story_count = baseline[mass.id]

    if session.masses:
        _ingest_site_search(session, archive)

    for mass in session.masses:
        mass.story_count = baseline.get(mass.id, mass.story_count)


def _evaluate(session: Any, archive: dict[str, Any], reason: str) -> None:
    result = solve_massing_study(session)
    performance = measure(result, session, archive=archive)
    archive_mod.insert(archive, session, result, performance, reason=reason)


def _ingest_site_search(session: Any, archive: dict[str, Any]) -> None:
    from ..search import SiteEnvelope, apply_scheme as apply_candidate, search_schemes

    envelope = SiteEnvelope(
        max_building_length_ft=session.constraints.get("max_building_length_ft"),
        max_building_width_ft=session.constraints.get("max_building_width_ft"),
        max_total_length_ft=session.constraints.get("max_total_length_ft"),
        max_stories=int(session.constraints.get("max_stories") or 4),
    )
    held = {
        m.id: (m.story_count, session.constraints.get(f"{m.id}_width_ft"))
        for m in session.masses
    }
    candidates, _notes = search_schemes(session, envelope, preference="balanced", top_n=8)
    session.last_search = [c.to_dict() for c in candidates]
    for candidate in candidates:
        apply_candidate(session, candidate, save=False)
        _evaluate(session, archive, "site-envelope G variant")
    for mass in session.masses:
        stories, width = held.get(mass.id, (mass.story_count, None))
        mass.story_count = stories
        key = f"{mass.id}_width_ft"
        if width is None:
            session.constraints.pop(key, None)
        else:
            session.constraints[key] = width


def _prepare_learn(archive: dict[str, Any], learning: dict[str, Any]) -> dict[str, Any]:
    from .preference import MAX_LEARN_COMPARISONS, describe_weights, next_pair, schemes_from_archive

    learning = dict(learning or {})
    weights = learning.get("weights") or {}
    comparisons = list(learning.get("comparisons") or [])
    schemes = schemes_from_archive(archive)
    learning["pending_pair"] = next_pair(schemes, weights, comparisons)
    if not learning.get("note"):
        learning["note"] = describe_weights(weights)
    learning.setdefault("weights", weights)
    learning.setdefault("comparisons", comparisons)
    learning["max_comparisons"] = MAX_LEARN_COMPARISONS
    return learning


def select_elites(archive: dict[str, Any], weights: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """Keep several legal cells, preferring different P when P was open."""
    return [row["entry"] for row in select_elites_explained(archive, weights)]


def _cell_badness(entry: dict[str, Any]) -> tuple:
    """Fewer failed limits first, then smaller longest plan edge."""
    perf = entry.get("performance") or {}
    max_edge = 0.0
    for plate in entry.get("plates") or []:
        try:
            max_edge = max(
                max_edge,
                float(plate.get("width_ft") or 0),
                float(plate.get("length_ft") or 0),
            )
        except (TypeError, ValueError):
            continue
    for length in perf.get("lengths") or []:
        try:
            max_edge = max(max_edge, float(length or 0))
        except (TypeError, ValueError):
            continue
    return (
        int(perf.get("limit_fails") or 0) + int(perf.get("failed_checks") or 0),
        max_edge,
        float(perf.get("preference_distance") or 0.0),
    )


def select_elites_explained(
    archive: dict[str, Any], weights: dict[str, float] | None = None
) -> list[dict[str, Any]]:
    """Same elites as select_elites, with a why-string for the UI."""
    from .performance import prefer_clean_splits

    legal_raw = archive_mod.legal_cells(archive)
    legal = prefer_clean_splits(legal_raw)
    if not legal:
        # Never promote awkward splits into top candidates.
        if legal_raw:
            return []
        cells = list((archive.get("cells") or {}).values())
        if not cells:
            return []
        ranked = sorted(cells, key=_cell_badness)
        return [
            {
                "entry": entry,
                "why": "No cell under limits. Closest COVER sample (fewest failed edges).",
            }
            for entry in ranked[:3]
        ]
    tasted = bool(weights and any(abs(float(v)) > 1e-9 for v in weights.values()))
    ranked = sorted(legal, key=lambda e: taste_weight(e, weights), reverse=True)
    best_why = (
        "Highest LEARN taste among legal cells (weird program splits are illegal)."
        if tasted
        else "Best stated-fit among legal cells — weird program splits are deal-breakers."
    )
    picked: list[dict[str, Any]] = [{"entry": ranked[0], "why": best_why}]

    for entry in ranked[1:]:
        if entry.get("partition") != picked[0]["entry"].get("partition"):
            picked.append({"entry": entry, "why": "Different program organization (P)."})
            break
        here_t = ((entry.get("strategy") or {}).get("T") or {}).get("kind")
        kept_t = ((picked[0]["entry"].get("strategy") or {}).get("T") or {}).get("kind")
        if here_t and kept_t and here_t != kept_t:
            picked.append({"entry": entry, "why": f"Different topology ({here_t} vs {kept_t})."})
            break
        here_e = ((entry.get("strategy") or {}).get("G") or {}).get("envelope")
        kept_e = ((picked[0]["entry"].get("strategy") or {}).get("G") or {}).get("envelope")
        if here_e and kept_e and here_e != kept_e:
            picked.append({"entry": entry, "why": f"Different envelope ({here_e} vs {kept_e})."})
            break
    if len(picked) < 2 and len(ranked) > 1:
        picked.append({"entry": ranked[1], "why": "Second-best stated-fit; keeps a nearby lineage."})
    light = min(ranked, key=lambda e: taste_weight(e, weights))
    if light.get("cell") not in {row["entry"].get("cell") for row in picked}:
        picked.append(
            {
                "entry": light,
                "why": "Contrast lineage — lowest fit still kept so search does not collapse to one basin.",
            }
        )
    return picked[:3]


def allocate_local_tries(elites: list[dict[str, Any]], budget: int, weights: dict[str, float] | None, floor: int = 1) -> dict[str, int]:
    if not elites or budget <= 0:
        return {}
    floor = max(0, int(floor))
    each = min(floor, budget // len(elites)) if elites else 0
    counts = {e["cell"]: each for e in elites}
    left = budget - sum(counts.values())
    raw = [max(0.0, taste_weight(e, weights)) for e in elites]
    total = sum(raw)
    if total <= 0 or left <= 0:
        return counts
    order = sorted(range(len(elites)), key=lambda i: raw[i], reverse=True)
    step = total / left
    cursor = step / 2.0
    running = 0.0
    index = 0
    for _ in range(left):
        while index < len(order) - 1 and running + raw[order[index]] <= cursor:
            running += raw[order[index]]
            index += 1
        counts[elites[order[index]]["cell"]] += 1
        cursor += step
    return counts


def effective_sample_size(weights: list[float]) -> float:
    total = sum(max(0.0, float(w)) for w in weights)
    if total <= 0:
        return 0.0
    return (total * total) / sum(max(0.0, float(w)) ** 2 for w in weights)


def _refine(session: Any, archive: dict[str, Any]) -> dict[str, Any]:
    from .mcts import catalog_actions

    store = session.constraints.get("explore") or {}
    weights = ((store.get("learning") or {}).get("weights") or {})
    elites = select_elites(archive, weights)
    if len(elites) < 1:
        return {"ran": False, "reason": "No legal lineage to refine."}
    budget = read_explore_budget(session)
    remaining = max(1, min(int(budget["refine"]), REFINE_CAP))
    raw = [taste_weight(e, weights) for e in elites]
    collapsed = len(elites) > 1 and effective_sample_size(raw) < 1.25
    if collapsed:
        allocation = {e["cell"]: 1 for e in elites}
        leftover = remaining - len(elites)
        if leftover > 0:
            allocation[elites[0]["cell"]] += leftover
        reason_prefix = "Elite weights collapsed; each lineage kept a floor of one try. "
    else:
        allocation = allocate_local_tries(elites, remaining, weights, floor=1)
        reason_prefix = ""

    improved = False
    tuned = 0
    kinds: set[str] = set()
    sat = Saturation(window=3, min_steps=min(REFINE_MIN, remaining))
    known_feat = encodings_from_archive(archive)
    known_cells = set((archive.get("cells") or {}).keys())
    for elite in elites:
        allowed = int(allocation.get(elite["cell"]) or 0)
        if allowed <= 0:
            continue
        archive_mod.restore_entry(session, elite)
        actions = catalog_actions(session, include_unsupported=False)
        taken = 0
        for action in actions:
            if taken >= allowed or tuned >= remaining or sat.stop():
                break
            held = archive_mod.capture(session)
            try:
                out = apply_action_safe(session, action)
            except Exception:
                archive_mod.restore_snapshot(session, held)
                continue
            if not out.get("ok"):
                archive_mod.restore_snapshot(session, held)
                continue
            _evaluate(session, archive, f"refine {action.get('op')}")
            tuned += 1
            taken += 1
            kinds.add(str(action.get("op") or ""))
            from .strategy import cell_key, read_strategy
            from .bayes import encode_strategy, evaluation_reward

            key = cell_key(session)
            feat = encode_strategy(read_strategy(session))
            new_legal = archive_mod.legal_cells(archive)
            better = any(
                e.get("cell") == elite.get("cell")
                and taste_weight(e, weights) > taste_weight(elite, weights)
                for e in new_legal
            ) or any(
                taste_weight(e, weights) > taste_weight(elite, weights)
                and e.get("partition") == elite.get("partition")
                for e in new_legal
            )
            gained = better or key not in known_cells or feature_is_novel(feat, known_feat)
            if better:
                improved = True
            if key not in known_cells:
                known_cells.add(key)
            known_feat.append(feat)
            sat.observe(gained)
            archive_mod.restore_snapshot(session, held)
        archive_mod.restore_entry(session, elite)

    extra = ""
    if kinds:
        extra = " Moves: " + ", ".join(sorted(kinds)) + "."
    if sat.stop():
        extra += " Stopped on saturation."
    if improved:
        reason = reason_prefix + "REFINE improved a kept lineage with a local neighbor." + extra
    elif collapsed:
        reason = reason_prefix + "REFINE stopped concentrating on one basin." + extra
    else:
        reason = reason_prefix + "REFINE tried local neighbors inside kept lineages." + extra
    return {
        "ran": True,
        "improved": improved,
        "tries": tuned,
        "elites": len(elites),
        "allocation": allocation,
        "saturated": sat.stop(),
        "effective_sample_size": round(effective_sample_size(raw), 3) if raw else 0.0,
        "reason": reason,
    }


def apply_action_safe(session: Any, action: dict[str, Any]) -> dict[str, Any]:
    from .actions import apply_action

    return apply_action(session, action)


def _pick_kept(
    archive: dict[str, Any],
    weights: dict[str, float] | None,
    preferred_partition: str | None = None,
) -> dict[str, Any]:
    from .performance import prefer_clean_splits

    tasted = bool(weights and any(abs(float(v)) > 1e-9 for v in (weights or {}).values()))
    if not tasted:
        stated = prefer_clean_splits(
            [
                e
                for e in (archive.get("cells") or {}).values()
                if str(e.get("reason") or "").startswith("COVER: stated")
                and e.get("fits_limitations")
            ]
        )
        if stated:
            return max(stated, key=lambda e: taste_weight(e, weights))
    legal = prefer_clean_splits(archive_mod.legal_cells(archive))
    if legal:
        if not tasted and preferred_partition:
            same = [e for e in legal if e.get("partition") == preferred_partition]
            if same:
                return max(same, key=lambda e: taste_weight(e, weights))
        return max(legal, key=lambda e: taste_weight(e, weights))
    # No legal cell: keep the least-bad COVER sample, not an illegal stated scheme.
    cells = list((archive.get("cells") or {}).values())
    if not cells:
        return {}
    return min(cells, key=_cell_badness)
