from __future__ import annotations

import math
from typing import Any

from .config import gsf_tolerance_from_config, load_project_config
from .massing_models import (
    CompromisedAnchor,
    FloorPlate,
    MassingStudyResult,
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


def _floor_programs(
    session: StudySession,
    departments: list[str],
    level: int,
    story_count: int,
) -> list[str]:
    """Simple stacking: all departments on every floor for equal plates;
    ground-heavy programs noted on level 0 when multi-story."""
    if story_count <= 1:
        return list(departments)

    # Prefer putting HPE/dining/media on lower floors when present
    ground_bias = ("HEALTH", "PHYSICAL", "DINING", "FOOD", "MEDIA", "CUSTODIAL")
    upper_bias = ("ACADEMIC", "SPECIAL EDUCATION", "ADMINISTRATION", "ART", "MUSIC")

    if level == 0:
        ground = [d for d in departments if any(b in d.upper() for b in ground_bias)]
        return ground or list(departments)

    upper = [d for d in departments if any(b in d.upper() for b in upper_bias)]
    # Always list all assigned programs so report stays clear
    return upper or list(departments)


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

    if story_count == 1:
        plate = target_gsf
    else:
        # One void floor between 0 and 1 when voids exist
        n_void_floors = 1 if void_area > 0 else 0
        plate = (target_gsf + void_area * n_void_floors) / story_count

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
                    available_ft=f"{ground.width_ft:g} x {ground.length_ft:.1f}",
                    reason="clear room dims do not fit ground floor footprint",
                )
            )
    return compromised


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
        rationale="Equal floor plates from target GSF / stories; voids deducted on floor above.",
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

    for mass_def in session.masses:
        target = _mass_target_gsf(session, mass_def.departments)
        width = _resolve_fixed_width(session, mass_def.id, config)
        voids = _void_area_for_mass(session, mass_def.departments, config)
        floors, actual = solve_mass_footprint(
            target_gsf=target,
            story_count=mass_def.story_count,
            fixed_width_ft=width,
            voids=voids,
            departments=mass_def.departments,
        )
        # Annotate programs per floor with simple bias
        for fl in floors:
            fl.programs = _floor_programs(
                session, mass_def.departments, fl.level, mass_def.story_count
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
        )
        result.masses.append(solved)

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
                            f"fits in {solved.floors[0].width_ft:g}x{solved.floors[0].length_ft:.1f}"
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

    return result


def get_unassigned(session: StudySession) -> list[str]:
    assigned = {d for m in session.masses for d in m.departments}
    return [d for d in session.department_names() if d not in assigned]
