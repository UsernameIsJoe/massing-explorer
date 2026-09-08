"""
Floor-by-floor program allocation.

Phase 4 sized footprints but reported the same department list on every floor.
This module answers the real question: which department sits on which level, and
with how much area. All arithmetic is deterministic; the LLM only supplies
preferences (pins, floor bias) that steer the ordering.

Area bookkeeping: room areas from the program are NFA, while floor capacity is
grossed. Rooms are multiplied by the grossing multiplier before placement so
that the sum of allocations equals the mass target GSF exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .massing_models import FloorPlate, ProgramAllocation
from .models import Room

EPS = 1.0  # SF slack, keeps float noise from forcing spurious splits

# A department slice smaller than this is noise on a concept massing drawing, so
# a floor is left slightly under-filled rather than carrying a token fragment —
# but only when the area can actually go somewhere else (see allocate_programs).
MIN_FRAGMENT_SF = 200.0
MIN_FRAGMENT_FRACTION = 0.03

DEFAULT_GROUND_KEYWORDS = (
    "dining",
    "food",
    "health",
    "physical",
    "media",
    "custodial",
    "maintenance",
    "administration",
    "guidance",
    "medical",
    "kitchen",
    "gymnasium",
    "auditorium",
)

DEFAULT_UPPER_KEYWORDS = (
    "academic",
    "special education",
    "art",
    "music",
    "science",
    "lab",
)


def _pref_keywords(
    config: dict[str, Any], key: str, default: tuple[str, ...]
) -> tuple[str, ...]:
    prefs = (config.get("floor_preferences") or {}) if config else {}
    values = prefs.get(key)
    if values:
        return tuple(str(v).strip().lower() for v in values if str(v).strip())
    return default


def ground_affinity(department: str, config: dict[str, Any] | None = None) -> float:
    """
    Positive = belongs low in the building, negative = happy upstairs.

    Driven by config `floor_preferences.ground` / `.upper` keyword lists so a
    project can override the school-oriented defaults.
    """
    config = config or {}
    name = department.lower()
    score = 0.0
    if any(k in name for k in _pref_keywords(config, "ground", DEFAULT_GROUND_KEYWORDS)):
        score += 1.0
    if any(k in name for k in _pref_keywords(config, "upper", DEFAULT_UPPER_KEYWORDS)):
        score -= 1.0
    return score


@dataclass
class _RoomUnit:
    """A single physical room instance, grossed."""

    name: str
    gsf: float


@dataclass
class _Unit:
    """One department queued for placement."""

    department: str
    gsf: float
    rooms: list[_RoomUnit] = field(default_factory=list)
    affinity: float = 0.0
    pinned_level: int | None = None
    forced_level: int | None = None

    @property
    def target_level(self) -> int | None:
        if self.pinned_level is not None:
            return self.pinned_level
        return self.forced_level


def _level_order(unit: _Unit, story_count: int) -> list[int]:
    """
    Levels to try, best first.

    Everything fills bottom-up. Vertical position is produced by the *order*
    departments are placed in (see `build_units`), not by each department
    hunting its own favourite level — that earlier approach left gaps on upper
    floors which later departments back-filled as meaningless slivers.
    """
    target = unit.target_level
    if target is not None:
        others = sorted(
            (lvl for lvl in range(story_count) if lvl != target),
            key=lambda lvl: abs(lvl - target),
        )
        return [target, *others]
    return list(range(story_count))


def build_units(
    departments: list[str],
    dept_gsf: dict[str, float],
    rooms: list[Room],
    multiplier: float,
    config: dict[str, Any] | None = None,
    pins: dict[str, int] | None = None,
    ground_required: set[str] | None = None,
) -> list[_Unit]:
    """Order departments for placement: pinned, then ground-seeking, then large."""
    config = config or {}
    pins = pins or {}
    ground_required = ground_required or set()

    # A program row with qty 18 is eighteen placeable rooms, not one 18x block,
    # so expand quantity into instances before packing.
    rooms_by_dept: dict[str, list[_RoomUnit]] = {}
    for room in rooms:
        if room.department not in departments:
            continue
        count = max(1, room.qty)
        each = (room.total_area_sf / count) * multiplier
        for _ in range(count):
            rooms_by_dept.setdefault(room.department, []).append(
                _RoomUnit(name=room.room_name, gsf=each)
            )

    units: list[_Unit] = []
    for dept in departments:
        gsf = dept_gsf.get(dept, 0.0)
        if gsf <= 0:
            continue
        units.append(
            _Unit(
                department=dept,
                gsf=gsf,
                rooms=sorted(rooms_by_dept.get(dept, []), key=lambda r: -r.gsf),
                affinity=ground_affinity(dept, config),
                pinned_level=pins.get(dept),
                forced_level=0 if dept in ground_required else None,
            )
        )

    # Placement order *is* the stacking strategy: whatever is placed first
    # claims the lower floors. Pins honour explicit user intent, then rooms
    # forced to grade by a void, then ground-seeking programs, then the largest
    # remaining department.
    units.sort(
        key=lambda u: (
            u.pinned_level is None,
            u.forced_level is None,
            -u.affinity,
            -u.gsf,
        )
    )
    return units


def _take_rooms(queue: list[_RoomUnit], area_sf: float) -> list[str]:
    """
    Pop the room instances that fall inside an area slice.

    Room names are indicative only — the allocated GSF is the authoritative
    number. A room straddling a floor boundary is credited to the floor holding
    more than half of it.
    """
    names: list[str] = []
    used = 0.0
    while queue and used + queue[0].gsf / 2 <= area_sf:
        room = queue.pop(0)
        names.append(room.name)
        used += room.gsf
    return names


def allocate_programs(
    floors: list[FloorPlate],
    departments: list[str],
    dept_gsf: dict[str, float],
    rooms: list[Room],
    multiplier: float,
    config: dict[str, Any] | None = None,
    pins: dict[str, int] | None = None,
    ground_required: set[str] | None = None,
    skip_ground_leftover: bool = False,
) -> list[str]:
    """
    Fill each floor's usable area with department program, splitting a
    department across levels only when it cannot fit on one.

    Mutates `floors` (sets `allocations` and `programs`).
    Returns human-readable notes about splits and overflow.
    """
    notes: list[str] = []
    if not floors:
        return notes

    story_count = len(floors)
    remaining = [f.usable_area_sf for f in floors]
    # level -> department -> (gsf, {room name: count})
    placed: list[dict[str, tuple[float, dict[str, int]]]] = [{} for _ in floors]

    def place(level: int, dept: str, gsf: float, room_names: list[str]) -> None:
        total, names = placed[level].get(dept, (0.0, {}))
        for name in room_names:
            names[name] = names.get(name, 0) + 1
        placed[level][dept] = (total + gsf, names)
        remaining[level] -= gsf

    units = build_units(
        departments, dept_gsf, rooms, multiplier, config, pins, ground_required
    )

    for unit in units:
        order = _level_order(unit, story_count)
        queue = list(unit.rooms)
        need = unit.gsf
        levels_used: list[int] = []

        # Pour the department's area over levels in preference order, cutting it
        # only where a floor runs out. Discrete rooms are not first-fit into
        # leftover gaps, which is what produced meaningless slivers of one
        # department stranded on another department's floor.
        for position, level in enumerate(order):
            if need <= EPS:
                break
            available = remaining[level]
            if available <= EPS:
                continue

            # A double-height program owns the ground plate. Do not pour an
            # upper program into the leftover scrap just to fill the floor.
            ground_owners = set(ground_required or [])
            already = placed[level]
            if (
                skip_ground_leftover
                and level == 0
                and unit.department not in ground_owners
                and unit.forced_level != 0
                and any(name in ground_owners for name in already)
                and unit.affinity <= 0
            ):
                capacity_elsewhere = sum(remaining[lvl] for lvl in order[position + 1 :])
                if capacity_elsewhere + EPS >= need:
                    continue

            # Skip a level that could only hold a token fragment of this
            # department, provided the remaining levels can absorb the area.
            floor_min = max(
                MIN_FRAGMENT_SF, floors[level].usable_area_sf * MIN_FRAGMENT_FRACTION
            )
            if available < floor_min and need > available:
                capacity_elsewhere = sum(
                    remaining[lvl] for lvl in order[position + 1 :]
                )
                if capacity_elsewhere >= need - EPS:
                    continue

            take = min(need, available)
            place(level, unit.department, take, _take_rooms(queue, take))
            need -= take
            levels_used.append(level)

        if need > EPS:
            # No capacity left anywhere: conserve the area on the emptiest floor
            # and tell the user, rather than silently dropping program.
            level = max(range(story_count), key=lambda lvl: remaining[lvl])
            place(level, unit.department, need, _take_rooms(queue, need))
            notes.append(
                f"{unit.department}: {need:,.0f} SF exceeds total usable area; "
                f"parked on L{floors[level].level}"
            )
            levels_used.append(level)

        # Flush any indicative room names left over to the last level used
        if queue and levels_used:
            place(levels_used[-1], unit.department, 0.0, [r.name for r in queue])
            queue.clear()

        distinct = sorted(set(levels_used))
        if len(distinct) > 1:
            notes.append(
                f"{unit.department} spans "
                + ", ".join(f"L{floors[lvl].level}" for lvl in distinct)
            )

    dept_levels: dict[str, int] = {}
    for by_dept in placed:
        for dept in by_dept:
            dept_levels[dept] = dept_levels.get(dept, 0) + 1

    for floor, by_dept in zip(floors, placed):
        allocs = [
            ProgramAllocation(
                department=dept,
                gsf=gsf,
                rooms=[
                    name if count == 1 else f"{name} x{count}"
                    for name, count in names.items()
                ],
                split=dept_levels.get(dept, 1) > 1,
            )
            for dept, (gsf, names) in by_dept.items()
        ]
        owned_by_ground = set(ground_required or [])
        floor.allocations = sorted(allocs, key=lambda a: -a.gsf)
        for alloc in floor.allocations:
            if floor.level == 0 and alloc.department in owned_by_ground:
                alloc.double_height = True
        floor.programs = [a.department for a in floor.allocations]

    return notes
