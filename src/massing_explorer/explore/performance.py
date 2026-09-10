"""
A performance vector, not a quality score.

Hard gate (requirements + limitations) decides legal archive membership.
Soft evaluation among legal schemes uses four composites — Program Coherence,
Preference Alignment, Performance Efficiency, Robustness / Flexibility.

Raw geometry signals (spread, leftover, …) stay for diagnostics and as inputs
to those composites. Courtyard enclosure and EnergyPlus stay omitted until the
drawing can report them. search.py's weighted sum is experimental only.
"""

from __future__ import annotations

from statistics import pstdev
from typing import Any

# LEARN / BT axes — soft only; never unlock a must.
EVAL_AXIS_NAMES = (
    "program_coherence",
    "preference_alignment",
    "performance_efficiency",
    "robustness",
)

# Back-compat alias used by novelty_versus_archive / older callers.
TRAIT_KEYS = EVAL_AXIS_NAMES

PUBLIC_TOKENS = ("health", "physical", "dining", "food", "art", "music", "gym")


def _is_hard_gate_check(check: str) -> bool:
    """Site caps and exact requirements both block the legal archive."""
    name = str(check or "")
    if name.startswith("site_length") or name.startswith("site_width"):
        return True
    if name in {"site_total_length", "site_total_width"} or name.startswith("site_total_"):
        return True
    if name.startswith("required_width") or name.startswith("min_edge"):
        return True
    return False


def measure(result: Any, session: Any = None, archive: dict[str, Any] | None = None) -> dict[str, Any]:
    failed = [c for c in (getattr(result, "validation", None) or []) if not c.passed]
    limit_fails = [c for c in failed if _is_hard_gate_check(str(c.check))]
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
    leftover = _leftover(masses)
    fragmentation = _fragmentation(masses)
    awkward = _awkward_floor_splits(masses)
    likeness = _likeness(aspects)
    anchor = _anchor_fit(failed, getattr(result, "validation", None) or [])
    pref_dist = preference_distance(result, session)
    edge = _street_edge(masses, session)
    public = _public_on_grade(masses)

    vector: dict[str, Any] = {
        "feasible": bool(feasible and not failed),
        "fits_limitations": len(limit_fails) == 0,
        "failed_checks": len(failed),
        "limit_fails": len(limit_fails),
        "failed_kinds": sorted({str(c.check).split(":")[0] for c in failed}),
        # Diagnostic / composite inputs (not LEARN axes).
        "spread": _spread(areas),
        "height_variance": float(pstdev(stories)) if len(stories) > 1 else 0.0,
        "footprint_likeness": likeness,
        "lengths": [round(v, 1) for v in lengths],
        "preference_distance": pref_dist,
        "leftover_area": leftover,
        "fragmentation": fragmentation,
        "awkward_splits": awkward,
        "anchor_fit": anchor,
    }
    if edge is not None:
        vector["street_edge"] = edge
    if public is not None:
        vector["public_on_grade"] = public

    vector.update(eval_composites(vector, session))
    vector["novelty"] = novelty_versus_archive(vector, archive)
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in vector.items()}


def eval_composites(vector: dict[str, Any], session: Any = None) -> dict[str, float]:
    """Four soft evaluation axes in [0, 1]. Hard gate is separate."""
    frag = float(vector.get("fragmentation") or 0.0)
    awkward = float(vector.get("awkward_splits") or 0.0)
    public = vector.get("public_on_grade")
    public_score = float(public) if public is not None else 0.7
    anchor = float(vector.get("anchor_fit") if vector.get("anchor_fit") is not None else 1.0)
    program_coherence = (
        0.40 * (1.0 - frag)
        + 0.25 * (1.0 - awkward)
        + 0.20 * public_score
        + 0.15 * anchor
    )

    pref_dist = float(vector.get("preference_distance") or 0.0)
    preference_alignment = 1.0 - min(1.0, max(0.0, pref_dist))

    leftover = float(vector.get("leftover_area") or 0.0)
    likeness = float(vector.get("footprint_likeness") if vector.get("footprint_likeness") is not None else 0.5)
    edge = vector.get("street_edge")
    edge_score = float(edge) if edge is not None else 0.5
    performance_efficiency = (
        0.35 * (1.0 - leftover)
        + 0.25 * likeness
        + 0.20 * edge_score
        + 0.20 * anchor
    )

    robustness = _robustness_score(vector, session)

    return {
        "program_coherence": max(0.0, min(1.0, program_coherence)),
        "preference_alignment": max(0.0, min(1.0, preference_alignment)),
        "performance_efficiency": max(0.0, min(1.0, performance_efficiency)),
        "robustness": max(0.0, min(1.0, robustness)),
    }


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
    preferred_stories = session.constraints.get("preferred_stories")
    if preferred_stories is not None:
        want = max(1, int(round(float(preferred_stories))))
        locks = dict(session.constraints.get("story_lock") or {})
        for mass in getattr(result, "masses", None) or []:
            mass_id = str(getattr(mass, "id", "") or "")
            if mass_id and mass_id in locks:
                continue
            floors = list(getattr(mass, "floors", None) or [])
            if not floors:
                continue
            got = len(floors)
            scores.append(min(1.0, abs(got - want) / max(float(want), 1.0)))
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
    """1 is unlike anything already legal in the archive. 0 is a duplicate eval point."""
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
    return tuple(float(vector.get(name, 0.0) or 0.0) for name in EVAL_AXIS_NAMES)


def _euclid(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _robustness_score(vector: dict[str, Any], session: Any) -> float:
    """Prefer an explicit probe score; else a structural proxy (not regrouping)."""
    if vector.get("_robustness_probe") is not None:
        return float(vector["_robustness_probe"])
    if session is not None:
        explore = (getattr(session, "constraints", None) or {}).get("explore") or {}
        report = explore.get("robustness") or {}
        if report.get("ran") and report.get("score") is not None:
            return float(report["score"])
    leftover = float(vector.get("leftover_area") or 0.0)
    anchor = float(vector.get("anchor_fit") if vector.get("anchor_fit") is not None else 1.0)
    fits = 1.0 if vector.get("fits_limitations") else 0.0
    # Headroom proxy: low leftover + anchor fit + legal.
    return 0.45 * fits + 0.30 * (1.0 - leftover) + 0.25 * anchor


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


def _awkward_floor_splits(masses: list[Any]) -> float:
    """
    Share of departments with a thin remnant on another floor (e.g. ~90/10).

    0 = no awkward splits. 1 = every multi-floor dept is badly unbalanced.
    """
    by_dept: dict[str, list[float]] = {}
    for mass in masses:
        for floor in getattr(mass, "floors", None) or []:
            for alloc in getattr(floor, "allocations", None) or []:
                by_dept.setdefault(str(alloc.department), []).append(float(alloc.gsf or 0))
    if not by_dept:
        return 0.0
    awkward = 0
    multi = 0
    for shares in by_dept.values():
        if len(shares) < 2:
            continue
        multi += 1
        total = sum(shares) or 1.0
        largest = max(shares) / total
        # Remnant under 15% of the department on a separate floor.
        if largest >= 0.85:
            awkward += 1
    if multi == 0:
        return 0.0
    return awkward / multi


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
