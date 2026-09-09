"""
Measured traits of a scheme the engine already built.

A trait is a number from the drawing. It is not a preference, and it is not
invented. Enclosure and street edge are omitted unless the scheme actually
has a frontage the engine can measure.
"""

from __future__ import annotations

from statistics import pstdev
from typing import Any

TRAIT_NAMES = (
    "spread",
    "height_variance",
    "footprint_likeness",
    "width_alignment",
)


def measure_traits(result: Any, session: Any = None) -> dict[str, float]:
    """Numbers the geometry can report on any brief."""
    masses = list(getattr(result, "masses", None) or [])
    if not masses:
        return {name: 0.0 for name in TRAIT_NAMES}

    areas = []
    stories = []
    aspects = []
    widths = []
    for mass in masses:
        floors = list(getattr(mass, "floors", None) or [])
        if not floors:
            continue
        ground = floors[0]
        width = float(ground.width_ft or 0)
        length = float(ground.length_ft or 0)
        areas.append(max(0.0, width * length))
        stories.append(float(len(floors)))
        aspects.append(length / width if width else 0.0)
        widths.append(width)

    traits = {
        "spread": _spread(areas),
        "height_variance": _spread_of(stories),
        "footprint_likeness": _likeness(aspects),
        "width_alignment": _likeness(widths),
    }
    edge = _street_edge(masses, session)
    if edge is not None:
        traits["street_edge"] = edge
    return {key: round(float(value), 4) for key, value in traits.items()}


def _spread(areas: list[float]) -> float:
    total = sum(areas)
    if total <= 0 or len(areas) < 2:
        return 0.0
    return 1.0 - (max(areas) / total)


def _spread_of(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return float(pstdev(values))


def _likeness(values: list[float]) -> float:
    if len(values) < 2:
        return 1.0
    mean = sum(values) / len(values)
    if mean <= 1e-9:
        return 1.0
    mad = sum(abs(v - mean) for v in values) / len(values)
    return max(0.0, 1.0 - mad / mean)


def _street_edge(masses: list[Any], session: Any) -> float | None:
    """How much of a stated frontage the masses occupy. Absent if none was stated."""
    if session is None:
        return None
    frontage = None
    constraints = getattr(session, "constraints", None) or {}
    if constraints.get("max_total_length_ft"):
        frontage = float(constraints["max_total_length_ft"])
    pairings = list(getattr(session, "pairings", None) or [])
    if pairings and getattr(pairings[0], "total_length_ft", 0):
        frontage = float(pairings[0].total_length_ft)
    if not frontage or frontage <= 0:
        return None
    used = 0.0
    for mass in masses:
        floors = list(getattr(mass, "floors", None) or [])
        if floors:
            used += float(floors[0].length_ft or 0)
    return max(0.0, min(1.0, used / frontage))
