"""
In-mass program footprints.

Masses stay cubic. A void on an upper floor can leave an L-shaped leftover —
but an L is only accepted when its arms are usable depths and any stated clear
room dims (anchor / user requirement) fit in a real rectangle piece of that
footprint. Leftover SF alone is never enough.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .massing_models import FloorPlate, FootprintRect, ProgramAllocation

EPS = 1e-4
AREA_EPS = 1.0
# When leftover SF is only a rounding-tip above the program GSF, claim the
# whole contiguous region rather than leaving an unusable 2 SF scrap.
NEAR_FULL_EPS = 5.0
DEFAULT_MIN_ARM_DEPTH_FT = 20.0


def _rectangle_fits_room(
    plate_w: float, plate_l: float, room_w: float, room_l: float
) -> bool:
    if room_w <= 0 or room_l <= 0:
        return True
    return (room_w <= plate_w + EPS and room_l <= plate_l + EPS) or (
        room_l <= plate_w + EPS and room_w <= plate_l + EPS
    )


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def area(self) -> float:
        return self.w * self.h

    @property
    def depth(self) -> float:
        """Shorter side — the usable program depth of a bar / arm."""
        return min(self.w, self.h)

    def to_footprint(self) -> FootprintRect:
        return FootprintRect(
            x_ft=self.x, y_ft=self.y, width_ft=self.w, length_ft=self.h
        )


@dataclass
class DimRequirement:
    """Clear size a department must be able to host on this floor."""

    department: str
    min_width_ft: float
    min_length_ft: float
    room: str = ""

    def fits_in(self, rect: Rect) -> bool:
        return _rectangle_fits_room(
            rect.w, rect.h, self.min_width_ft, self.min_length_ft
        )


def clear_dims_for_floor(
    floor: FloorPlate,
    config: dict[str, Any] | None = None,
) -> dict[str, DimRequirement]:
    """
    Clear dims that gate an in-floor footprint on *this* floor.

    Only voided floors use these. On a solid rectangular plate the mass-level
    anchor check already proves the room fits the box; carving clear-dim
    blocks there just fragments the floor. On a voided floor, leftover program
    wrapping the shaft must still host its own clear room (e.g. cafeteria
    40x60 beside a gym void) in a real rectangle arm — leftover SF alone is
    not enough.
    """
    if not floor.voids:
        return {}
    config = config or {}
    void_rooms = {v.room.lower() for v in floor.voids}
    depts = [a.department for a in floor.allocations]
    gsf_by_dept = {a.department: a.gsf for a in floor.allocations}
    all_reqs = clear_dims_for_departments(depts, config)
    out: dict[str, DimRequirement] = {}
    for dept, req in all_reqs.items():
        room = (req.room or "").lower()
        # The void's own room sits on the floor below — not here
        if room and any(room in v or v in room for v in void_rooms):
            continue
        anchors = (config.get("anchor_rooms") or {}).get(req.room) or {}
        if anchors.get("double_height"):
            continue
        clear_area = req.min_width_ft * req.min_length_ft
        if gsf_by_dept.get(dept, 0.0) + AREA_EPS < clear_area:
            continue
        out[dept] = req
    return out


def min_arm_depth_ft(config: dict[str, Any] | None = None) -> float:
    layout = (config or {}).get("layout") or {}
    return float(layout.get("min_arm_depth_ft", DEFAULT_MIN_ARM_DEPTH_FT))


def clear_dims_for_departments(
    departments: list[str],
    config: dict[str, Any] | None = None,
) -> dict[str, DimRequirement]:
    """
    Map department → clear dims from config anchor rooms.

    Matching is by keyword: cafeteria → dining, gym → health/physical, etc.
    """
    config = config or {}
    anchors = config.get("anchor_rooms") or {}
    # keyword fragments in the department name that claim an anchor
    hints: list[tuple[str, tuple[str, ...]]] = [
        ("cafeteria", ("dining", "food", "kitchen")),
        ("gym", ("health", "physical", "gym")),
        ("auditorium", ("auditorium", "performing", "theater", "theatre")),
    ]
    out: dict[str, DimRequirement] = {}
    for key, spec in anchors.items():
        rw = float(spec.get("min_width_ft") or 0)
        rl = float(spec.get("min_length_ft") or 0)
        if rw <= 0 or rl <= 0:
            continue
        key_l = key.lower()
        matched_hint = next((h for h in hints if h[0] in key_l or key_l in h[0]), None)
        keywords = matched_hint[1] if matched_hint else (key_l,)
        for dept in departments:
            dlow = dept.lower()
            if any(k in dlow for k in keywords):
                # Keep the stricter requirement if several anchors match
                prev = out.get(dept)
                if prev is None or rw * rl > prev.min_width_ft * prev.min_length_ft:
                    out[dept] = DimRequirement(dept, rw, rl, room=key)
    return out


def fit_void_dims(vw: float, vl: float, plate_w: float, plate_l: float) -> tuple[float, float]:
    if vw <= plate_w + EPS and vl <= plate_l + EPS:
        return vw, vl
    if vl <= plate_w + EPS and vw <= plate_l + EPS:
        return vl, vw
    return min(vw, plate_w), min(vl, plate_l)


def _void_candidates(
    vw: float, vl: float, plate_w: float, plate_l: float
) -> list[tuple[float, float, float, float]]:
    """
    Candidate (x, y, w, h) placements for a void that leave usable side arms.

    Prefer corners; try both orientations when both fit.
    """
    dims: list[tuple[float, float]] = []
    for a, b in ((vw, vl), (vl, vw)):
        if a <= plate_w + EPS and b <= plate_l + EPS and (a, b) not in dims:
            dims.append((a, b))
    if not dims:
        a, b = fit_void_dims(vw, vl, plate_w, plate_l)
        dims = [(a, b)]

    out: list[tuple[float, float, float, float]] = []
    for a, b in dims:
        # four corners
        out.append((0.0, 0.0, a, b))
        out.append((plate_w - a, 0.0, a, b))
        out.append((0.0, plate_l - b, a, b))
        out.append((plate_w - a, plate_l - b, a, b))
    return out


def place_voids(
    floor: FloorPlate,
    min_depth: float = DEFAULT_MIN_ARM_DEPTH_FT,
) -> None:
    """
    Place voids so leftover arms stay deep enough to be program, when possible.

    Score = usable leftover area whose every rect meets min_depth. Skinny
    leftover that would force stuffing is avoided when another placement works.
    """
    if not floor.voids:
        return
    # Single-void case dominates school massing; multi-void keeps sequential park.
    if len(floor.voids) == 1:
        void = floor.voids[0]
        best = None
        best_score = -1.0
        for x, y, a, b in _void_candidates(
            void.width_ft, void.length_ft, floor.width_ft, floor.length_ft
        ):
            void.x_ft, void.y_ft = x, y
            void.width_ft, void.length_ft = a, b
            leftover = remaining_region(floor)
            usable = [r for r in leftover if r.depth + EPS >= min_depth]
            score = sum(r.area for r in usable)
            # Prefer placements that leave an L of usable arms when area matches
            if region_is_l(usable):
                score += 1.0
            if score > best_score:
                best_score = score
                best = (x, y, a, b)
        if best:
            void.x_ft, void.y_ft, void.width_ft, void.length_ft = best
        return

    cursor_x = 0.0
    cursor_y = 0.0
    col_w = 0.0
    for void in floor.voids:
        vw, vl = fit_void_dims(
            void.width_ft, void.length_ft, floor.width_ft, floor.length_ft
        )
        if cursor_y + vl > floor.length_ft + EPS:
            cursor_x += col_w
            cursor_y = 0.0
            col_w = 0.0
        if cursor_x + vw > floor.width_ft + EPS:
            cursor_x = 0.0
            cursor_y = 0.0
        void.x_ft = cursor_x
        void.y_ft = cursor_y
        void.width_ft = vw
        void.length_ft = vl
        cursor_y += vl
        col_w = max(col_w, vw)


def _clip(space: Rect, hole: Rect) -> Rect | None:
    x0 = max(space.x, hole.x)
    y0 = max(space.y, hole.y)
    x1 = min(space.x + space.w, hole.x + hole.w)
    y1 = min(space.y + space.h, hole.y + hole.h)
    if x1 <= x0 + EPS or y1 <= y0 + EPS:
        return None
    return Rect(x0, y0, x1 - x0, y1 - y0)


def subtract(space: Rect, hole: Rect) -> list[Rect]:
    cut = _clip(space, hole)
    if cut is None:
        return [space]
    if cut.area >= space.area - AREA_EPS:
        return []
    out: list[Rect] = []
    if cut.x > space.x + EPS:
        out.append(Rect(space.x, space.y, cut.x - space.x, space.h))
    right = space.x + space.w
    if cut.x + cut.w < right - EPS:
        out.append(Rect(cut.x + cut.w, space.y, right - (cut.x + cut.w), space.h))
    if cut.y > space.y + EPS:
        out.append(Rect(cut.x, space.y, cut.w, cut.y - space.y))
    top = space.y + space.h
    if cut.y + cut.h < top - EPS:
        out.append(Rect(cut.x, cut.y + cut.h, cut.w, top - (cut.y + cut.h)))
    return [r for r in out if r.w > EPS and r.h > EPS]


def remaining_region(floor: FloorPlate) -> list[Rect]:
    spaces = [Rect(0.0, 0.0, floor.width_ft, floor.length_ft)]
    for void in floor.voids:
        hole = Rect(void.x_ft, void.y_ft, void.width_ft, void.length_ft)
        next_spaces: list[Rect] = []
        for space in spaces:
            next_spaces.extend(subtract(space, hole))
        spaces = next_spaces
    return spaces


def region_is_l(spaces: list[Rect]) -> bool:
    if len(spaces) != 2:
        return False
    return _share_edge(spaces[0], spaces[1])


def _share_edge(a: Rect, b: Rect) -> bool:
    x_overlap = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
    y_overlap = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
    touch_x = abs((a.x + a.w) - b.x) < 0.05 or abs((b.x + b.w) - a.x) < 0.05
    touch_y = abs((a.y + a.h) - b.y) < 0.05 or abs((b.y + b.h) - a.y) < 0.05
    if touch_x and y_overlap > 0.05:
        return True
    if touch_y and x_overlap > 0.05:
        return True
    return False


def _usable(spaces: list[Rect], min_depth: float) -> list[Rect]:
    return [r for r in spaces if r.depth + EPS >= min_depth]


def _clear_block_in(
    space: Rect, req: DimRequirement, min_depth: float = 0.0
) -> Rect | None:
    """
    Carve a clear-dim rectangle out of `space`, or None if it cannot fit.

    If carving the exact clear size would leave a strip thinner than min_depth,
    expand the block to absorb that strip — the clear room still sits inside a
    real rectangle, and we do not invent a dead alley beside it.
    """
    if not req.fits_in(space):
        return None
    candidates: list[Rect] = []
    for w, h in (
        (req.min_width_ft, req.min_length_ft),
        (req.min_length_ft, req.min_width_ft),
    ):
        if w > space.w + EPS or h > space.h + EPS:
            continue
        # Four corner placements of the exact clear block
        origins = (
            (space.x, space.y),
            (space.x + space.w - w, space.y),
            (space.x, space.y + space.h - h),
            (space.x + space.w - w, space.y + space.h - h),
        )
        for ox, oy in origins:
            block = Rect(ox, oy, w, h)
            # Absorb skinny remainders in this space into the block
            x0, y0 = block.x, block.y
            x1, y1 = block.x + block.w, block.y + block.h
            left = x0 - space.x
            right = (space.x + space.w) - x1
            bottom = y0 - space.y
            top = (space.y + space.h) - y1
            if 0 < left + EPS < min_depth:
                x0 = space.x
            if 0 < right + EPS < min_depth:
                x1 = space.x + space.w
            if 0 < bottom + EPS < min_depth:
                y0 = space.y
            if 0 < top + EPS < min_depth:
                y1 = space.y + space.h
            candidates.append(Rect(x0, y0, x1 - x0, y1 - y0))
    if not candidates:
        return None
    # Prefer absorbing skinny waste, then leaving the most usable leftover area
    return max(
        candidates,
        key=lambda c: (c.area, -(space.area - c.area)),
    )


def _slice_options(
    space: Rect, area: float, min_depth: float
) -> list[tuple[Rect, list[Rect]]]:
    """
    All ways to cut `area` from `space` with usable depth.

    Tries both ends of each axis so an L-arm can stay attached to its neighbor
    instead of always growing from the low corner and breaking the corner joint.
    """
    if space.area + AREA_EPS < area or area <= 0:
        return []
    if space.depth + EPS < min_depth:
        return []
    if abs(space.area - area) <= AREA_EPS:
        return [(space, [])]

    def _finish(placed: Rect, rem: Rect | None) -> tuple[Rect, list[Rect]] | None:
        if rem is None or rem.area <= AREA_EPS:
            return placed, []
        if rem.depth + EPS < min_depth:
            # A dead tip is only acceptable when this pour already claims the
            # whole space. Otherwise absorbing it steals SF from later programs
            # ("stuff the leftover" with an oversized earlier footprint).
            if area + AREA_EPS >= space.area:
                return space, []
            return None
        return placed, [rem]

    options: list[tuple[Rect, list[Rect]]] = []
    h = area / space.w
    if min_depth <= h <= space.h + EPS:
        rem_h = space.h - h
        rem = Rect(space.x, space.y + h, space.w, rem_h) if rem_h > EPS else None
        finished = _finish(Rect(space.x, space.y, space.w, h), rem)
        if finished:
            options.append(finished)
        rem = Rect(space.x, space.y, space.w, rem_h) if rem_h > EPS else None
        finished = _finish(Rect(space.x, space.y + space.h - h, space.w, h), rem)
        if finished:
            options.append(finished)
    w = area / space.h
    if min_depth <= w <= space.w + EPS:
        rem_w = space.w - w
        rem = Rect(space.x + w, space.y, rem_w, space.h) if rem_w > EPS else None
        finished = _finish(Rect(space.x, space.y, w, space.h), rem)
        if finished:
            options.append(finished)
        rem = Rect(space.x, space.y, rem_w, space.h) if rem_w > EPS else None
        finished = _finish(Rect(space.x + space.w - w, space.y, w, space.h), rem)
        if finished:
            options.append(finished)
    # De-dupe near-identical cuts
    uniq: list[tuple[Rect, list[Rect]]] = []
    for opt in options:
        p = opt[0]
        if any(
            abs(p.x - q[0].x) < EPS
            and abs(p.y - q[0].y) < EPS
            and abs(p.w - q[0].w) < EPS
            and abs(p.h - q[0].h) < EPS
            for q in uniq
        ):
            continue
        uniq.append(opt)
    return uniq


def _slice_rect(
    space: Rect, area: float, min_depth: float
) -> tuple[Rect, list[Rect]] | None:
    """
    Cut `area` from `space`.

    The placed piece must meet min_depth. A leftover thinner than min_depth is
    absorbed into the placed piece (not left as a dead strip that later looks
    like free area but cannot host program).
    """
    options = _slice_options(space, area, min_depth)
    if not options:
        return None
    return min(options, key=lambda o: abs(o[0].w - o[0].h))


def _try_rectangle(
    spaces: list[Rect], area: float, min_depth: float, req: DimRequirement | None
) -> tuple[list[Rect], list[Rect]] | None:
    spaces = sorted(spaces, key=lambda r: (-r.area, r.y, r.x))
    for i, space in enumerate(spaces):
        if space.depth + EPS < min_depth:
            continue
        if req and not req.fits_in(space) and space.area + AREA_EPS < area:
            # Space can't hold clear dims and isn't the full pour either
            pass
        sliced = _slice_rect(space, area, min_depth)
        if sliced is None:
            continue
        placed, leftover = sliced
        if req and not req.fits_in(placed):
            # Area-matched strip too skinny for the clear room — reject
            continue
        rest = spaces[:i] + leftover + spaces[i + 1 :]
        return [placed], _usable(rest, min_depth)
    return None


def _try_clear_then_fill(
    spaces: list[Rect],
    area: float,
    min_depth: float,
    req: DimRequirement,
) -> tuple[list[Rect], list[Rect]] | None:
    """
    Place the clear-dim block first, then pour remaining GSF into adjacent arms.

    This is the dining-around-gym case: cafeteria 40x60 must exist as a real
    rectangle; the rest of dining may wrap into an L beside the void.
    """
    spaces = sorted(spaces, key=lambda r: (-r.area, r.y, r.x))
    for i, space in enumerate(spaces):
        block = _clear_block_in(space, req, min_depth=min_depth)
        if block is None:
            continue
        # Remaining of this space after the clear block
        after_block = subtract(space, block)
        rest = spaces[:i] + after_block + spaces[i + 1 :]
        # Keep skinny scraps that still touch the block — they belong to this
        # program's L, not free area for someone else.
        touching = [r for r in rest if _share_edge(block, r)]
        rest_usable = _usable(rest, min_depth)
        for r in touching:
            if r not in rest_usable:
                rest_usable.append(r)
        need = area - block.area
        if need <= AREA_EPS:
            return [block], _usable(rest, min_depth)
        fill, rest2 = _fill_wrap(rest_usable, need, min_depth, attach_to=[block])
        if fill is None:
            continue
        if not _parts_connected([block, *fill]):
            continue
        # Drop any absorbed skinny tip that somehow remained as its own piece
        # by merging collinear parts; reject if a sub-min-depth island remains.
        merged = _merge_parts([block, *fill])
        if any(p.depth + EPS < min_depth for p in merged):
            continue
        return merged, rest2
    return None


def _parts_connected(parts: list[Rect]) -> bool:
    if len(parts) <= 1:
        return True
    seen = {0}
    stack = [0]
    while stack:
        i = stack.pop()
        for j, other in enumerate(parts):
            if j in seen:
                continue
            if _share_edge(parts[i], other):
                seen.add(j)
                stack.append(j)
    return len(seen) == len(parts)


def _connected_components(spaces: list[Rect]) -> list[list[Rect]]:
    """Group leftover rects that share edges into contiguous regions."""
    if not spaces:
        return []
    parent = list(range(len(spaces)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(len(spaces)):
        for j in range(i + 1, len(spaces)):
            if _share_edge(spaces[i], spaces[j]):
                union(i, j)
    groups: dict[int, list[Rect]] = {}
    for i, space in enumerate(spaces):
        groups.setdefault(find(i), []).append(space)
    return list(groups.values())


def _fill_wrap(
    spaces: list[Rect],
    need: float,
    min_depth: float,
    attach_to: list[Rect] | None = None,
) -> tuple[list[Rect] | None, list[Rect]]:
    """
    Consume `need` SF from one contiguous leftover region.

    Never stitches disconnected scraps into a fake footprint — that was the
    "stuff the leftover" failure mode. When slicing a bar, prefer the end that
    keeps the new piece attached to already-placed parts (or to `attach_to`).
    """
    if need <= AREA_EPS:
        return [], spaces
    components = sorted(
        _connected_components(spaces),
        key=lambda c: -sum(r.area for r in c),
    )
    anchors = list(attach_to or [])
    for comp in components:
        # Include skinny members of this component when the program is taking
        # essentially the whole contiguous leftover — they are part of the L,
        # not a separate stuffed strip.
        usable = [r for r in comp if min_depth <= 0 or r.depth + EPS >= min_depth]
        skinny = [r for r in comp if r not in usable]
        comp_area = sum(r.area for r in comp)
        usable_area = sum(r.area for r in usable)
        if usable_area + AREA_EPS < need:
            if skinny and need <= comp_area + AREA_EPS:
                usable = list(comp)
                usable_area = comp_area
            else:
                continue
        # Program claims essentially this whole contiguous leftover — take it as
        # one footprint rather than slicing a leftover tip that breaks the L.
        if need + NEAR_FULL_EPS >= usable_area and _parts_connected(usable):
            other = [r for c in components if c is not comp for r in c]
            rest = other if min_depth <= 0 else _usable(other, min_depth)
            if not anchors or _parts_connected([*anchors, *usable]):
                return usable, rest
        # Grow from pieces that already touch anchors when possible. Prefer
        # taking whole rectangles first so the remainder can be sliced from a
        # deep arm without leaving a dead tip (partial-first packing stranded
        # custodial when the large arm couldn't host the full need alone).
        queue = sorted(
            usable,
            key=lambda r: (
                0 if any(_share_edge(r, a) for a in anchors) else 1,
                0 if r.area <= need + AREA_EPS else 1,
                -r.area if r.area <= need + AREA_EPS else r.area,
                r.y,
                r.x,
            ),
        )
        placed: list[Rect] = []
        leftover_comp: list[Rect] = []
        remaining_need = need
        skipped_for_slice: list[Rect] = []
        for space in queue:
            if remaining_need <= AREA_EPS:
                leftover_comp.append(space)
                continue
            depth_gate = 0.0 if space in skinny else max(min_depth, 0.0)
            if depth_gate > 0 and space.depth + EPS < depth_gate:
                leftover_comp.append(space)
                continue
            if space.area <= remaining_need + AREA_EPS:
                placed.append(space)
                remaining_need -= space.area
                continue
            options = _slice_options(space, remaining_need, depth_gate)
            if not options:
                # Cannot cut without a dead tip yet — retry after wholes land
                skipped_for_slice.append(space)
                continue
            targets = anchors + placed

            def _slice_score(opt: tuple[Rect, list[Rect]]) -> tuple:
                piece = opt[0]
                touches = (
                    any(_share_edge(piece, t) for t in targets) if targets else True
                )
                return (0 if touches else 1, abs(piece.w - piece.h))

            piece, rem = min(options, key=_slice_score)
            if targets and not any(_share_edge(piece, t) for t in targets):
                skipped_for_slice.append(space)
                continue
            placed.append(piece)
            leftover_comp.extend(rem)
            remaining_need = 0.0
        if remaining_need > AREA_EPS:
            for space in skipped_for_slice:
                if remaining_need <= AREA_EPS:
                    leftover_comp.append(space)
                    continue
                depth_gate = 0.0 if space in skinny else max(min_depth, 0.0)
                options = _slice_options(space, remaining_need, depth_gate)
                if not options:
                    leftover_comp.append(space)
                    continue
                targets = anchors + placed

                def _slice_score2(opt: tuple[Rect, list[Rect]]) -> tuple:
                    piece = opt[0]
                    touches = (
                        any(_share_edge(piece, t) for t in targets) if targets else True
                    )
                    return (0 if touches else 1, abs(piece.w - piece.h))

                piece, rem = min(options, key=_slice_score2)
                if targets and not any(_share_edge(piece, t) for t in targets):
                    leftover_comp.append(space)
                    continue
                placed.append(piece)
                leftover_comp.extend(rem)
                remaining_need = 0.0
        else:
            leftover_comp.extend(skipped_for_slice)
        if remaining_need > AREA_EPS:
            continue
        other = [r for c in components if c is not comp for r in c]
        rest = leftover_comp + other
        if min_depth > 0:
            rest = _usable(rest, min_depth)
        if not placed:
            continue
        check = [*anchors, *placed] if anchors else placed
        if _parts_connected(check):
            return placed, rest
    return None, spaces


def _place_area(
    spaces: list[Rect],
    area: float,
    min_depth: float,
    req: DimRequirement | None,
) -> tuple[list[Rect] | None, list[Rect], str]:
    """
    Place `area` into usable leftover. Returns (parts|None, leftover, issue).

    Never returns a footprint that fails clear dims or min arm depth.
    """
    usable = _usable(spaces, min_depth)
    if sum(r.area for r in usable) + AREA_EPS < area:
        skinny = sum(r.area for r in spaces) - sum(r.area for r in usable)
        return (
            None,
            usable,
            (
                f"usable leftover {sum(r.area for r in usable):,.0f} SF "
                f"(min arm {min_depth:g} ft) cannot hold {area:,.0f} SF"
                + (f"; {skinny:,.0f} SF of leftover is too skinny" if skinny > 1 else "")
            ),
        )

    if req is not None:
        # Clear dims first — never wrap the required room across an L corner
        result = _try_clear_then_fill(usable, area, min_depth, req)
        if result is not None:
            return result[0], result[1], ""
        # Fallback: whole pour as one rectangle that fits clear dims
        result = _try_rectangle(usable, area, min_depth, req)
        if result is not None:
            return result[0], result[1], ""
        return (
            None,
            usable,
            (
                f"clear dims {req.min_width_ft:g}x{req.min_length_ft:g} ft "
                f"({req.room or 'required'}) do not fit in any usable leftover "
                f"rectangle on this floor"
            ),
        )

    result = _try_rectangle(usable, area, min_depth, None)
    if result is not None:
        return result[0], result[1], ""

    # L / multi-arm wrap only when every arm is deep enough
    fill, rest = _fill_wrap(usable, area, min_depth)
    if fill is not None and _parts_connected(fill):
        return fill, rest, ""
    if fill is not None:
        return (
            None,
            usable,
            "leftover pieces are not connected into one usable footprint",
        )
    return (
        None,
        usable,
        f"cannot form a usable footprint of {area:,.0f} SF with arm depth "
        f">= {min_depth:g} ft",
    )


def classify_shape(parts: list[Rect]) -> str:
    merged = _merge_parts(parts)
    if len(merged) <= 1:
        return "rectangle"
    if len(merged) == 2 and _share_edge(merged[0], merged[1]):
        return "L"
    if len(merged) >= 3:
        return "U"
    return "irregular"


def _merge_parts(parts: list[Rect]) -> list[Rect]:
    work = list(parts)
    changed = True
    while changed:
        changed = False
        for i in range(len(work)):
            for j in range(i + 1, len(work)):
                a, b = work[i], work[j]
                if abs(a.x - b.x) < EPS and abs(a.w - b.w) < EPS:
                    if abs((a.y + a.h) - b.y) < 0.05:
                        work[i] = Rect(a.x, a.y, a.w, a.h + b.h)
                        del work[j]
                        changed = True
                        break
                    if abs((b.y + b.h) - a.y) < 0.05:
                        work[i] = Rect(b.x, b.y, b.w, b.h + a.h)
                        del work[j]
                        changed = True
                        break
                if abs(a.y - b.y) < EPS and abs(a.h - b.h) < EPS:
                    if abs((a.x + a.w) - b.x) < 0.05:
                        work[i] = Rect(a.x, a.y, a.w + b.w, a.h)
                        del work[j]
                        changed = True
                        break
                    if abs((b.x + b.w) - a.x) < 0.05:
                        work[i] = Rect(b.x, b.y, b.w + a.w, b.h)
                        del work[j]
                        changed = True
                        break
            if changed:
                break
    return work


def _snap_voids_to(floor: FloorPlate, locked: list[Any]) -> None:
    """Keep the gym cut in the same place on every floor above it."""
    by_room = {v.room: v for v in locked}
    for void in floor.voids:
        src = by_room.get(void.room)
        if src is None:
            continue
        x = min(max(src.x_ft, 0.0), floor.width_ft)
        y = min(max(src.y_ft, 0.0), floor.length_ft)
        w = min(src.width_ft, max(0.0, floor.width_ft - x))
        h = min(src.length_ft, max(0.0, floor.length_ft - y))
        if w > 1.0 and h > 1.0:
            void.x_ft, void.y_ft = x, y
            void.width_ft, void.length_ft = w, h


def layout_floor(
    floor: FloorPlate,
    config: dict[str, Any] | None = None,
    requirements: dict[str, DimRequirement] | None = None,
    void_lock: list[Any] | None = None,
) -> list[str]:
    """
    Give each allocation a plan footprint that meets dim rules.

    Returns human-readable FAIL messages (empty when every allocation is ok).
    """
    min_depth = min_arm_depth_ft(config)
    # Min arm depth gates L/U leftovers around voids. On a plain rectangular
    # floor there is no hole to wrap — strip packing should not strand area.
    if not floor.voids:
        min_depth = 0.0
    if requirements is None:
        requirements = clear_dims_for_floor(floor, config)
    failures: list[str] = []

    if floor.voids:
        place_voids(floor, min_depth=min_depth)
        if void_lock:
            _snap_voids_to(floor, void_lock)

    spaces = remaining_region(floor)
    for alloc in sorted(floor.allocations, key=lambda a: -a.gsf):
        alloc.footprints = []
        alloc.shape = "rectangle"
        alloc.layout_ok = True
        alloc.layout_issue = ""
        if alloc.gsf <= AREA_EPS:
            continue
        req = requirements.get(alloc.department)
        parts, spaces, issue = _place_area(spaces, alloc.gsf, min_depth, req)
        if parts is None:
            alloc.layout_ok = False
            alloc.layout_issue = issue
            failures.append(
                f"L{floor.level} {alloc.department}: {issue}"
            )
            continue
        alloc.footprints = [p.to_footprint() for p in parts]
        alloc.shape = classify_shape(parts)
        # Final belt-and-suspenders on arm depth
        for p in parts:
            if p.depth + EPS < min_depth:
                alloc.layout_ok = False
                alloc.layout_issue = (
                    f"arm {p.w:.0f}x{p.h:.0f} ft is thinner than "
                    f"min depth {min_depth:g} ft"
                )
                failures.append(f"L{floor.level} {alloc.department}: {alloc.layout_issue}")
                alloc.footprints = []
                break
        if not alloc.layout_ok:
            continue
        if req is not None and not any(req.fits_in(p) for p in parts):
            alloc.layout_ok = False
            alloc.layout_issue = (
                f"clear dims {req.min_width_ft:g}x{req.min_length_ft:g} ft do not "
                f"fit in any piece of the footprint"
            )
            failures.append(f"L{floor.level} {alloc.department}: {alloc.layout_issue}")
            alloc.footprints = []
    return failures


def layout_programs(
    floors: list[FloorPlate],
    config: dict[str, Any] | None = None,
    departments: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """
    Layout every floor.

    Returns (ok_notes, fail_messages). Failures mean the geometry does not meet
    dimension requirements — area allocation may still sum, but the plan shape
    is not acceptable.
    """
    depts = departments or [
        a.department for f in floors for a in f.allocations
    ]
    ok_notes: list[str] = []
    fails: list[str] = []
    void_lock: list[Any] | None = None
    for floor in floors:
        # Per-floor requirements: gym dims stay on the gym floor, not the void
        floor_reqs = clear_dims_for_floor(floor, config)
        fails.extend(
            layout_floor(
                floor, config=config, requirements=floor_reqs, void_lock=void_lock
            )
        )
        if floor.voids and void_lock is None:
            void_lock = list(floor.voids)
        for alloc in floor.allocations:
            if alloc.layout_ok and alloc.shape in {"L", "U"}:
                around = ", ".join(v.room for v in floor.voids) or "the leftover"
                req = floor_reqs.get(alloc.department)
                ok_notes.append(
                    f"L{floor.level}: {alloc.department} is {alloc.shape}-shaped "
                    f"around {around} (arms >= {min_arm_depth_ft(config):g} ft"
                    + (
                        f"; clear {req.min_width_ft:g}x{req.min_length_ft:g} ft held"
                        if req
                        else ""
                    )
                    + ")"
                )
    return ok_notes, fails


def void_leftover_arms(
    plate_w: float, plate_l: float, void_w: float, void_l: float
) -> list[tuple[float, float]]:
    """Side and end arm sizes for a corner void (both orientations tried by caller)."""
    if void_w > plate_w + EPS or void_l > plate_l + EPS:
        return []
    arms: list[tuple[float, float]] = []
    side = plate_w - void_w
    if side > EPS:
        arms.append((side, plate_l))
    end = plate_l - void_l
    if end > EPS:
        arms.append((void_w, end))
    return arms


def plate_can_host_void_layout(
    plate_w: float,
    plate_l: float,
    voids: list[Any],
    clear_reqs: list[DimRequirement] | None = None,
    min_arm: float = DEFAULT_MIN_ARM_DEPTH_FT,
) -> bool:
    """
    Cheap feasibility: some void orientation leaves an arm deep enough for every
    clear-dim requirement (or at least min_arm when there are no clear dims).
    """
    if not voids:
        return True
    clears = list(clear_reqs or [])
    for void in voids:
        vw = float(getattr(void, "width_ft", 0) or 0)
        vl = float(getattr(void, "length_ft", 0) or 0)
        if vw <= 0 or vl <= 0:
            continue
        for a, b in ((vw, vl), (vl, vw)):
            arms = void_leftover_arms(plate_w, plate_l, a, b)
            usable = [(w, h) for w, h in arms if min(w, h) + EPS >= min_arm]
            if not usable:
                continue
            if not clears:
                return True
            if all(
                any(_rectangle_fits_room(w, h, req.min_width_ft, req.min_length_ft)
                    for w, h in usable)
                for req in clears
            ):
                return True
    return False


def suggested_widths_for_void_layout(
    plate_area: float,
    voids: list[Any],
    clear_reqs: list[DimRequirement] | None = None,
    min_arm: float = DEFAULT_MIN_ARM_DEPTH_FT,
    lo: float = 40.0,
    hi: float = 200.0,
) -> list[float]:
    """
    Widths that are likely to leave a usable L around the void for clear dims.

    Used when the current plate fails layout — try these before giving up.
    """
    if plate_area <= 0 or not voids:
        return []
    clears = list(clear_reqs or [])
    # Always try min_arm-only widen even without clear dims
    dummy = clears or [
        DimRequirement("", min_arm, min_arm * 2, room="")
    ]
    cands: set[float] = set()
    for void in voids:
        vw = float(getattr(void, "width_ft", 0) or 0)
        vl = float(getattr(void, "length_ft", 0) or 0)
        if vw <= 0 or vl <= 0:
            continue
        for a, b in ((vw, vl), (vl, vw)):
            for req in dummy:
                for cw, cl in (
                    (req.min_width_ft, req.min_length_ft),
                    (req.min_length_ft, req.min_width_ft),
                ):
                    # Side arm hosts the clear rectangle
                    for side in (max(cw, min_arm), max(cl, min_arm)):
                        width = a + side
                        if width < lo - EPS or width > hi + EPS:
                            continue
                        length = plate_area / width
                        if plate_can_host_void_layout(
                            width, length, [void], clears or None, min_arm
                        ):
                            cands.add(round(width, 1))
                    # End arm hosts the clear rectangle: length = void + clear
                    for end in (max(cw, min_arm), max(cl, min_arm)):
                        length = b + end
                        if length <= EPS:
                            continue
                        width = plate_area / length
                        if width < lo - EPS or width > hi + EPS:
                            continue
                        if plate_can_host_void_layout(
                            width, length, [void], clears or None, min_arm
                        ):
                            cands.add(round(width, 1))
    return sorted(cands)
