"""
When an in-mass L around a void fails clear dims / arm depth, try other
widths and story counts within site limits before reporting layout_dims FAIL.
"""

from __future__ import annotations

from typing import Any

from .allocate import allocate_programs
from .layout import (
    clear_dims_for_departments,
    layout_programs,
    min_arm_depth_ft,
    suggested_widths_for_void_layout,
)
from .massing_models import FloorPlate, VoidRegion
from .session import StudySession


def clear_reqs_for_void_mass(
    session: StudySession,
    departments: list[str],
    config: dict[str, Any],
    voids: list[VoidRegion],
) -> list[Any]:
    """Clear dims that must fit in leftover around a void (not the void room)."""
    reqs = clear_dims_for_departments(departments, config)
    void_rooms = {v.room.lower() for v in voids}
    out = []
    for req in reqs.values():
        room = (req.room or "").lower()
        if room and any(room in v or v in room for v in void_rooms):
            continue
        anchors = (config.get("anchor_rooms") or {}).get(req.room) or {}
        if anchors.get("double_height"):
            continue
        out.append(req)
    return out


def within_mass_site_limits(
    width: float, length: float, limits: dict[str, float]
) -> bool:
    max_w = limits.get("max_building_width_ft")
    max_l = limits.get("max_building_length_ft")
    if max_w is not None and width > max_w + 1.0:
        return False
    if max_l is not None and length > max_l + 1.0:
        return False
    return True


def build_allocated_mass(
    session: StudySession,
    mass_def: Any,
    config: dict[str, Any],
    width: float,
    story_count: int,
    voids: list[VoidRegion],
) -> tuple[list[FloorPlate], float, list[str], list[str], list[str]]:
    """Footprint + allocate + layout."""
    from . import solver as S

    target = S._mass_target_gsf(session, mass_def.departments)
    depts = S._dept_map(session)
    ground_required = S._ground_required_departments(
        session, mass_def.departments, voids
    )
    dh_depts = set(session.constraints.get("double_height_departments") or [])
    co_double = bool(dh_depts) and set(mass_def.departments) <= dh_depts
    stack_above = bool(
        ground_required
        and session.constraints.get("stack_above_double_height")
        and not co_double
    )
    if co_double:
        target = S._mass_target_gsf(session, mass_def.departments)
        length = target / width if width else 0.0
        floors = [
            FloorPlate(
                level=0,
                width_ft=width,
                length_ft=length,
                area_sf=target,
                programs=list(mass_def.departments),
            )
        ]
        actual = target
    elif stack_above:
        ground_gsf = sum(
            depts[d].target_gsf for d in ground_required if d in depts
        )
        upper_gsf = sum(
            depts[d].target_gsf
            for d in mass_def.departments
            if d in depts and d not in ground_required
        )
        min_length = max((v.length_ft for v in voids), default=0.0)
        floors = S.solve_stack_above_double_height(
            width, ground_gsf, upper_gsf, min_length, mass_def.departments
        )
        actual = sum(f.usable_area_sf for f in floors)
    else:
        floors, actual = S.solve_mass_footprint(
            target_gsf=target,
            story_count=story_count,
            fixed_width_ft=width,
            voids=voids,
            departments=mass_def.departments,
            weights=S.resolve_step_weights(session, mass_def.id, story_count),
            open_void=False,
        )
    dept_gsf = {
        d: depts[d].target_gsf for d in mass_def.departments if d in depts
    }
    alloc_notes: list[str] = []
    if co_double:
        alloc_notes.append(
            "double-height programs share the ground plate and are both two stories"
        )
    elif stack_above:
        alloc_notes.append(
            "double-height program is two stories; programs above sit on its roof"
        )
    from .tools import resolved_floor_pins

    resolved = resolved_floor_pins(session)
    alloc_notes.extend(
        allocate_programs(
            floors=floors,
            departments=list(mass_def.departments),
            dept_gsf=dept_gsf,
            rooms=session.program.rooms,
            multiplier=session.program.grossing.combined_multiplier,
            config=config,
            pins={
                d: lvl
                for d, lvl in resolved.items()
                if d in mass_def.departments
            },
            ground_required=ground_required,
            skip_ground_leftover=stack_above,
        )
    )
    if co_double:
        for floor in floors:
            if floor.level != 0:
                continue
            for alloc in floor.allocations:
                if alloc.department in dh_depts:
                    alloc.double_height = True
    layout_notes, layout_fails = layout_programs(
        floors, config=config, departments=list(mass_def.departments)
    )
    return floors, actual, alloc_notes, layout_notes, layout_fails


def layout_retry_candidates(
    session: StudySession,
    mass_def: Any,
    config: dict[str, Any],
    width: float,
    voids: list[VoidRegion],
    limits: dict[str, float],
    paired: bool,
) -> list[tuple[int, float]]:
    """Alternate (stories, width) to try when the void-floor L fails dims."""
    from . import solver as S

    target = S._mass_target_gsf(session, mass_def.departments)
    clears = clear_reqs_for_void_mass(session, mass_def.departments, config, voids)
    min_arm = min_arm_depth_ft(config)
    max_w = float(limits.get("max_building_width_ft") or 200.0)
    max_stories = int(
        session.constraints.get("max_stories")
        or (config.get("planning_limits") or {}).get("max_stories")
        or 6
    )
    lo = 40.0
    seen: set[tuple[int, float]] = set()
    out: list[tuple[int, float]] = []

    def add(stories: int, w: float) -> None:
        stories = max(1, int(stories))
        w = round(float(w), 1)
        if w < lo - 0.05 or w > max_w + 0.05:
            return
        key = (stories, w)
        if key in seen:
            return
        seen.add(key)
        out.append(key)

    add(mass_def.story_count, width)
    story_options = [mass_def.story_count]
    for extra in (1, 2):
        s = mass_def.story_count + extra
        if s <= max_stories:
            story_options.append(s)

    for stories in story_options:
        void_area = sum(v.area_sf for v in voids)
        weights = S.resolve_step_weights(session, mass_def.id, stories)
        plates = S.solve_stepped_plates(target, weights, void_area)
        if not plates:
            continue
        void_plate = plates[1] if len(plates) > 1 and voids else plates[0]
        ground = plates[0]
        local_lo = lo
        # A length cap is checked after sizing. Do not raise the trial width
        # to plate / cap — that would make the cap the length.

        if paired:
            pairing = next(
                (p for p in session.pairings if mass_def.id in p.mass_ids), None
            )
            if pairing and pairing.total_length_ft > 0:
                if getattr(pairing, "length_is_cap", False):
                    # The stated length is a maximum. Do not shrink the bar
                    # so the combined length lands on that number.
                    add(stories, width)
                    continue
                by_id = {m.id: m for m in session.masses}
                plates_all: list[float] = []
                for mid in pairing.mass_ids:
                    m = by_id.get(mid)
                    if m is None:
                        continue
                    if mid == mass_def.id:
                        plates_all.append(ground)
                    else:
                        plates_all.append(S._mass_plate_area(session, m, config))
                if plates_all:
                    # Keep the locked frontage; only story changes move shared W.
                    add(stories, sum(plates_all) / pairing.total_length_ft)
            continue

        for want in suggested_widths_for_void_layout(
            void_plate, voids, clears, min_arm, local_lo, max_w
        ):
            add(stories, want)
        for step in (10, 20, 30, 40):
            add(stories, width + step)
        for void in voids:
            add(stories, void.width_ft + 40)
            add(stories, void.length_ft + 40)
            add(stories, void.width_ft + min_arm)
            add(stories, void.length_ft + min_arm)

    out.sort(key=lambda sw: (abs(sw[0] - mass_def.story_count), abs(sw[1] - width)))
    return out


def apply_layout_success(
    session: StudySession,
    mass_def: Any,
    stories: int,
    width: float,
    paired: bool,
    config: dict[str, Any],
) -> list[str]:
    """Persist a working retry onto the session."""
    notes: list[str] = []
    if stories != mass_def.story_count:
        mass_def.story_count = stories
        notes.append(f"{stories} stories")
    if paired:
        # Pairing frontage is user/search-owned. Story changes already shift
        # shared width via plate area; do not rewrite the locked length here.
        return notes
    prev = session.constraints.get(f"{mass_def.id}_width_ft")
    session.constraints[f"{mass_def.id}_width_ft"] = float(width)
    if prev is None or abs(float(prev) - width) > 0.05:
        notes.append(f"width {width:g} ft")
    return notes


def retry_void_layout(
    session: StudySession,
    mass_def: Any,
    config: dict[str, Any],
    width: float,
    voids: list[VoidRegion],
    limits: dict[str, float],
    paired: bool,
    floors: list[FloorPlate],
    actual: float,
    alloc_notes: list[str],
    layout_notes: list[str],
    layout_fails: list[str],
) -> tuple[list[FloorPlate], float, float, list[str], list[str], list[str], str]:
    """
    If layout failed around a void, try other widths/stories.

    Returns floors, actual, width, alloc_notes, layout_notes, layout_fails, retry_note.
    """
    from . import solver as S

    if not layout_fails or not voids:
        return floors, actual, width, alloc_notes, layout_notes, layout_fails, ""

    target = S._mass_target_gsf(session, mass_def.departments)

    for stories, try_w in layout_retry_candidates(
        session, mass_def, config, width, voids, limits, paired
    ):
        if stories == mass_def.story_count and abs(try_w - width) < 0.05:
            continue
        void_area = sum(v.area_sf for v in voids)
        weights = S.resolve_step_weights(session, mass_def.id, stories)
        plates = S.solve_stepped_plates(target, weights, void_area)
        if not plates or try_w <= 0:
            continue
        longest = max(plates) / try_w
        if not within_mass_site_limits(try_w, longest, limits):
            continue
        trial = build_allocated_mass(
            session, mass_def, config, try_w, stories, voids
        )
        trial_floors, trial_actual, trial_alloc, trial_notes, trial_fails = trial
        if trial_fails:
            continue
        applied = apply_layout_success(
            session, mass_def, stories, try_w, paired, config
        )
        retry_note = (
            f"Adjusted {mass_def.name} so void-floor L meets dims "
            f"({', '.join(applied) or f'{try_w:g} ft wide'})"
        )
        return (
            trial_floors,
            trial_actual,
            try_w,
            trial_alloc,
            trial_notes,
            trial_fails,
            retry_note,
        )

    return floors, actual, width, alloc_notes, layout_notes, layout_fails, ""
