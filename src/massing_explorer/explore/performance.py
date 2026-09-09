"""
A performance vector, not a quality score.

Hard feasibility first. Measured numbers come from a drawing the engine
already built. search.py's weighted sum is stored only as an experimental
baseline, never as 'quality'. Courtyard enclosure and daylight stay omitted
until the drawing can report them.
"""

from __future__ import annotations

from statistics import pstdev
from typing import Any

TRAIT_KEYS = ("spread", "height_variance", "footprint_likeness", "street_edge")
PUBLIC_TOKENS = ("health", "physical", "dining", "food", "art", "music", "gym")


def measure(result: Any, session: Any = None, archive: dict[str, Any] | None = None) -> dict[str, Any]:
    failed = [c for c in (getattr(result, "validation", None) or []) if not c.passed]
    limit_fails = [
        c for c in failed
        if str(c.check).startswith("site_length")
        or str(c.check).startswith("site_width")
        or str(c.check) in {"site_total_length", "site_total_width"}
        or str(c.check).startswith("site_total_")
    ]
    masses = list(getattr(result, "masses", None) or [])
    areas: list[float] = []
    stories: list[float] = []
    aspects: list[float] = []
    lengths: list[float] = []
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
        lengths.append(length)

    feasible = len(limit_fails) == 0 and not any(
        str(c.check).startswith("anchor") and not c.passed for c in failed
    )
    vector: dict[str, Any] = {
        "feasible": bool(feasible and not failed),
        "fits_limitations": len(limit_fails) == 0,
        "failed_checks": len(failed),
        "limit_fails": len(limit_fails),
        "failed_kinds": sorted({str(c.check).split(":")[0] for c in failed}),
        "spread": _spread(areas),
        "height_variance": float(pstdev(stories)) if len(stories) > 1 else 0.0,
        "footprint_likeness": _likeness(aspects),
        "lengths": [round(v, 1) for v in lengths],
        "preference_distance": preference_distance(result, session),
        "leftover_area": _leftover(masses),
        "fragmentation": _fragmentation(masses),
        "anchor_fit": _anchor_fit(failed, getattr(result, "validation", None) or []),
    }
    edge = _street_edge(masses, session)
    if edge is not None:
        vector["street_edge"] = edge
    public = _public_on_grade(masses)
    if public is not None:
        vector["public_on_grade"] = public
    vector["novelty"] = novelty_versus_archive(vector, archive)
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in vector.items()}


def preference_distance(result: Any, session: Any = None) -> float:
    """0 matches stated preferences. 1 is far. Unused until the user compares."""
    if session is None:
        return 0.0
    scores: list[float] = []
    pins = dict(getattr(session, "floor_pins", None) or {})
    levels = _department_levels(result)
    for dept, want in pins.items():
        got = levels.get(dept)
        if got is None:
            scores.append(1.0)
        else:
            scores.append(0.0 if int(got) == int(want) else min(1.0, abs(int(got) - int(want)) / 3.0))
    ratio = session.constraints.get("length_over_width")
    if ratio:
        want = float(ratio)
        for mass in getattr(result, "masses", None) or []:
            floors = list(getattr(mass, "floors", None) or [])
            if not floors or not floors[0].width_ft:
                continue
            actual = float(floors[0].length_ft) / float(floors[0].width_ft)
            scores.append(min(1.0, abs(actual - want) / max(want, 0.15)))
    briefing = session.constraints.get("briefing") or {}
    for clause in briefing.get("preferences") or []:
        if clause.get("lever") == "low_rise":
            stories = [len(list(m.floors or [])) for m in (getattr(result, "masses", None) or []) if m.floors]
            if stories:
                scores.append(min(1.0, max(0.0, (max(stories) - 2) / 3.0)))
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def novelty_versus_archive(vector: dict[str, Any], archive: dict[str, Any] | None) -> float:
    """1 is unlike anything already legal in the archive. 0 is a duplicate trait."""
    if not archive:
        return 1.0
    others = [
        e.get("performance") or {}
        for e in (archive.get("cells") or {}).values()
        if e.get("fits_limitations")
    ]
    if not others:
        return 1.0
    here = _trait_point(vector)
    nearest = min(_euclid(here, _trait_point(p)) for p in others)
    return min(1.0, nearest)


def _trait_point(vector: dict[str, Any]) -> tuple[float, ...]:
    return tuple(float(vector.get(name, 0.0) or 0.0) for name in TRAIT_KEYS)


def _euclid(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _spread(areas: list[float]) -> float:
    total = sum(areas)
    if total <= 0 or len(areas) < 2:
        return 0.0
    return 1.0 - (max(areas) / total)


def _likeness(values: list[float]) -> float:
    if len(values) < 2:
        return 1.0
    mean = sum(values) / len(values)
    if abs(mean) <= 1e-9:
        return 1.0
    mad = sum(abs(v - mean) for v in values) / len(values)
    return max(0.0, 1.0 - mad / mean)


def _street_edge(masses: list[Any], session: Any) -> float | None:
    if session is None:
        return None
    frontage = session.constraints.get("max_total_length_ft")
    if not frontage:
        return None
    used = 0.0
    for mass in masses:
        floors = list(getattr(mass, "floors", None) or [])
        if floors:
            used += float(floors[0].length_ft or 0)
    return min(1.0, used / float(frontage)) if float(frontage) > 0 else None


def _leftover(masses: list[Any]) -> float:
    """Unused share of usable floor. Measured, not a quality score."""
    usable = 0.0
    allocated = 0.0
    for mass in masses:
        for floor in getattr(mass, "floors", None) or []:
            usable += float(getattr(floor, "usable_area_sf", 0) or 0)
            allocated += float(getattr(floor, "allocated_gsf", 0) or 0)
    if usable <= 0:
        return 0.0
    return max(0.0, 1.0 - allocated / usable)


def _fragmentation(masses: list[Any]) -> float:
    """Share of departments that occupy more than one floor."""
    floors_of: dict[str, set[int]] = {}
    for mass in masses:
        for floor in getattr(mass, "floors", None) or []:
            level = int(getattr(floor, "level", 0) or 0)
            for alloc in getattr(floor, "allocations", None) or []:
                floors_of.setdefault(str(alloc.department), set()).add(level)
    if not floors_of:
        return 0.0
    split = sum(1 for levels in floors_of.values() if len(levels) > 1)
    return split / len(floors_of)


def _public_on_grade(masses: list[Any]) -> float | None:
    total = 0.0
    ground = 0.0
    found = False
    for mass in masses:
        for floor in getattr(mass, "floors", None) or []:
            level = int(getattr(floor, "level", 0) or 0)
            for alloc in getattr(floor, "allocations", None) or []:
                name = str(alloc.department).lower()
                if not any(tok in name for tok in PUBLIC_TOKENS):
                    continue
                found = True
                gsf = float(alloc.gsf or 0)
                total += gsf
                if level == 0:
                    ground += gsf
    if not found or total <= 0:
        return None
    return ground / total


def _anchor_fit(failed: list[Any], all_checks: list[Any]) -> float:
    anchors = [c for c in all_checks if str(getattr(c, "check", "")).startswith("anchor")]
    if not anchors:
        return 1.0
    passed = sum(1 for c in anchors if getattr(c, "passed", False))
    return passed / len(anchors)


def _department_levels(result: Any) -> dict[str, int]:
    levels: dict[str, int] = {}
    for mass in getattr(result, "masses", None) or []:
        for floor in getattr(mass, "floors", None) or []:
            level = int(getattr(floor, "level", 0) or 0)
            for alloc in getattr(floor, "allocations", None) or []:
                name = str(alloc.department)
                levels[name] = min(level, levels.get(name, level))
    return levels
