"""
Behavior archive: one elite per legal cell.

Illegal evaluations are attempts, not coverage. Unsupported topologies are
labeled unsupported, not 'we failed to sample'.
"""

from __future__ import annotations

import copy
from typing import Any

from .strategy import cell_key, partition_id, read_strategy

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
)

def empty_archive() -> dict[str, Any]:
    return {
        "cells": {},
        "attempts": 0,
        "legal": 0,
        "unsupported": ["courtyard", "podium", "perpendicular_wings"],
        "mode": "cover",
        "note": "",
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
    archive["attempts"] = int(archive.get("attempts") or 0) + 1
    key = cell_key(session)
    legal = bool(performance.get("fits_limitations"))
    entry = {
        "cell": key,
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
    if current and str(current.get("reason") or "").startswith("COVER: stated"):
        # Keep the brief's drawing as the elite for this cell.
        if legal and not current.get("fits_limitations"):
            archive["legal"] = int(archive.get("legal") or 0) + 1
            current["fits_limitations"] = True
            current["performance"] = performance
        return current
    if legal:
        archive["legal"] = int(archive.get("legal") or 0) + (0 if current and current.get("fits_limitations") else 1)
        if current is None or not current.get("fits_limitations"):
            cells[key] = entry
        elif _better(performance, current.get("performance") or {}):
            cells[key] = entry
    elif current is None:
        cells[key] = entry
    return entry


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
    return " ".join(bits)


def _better(new: dict[str, Any], old: dict[str, Any]) -> bool:
    """Prefer fewer failed checks. Not a quality score."""
    return int(new.get("failed_checks") or 0) < int(old.get("failed_checks") or 0)


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
