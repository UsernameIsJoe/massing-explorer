"""
Rescue a near-feasible idea.

Dimensional misses are filled by realize(s). If the idea is still illegal,
one strategic nudge is chosen from the violation (stories, split, envelope)
and realized again. Progress is d_before - d_after. Global search quality
stays 0 while the scheme is illegal. REPAIR does not regroup programs.
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
    progress = 0.0
    moves: list[str] = []

    for entry in candidates:
        if spent >= budget:
            break
        if not entry.get("snapshot"):
            continue
        archive_mod.restore_entry(session, entry)
        held_partition = partition_id(session)
        before = feasibility_distance_of(entry)
        result, perf = realize(session)
        spent += 1
        best = (result, perf, "realize")
        best_d = _distance(perf)
        held_dim = archive_mod.capture(session)
        if partition_id(session) != held_partition:
            archive_mod.restore_entry(session, entry)
            continue
        if not perf.get("fits_limitations") and spent < budget:
            nudge = _nudge_for_violations(session, perf.get("violations") or {})
            if nudge:
                result2, perf2 = realize(session)
                spent += 1
                if partition_id(session) == held_partition and _distance(perf2) < best_d - 1e-9:
                    best = (result2, perf2, nudge)
                    best_d = _distance(perf2)
                else:
                    archive_mod.restore_snapshot(session, held_dim)
        result, perf, label = best
        reason = f"COVER repair: {label}"
        inserted = archive_mod.insert(archive, session, result, perf, reason=reason)
        inserted["repair"] = reason
        inserted["repair_progress"] = round(max(0.0, before - best_d), 4)
        if hasattr(result, "to_dict"):
            session.last_massing = result.to_dict()
        progress += max(0.0, before - best_d)
        moves.append(f"{label} Δ{before - best_d:+.3f}")
        if perf.get("fits_limitations"):
            legalized += 1

    archive_mod.restore_snapshot(session, origin)
    archive_mod.refresh_frontier(archive)
    report["tried"] = spent
    report["legalized"] = legalized
    report["progress"] = round(progress, 4)
    report["moves"] = moves[:12]
    report["note"] = (
        f"REPAIR: {spent} projection(s) on {len(candidates)} idea(s), "
        f"{legalized} became legal, distance improved by {progress:.3f}."
    )
    archive["repair"] = report
    return report


def _distance(perf: dict[str, Any] | None) -> float:
    perf = perf or {}
    if perf.get("fits_limitations"):
        return 0.0
    raw = perf.get("feasibility_distance")
    if raw is None:
        return 1.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 1.0


def _nudge_for_violations(session: Any, violations: dict[str, Any]) -> str | None:
    """One strategic move owned by the remaining violation. No regrouping."""
    vec = {
        str(k): float(v or 0.0)
        for k, v in (violations or {}).items()
        if k != "owners" and isinstance(v, (int, float)) and not isinstance(v, bool)
    }
    owners = violations.get("owners") if isinstance(violations, dict) else None
    owner_ids = []
    if isinstance(owners, dict):
        for key in ("edge_overrun", "split", "stories", "ratio_hard", "min_edge_short"):
            raw = owners.get(key)
            if isinstance(raw, (list, tuple)):
                owner_ids.extend(str(x) for x in raw)
            elif raw:
                owner_ids.append(str(raw))
    if vec.get("stories", 0.0) >= 0.5:
        if _clamp_stories_to_cap(session):
            return "stories→cap"
    # Awkward/program splits: prefer envelope over +1 story (which can worsen splits).
    if vec.get("split", 0.0) >= 0.5:
        if _flip_envelope(session):
            return "envelope"
        return None
    if vec.get("edge_overrun", 0.0) >= 0.05:
        if _bump_stories(session, +1, prefer_ids=owner_ids):
            return "stories+1"
        if _bump_stories(session, +1):
            return "stories+1"
    if vec.get("ratio_hard", 0.0) >= 0.05 or vec.get("min_edge_short", 0.0) >= 0.05:
        if _flip_envelope(session):
            return "envelope"
    return None


def _unlocked_masses(session: Any) -> list[Any]:
    lock = (session.constraints or {}).get("story_lock") or {}
    return [m for m in (session.masses or []) if m.id not in lock]


def _clamp_stories_to_cap(session: Any) -> bool:
    cap = max(1, int((session.constraints or {}).get("max_stories") or 4))
    changed = False
    for mass in _unlocked_masses(session):
        if int(mass.story_count or 1) > cap:
            mass.story_count = cap
            changed = True
    return changed


def _bump_stories(
    session: Any,
    delta: int,
    *,
    prefer_ids: list[str] | None = None,
) -> bool:
    """Raise/lower stories on the violating mass when known; else try each unlocked mass."""
    cap = max(1, int((session.constraints or {}).get("max_stories") or 4))
    masses = _unlocked_masses(session)
    if not masses:
        return False
    prefer = {str(x) for x in (prefer_ids or []) if x}
    ordered = sorted(
        masses,
        key=lambda m: (
            0 if m.id in prefer else 1,
            -len(m.departments or []),
            -int(m.story_count or 1),
        ),
    )
    for mass in ordered:
        nxt = int(mass.story_count or 1) + int(delta)
        if nxt < 1 or nxt > cap or nxt == int(mass.story_count or 1):
            continue
        mass.story_count = nxt
        return True
    return False


def _flip_envelope(session: Any) -> bool:
    current = str((session.constraints or {}).get("cover_envelope") or "balanced")
    nxt = {"compact": "elongated", "elongated": "compact"}.get(current, "compact")
    if nxt == current:
        return False
    session.constraints["cover_envelope"] = nxt
    return True


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
