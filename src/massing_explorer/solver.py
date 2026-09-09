from __future__ import annotations

import math
from typing import Any

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


def resolve_step_weights(
    session: StudySession,
    mass_id: str,
    story_count: int,
) -> list[float]:
    """
    Relative plate size per level. Uniform (all 1.0) unless the mass is stepped.

    Explicit weights win over a taper ratio. A taper is story-count independent
    (each level is `ratio` times the one below), so a search over story counts
    can still vary the height of a tapered mass.
    """
    story_count = max(1, story_count)

    explicit = session.floor_steps.get(mass_id)
    if explicit:
        weights = [float(w) for w in explicit][:story_count]
        while len(weights) < story_count:
            weights.append(weights[-1] if weights else 1.0)
        return weights

    taper = session.floor_tapers.get(mass_id)
    if taper:
        return [float(taper) ** level for level in range(story_count)]

    return [1.0] * story_count


def solve_stepped_plates(
    target_gsf: float,
    weights: list[float],
    void_area_sf: float = 0.0,
) -> list[float]:
    """
    Plate area per level for a stepped mass, from relative weights.

    Usable area is the sum of the plates less the void cut on the level above
    the double-height room, so with P_i = k * w_i:

        k * sum(w) - void = target   ->   k = (target + void) / sum(w)

    Uniform weights reduce this to `plate_area`, which is why both the flat and
    the stepped case go through one code path.
    """
    if not weights:
        return []
    total_weight = sum(weights)
    if total_weight <= 0:
        raise ValueError("step weights must sum to > 0")
    if any(w <= 0 for w in weights):
        raise ValueError("every step weight must be > 0")

    void = void_area_sf if (len(weights) > 1 and void_area_sf > 0) else 0.0
    k = (target_gsf + void) / total_weight
    return [k * w for w in weights]


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
    # `academic_width_ft` is a per-mass key carried over from the manual
    # workflow, so it must only size an academic mass. Left in the general
    # chain, one mistyped key silently gave every other mass the academic
    # width instead of the width the user asked for.
    is_academic = "academic" in mass_id.lower()
    keys = [f"{mass_id}_width_ft", "fixed_width_ft"]
    if is_academic:
        keys.append("academic_width_ft")

    for key in keys:
        if key in c and c[key] is not None:
            return float(c[key])

    # Department-scoped brief widths (before / without mass-id keys).
    dept_widths = c.get("department_widths")
    if isinstance(dept_widths, dict):
        mass = next((m for m in session.masses if m.id == mass_id), None)
        if mass:
            for dept in mass.departments:
                if dept in dept_widths and dept_widths[dept] is not None:
                    return float(dept_widths[dept])

    if "max_building_width_ft" in c and c["max_building_width_ft"] is not None:
        return float(c["max_building_width_ft"])

    planning = config.get("planning_limits", {})
    config_keys = (
        ("academic_width_ft", "max_building_width_ft")
        if is_academic
        else ("max_building_width_ft",)
    )
    for key in config_keys:
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
    weights: list[float] | None = None,
    open_void: bool = True,
) -> tuple[list[FloorPlate], float]:
    """
    Solve a fixed-width footprint with an optional double-height void on floor 1.

    Width is constant up the mass and each level's length follows its plate, so
    `weights` produces a stepped (terraced) mass. Omitting them gives equal
    plates, where P satisfies n*P - void_area ~= target_gsf, with the void
    counted once as a hole in the floor above rather than as extra GSF.

    The void is assumed to lie within the upper floor's footprint. Where a mass
    steps back past the double-height room, part of that roof would in reality
    be open rather than a hole in the plate; the engine has no plan positions,
    so it cannot tell the difference.
    """
    voids = voids or []
    departments = departments or []
    story_count = max(1, story_count)
    void_area = sum(v.area_sf for v in voids)

    if weights is None:
        weights = [1.0] * story_count
    plates = solve_stepped_plates(target_gsf, weights, void_area)
    floors: list[FloorPlate] = []

    for level, area in enumerate(plates):
        level_voids = list(voids) if (level == 1 and void_area > 0) else []
        programs = (
            programs_by_level.get(level, departments)
            if programs_by_level
            else departments
        )
        floors.append(
            FloorPlate(
                level=level,
                width_ft=fixed_width_ft,
                length_ft=solve_other_side(area, fixed_width_ft),
                area_sf=area,
                programs=list(programs),
                voids=level_voids,
            )
        )

    if open_void:
        floors = open_void_through_upper_floors(floors)
    actual = sum(f.usable_area_sf for f in floors)
    return floors, actual


def _copy_void(void: VoidRegion) -> VoidRegion:
    return VoidRegion(
        room=void.room,
        width_ft=void.width_ft,
        length_ft=void.length_ft,
        area_sf=void.area_sf,
        x_ft=void.x_ft,
        y_ft=void.y_ft,
    )


def open_void_through_upper_floors(floors: list[FloorPlate]) -> list[FloorPlate]:
    """
    Keep a double-height hole open. A higher floor must not sit back over the
    gym while the floor under it is set in — that is the inward second floor.

    Area that used to land on that lid stacks as more stories on the same
    outline. Ground length is left alone so a pairing frontage does not grow.
    """
    if len(floors) < 3:
        return floors
    source = next((f for f in floors if f.voids), None)
    if source is None:
        return floors
    void_area = sum(v.area_sf for v in source.voids)
    if void_area <= 0:
        return floors

    target = sum(f.usable_area_sf for f in floors)
    width = source.width_ft
    programs = list(dict.fromkeys(p for f in floors for p in f.programs))
    out: list[FloorPlate] = []
    for floor in floors:
        if floor.level == 0 or floor.voids:
            out.append(floor)
            continue
        out.append(
            FloorPlate(
                level=floor.level,
                width_ft=floor.width_ft,
                length_ft=floor.length_ft,
                area_sf=floor.area_sf,
                programs=list(floor.programs),
                voids=[_copy_void(v) for v in source.voids],
            )
        )

    usable = sum(f.usable_area_sf for f in out)
    upper_usable = max(0.0, source.area_sf - void_area)
    if upper_usable <= 1.0:
        return out

    level = out[-1].level + 1
    while target - usable > 1.0 and level < 12:
        need = target - usable
        if need + void_area <= source.area_sf + 1.0:
            area = need + void_area
        else:
            area = source.area_sf
        out.append(
            FloorPlate(
                level=level,
                width_ft=width,
                length_ft=solve_other_side(area, width),
                area_sf=area,
                programs=list(programs),
                voids=[_copy_void(v) for v in source.voids],
            )
        )
        usable += out[-1].usable_area_sf
        level += 1
    return out


def solve_stack_above_double_height(
    width_ft: float,
    ground_gsf: float,
    upper_gsf: float,
    min_length_ft: float,
    departments: list[str],
) -> list[FloorPlate]:
    """
    Ground plate holds the double-height program. Programs above sit on its
    roof, so they start at level 2 — the missing level is the gym's volume,
    not a hole to wrap around.

    Width is already the functional bar. Length follows that width. It is not
    stretched to a site cap. Extra stories are added only so upper floors stay
    within the ground outline instead of cantilevering past it.
    """
    width = max(float(width_ft), 1.0)
    ground_length = max(float(min_length_ft or 0.0), ground_gsf / width)
    ground_area = width * ground_length
    floors = [
        FloorPlate(
            level=0,
            width_ft=width,
            length_ft=ground_length,
            area_sf=ground_area,
            programs=list(departments),
        )
    ]
    if upper_gsf <= 1.0:
        return floors

    outline = ground_area
    n = max(1, math.ceil(upper_gsf / outline - 1e-9)) if outline > 1.0 else 1
    left = upper_gsf
    for i in range(n):
        area = outline if i < n - 1 else left
        if area <= 1.0:
            break
        floors.append(
            FloorPlate(
                level=2 + i,
                width_ft=width,
                length_ft=solve_other_side(area, width),
                area_sf=area,
                programs=list(departments),
            )
        )
        left -= area
    return floors


def reserve_upper_for_stacked_programs(
    floors: list[FloorPlate],
    ground_gsf: float,
    upper_gsf: float,
) -> list[FloorPlate]:
    """
    Keep a double-height ground program from eating the whole plate.

    Upper programs need enough usable area above that volume. Extra stories
    use the same outline as the first upper floor. Ground length does not grow.
    """
    if upper_gsf <= 1 or not floors:
        return floors
    uppers = [f for f in floors if f.level > 0]
    if not uppers:
        return floors
    capacity = sum(f.usable_area_sf for f in uppers)
    if capacity + 1 >= upper_gsf:
        return floors
    template = uppers[0]
    level = floors[-1].level + 1
    out = list(floors)
    while capacity + 1 < upper_gsf and level < 12:
        need = upper_gsf - capacity
        area = min(template.area_sf, need + sum(v.area_sf for v in template.voids))
        if template.voids and area < template.area_sf:
            area = need + sum(v.area_sf for v in template.voids)
        floor = FloorPlate(
            level=level,
            width_ft=template.width_ft,
            length_ft=solve_other_side(area, template.width_ft),
            area_sf=area,
            programs=list(template.programs),
            voids=[_copy_void(v) for v in template.voids],
        )
        out.append(floor)
        capacity += floor.usable_area_sf
        level += 1
    return out


def check_step_geometry(mass: SolvedMass) -> list[ValidationCheck]:
    """
    Checks that only matter once floor plates differ.

    A void bigger than the plate it is cut into would silently clamp usable area
    to zero and lose program area, and an upper floor larger than the one below
    is a cantilever the user should be told about rather than discover in Rhino.
    """
    checks: list[ValidationCheck] = []
    if len(mass.floors) < 2:
        return checks

    for floor in mass.floors:
        void_area = sum(v.area_sf for v in floor.voids)
        if void_area > 0 and void_area > floor.area_sf + 1e-6:
            checks.append(
                ValidationCheck(
                    check=f"step_void:{mass.id}",
                    passed=False,
                    message=(
                        f"{mass.name}: L{floor.level} plate {floor.area_sf:,.0f} SF is "
                        f"smaller than the {void_area:,.0f} SF void cut into it. "
                        f"Raise the L{floor.level} step or shrink the double-height room."
                    ),
                )
            )

    for lower, upper in zip(mass.floors, mass.floors[1:]):
        if upper.area_sf > lower.area_sf + 1e-6:
            checks.append(
                ValidationCheck(
                    check=f"step_cantilever:{mass.id}",
                    passed=True,
                    message=(
                        f"{mass.name}: L{upper.level} ({upper.area_sf:,.0f} SF) is larger "
                        f"than L{lower.level} ({lower.area_sf:,.0f} SF) - cantilever"
                    ),
                )
            )

    return checks


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
    """
    Ground-floor plate. This is what a pairing divides frontage by, since the
    shared facade is set by the footprint on the site, not the average floor.
    Equals the uniform plate when the mass is not stepped.
    """
    target = _mass_target_gsf(session, mass_def.departments)
    voids = _void_area_for_mass(session, mass_def.departments, config)
    weights = resolve_step_weights(session, mass_def.id, mass_def.story_count)
    plates = solve_stepped_plates(target, weights, sum(v.area_sf for v in voids))
    return plates[0] if plates else 0.0


def functional_bar_width(
    session: StudySession,
    mass_def: Any,
    config: dict[str, Any],
) -> float:
    """
    Depth of a bar from how it should work, not from a length cap.

    A stated width or ratio wins. Otherwise classrooms are single- or
    double-loaded around a corridor. Anchor rooms (gym) cannot be narrower
    than their clear dimension.
    """
    c = session.constraints
    stated = c.get(f"{mass_def.id}_width_ft") or c.get("preferred_width_ft")
    if stated:
        width = float(stated)
    else:
        bar = config.get("bar") or {}
        loading = str(c.get("loading") or bar.get("loading") or "double").lower()
        corridor = float(c.get("corridor_ft") or bar.get("corridor_ft") or 8)
        depth = float(c.get("classroom_depth_ft") or bar.get("classroom_depth_ft") or 30)
        if loading.startswith("single"):
            width = depth + corridor
        else:
            width = (2 * depth) + corridor
    for anchor in _match_anchor_rooms(session, mass_def.departments, config):
        width = max(width, float(anchor.get("min_width_ft") or 0))
    max_w = c.get("max_building_width_ft")
    if max_w:
        width = min(width, float(max_w))
    return max(width, 20.0)


def _fit_width_under_cap(
    width: float,
    plate_sf: float,
    cap_ft: float,
    plates_before: float,
) -> float:
    """
    A length cap is not a length to hit.

    Keep the functional width even if the plate then runs past the cap.
    Widening until length equals the remaining allowance would turn the
    maximum into the design length.
    """
    return width


def _resolve_widths(
    session: StudySession,
    config: dict[str, Any],
) -> tuple[dict[str, float], dict[str, str]]:
    """
    Width per mass. An exact pairing shares W = sum(plates) / stated length.
    A length cap does not set that width — each mass is sized for function,
    then widened only if the combined length would exceed the cap.
    """
    widths: dict[str, float] = {}
    pairing_of: dict[str, str] = {}
    by_id = {m.id: m for m in session.masses}

    for pairing in session.pairings:
        members = [by_id[mid] for mid in pairing.mass_ids if mid in by_id]
        if len(members) < 2 or pairing.total_length_ft <= 0:
            continue
        if getattr(pairing, "length_is_cap", False):
            used = 0.0
            for m in members:
                plate = _mass_plate_area(session, m, config)
                width = functional_bar_width(session, m, config)
                width = _fit_width_under_cap(
                    width, plate, pairing.total_length_ft, used
                )
                widths[m.id] = width
                pairing_of[m.id] = pairing.id
                used += plate / width if width else 0.0
            continue
        plates = [_mass_plate_area(session, m, config) for m in members]
        shared_width, _ = solve_paired_masses(plates, pairing.total_length_ft)
        for m in members:
            widths[m.id] = shared_width
            pairing_of[m.id] = pairing.id

    for mass_def in session.masses:
        widths.setdefault(
            mass_def.id, _width_from_brief(session, mass_def, config)
        )
    return widths, pairing_of


def _width_from_brief(session: StudySession, mass_def: Any, config: dict[str, Any]) -> float:
    """
    Size the bar from how it should work, or from a stated ratio.

    A maximum length is checked later. It is never used as the length.
    An exact length ("the length should be 50") is the only case that
    sets length to that number.
    """
    c = session.constraints
    stated = c.get(f"{mass_def.id}_width_ft") or c.get("preferred_width_ft")
    if stated:
        return functional_bar_width(session, mass_def, config)

    dh = set(c.get("double_height_departments") or [])
    if dh and set(mass_def.departments) <= dh:
        plate = _mass_target_gsf(session, mass_def.departments)
    else:
        plate = _mass_plate_area(session, mass_def, config)
    width = functional_bar_width(session, mass_def, config)
    exact = c.get("exact_building_length_ft")
    if exact and plate > 0:
        return plate / float(exact)
    ratio = c.get("length_over_width")
    if ratio and plate > 0:
        # Prefer the stated proportion. If that length then exceeds a cap,
        # the check fails. Do not widen until length equals the cap.
        width = max(width, math.sqrt(plate / float(ratio)))
    return width


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
        width_word = (
            "functional width" if getattr(pairing, "length_is_cap", False) else "shared width"
        )
        checks.append(
            ValidationCheck(
                check=f"pairing_length:{pairing.id}",
                passed=ok,
                message=(
                    f"{pairing.id}: combined {combined:.1f} ft vs allowed "
                    f"{pairing.total_length_ft:g} ft at {width_word} "
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
        # On a stepped mass the ground floor is usually the longest, but a
        # cantilever can put the worst floor higher up, so check them all.
        longest = max(mass.floors, key=lambda f: f.length_ft)
        over = longest.length_ft > max_len + 1e-6
        where = "" if longest.level == 0 else f" at L{longest.level}"
        checks.append(
            ValidationCheck(
                check=f"site_length:{mass.id}",
                passed=not over,
                message=(
                    f"{mass.name}: length {longest.length_ft:.1f} ft{where} vs max "
                    f"{max_len:g} ft"
                ),
            )
        )
        if over:
            stories = len(mass.floors)
            plate_cap = longest.width_ft * max_len
            # Total footprint the mass needs (includes void area carried in the
            # plate), not just target GSF, so voided masses get honest advice.
            needed_footprint = sum(f.area_sf for f in mass.floors)
            needed_stories = (
                max(stories + 1, math.ceil(needed_footprint / plate_cap))
                if plate_cap > 0
                else stories + 1
            )
            needed_width = longest.area_sf / max_len if max_len > 0 else longest.width_ft
            suggestions.append(
                ResizeSuggestion(
                    mass_id=mass.id,
                    issue=(
                        f"length {longest.length_ft:.1f} ft exceeds max {max_len:g} ft"
                    ),
                    suggestion=(
                        f"{max_len:g} ft is a maximum, not a length to use. "
                        f"Try {needed_stories} stories at {longest.width_ft:.1f} ft wide. "
                        "Do not set the length to the maximum."
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


def _pairing_index(session: StudySession) -> dict[str, Any]:
    by_id = {}
    for pairing in session.pairings:
        for mid in pairing.mass_ids:
            by_id[mid] = pairing
    return by_id


def _rewrite_paired_suggestion(
    suggestion: ResizeSuggestion,
    mass: SolvedMass,
    pairing: Any,
) -> ResizeSuggestion:
    """
    Independent width is not a lever on a paired mass: the shared facade
    fixes W = sum(plates) / pairing_length. Extra stories on this member
    shrink its share of that locked frontage. Changing width means changing
    the pairing length for every member.
    """
    stories = suggestion.option_stories
    if stories is None:
        stories = len(mass.floors) + 1 if mass.floors else 2
    return ResizeSuggestion(
        mass_id=mass.id,
        issue=suggestion.issue,
        suggestion=(
            f"{mass.name} is paired ({pairing.id}) along "
            f"{pairing.total_length_ft:g} ft, so width is locked at "
            f"{mass.fixed_dim_ft:.1f} ft. Adding stories shrinks this mass's "
            f"share of that frontage — use {stories} stories. Do not set an "
            f"independent width; resize_mass will ignore it and change the "
            f"pairing length instead."
        ),
        option_stories=stories,
        option_width_ft=None,
    )


def suggest_resizes(
    session: StudySession,
    result: MassingStudyResult,
    limits: dict[str, float],
) -> list[ResizeSuggestion]:
    """
    Whole-study resize advice. Per-mass check_site_limits assumes width is
    free; this wraps those suggestions so a pairing is never treated as an
    independent bar, and a site-total overage is not "fixed" by adding
    stories to a mass whose frontage is already pinned.
    """
    pairing_of = _pairing_index(session)
    out: list[ResizeSuggestion] = []

    for mass in result.masses:
        _, raw = check_site_limits(mass, limits, mass.target_gsf)
        pairing = pairing_of.get(mass.id)
        if pairing is None:
            out.extend(raw)
        else:
            out.extend(_rewrite_paired_suggestion(s, mass, pairing) for s in raw)

    max_total = limits.get("max_total_length_ft")
    if max_total is not None and result.masses:
        combined = sum(m.floors[0].length_ft for m in result.masses if m.floors)
        if combined > max_total + 1.0:
            unpaired = [m for m in result.masses if m.id not in pairing_of and m.floors]
            locked = sorted({p.id: p.total_length_ft for p in pairing_of.values()}.items())
            locked_ft = sum(length for _, length in locked)
            if unpaired:
                target = max(unpaired, key=lambda m: m.floors[0].length_ft)
                out.append(
                    ResizeSuggestion(
                        mass_id=target.id,
                        issue=(
                            f"combined length {combined:.1f} ft exceeds site max "
                            f"{max_total:g} ft"
                        ),
                        suggestion=(
                            f"Pairings already pin {locked_ft:g} ft of frontage, so "
                            f"adding stories to paired masses will not shorten the "
                            f"site. Add a story to unpaired {target.name} "
                            f"(now {len(target.floors)} st, {target.floors[0].length_ft:.1f} ft)."
                        ),
                        option_stories=len(target.floors) + 1,
                    )
                )
            elif locked:
                out.append(
                    ResizeSuggestion(
                        mass_id=result.masses[0].id,
                        issue=(
                            f"combined length {combined:.1f} ft exceeds site max "
                            f"{max_total:g} ft"
                        ),
                        suggestion=(
                            "Every mass is paired, so story changes only shuffle "
                            "length inside a locked frontage. Shorten a pairing "
                            f"or regroup. Locked: "
                            + ", ".join(f"{pid} {length:g} ft" for pid, length in locked)
                        ),
                    )
                )
    # Layout failures that survived auto-retry — point at a concrete next try
    layout_failed_ids = {
        v.check.split(":", 1)[-1]
        for v in result.validation
        if not v.passed and v.check.startswith("layout_dims:")
    }
    for mass in result.masses:
        if mass.id not in layout_failed_ids or not mass.floors:
            continue
        if any(s.mass_id == mass.id and "void" in s.issue.lower() for s in out):
            continue
        wider = round(mass.fixed_dim_ft + 20.0, 1)
        out.append(
            ResizeSuggestion(
                mass_id=mass.id,
                issue=(
                    f"{mass.name}: leftover around a void cannot host required "
                    f"clear dims / usable arm depths at {mass.fixed_dim_ft:.0f} ft wide"
                ),
                suggestion=(
                    f"Widen {mass.name} toward {wider:g} ft, or add a story so the "
                    f"void floor leaves deeper L arms. Auto-retry already tried "
                    f"nearby widths/stories within site limits."
                ),
                option_width_ft=wider,
                option_stories=len(mass.floors) + 1,
            )
        )
    return out


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
            "Cubic masses with rectangular plates. Voids cut a hole on the floor "
            "above a double-height room; leftover program on that floor may be "
            "L-shaped. Departments allocated by floor preference and area fit. "
            "If an L around a void fails clear dims or arm depth, the solver "
            "retries other widths/stories within site limits before failing. "
            "A stated pairing length is a cap, not a length to fill: width "
            "comes from classroom loading and corridor, or a stated ratio."
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
        paired = mass_def.id in pairing_of

        from .layout_retry import build_allocated_mass, retry_void_layout

        floors, actual, alloc_notes, layout_notes, layout_fails = build_allocated_mass(
            session, mass_def, config, width, mass_def.story_count, voids
        )
        floors, actual, width, alloc_notes, layout_notes, layout_fails, retry_note = (
            retry_void_layout(
                session,
                mass_def,
                config,
                width,
                voids,
                limits,
                paired,
                floors,
                actual,
                alloc_notes,
                layout_notes,
                layout_fails,
            )
        )

        for note in alloc_notes + layout_notes:
            result.validation.append(
                ValidationCheck(
                    check=f"allocation:{mass_def.id}",
                    passed=True,
                    message=f"{mass_def.name}: {note}",
                )
            )
        if retry_note:
            result.validation.append(
                ValidationCheck(
                    check=f"layout_retry:{mass_def.id}",
                    passed=True,
                    message=retry_note,
                )
            )
        for fail in layout_fails:
            result.validation.append(
                ValidationCheck(
                    check=f"layout_dims:{mass_def.id}",
                    passed=False,
                    message=f"{mass_def.name}: {fail}",
                )
            )

        delta = actual - target
        fit_ok = gsf_fit_pass(actual, target, tolerance)
        depts = _dept_map(session)
        dept_gsf = {
            d: depts[d].target_gsf for d in mass_def.departments if d in depts
        }
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

        site_checks, _ = check_site_limits(solved, limits, target)
        result.validation.extend(site_checks)
        preferred = session.constraints.get("preferred_length_ft")
        if preferred and solved.floors:
            longest = max(solved.floors, key=lambda f: f.length_ft)
            over_pref = longest.length_ft > float(preferred) + 1e-6
            result.validation.append(
                ValidationCheck(
                    check=f"preferred_length:{mass_def.id}",
                    passed=not over_pref,
                    message=(
                        f"{mass_def.name}: length {longest.length_ft:.1f} ft vs "
                        f"preferred under {float(preferred):g} ft"
                        + ("" if not over_pref else " (preference missed; the cap is not filled)")
                    ),
                )
            )
        result.validation.extend(check_allocation(solved, dept_gsf))
        result.validation.extend(check_step_geometry(solved))

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

    result.resize_suggestions = suggest_resizes(session, result, limits)
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
        if any(getattr(p, "length_is_cap", False) for p in session.pairings):
            result.rationale += (
                " Named wings are sized for function; their combined length "
                "only has to stay under the stated cap."
            )
        else:
            result.rationale += (
                " Paired masses share a width solved from combined area / total length."
            )
    stepped = [m.name for m in result.masses if m.is_stepped]
    if stepped:
        result.rationale += (
            f" Stepped plates on {', '.join(stepped)}: width held constant, each "
            f"level's length follows its share of the target GSF."
        )

    return result


def get_unassigned(session: StudySession) -> list[str]:
    assigned = {d for m in session.masses for d in m.departments}
    return [d for d in session.department_names() if d not in assigned]
