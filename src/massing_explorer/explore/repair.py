"""
Project an illegal COVER idea onto the nearest legal twin.

COVER explores architectural ideas. REPAIR restores the idea and runs
realize(s) to fill feet. It does not regroup programs, bump stories, chase
preference scores, or invent a second sampler. Story ±1 stays an MCTS lever.
"""

from __future__ import annotations

from typing import Any

from . import archive as archive_mod
from .feasibility import (
    FRONTIER_DISTANCE,
    feasibility_distance_of,
    idea_of,
)
from .realize import realize
from .saturate import REPAIR_CAP, read_explore_budget
from .strategy import partition_id

REPAIR_CANDIDATES = 8


def run_repair(
    session: Any,
    archive: dict[str, Any],
) -> dict[str, Any]:
    """Restore near-feasible ideas and fill dimensions via realize."""
    budget = int(read_explore_budget(session).get("repair") or REPAIR_CAP)
    budget = max(0, budget)
    origin = archive_mod.capture(session)
    candidates = _repair_candidates(archive)
    report: dict[str, Any] = {
        "ran": bool(candidates) and budget > 0,
        "candidates": len(candidates),
        "tried": 0,
        "legalized": 0,
        "moves": [],
        "note": "",
    }
    if not candidates or budget <= 0:
        report["note"] = "REPAIR: no near-feasible COVER samples to project."
        archive["repair"] = report
        archive_mod.restore_snapshot(session, origin)
        return report

    spent = 0
    legalized = 0
    moves: list[str] = []

    for entry in candidates:
        if spent >= budget:
            break
        if not entry.get("snapshot"):
            continue
        archive_mod.restore_entry(session, entry)
        held_partition = partition_id(session)
        held_stories = {m.id: int(m.story_count) for m in session.masses}
        before = feasibility_distance_of(entry)
        result, perf = realize(session)
        for mass in session.masses or []:
            if mass.id in held_stories:
                mass.story_count = held_stories[mass.id]
        if partition_id(session) != held_partition:
            archive_mod.restore_entry(session, entry)
            continue
        reason = "COVER repair: realize(s)"
        inserted = archive_mod.insert(archive, session, result, perf, reason=reason)
        inserted["repair"] = reason
        if hasattr(result, "to_dict"):
            session.last_massing = result.to_dict()
        spent += 1
        moves.append(reason)
        after_legal = bool(perf.get("fits_limitations"))
        after_raw = perf.get("feasibility_distance")
        after_d = (
            0.0
            if after_legal
            else (float(after_raw) if after_raw is not None else 1.0)
        )
        if after_legal:
            legalized += 1
            continue
        if after_d + 1e-9 >= before:
            archive_mod.restore_entry(session, entry)

    archive_mod.restore_snapshot(session, origin)
    archive_mod.refresh_frontier(archive)
    report["tried"] = spent
    report["legalized"] = legalized
    report["moves"] = moves[:12]
    report["note"] = (
        f"REPAIR: {spent} realize projection(s) on {len(candidates)} idea(s), "
        f"{legalized} became legal."
    )
    archive["repair"] = report
    return report


def _repair_candidates(archive: dict[str, Any]) -> list[dict[str, Any]]:
    cells = list((archive.get("cells") or {}).values())
    legal_ideas = {idea_of(e) for e in cells if e.get("fits_limitations") and idea_of(e)}
    best: dict[str, dict[str, Any]] = {}
    for entry in cells:
        if entry.get("fits_limitations"):
            continue
        idea = idea_of(entry)
        if not idea or idea in legal_ideas:
            continue
        dist = feasibility_distance_of(entry)
        if dist >= FRONTIER_DISTANCE:
            continue
        prev = best.get(idea)
        if prev is None or dist < feasibility_distance_of(prev):
            best[idea] = entry
    ranked = sorted(best.values(), key=feasibility_distance_of)
    return ranked[:REPAIR_CANDIDATES]
