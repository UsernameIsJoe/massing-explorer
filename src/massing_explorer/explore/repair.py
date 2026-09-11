"""
Project an illegal COVER idea onto the nearest legal twin.

COVER explores architectural ideas. REPAIR only reduces hard-constraint
violation using existing typed actions + a re-solve. It does not regroup
programs, chase preference scores, or invent a second sampler.
"""

from __future__ import annotations

from typing import Any

from ..solver import required_width_ft, solve_massing_study
from . import archive as archive_mod
from .actions import apply_action
from .feasibility import (
    FRONTIER_DISTANCE,
    feasibility_distance_of,
    idea_of,
)
from .performance import measure
from .saturate import REPAIR_CAP, read_explore_budget
from .strategy import partition_id
from .topology import topology_is_required

REPAIR_CANDIDATES = 8
REPAIR_MOVES = 3


def run_repair(
    session: Any,
    archive: dict[str, Any],
) -> dict[str, Any]:
    """Try a few deterministic repairs on the closest illegal ideas."""
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
        result = solve_massing_study(session)
        perf = measure(result, session, archive=archive)
        if hasattr(result, "to_dict"):
            session.last_massing = result.to_dict()
        tried: set[str] = set()
        for _ in range(REPAIR_MOVES):
            if spent >= budget:
                break
            if perf.get("fits_limitations"):
                break
            action = pick_repair_action(session, result, perf, tried)
            if not action:
                break
            tag = str(action.pop("tag", "") or action.get("op") or "repair")
            tried.add(tag)
            applied = apply_action(session, action)
            if not applied.get("ok"):
                continue
            if partition_id(session) != held_partition:
                archive_mod.restore_entry(session, entry)
                continue
            before_raw = perf.get("feasibility_distance")
            before = (
                float(before_raw)
                if before_raw is not None
                else feasibility_distance_of(entry)
            )
            reason = (
                f"COVER repair: {applied.get('op') or action.get('op')} "
                f"{applied.get('reason') or applied.get('note') or tag}"
            )
            result = solve_massing_study(session)
            perf = measure(result, session, archive=archive)
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
                break
            if after_d + 1e-9 >= before:
                archive_mod.restore_entry(session, entry)
                result = solve_massing_study(session)
                perf = measure(result, session, archive=archive)
                break

    archive_mod.restore_snapshot(session, origin)
    archive_mod.refresh_frontier(archive)
    report["tried"] = spent
    report["legalized"] = legalized
    report["moves"] = moves[:12]
    report["note"] = (
        f"REPAIR: {spent} projection(s) on {len(candidates)} idea(s), "
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


def pick_repair_action(
    session: Any,
    result: Any,
    performance: dict[str, Any],
    tried: set[str],
) -> dict[str, Any] | None:
    """First matching legal repair. Skip locked levers and already-tried tags."""
    kinds = {str(k).split(":")[0] for k in (performance.get("failed_kinds") or [])}
    suggestions = list(getattr(result, "resize_suggestions", None) or [])

    for sug in suggestions:
        mid = str(getattr(sug, "mass_id", "") or "")
        width = getattr(sug, "option_width_ft", None)
        if not mid or width is None:
            continue
        tag = f"width:{mid}"
        if tag in tried:
            continue
        if _width_locked(session, mid):
            continue
        # Sit slightly inside the cap so float length is not 0.00005 ft over.
        return {
            "op": "SET_WIDTH",
            "mass": mid,
            "width_ft": float(width) + 0.25,
            "tag": tag,
        }

    if "site_length" in kinds or "site_total_length" in kinds:
        for mass in session.masses or []:
            tag = f"widen:{mass.id}"
            if tag in tried or _width_locked(session, mass.id):
                continue
            return {"op": "SET_WIDTH", "mass": mass.id, "delta_ft": 10.0, "tag": tag}

    if kinds & {"site_length", "site_width", "site_total_length"}:
        lock = session.constraints.get("story_lock") or {}
        cap = max(1, int(session.constraints.get("max_stories") or 4))
        for mass in session.masses or []:
            tag = f"stories+1:{mass.id}"
            if tag in tried or mass.id in lock:
                continue
            nxt = int(mass.story_count) + 1
            if nxt > cap:
                continue
            return {"op": "SET_STORIES", "mass": mass.id, "stories": nxt, "tag": tag}

    if "ratio_band" in kinds:
        action = _ratio_width_action(session, result, tried)
        if action:
            return action

    if kinds == {"program_split"}:
        lock = session.constraints.get("story_lock") or {}
        cap = max(1, int(session.constraints.get("max_stories") or 4))
        for mass in session.masses or []:
            current = int(mass.story_count or 1)
            tag_up = f"stories+1:{mass.id}"
            if current < cap and mass.id not in lock and tag_up not in tried:
                return {
                    "op": "SET_STORIES",
                    "mass": mass.id,
                    "stories": current + 1,
                    "tag": tag_up,
                }
            tag_dn = f"stories-1:{mass.id}"
            if current == 2 and mass.id not in lock and tag_dn not in tried:
                return {
                    "op": "SET_STORIES",
                    "mass": mass.id,
                    "stories": 1,
                    "tag": tag_dn,
                }

    if not topology_is_required(session) and session.pairings:
        if kinds & {"pairing_length", "site_length", "site_total_length"}:
            if "clear_pairings" not in tried:
                return {"op": "CLEAR_PAIRINGS", "tag": "clear_pairings"}

    return None


def _width_locked(session: Any, mass_id: str) -> bool:
    mass = next((m for m in (session.masses or []) if m.id == mass_id), None)
    if mass is None:
        return True
    if required_width_ft(session, mass) is not None:
        return True
    if any(mass_id in (p.mass_ids or []) for p in (session.pairings or [])):
        return True
    return False


def _ratio_width_action(
    session: Any,
    result: Any,
    tried: set[str],
) -> dict[str, Any] | None:
    role = str(session.constraints.get("ratio_band_role") or "limitation")
    if role == "preference":
        return None
    band = session.constraints.get("ratio_band")
    if not isinstance(band, (list, tuple)) or len(band) < 2:
        return None
    try:
        lo, hi = float(band[0]), float(band[1])
    except (TypeError, ValueError):
        return None
    solved = {m.id: m for m in (getattr(result, "masses", None) or [])}
    for mass in session.masses or []:
        tag = f"ratio:{mass.id}"
        if tag in tried or _width_locked(session, mass.id):
            continue
        solved_mass = solved.get(mass.id)
        floors = list(getattr(solved_mass, "floors", None) or [])
        if not floors:
            continue
        width = float(floors[0].width_ft or 0)
        length = float(floors[0].length_ft or 0)
        target = _ratio_target_width(width, length, lo, hi)
        if target is None or abs(target - width) < 0.5:
            continue
        return {
            "op": "SET_WIDTH",
            "mass": mass.id,
            "width_ft": target,
            "tag": tag,
        }
    return None


def _ratio_target_width(width: float, length: float, lo: float, hi: float) -> float | None:
    from ..aspect import aspect_in_band, normalize_band

    if width <= 0 or length <= 0:
        return None
    area = width * length
    lo, hi = normalize_band(lo, hi)
    tol = 0.03 * max(hi - lo, 0.15)
    candidates = [lo, hi]
    if lo > 0:
        candidates.extend([1.0 / lo, 1.0 / hi])
    best = None
    best_d = 1e9
    for asp in candidates:
        if asp <= 0:
            continue
        w = (area / asp) ** 0.5
        new_aspect = (area / w) / w if w else 0.0
        if not aspect_in_band(new_aspect, lo, hi, tol=tol):
            continue
        d = abs(w - width)
        if d < best_d:
            best_d = d
            best = w
    return best
