"""
Search controller: COVER / LEARN / REFINE over the archive.

Default after a brief: COVER until story margins are tried, REFINE around
several legal lineages, LEARN prepares an A/B pair. A written brief is not
a pairwise sample.
"""

from __future__ import annotations

from typing import Any

from ..solver import solve_massing_study
from ..tools import solve_dimensions
from . import archive as archive_mod
from .partitions import apply_partition, enumerate_partitions
from .performance import measure
from .preference import next_pair, schemes_from_archive, taste_weight
from .strategy import grouping_is_required, partition_id, read_strategy

REFINE_CAP = 6


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
    plan_report: dict[str, Any] = {"ran": False}
    mcts_report: dict[str, Any] = {"ran": False}
    bayes_report: dict[str, Any] = {"ran": False}

    if mode == "cover":
        _cover(session, archive)
        plan_report = _run_planner(session, archive, client, plan)
        mcts_report = _run_mcts(session, archive, client, plan)
        bayes_report = _run_bayes(session, archive)
        refine_report = _refine(session, archive)
        learning = _prepare_learn(archive, learning)
    elif mode == "learn":
        if not archive.get("attempts"):
            _cover(session, archive)
        learning = _prepare_learn(archive, learning)
    else:
        if not archive.get("attempts"):
            _cover(session, archive)
        refine_report = _refine(session, archive)
        learning = _prepare_learn(archive, learning)

    locked = grouping_is_required(session)
    note = archive_mod.explain(archive, locked)
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
    solved = solve_dimensions(session)

    store["archive"] = archive
    store["strategy"] = strategy
    store["mode"] = mode
    store["kept_cell"] = kept.get("cell") if kept else None
    store["learning"] = learning
    store["refine"] = refine_report
    store["planner"] = plan_report
    store["mcts"] = {
        "ran": mcts_report.get("ran"),
        "simulations": mcts_report.get("simulations"),
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


def _run_mcts(session: Any, archive: dict[str, Any], client: Any, plan: Any) -> dict[str, Any]:
    from .mcts import run_mcts

    return run_mcts(session, archive=archive, client=client, plan=plan)


def _run_bayes(session: Any, archive: dict[str, Any]) -> dict[str, Any]:
    from .bayes import run_bayes

    return run_bayes(session, archive=archive)


def _cover(session: Any, archive: dict[str, Any]) -> None:
    origin = archive_mod.capture(session)
    archive["stated_partition"] = partition_id(session)
    _cover_geometry(session, archive)
    _cover_topology(session, archive)
    if grouping_is_required(session):
        archive_mod.restore_snapshot(session, origin)
        return
    stated = partition_signature_of(session)
    for item in enumerate_partitions(session):
        if partition_signature_of_groups(item["groups"]) == stated:
            continue
        apply_partition(session, item["groups"])
        _evaluate(session, archive, f"open P: {item['reason']}")
    archive_mod.restore_snapshot(session, origin)


def _cover_topology(session: Any, archive: dict[str, Any]) -> None:
    """Sample paired bars only when T is open and D is stated."""
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
    learning = dict(learning or {})
    weights = learning.get("weights") or {}
    comparisons = list(learning.get("comparisons") or [])
    schemes = schemes_from_archive(archive)
    learning["pending_pair"] = next_pair(schemes, weights, comparisons)
    if not learning.get("note"):
        from .preference import describe_weights

        learning["note"] = describe_weights(weights)
    learning.setdefault("weights", weights)
    learning.setdefault("comparisons", comparisons)
    return learning


def select_elites(archive: dict[str, Any], weights: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """Keep several legal cells, preferring different P when P was open."""
    legal = archive_mod.legal_cells(archive)
    if not legal:
        return []
    ranked = sorted(legal, key=lambda e: taste_weight(e, weights), reverse=True)
    kept = [ranked[0]]
    for entry in ranked[1:]:
        if entry.get("partition") != kept[0].get("partition"):
            kept.append(entry)
            break
        here_t = ((entry.get("strategy") or {}).get("T") or {}).get("kind")
        kept_t = ((kept[0].get("strategy") or {}).get("T") or {}).get("kind")
        if here_t and kept_t and here_t != kept_t:
            kept.append(entry)
            break
    if len(kept) < 2 and len(ranked) > 1:
        kept.append(ranked[1])
    light = min(ranked, key=lambda e: taste_weight(e, weights))
    if light.get("cell") not in {e.get("cell") for e in kept}:
        kept.append(light)
    return kept[:3]


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
    store = session.constraints.get("explore") or {}
    weights = ((store.get("learning") or {}).get("weights") or {})
    elites = select_elites(archive, weights)
    if len(elites) < 1:
        return {"ran": False, "reason": "No legal lineage to refine."}
    remaining = REFINE_CAP
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
    lock = session.constraints.get("story_lock") or {}
    cap = max(1, int(session.constraints.get("max_stories") or 4))
    for elite in elites:
        allowed = int(allocation.get(elite["cell"]) or 0)
        if allowed <= 0:
            continue
        archive_mod.restore_entry(session, elite)
        taken = 0
        for mass in list(session.masses):
            if taken >= allowed or tuned >= REFINE_CAP:
                break
            if mass.id in lock:
                continue
            current = int(mass.story_count)
            for nxt in (current - 1, current + 1):
                if taken >= allowed or tuned >= REFINE_CAP:
                    break
                if nxt < 1 or nxt > cap:
                    continue
                mass.story_count = nxt
                _evaluate(session, archive, f"refine {mass.name} to {nxt} stories")
                tuned += 1
                taken += 1
                new_legal = archive_mod.legal_cells(archive)
                if any(
                    e.get("cell") == elite.get("cell")
                    and taste_weight(e, weights) > taste_weight(elite, weights)
                    for e in new_legal
                ):
                    improved = True
            mass.story_count = current
        archive_mod.restore_entry(session, elite)

    if improved:
        reason = reason_prefix + "REFINE improved a kept lineage with a one-step story move."
    elif collapsed:
        reason = reason_prefix + "REFINE stopped concentrating on one basin."
    else:
        reason = reason_prefix + "REFINE tried one-step neighbors inside kept lineages."
    return {
        "ran": True,
        "improved": improved,
        "tries": tuned,
        "elites": len(elites),
        "allocation": allocation,
        "effective_sample_size": round(effective_sample_size(raw), 3) if raw else 0.0,
        "reason": reason,
    }


def _pick_kept(
    archive: dict[str, Any],
    weights: dict[str, float] | None,
    preferred_partition: str | None = None,
) -> dict[str, Any]:
    legal = archive_mod.legal_cells(archive)
    if legal:
        tasted = bool(weights and any(abs(float(v)) > 1e-9 for v in weights.values()))
        if not tasted and preferred_partition:
            same = [e for e in legal if e.get("partition") == preferred_partition]
            if same:
                return max(same, key=lambda e: taste_weight(e, weights))
        return max(legal, key=lambda e: taste_weight(e, weights))
    # No legal cell: keep the least-bad attempt so the UI still shows the brief.
    cells = list((archive.get("cells") or {}).values())
    if not cells:
        return {}
    def _badness(entry: dict[str, Any]) -> tuple[int, float]:
        perf = entry.get("performance") or {}
        return (
            int(perf.get("limit_fails") or 0) + int(perf.get("failed_checks") or 0),
            float(perf.get("preference_distance") or 0.0),
        )
    return min(cells, key=_badness)
