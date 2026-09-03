from __future__ import annotations

import math
from typing import Any

from .allocate import allocate_programs
from .config import gsf_tolerance_from_config, load_project_config
from .massing_models import (
    CompromisedAnchor,
    FloorPlate,
    MassingStudyResult,
    ResizeSuggestion,
    SolvedMass,
    ValidationCheck,
    VoidRegion,
)
from .session import StudySession


def solve_other_side(area_sf: float, fixed_side_ft: float) -> float:
    """other_side = required_floor_area / fixed_side"""
    if fixed_side_ft <= 0:
        raise ValueError("fixed_side_ft must be > 0")
    if area_sf < 0:
        raise ValueError("area_sf must be >= 0")
    return area_sf / fixed_side_ft


def solve_paired_width(areas_sf: list[float], total_length_ft: float) -> float:
    """Shared width for adjacent masses: W = (A1 + A2 + ...) / L_total"""
    if total_length_ft <= 0:
        raise ValueError("total_length_ft must be > 0")
    total_area = sum(areas_sf)
    if total_area < 0:
        raise ValueError("areas must be >= 0")
    return total_area / total_length_ft


def solve_paired_masses(
    areas_sf: list[float],
    total_length_ft: float,
) -> tuple[float, list[float]]:
    """
    Manual workflow Step 11.

    W = (A1 + A2) / L_total, then L_i = A_i / W.
    Returns (shared_width_ft, [length_per_mass_ft]).
    """
    width = solve_paired_width(areas_sf, total_length_ft)
    if width <= 0:
        return 0.0, [0.0 for _ in areas_sf]
    lengths = [solve_other_side(a, width) for a in areas_sf]
    return width, lengths


def plate_area(target_gsf: float, story_count: int, void_area_sf: float = 0.0) -> float:
    """Floor plate area needed so n floors minus voids equal target GSF."""
    story_count = max(1, story_count)
    if story_count == 1:
        return target_gsf
    n_void_floors = 1 if void_area_sf > 0 else 0
    return (target_gsf + void_area_sf * n_void_floors) / story_count


def gsf_fit_pass(actual: float, target: float, tolerance: float = 0.03) -> bool:
    if target <= 0:
        return actual == 0
    return abs(actual - target) / target <= tolerance


def rectangle_fits_room(
    mass_w: float,
    mass_l: float,
    room_w: float,
    room_l: float,
) -> bool:
    """True if room clear dims fit in mass rectangle (rotation allowed)."""
    return (room_w <= mass_w and room_l <= mass_l) or (
        room_w <= mass_l and room_l <= mass_w
    )


def _dept_map(session: StudySession) -> dict[str, Any]:
    return {d.name: d for d in session.program.departments}


def _mass_target_gsf(session: StudySession, departments: list[str]) -> float:
    depts = _dept_map(session)
    return sum(depts[d].target_gsf for d in departments if d in depts)


def _resolve_fixed_width(
    session: StudySession,
    mass_id: str,
    config: dict[str, Any],
) -> float:
    """Prefer mass-specific constraint, then global constraints, then config defaults."""
    c = session.constraints
    for key in (
        f"{mass_id}_width_ft",
        "fixed_width_ft",
        "academic_width_ft",
        "max_building_width_ft",
    ):
        if key in c and c[key] is not None:
            return float(c[key])

    planning = config.get("planning_limits", {})
    for key in ("academic_width_ft", "max_building_width_ft"):
        if planning.get(key) is not None:
            return float(planning[key])

    # Fallback: compact-ish default for studies without a fixed width stated
    return 80.0


def _match_anchor_rooms(
    session: StudySession,
    departments: list[str],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Find program rooms that match configured anchor room keys."""
    anchors = config.get("anchor_rooms", {}) or {}
    matched: list[dict[str, Any]] = []
    dept_set = set(departments)

    for key, spec in anchors.items():
        key_l = key.lower().replace("_", " ")
        for room in session.program.rooms:
            if room.department not in dept_set:
                continue
            name_l = room.room_name.lower()
            if key_l in name_l or key_l.replace(" ", "") in name_l.replace(" ", ""):
                matched.append(
                    {
                        "key": key,
                        "room_name": room.room_name,
                        "min_width_ft": float(spec.get("min_width_ft", 0)),
                        "min_length_ft": float(spec.get("min_length_ft", 0)),
                        "double_height": bool(spec.get("double_height", False)),
                        "area_sf": room.total_area_sf,
                    }
                )
                break
    return matched


def _void_area_for_mass(
    session: StudySession,
    departments: list[str],
    config: dict[str, Any],
) -> list[VoidRegion]:
    """Build void regions from chat-flagged rooms and config double-height anchors."""
    voids: list[VoidRegion] = []
    anchors = _match_anchor_rooms(session, departments, config)
    flagged = {r.lower() for r in session.double_height_rooms}

    for a in anchors:
        name = a["room_name"]
        is_dh = a["double_height"] or name.lower() in flagged
        # Also match if any flagged string is substring of room name
        if not is_dh:
            is_dh = any(f in name.lower() for f in flagged)
        if not is_dh:
            continue
        w = a["min_width_ft"]
        l = a["min_length_ft"]
        area = w * l if w > 0 and l > 0 else a["area_sf"]
        voids.append(VoidRegion(room=name, width_ft=w, length_ft=l, area_sf=area))

    # Chat-flagged rooms not already covered via anchors
    for room in session.program.rooms:
        if room.department not in departments:
            continue
        if room.room_name.lower() not in flagged and not any(
            f in room.room_name.lower() for f in flagged
        ):
            continue
        if any(v.room == room.room_name for v in voids):
            continue
        # Approximate void as room NFA (clear dims unknown)
        side = math.sqrt(max(room.total_area_sf, 1.0))
        voids.append(
            VoidRegion(
                room=room.room_name,
                width_ft=side,
                length_ft=side,
                area_sf=room.total_area_sf,
            )
        )
    return voids


def _ground_required_departments(
    session: StudySession,
    departments: list[str],
    voids: list[VoidRegion],
) -> set[str]:
    """
    Departments owning a double-height room must sit on the ground floor, since
    the void is cut in the plate directly above them.
    """
    void_rooms = {v.room.lower() for v in voids}
    required: set[str] = set()
    for room in session.program.rooms:
        if room.department not in departments:
            continue
        if room.room_name.lower() in void_rooms:
            required.add(room.department)
    return required


def solve_mass_footprint(
    target_gsf: float,
    story_count: int,
    fixed_width_ft: float,
    voids: list[VoidRegion] | None = None,
    departments: list[str] | None = None,
    programs_by_level: dict[int, list[str]] | None = None,
) -> tuple[list[FloorPlate], float]:
    """
    Solve equal-width footprint with optional double-height void on floor 1.

    Plate P satisfies: n*P - void_area ≈ target_gsf
    (void counted once: present as hole on floor above, not as extra GSF)
    """
    voids = voids or []
    departments = departments or []
    story_count = max(1, story_count)
    void_area = sum(v.area_sf for v in voids)

    plate = plate_area(target_gsf, story_count, void_area)
    length = solve_other_side(plate, fixed_width_ft)
    floors: list[FloorPlate] = []

    for level in range(story_count):
        level_voids: list[VoidRegion] = []
        area = plate
        if level == 1 and void_area > 0:
            level_voids = list(voids)
            # Footprint stays same; usable area reduced
            area = plate

        programs = (
            programs_by_level.get(level, departments)
            if programs_by_level
            else departments
        )
        floors.append(
            FloorPlate(
                level=level,
                width_ft=fixed_width_ft,
                length_ft=length,
                area_sf=area,
                programs=list(programs),
                voids=level_voids,
            )
        )

    actual = sum(f.usable_area_sf for f in floors)
    return floors, actual


def solve_stepped_floors(
    floor_areas: list[float],
    fixed_width_ft: float,
    departments: list[str] | None = None,
    voids_by_level: dict[int, list[VoidRegion]] | None = None,
) -> tuple[list[FloorPlate], float]:
    """Build floors with independent plate areas (stepped massing)."""
    departments = departments or []
    voids_by_level = voids_by_level or {}
    floors: list[FloorPlate] = []
    for level, area in enumerate(floor_areas):
        length = solve_other_side(area, fixed_width_ft) if area > 0 else 0.0
        floors.append(
            FloorPlate(
                level=level,
                width_ft=fixed_width_ft,
                length_ft=length,
                area_sf=area,
                programs=list(departments),
                voids=list(voids_by_level.get(level, [])),
            )
        )
    actual = sum(f.usable_area_sf for f in floors)
    return floors, actual


def check_anchor_fit(
    mass: SolvedMass,
    anchors: list[dict[str, Any]],
) -> list[CompromisedAnchor]:
    compromised: list[CompromisedAnchor] = []
    if not mass.floors:
        return compromised
    # Anchor rooms must fit on ground floor plate
    ground = mass.floors[0]
    for a in anchors:
        rw, rl = a["min_width_ft"], a["min_length_ft"]
        if rw <= 0 or rl <= 0:
            continue
        ok = rectangle_fits_room(ground.width_ft, ground.length_ft, rw, rl)
        if not ok:
            compromised.append(
                CompromisedAnchor(
                    room=a["room_name"],
                    mass_id=mass.id,
                    required_ft=f"{rw:g} x {rl:g}",
                    available_ft=f"{ground.width_ft:.1f} x {ground.length_ft:.1f}",
                    reason="clear room dims do not fit ground floor footprint",
                )
            )
    return compromised


def _mass_plate_area(
    session: StudySession,
    mass_def: Any,
    config: dict[str, Any],
) -> float:
    target = _mass_target_gsf(session, mass_def.departments)
    voids = _void_area_for_mass(session, mass_def.departments, config)
    return plate_area(target, mass_def.story_count, sum(v.area_sf for v in voids))


def _resolve_widths(
    session: StudySession,
    config: dict[str, Any],
) -> tuple[dict[str, float], dict[str, str]]:
    """
    Width per mass. Paired masses share W = sum(plates) / total_length;
    unpaired masses fall back to the fixed-width constraint chain.
    """
    widths: dict[str, float] = {}
    pairing_of: dict[str, str] = {}
    by_id = {m.id: m for m in session.masses}

    for pairing in session.pairings:
        members = [by_id[mid] for mid in pairing.mass_ids if mid in by_id]
        if len(members) < 2 or pairing.total_length_ft <= 0:
            continue
        plates = [_mass_plate_area(session, m, config) for m in members]
        shared_width, _ = solve_paired_masses(plates, pairing.total_length_ft)
        for m in members:
            widths[m.id] = shared_width
            pairing_of[m.id] = pairing.id

    for mass_def in session.masses:
        widths.setdefault(
            mass_def.id, _resolve_fixed_width(session, mass_def.id, config)
        )
    return widths, pairing_of


def check_pairing_lengths(
    session: StudySession,
    result: MassingStudyResult,
) -> list[ValidationCheck]:
    """Combined ground-floor length of each pairing vs its stated total length."""
    checks: list[ValidationCheck] = []
    solved_by_id = {m.id: m for m in result.masses}

    for pairing in session.pairings:
        members = [solved_by_id[mid] for mid in pairing.mass_ids if mid in solved_by_id]
        if not members or pairing.total_length_ft <= 0:
            continue
        combined = sum(m.floors[0].length_ft for m in members if m.floors)
        ok = combined <= pairing.total_length_ft + 1.0
        parts = ", ".join(
            f"{m.name} {m.floors[0].length_ft:.1f} ft" for m in members if m.floors
        )
        checks.append(
            ValidationCheck(
                check=f"pairing_length:{pairing.id}",
                passed=ok,
                message=(
                    f"{pairing.id}: combined {combined:.1f} ft vs allowed "
                    f"{pairing.total_length_ft:g} ft at shared width "
                    f"{members[0].fixed_dim_ft:.1f} ft ({parts})"
                ),
            )
        )
    return checks


def check_allocation(
    mass: SolvedMass,
    dept_gsf: dict[str, float],
) -> list[ValidationCheck]:
    """
    Two guarantees the allocation must hold:
    1. Conservation — every department's GSF lands somewhere, in full.
    2. Capacity — no floor is allocated more than its usable area.
    """
    checks: list[ValidationCheck] = []

    allocated: dict[str, float] = {}
    for floor in mass.floors:
        for alloc in floor.allocations:
            allocated[alloc.department] = (
                allocated.get(alloc.department, 0.0) + alloc.gsf
            )

    missing = []
    for dept, want in dept_gsf.items():
        got = allocated.get(dept, 0.0)
        if abs(got - want) > max(1.0, want * 0.001):
            missing.append(f"{dept}: allocated {got:,.0f} of {want:,.0f} SF")
    checks.append(
        ValidationCheck(
            check=f"allocation_conserved:{mass.id}",
            passed=not missing,
            message=(
                f"{mass.name}: all {len(dept_gsf)} departments fully placed "
                f"({sum(allocated.values()):,.0f} SF)"
                if not missing
                else f"{mass.name}: area lost in allocation - " + "; ".join(missing)
            ),
        )
    )

    over = [
        f"L{f.level} {f.allocated_gsf:,.0f} SF in {f.usable_area_sf:,.0f} SF"
        for f in mass.floors
        if f.allocated_gsf > f.usable_area_sf + 1.0
    ]
    checks.append(
        ValidationCheck(
            check=f"floor_capacity:{mass.id}",
            passed=not over,
            message=(
                f"{mass.name}: every floor within usable area "
                + ", ".join(f"L{f.level} {f.utilization * 100:.0f}%" for f in mass.floors)
                if not over
                else f"{mass.name}: floor over capacity - " + "; ".join(over)
            ),
        )
    )
    return checks


def _site_limits(session: StudySession, config: dict[str, Any]) -> dict[str, float]:
    """Merge site limits from chat constraints (priority) and config."""
    limits: dict[str, float] = {}
    planning = config.get("planning_limits", {}) or {}
    site = config.get("site", {}) or {}

    sources = [
        ("max_building_length_ft", planning.get("max_building_length_ft")),
        ("max_building_width_ft", planning.get("max_building_width_ft")),
        ("max_total_length_ft", site.get("max_total_length_ft")),
        ("max_total_width_ft", site.get("max_total_width_ft")),
    ]
    for key, value in sources:
        if value is not None:
            limits[key] = float(value)

    for key in (
        "max_building_length_ft",
        "max_building_width_ft",
        "max_total_length_ft",
        "max_total_width_ft",
        "max_length_ft",
        "max_width_ft",
    ):
        value = session.constraints.get(key)
        if value is not None:
            canonical = {
                "max_length_ft": "max_building_length_ft",
                "max_width_ft": "max_building_width_ft",
            }.get(key, key)
            limits[canonical] = float(value)

    return limits


def check_site_limits(
    mass: SolvedMass,
    limits: dict[str, float],
    target_gsf: float,
) -> tuple[list[ValidationCheck], list[ResizeSuggestion]]:
    """Check per-mass length/width vs site limits; suggest resize when over."""
    checks: list[ValidationCheck] = []
    suggestions: list[ResizeSuggestion] = []
    if not mass.floors:
        return checks, suggestions

    ground = mass.floors[0]
    max_len = limits.get("max_building_length_ft")
    max_wid = limits.get("max_building_width_ft")

    if max_len is not None:
        over = ground.length_ft > max_len + 1e-6
        checks.append(
            ValidationCheck(
                check=f"site_length:{mass.id}",
                passed=not over,
                message=(
                    f"{mass.name}: length {ground.length_ft:.1f} ft vs max {max_len:g} ft"
                ),
            )
        )
        if over:
            stories = len(mass.floors)
            plate_cap = ground.width_ft * max_len
            # Total footprint the mass needs (includes void area carried in the
            # plate), not just target GSF, so voided masses get honest advice.
            needed_footprint = ground.area_sf * stories
            needed_stories = (
                max(stories + 1, math.ceil(needed_footprint / plate_cap))
                if plate_cap > 0
                else stories + 1
            )
            needed_width = ground.area_sf / max_len if max_len > 0 else ground.width_ft
            suggestions.append(
                ResizeSuggestion(
                    mass_id=mass.id,
                    issue=(
                        f"length {ground.length_ft:.1f} ft exceeds max {max_len:g} ft"
                    ),
                    suggestion=(
                        f"Use {needed_stories} stories at {ground.width_ft:.1f} ft wide, "
                        f"or widen to {needed_width:.1f} ft to hold {max_len:g} ft length"
                    ),
                    option_stories=needed_stories,
                    option_width_ft=needed_width,
                )
            )

    if max_wid is not None:
        over = ground.width_ft > max_wid + 1e-6
        checks.append(
            ValidationCheck(
                check=f"site_width:{mass.id}",
                passed=not over,
                message=(
                    f"{mass.name}: width {ground.width_ft:.1f} ft vs max {max_wid:g} ft"
                ),
            )
        )
        if over:
            suggestions.append(
                ResizeSuggestion(
                    mass_id=mass.id,
                    issue=f"width {ground.width_ft:.1f} ft exceeds max {max_wid:g} ft",
                    suggestion=(
                        f"Narrow to {max_wid:g} ft; length becomes "
                        f"{ground.area_sf / max_wid:.1f} ft"
                    ),
                    option_width_ft=max_wid,
                )
            )

    return checks, suggestions


def solve_massing_study(
    session: StudySession,
    config_path: str | None = None,
    gsf_tolerance: float | None = None,
) -> MassingStudyResult:
    config = load_project_config(config_path or session.config_path or None)
    tolerance = (
        gsf_tolerance
        if gsf_tolerance is not None
        else gsf_tolerance_from_config(config)
        if config
        else float(session.constraints.get("gsf_tolerance", 0.03))
    )
    if "gsf_tolerance" in session.constraints:
        tolerance = float(session.constraints["gsf_tolerance"])

    result = MassingStudyResult(
        study_id=session.study_id,
        gsf_tolerance=tolerance,
        rationale=(
            "Equal floor plates from target GSF / stories; voids deducted on floor "
            "above; departments allocated to levels by floor preference and area fit."
        ),
    )

    if not session.masses:
        result.validation.append(
            ValidationCheck(
                check="grouping",
                passed=False,
                message="No masses defined. Set groupings before solving dimensions.",
            )
        )
        return result

    limits = _site_limits(session, config)
    widths, pairing_of = _resolve_widths(session, config)

    for mass_def in session.masses:
        target = _mass_target_gsf(session, mass_def.departments)
        width = widths[mass_def.id]
        voids = _void_area_for_mass(session, mass_def.departments, config)
        floors, actual = solve_mass_footprint(
            target_gsf=target,
            story_count=mass_def.story_count,
            fixed_width_ft=width,
            voids=voids,
            departments=mass_def.departments,
        )
        # Place each department on specific levels with real area math
        depts = _dept_map(session)
        dept_gsf = {
            d: depts[d].target_gsf for d in mass_def.departments if d in depts
        }
        alloc_notes = allocate_programs(
            floors=floors,
            departments=list(mass_def.departments),
            dept_gsf=dept_gsf,
            rooms=session.program.rooms,
            multiplier=session.program.grossing.combined_multiplier,
            config=config,
            pins={
                d: lvl
                for d, lvl in session.floor_pins.items()
                if d in mass_def.departments
            },
            ground_required=_ground_required_departments(
                session, mass_def.departments, voids
            ),
        )
        for note in alloc_notes:
            result.validation.append(
                ValidationCheck(
                    check=f"allocation:{mass_def.id}",
                    passed=True,
                    message=f"{mass_def.name}: {note}",
                )
            )

        delta = actual - target
        fit_ok = gsf_fit_pass(actual, target, tolerance)
        solved = SolvedMass(
            id=mass_def.id,
            name=mass_def.name,
            departments=list(mass_def.departments),
            floors=floors,
            target_gsf=target,
            actual_gsf=actual,
            fit_delta_sf=delta,
            fit_pass=fit_ok,
            fixed_side="width",
            fixed_dim_ft=width,
            pairing_id=pairing_of.get(mass_def.id, ""),
        )
        result.masses.append(solved)

        site_checks, site_suggestions = check_site_limits(solved, limits, target)
        result.validation.extend(site_checks)
        result.resize_suggestions.extend(site_suggestions)
        result.validation.extend(check_allocation(solved, dept_gsf))

        result.validation.append(
            ValidationCheck(
                check=f"gsf_fit:{mass_def.id}",
                passed=fit_ok,
                message=(
                    f"{mass_def.name}: actual {actual:,.0f} SF vs target {target:,.0f} SF "
                    f"(delta {delta:+,.0f}, tol +/-{tolerance*100:.0f}%)"
                ),
            )
        )

        anchors = _match_anchor_rooms(session, mass_def.departments, config)
        # Prefer ground-floor fit for anchors that are in this mass
        compromised = check_anchor_fit(solved, anchors)
        result.compromised_anchor_rooms.extend(compromised)
        for c in compromised:
            result.validation.append(
                ValidationCheck(
                    check=f"anchor_fit:{c.room}",
                    passed=False,
                    message=f"{c.room} needs {c.required_ft} ft; mass is {c.available_ft} ft",
                )
            )
        for a in anchors:
            if not any(c.room == a["room_name"] for c in compromised):
                result.validation.append(
                    ValidationCheck(
                        check=f"anchor_fit:{a['room_name']}",
                        passed=True,
                        message=(
                            f"{a['room_name']} ({a['min_width_ft']:g}x{a['min_length_ft']:g}) "
                            f"fits in {solved.floors[0].width_ft:.1f}x{solved.floors[0].length_ft:.1f}"
                        ),
                    )
                )

    result.validation.extend(check_pairing_lengths(session, result))

    max_total = limits.get("max_total_length_ft")
    if max_total is not None and result.masses:
        combined = sum(m.floors[0].length_ft for m in result.masses if m.floors)
        result.validation.append(
            ValidationCheck(
                check="site_total_length",
                passed=combined <= max_total + 1.0,
                message=(
                    f"All masses combined length {combined:.1f} ft vs site max "
                    f"{max_total:g} ft"
                ),
            )
        )

    unassigned = get_unassigned(session)
    if unassigned:
        result.validation.append(
            ValidationCheck(
                check="unassigned",
                passed=False,
                message=f"Unassigned departments: {', '.join(unassigned)}",
            )
        )

    if session.pairings:
        result.rationale += (
            " Paired masses share a width solved from combined area / total length."
        )

    return result


def get_unassigned(session: StudySession) -> list[str]:
    assigned = {d for m in session.masses for d in m.departments}
    return [d for d in session.department_names() if d not in assigned]
