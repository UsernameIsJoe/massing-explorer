"""
Behavior archive: one elite per legal cell.

Illegal evaluations are attempts, not coverage. Unsupported topologies are
labeled unsupported, not 'we failed to sample'.
"""

from __future__ import annotations

import copy
from typing import Any

from .strategy import cell_key, idea_key, partition_id, read_strategy

# Hard brief constraints COVER / DIAGNOSE / REFINE may temporarily change.
# Snapshots must round-trip these or a probe permanently erases the brief.
_CONSTRAINT_SNAPSHOT_KEYS = (
    "max_total_length_ft",
    "max_building_length_ft",
    "max_building_width_ft",
    "max_edge_ft",
    "min_edge_ft",
    "min_building_length_ft",
    "length_limit_is_cap",
    "department_widths",
    "department_max_edge_ft",
    "academic_width_ft",
    "fixed_width_ft",
    "preferred_width_ft",
    "preferred_stories",
    "max_stories",
    "story_lock",
    "loading",
    "cover_envelope",
    "cover_geom_rank",
    "loading_required",
    "ratio_band",
    "ratio_band_parts",
    "ratio_band_role",
    "edge_sum_limits",
    "length_over_width",
    "floor_pins",
    "floor_pin_kinds",
    "stack_above",
)

def empty_archive() -> dict[str, Any]:
    return {
        "cells": {},
        "attempts": 0,
        "legal": 0,
        "unsupported": ["courtyard", "podium", "perpendicular_wings"],
        "mode": "cover",
        "note": "",
        "frontier": [],
    }


def load_archive(session: Any) -> dict[str, Any]:
    stored = (session.constraints.get("explore") or {}).get("archive")
    if isinstance(stored, dict) and "cells" in stored:
        return stored
    return empty_archive()


def insert(
    archive: dict[str, Any],
    session: Any,
    result: Any,
    performance: dict[str, Any],
    reason: str = "",
) -> dict[str, Any]:
    """
    Record one evaluation in the behavior archive.

    Every stage (COVER, MCTS, REPAIR, BO, REFINE) must go through here so
    P-pool organization status stays in sync with accepted results.
    """
    archive["attempts"] = int(archive.get("attempts") or 0) + 1
    key = cell_key(session)
    legal = bool(performance.get("fits_limitations"))
    entry = {
        "cell": key,
        "idea": idea_key(session),
        "partition": partition_id(session),
        "strategy": read_strategy(session),
        "fits_limitations": legal,
        "performance": performance,
        "reason": reason,
        "stories": {m.id: m.story_count for m in session.masses},
        "snapshot": _snapshot(session),
        "plates": _plates_from_result(result),
    }
    cells = archive.setdefault("cells", {})
    current = cells.get(key)
    if legal:
        archive["legal"] = int(archive.get("legal") or 0) + (
            0 if current and current.get("fits_limitations") else 1
        )
        if current is None or not current.get("fits_limitations"):
            # Illegal → legal (including stated COVER) replaces atomically.
            cells[key] = entry
        elif _better(performance, current.get("performance") or {}):
            cells[key] = entry
    elif current is None:
        cells[key] = entry
    elif not current.get("fits_limitations") and _closer_illegal(entry, current):
        cells[key] = entry
    refresh_frontier(archive)
    _sync_p_pool(archive, session, performance)
    return cells.get(key, entry)


def _sync_p_pool(
    archive: dict[str, Any],
    session: Any,
    performance: dict[str, Any] | None,
) -> None:
    """One shared P-status update for every archive insert (all search stages)."""
    try:
        from .p_pool import record_p_outcome

        record_p_outcome(archive, session, performance)
    except Exception:
        pass


def legal_cells(archive: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in (archive.get("cells") or {}).values() if e.get("fits_limitations")]


def explain(archive: dict[str, Any], grouping_locked: bool) -> str:
    legal = legal_cells(archive)
    cells = archive.get("cells") or {}
    infeasible = [k for k, e in cells.items() if not e.get("fits_limitations")]
    unsupported = archive.get("unsupported") or []
    parts = {e.get("partition") for e in cells.values() if e.get("partition")}
    lock = " Program organization was required, so P was not sampled." if grouping_locked else ""
    if not archive.get("attempts"):
        return "No strategy has been evaluated yet."
    bits = [
        f"COVER: {len(legal)} legal cell(s), {len(infeasible)} infeasible, "
        f"{archive['attempts']} attempt(s)."
    ]
    if infeasible:
        bits.append("Infeasible cells do not count as coverage.")
    frontier = archive.get("frontier") or []
    if frontier:
        bits.append(f"{len(frontier)} near-feasible on the frontier.")
    bits.append("Unsupported (not drawable): " + ", ".join(unsupported) + ".")
    if grouping_locked:
        bits.append(lock.strip())
    else:
        bits.append(f"{len(parts)} program organization(s) in the archive.")
    bits.append("A length cap was a filter, not a target.")
    cover = archive.get("cover") or {}
    if cover.get("ran"):
        bits.append(
            f"Adaptive COVER used {cover.get('attempts')} attempt(s) "
            f"across {cover.get('legal_regions')} legal region(s)"
            f"{' (stopped: stagnant)' if cover.get('stagnant') else ''}"
            f"{' (map incomplete at cap)' if cover.get('incomplete') else ''}."
        )
        if cover.get("csp_truncated"):
            bits.append("CSP partition enumeration was truncated.")
        cov = cover.get("partition_coverage")
        if isinstance(cov, (int, float)) and cov > 0:
            bits.append(
                f"COVER searched {int((archive.get('cover_plan') or {}).get('partitions') or 0)} "
                f"of ~{int(cover.get('csp_feasible') or 0)} feasible organizations."
            )
    return " ".join(bits)


def _better(new: dict[str, Any], old: dict[str, Any]) -> bool:
    """Among accepted elites, prefer higher architectural reward; else fewer fails."""
    from .saturate import architectural_reward

    new_fail = int(new.get("failed_checks") or 0)
    old_fail = int(old.get("failed_checks") or 0)
    if new_fail != old_fail:
        return new_fail < old_fail
    return architectural_reward(new) > architectural_reward(old) + 1e-9


def _closer_illegal(new_entry: dict[str, Any], old_entry: dict[str, Any]) -> bool:
    from .feasibility import feasibility_distance_of

    return feasibility_distance_of(new_entry) < feasibility_distance_of(old_entry)


def refresh_frontier(archive: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep the closest illegal occupant per idea, capped, below the distance cut."""
    from .feasibility import (
        FRONTIER_DISTANCE,
        FRONTIER_MAX,
        feasibility_distance_of,
        idea_of,
    )

    cells = archive.get("cells") or {}
    legal_ideas = {
        idea_of(e) for e in cells.values() if e.get("fits_limitations") and idea_of(e)
    }
    best: dict[str, dict[str, Any]] = {}
    for entry in cells.values():
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
    ranked = sorted(best.values(), key=feasibility_distance_of)[:FRONTIER_MAX]
    archive["frontier"] = [
        {
            "cell": e.get("cell"),
            "idea": idea_of(e),
            "distance": round(feasibility_distance_of(e), 4),
            "failed_kinds": list((e.get("performance") or {}).get("failed_kinds") or []),
            "reason": e.get("reason") or "",
        }
        for e in ranked
    ]
    return archive["frontier"]


def frontier_entries(archive: dict[str, Any]) -> list[dict[str, Any]]:
    cells = archive.get("cells") or {}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in archive.get("frontier") or []:
        key = str(row.get("cell") or "")
        entry = cells.get(key)
        if not entry or entry.get("fits_limitations"):
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    if not out and not (archive.get("frontier") or []):
        refresh_frontier(archive)
        for row in archive.get("frontier") or []:
            entry = cells.get(str(row.get("cell") or ""))
            if entry and not entry.get("fits_limitations"):
                out.append(entry)
    return out


def capture(session: Any) -> dict[str, Any]:
    return _snapshot(session)


def restore_entry(session: Any, entry: dict[str, Any]) -> None:
    """Write a stored elite back onto the study. Does not invent feet."""
    restore_snapshot(session, entry.get("snapshot") or {}, entry.get("stories") or {})


def restore_snapshot(session: Any, snap: dict[str, Any], stories: dict[str, Any] | None = None) -> None:
    from ..study_state import MassGrouping, MassPairing

    if snap.get("masses"):
        session.masses = [MassGrouping.from_dict(m) for m in snap["masses"]]
    if "pairings" in snap:
        session.pairings = [MassPairing.from_dict(p) for p in snap["pairings"]]
    stories = stories or snap.get("stories") or {}
    ids = {m.id for m in session.masses}
    # Restore hard constraints before story_lock is read.
    saved = snap.get("constraints")
    if isinstance(saved, dict):
        for key, value in saved.items():
            if value is None:
                session.constraints.pop(key, None)
            else:
                session.constraints[key] = copy.deepcopy(value)
        # Drop mass-scoped keys that belonged to masses no longer present.
        for key in list(session.constraints):
            if str(key).endswith("_max_edge_ft"):
                owner = str(key)[: -len("_max_edge_ft")]
                if owner not in ids and key not in saved:
                    session.constraints.pop(key, None)
    lock = session.constraints.get("story_lock") or {}
    lock_applies = bool(lock) and set(lock).issubset(ids)
    for mass in session.masses:
        if lock_applies and mass.id in lock:
            mass.story_count = int(lock[mass.id])
            continue
        if mass.id in stories:
            mass.story_count = int(stories[mass.id])
    if snap.get("loading"):
        session.constraints["loading"] = snap["loading"]
    if "cover_envelope" in snap:
        if snap.get("cover_envelope"):
            session.constraints["cover_envelope"] = snap["cover_envelope"]
        else:
            session.constraints.pop("cover_envelope", None)
    if "cover_geom_rank" in snap:
        if snap.get("cover_geom_rank") is not None:
            session.constraints["cover_geom_rank"] = snap["cover_geom_rank"]
        else:
            session.constraints.pop("cover_geom_rank", None)
    if "pins" in snap:
        session.floor_pins = dict(snap.get("pins") or {})
    session.floor_steps = {
        str(k): list(v) for k, v in (snap.get("floor_steps") or {}).items()
    }
    session.floor_tapers = {
        str(k): float(v) for k, v in (snap.get("floor_tapers") or {}).items()
    }
    for mid, width in (snap.get("widths") or {}).items():
        key = f"{mid}_width_ft"
        if width is None:
            session.constraints.pop(key, None)
        else:
            session.constraints[key] = width
    for key in list(session.constraints):
        if not str(key).endswith("_width_ft"):
            continue
        if key in {"fixed_width_ft", "max_building_width_ft", "academic_width_ft"}:
            continue
        owner = str(key)[: -len("_width_ft")]
        if owner not in ids:
            session.constraints.pop(key, None)
    if getattr(session, "floor_steps", None):
        session.floor_steps = {k: v for k, v in session.floor_steps.items() if k in ids}
    if getattr(session, "floor_tapers", None):
        session.floor_tapers = {k: v for k, v in session.floor_tapers.items() if k in ids}
    if "brief_locked" in snap:
        session.brief_locked = bool(snap.get("brief_locked"))


def _snapshot(session: Any) -> dict[str, Any]:
    constraints: dict[str, Any] = {}
    for key in _CONSTRAINT_SNAPSHOT_KEYS:
        if key in session.constraints:
            constraints[key] = copy.deepcopy(session.constraints.get(key))
    for key, value in list(session.constraints.items()):
        if str(key).endswith("_max_edge_ft") or str(key).endswith("_width_ft"):
            constraints[key] = copy.deepcopy(value)
    return {
        "stories": {m.id: m.story_count for m in session.masses},
        "departments": {m.id: list(m.departments) for m in session.masses},
        "masses": [m.to_dict() for m in session.masses],
        "pairings": [p.to_dict() for p in (session.pairings or [])],
        "loading": session.constraints.get("loading"),
        "cover_envelope": session.constraints.get("cover_envelope"),
        "cover_geom_rank": session.constraints.get("cover_geom_rank"),
        "pins": dict(session.floor_pins or {}),
        "floor_steps": {
            str(k): list(v) for k, v in (getattr(session, "floor_steps", None) or {}).items()
        },
        "floor_tapers": {
            str(k): float(v) for k, v in (getattr(session, "floor_tapers", None) or {}).items()
        },
        "widths": {
            m.id: session.constraints.get(f"{m.id}_width_ft") for m in session.masses
        },
        "constraints": constraints,
        "brief_locked": bool(getattr(session, "brief_locked", False)),
    }


def _plates_from_result(result: Any) -> list[dict[str, Any]]:
    """Footprints from a solved drawing, for UI envelope thumbs."""
    out: list[dict[str, Any]] = []
    for mass in getattr(result, "masses", None) or []:
        floors = list(getattr(mass, "floors", None) or [])
        if not floors:
            continue
        ground = floors[0]
        try:
            width = float(getattr(ground, "width_ft", 0) or 0)
            length = float(getattr(ground, "length_ft", 0) or 0)
        except (TypeError, ValueError):
            continue
        if width <= 0 or length <= 0:
            continue
        out.append(
            {
                "mass_id": getattr(mass, "id", "") or "",
                "mass_name": getattr(mass, "name", "") or getattr(mass, "id", "") or "",
                "stories": len(floors),
                "width_ft": round(width, 3),
                "length_ft": round(length, 3),
            }
        )
    return out
