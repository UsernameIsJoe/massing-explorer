"""
Write a solved study as Rhino solids.

Each program on a checked floor is its own cube, colored by department.
Those cubes stack into the same mass. A void stays open. Masses sit along X
with a gap between them, depth along Y, up along Z. No new sizes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .layout import remaining_region
from .massing_models import FloorPlate, MassingStudyResult, SolvedMass

DEFAULT_STORY_HEIGHT_FT = 14.0
# Clear air between masses so adjacent wings do not read as one bar.
MASS_GAP_FT = 20.0

# Same family of colours as the plan drawings, as RGBA.
_PALETTE = (
    (76, 120, 168, 255),
    (245, 133, 24, 255),
    (84, 162, 75, 255),
    (228, 87, 86, 255),
    (178, 121, 162, 255),
    (114, 183, 178, 255),
    (238, 202, 59, 255),
    (157, 117, 93, 255),
)


def story_height_from_config(config: dict[str, Any] | None) -> float:
    if not config:
        return DEFAULT_STORY_HEIGHT_FT
    value = config.get("story_height_ft")
    if value is None:
        return DEFAULT_STORY_HEIGHT_FT
    height = float(value)
    if height <= 0:
        return DEFAULT_STORY_HEIGHT_FT
    return height


def _layer_token(name: str) -> str:
    token = re.sub(r"[\\/:*?\"<>|]+", " - ", name or "").strip(" -")
    token = re.sub(r"\s+", " ", token)
    return token or "mass"


def _place_order(result: MassingStudyResult) -> list[SolvedMass]:
    """Pairing members stay contiguous, same order as the site plan."""
    masses = [m for m in result.masses if m.floors]
    masses.sort(key=lambda m: (m.pairing_id == "", m.pairing_id, m.id))
    return masses


def _snap(value: float) -> float:
    return round(float(value), 4)


def _union_loop(rects: list[tuple[float, float, float, float]]) -> list[tuple[float, float]] | None:
    """
    Closed outline of a hole-free union of axis-aligned rects.

    A frame around an interior void is rejected so the hole is not filled.
    Local axes: x = width, y = length.
    """
    if not rects:
        return None
    if len(rects) == 1:
        x, y, w, h = rects[0]
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]

    def key(p: tuple[float, float]) -> tuple[float, float]:
        return (_snap(p[0]), _snap(p[1]))

    directed: list[tuple[tuple[float, float], tuple[float, float]]] = []
    undirected: dict[tuple[tuple[float, float], tuple[float, float]], int] = {}
    area = 0.0
    for x, y, w, h in rects:
        if w <= 0 or h <= 0:
            continue
        area += w * h
        pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        for i in range(4):
            a, b = key(pts[i]), key(pts[(i + 1) % 4])
            undirected[tuple(sorted((a, b)))] = undirected.get(tuple(sorted((a, b))), 0) + 1
            directed.append((a, b))

    nxt: dict[tuple[float, float], tuple[float, float]] = {}
    boundary = 0
    for a, b in directed:
        if undirected[tuple(sorted((a, b)))] != 1:
            continue
        if a in nxt:
            return None
        nxt[a] = b
        boundary += 1
    if boundary < 4 or not nxt:
        return None

    start = next(iter(nxt))
    loop = [start]
    cur = start
    for _ in range(boundary):
        cur = nxt.get(cur)  # type: ignore[assignment]
        if cur is None or cur == start:
            break
        loop.append(cur)
    if cur != start or len(loop) != boundary:
        return None

    shoelace = 0.0
    for i, (x, y) in enumerate(loop):
        x2, y2 = loop[(i + 1) % len(loop)]
        shoelace += x * y2 - x2 * y
    if abs(abs(shoelace) / 2.0 - area) > 1.0:
        return None
    return loop


def _department_colors(result: MassingStudyResult) -> dict[str, tuple[int, int, int, int]]:
    seen: list[str] = []
    for mass in result.masses:
        for name in list(mass.departments) + [
            a.department for f in mass.floors for a in f.allocations
        ] + [p for f in mass.floors for p in f.programs]:
            if name and name not in seen:
                seen.append(name)
    return {name: _PALETTE[i % len(_PALETTE)] for i, name in enumerate(seen)}


def _program_rects(
    floor: FloorPlate,
) -> list[tuple[str, tuple[float, float, float, float], float]]:
    """One cube per program piece. Footprints if laid out, else split the leftover by GSF."""
    placed: list[tuple[str, tuple[float, float, float, float], float]] = []
    for alloc in floor.allocations:
        for piece in alloc.footprints:
            placed.append(
                (
                    alloc.department,
                    (piece.x_ft, piece.y_ft, piece.width_ft, piece.length_ft),
                    alloc.gsf,
                )
            )
    if placed:
        return placed

    space = _pieces(floor)
    if not space:
        return []
    allocs = [a for a in floor.allocations if a.gsf > 0]
    if not allocs:
        name = floor.programs[0] if floor.programs else "program"
        return [(name, rect, 0.0) for rect in space]
    if len(space) == 1 and len(allocs) >= 1:
        return _split_bar(space[0], allocs)
    # Several leftover pieces: give each program the next piece, then split the last.
    out: list[tuple[str, tuple[float, float, float, float], float]] = []
    for i, alloc in enumerate(allocs):
        if i < len(space) - 1:
            out.append((alloc.department, space[i], alloc.gsf))
        else:
            out.extend(_split_bar(space[-1], allocs[i:]))
            break
    return out


def _alignment_deltas(
    pieces: list[tuple[str, tuple[float, float, float, float], float]],
    align_length_ft: float,
) -> tuple[float, float]:
    """Return (shift_x, shift_y) that flushes pieces to width=0 and far length."""
    if not pieces or align_length_ft <= 0:
        return 0.0, 0.0
    min_x = min(rect[0] for _, rect, _ in pieces)
    max_y = max(rect[1] + rect[3] for _, rect, _ in pieces)
    return -min_x, align_length_ft - max_y


def _apply_alignment(
    pieces: list[tuple[str, tuple[float, float, float, float], float]],
    shift_x: float,
    shift_y: float,
) -> list[tuple[str, tuple[float, float, float, float], float]]:
    if abs(shift_x) < 1e-6 and abs(shift_y) < 1e-6:
        return pieces
    aligned: list[tuple[str, tuple[float, float, float, float], float]] = []
    for dept, (x, y, w, h), gsf in pieces:
        aligned.append((dept, (x + shift_x, y + shift_y, w, h), gsf))
    return aligned


def _align_pieces_to_side(
    pieces: list[tuple[str, tuple[float, float, float, float], float]],
    align_length_ft: float,
    *,
    shift_x: float | None = None,
) -> list[tuple[str, tuple[float, float, float, float], float]]:
    """
    Flush every floor to the same two faces.

    Length stacks against the far end of the mass so a shorter story only
    steps back from the other end. Width stacks against local x = 0.

    When shift_x is supplied (mass-wide shared width flush), only length is
    computed from this piece set — programs and voids stay in one frame.
    """
    if not pieces or align_length_ft <= 0:
        return pieces
    dx, dy = _alignment_deltas(pieces, align_length_ft)
    if shift_x is not None:
        dx = float(shift_x)
    return _apply_alignment(pieces, dx, dy)


def _mass_shared_shift_x(mass: SolvedMass) -> float:
    """One width flush for every program and void in the mass."""
    xs: list[float] = []
    for floor in mass.floors:
        for _dept, rect, _gsf in _program_rects(floor):
            xs.append(float(rect[0]))
        for void in floor.voids:
            if float(void.width_ft or 0) > 0 and float(void.length_ft or 0) > 0:
                xs.append(float(void.x_ft or 0))
    if not xs:
        return 0.0
    return -min(xs)


def _void_dummies(
    floor: FloorPlate,
) -> list[tuple[str, tuple[float, float, float, float], float]]:
    return [
        ("void", (float(v.x_ft), float(v.y_ft), float(v.width_ft), float(v.length_ft)), 0.0)
        for v in floor.voids
        if float(v.width_ft or 0) > 0 and float(v.length_ft or 0) > 0
    ]


def _split_bar(
    rect: tuple[float, float, float, float],
    allocs: list[Any],
) -> list[tuple[str, tuple[float, float, float, float], float]]:
    """Slice a leftover rectangle along its length so programs sit end to end."""
    x, y, w, h = rect
    total = sum(a.gsf for a in allocs) or 1.0
    cursor = y
    end = y + h
    out: list[tuple[str, tuple[float, float, float, float], float]] = []
    for i, alloc in enumerate(allocs):
        if i == len(allocs) - 1:
            span = end - cursor
        else:
            span = h * (alloc.gsf / total)
        if span <= 0:
            continue
        out.append((alloc.department, (x, cursor, w, span), alloc.gsf))
        cursor += span
    return out


def _pieces(floor: FloorPlate) -> list[tuple[float, float, float, float]]:
    """Local rectangles that make this floor, after voids are cut out."""
    if floor.width_ft <= 0 or floor.length_ft <= 0:
        return []
    if not floor.voids:
        return [(0.0, 0.0, floor.width_ft, floor.length_ft)]
    leftover = remaining_region(floor)
    rects = [(r.x, r.y, r.w, r.h) for r in leftover if r.w > 0 and r.h > 0]
    return rects


def export_rhino(
    result: MassingStudyResult,
    output_path: str | Path,
    story_height_ft: float = DEFAULT_STORY_HEIGHT_FT,
) -> Path:
    """Write `.3dm` solids. Requires rhino3dm."""
    try:
        import rhino3dm
    except ImportError as e:
        raise ImportError(
            "rhino3dm is required for Rhino export. pip install rhino3dm"
        ) from e

    out = Path(output_path)
    if out.suffix.lower() != ".3dm":
        out = out.with_suffix(".3dm")
    out.parent.mkdir(parents=True, exist_ok=True)

    height = float(story_height_ft) if story_height_ft and story_height_ft > 0 else DEFAULT_STORY_HEIGHT_FT
    model = rhino3dm.File3dm()
    model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Feet
    model.ApplicationName = "Massing Explorer"
    notes = (
        f"Massing Explorer {result.study_id or 'study'}. Units: feet. "
        f"Story height {height:g} ft (extrusion only, not a solved dimension). "
        "X is frontage / length, Y is width, Z is up. "
        "Each solid is one program on a checked floor. Cubes of the same mass stack "
        "flush to one end; a shorter story steps back from the other end only. "
        f"Masses are separated by {MASS_GAP_FT:g} ft so they can be read apart. "
        "Color is the department. Voids stay open."
    )
    try:
        model.StartSectionComments = notes
    except Exception:
        pass

    layers = _LayerIndex(model)
    colors = _department_colors(result)
    masses = _place_order(result)
    origin_x = 0.0
    written = 0

    for i, mass in enumerate(masses):
        origin_x, count = _write_mass(
            model, layers, mass, origin_x, height, colors
        )
        written += count
        if i < len(masses) - 1:
            origin_x += MASS_GAP_FT

    if written == 0:
        raise ValueError("No floor plates to export.")

    if not model.Write(str(out), 7):
        raise OSError(f"Rhino write failed: {out}")
    return out


def _write_mass(
    model: Any,
    layers: Any,
    mass: SolvedMass,
    origin_x: float,
    height: float,
    colors: dict[str, tuple[int, int, int, int]],
) -> tuple[float, int]:
    written = 0
    fallback = _PALETTE[0]
    align_length = mass.floors[0].length_ft
    # One width flush for programs and voids so the DH column lands in the
    # punched hole instead of on a neighboring ground program.
    shared_x = _mass_shared_shift_x(mass)
    # A double-height room is the void the upper floor wraps, not the whole
    # ground plate. Extruding the whole plate fills the L and they intersect.
    voids = _aligned_voids(mass, align_length, shift_x=shared_x)
    void_dept = _double_height_department(mass)
    for floor in mass.floors:
        z0 = floor.level * height
        pieces = _aligned_floor_programs(floor, align_length, shift_x=shared_x)
        double_height = {
            a.department: a.double_height for a in floor.allocations
        }
        if floor.level == 0 and voids and void_dept:
            pieces = _punch_voids(pieces, voids, void_dept)
        counts: dict[str, int] = {}
        for dept, rect, gsf in pieces:
            counts[dept] = counts.get(dept, 0) + 1
        seen: dict[str, int] = {}
        for dept, rect, gsf in pieces:
            stories = 1
            if (
                double_height.get(dept)
                and floor.level == 0
                and not voids
            ):
                stories = 2
            ext = _extrude_rect(rect, origin_x, z0, height * stories)
            if ext is None:
                continue
            seen[dept] = seen.get(dept, 0) + 1
            label = f"{dept} / {mass.name} L{floor.level}"
            if counts[dept] > 1:
                label = f"{label} part {seen[dept]}"
            color = colors.get(dept, fallback)
            layer = layers.child(_layer_token(mass.name), _layer_token(dept), color)
            x, y, w, h = rect
            _add(
                model,
                ext,
                layer,
                label,
                {
                    "role": "program",
                    "mass_id": mass.id,
                    "department": dept,
                    "level": str(floor.level),
                    "gsf": f"{gsf:.1f}",
                    "width_ft": f"{w:.2f}",
                    "length_ft": f"{h:.2f}",
                    "story_height_ft": f"{height * stories:g}",
                    "double_height": "true" if stories == 2 else "false",
                    "origin_x_ft": f"{origin_x:.2f}",
                    "voids": str(len(floor.voids)),
                },
            )
            written += 1

    if voids and void_dept:
        written += _write_void_volumes(
            model, layers, mass, origin_x, height, colors, voids, void_dept, fallback
        )

    ground = mass.floors[0]
    return origin_x + ground.length_ft, written


def _double_height_department(mass: SolvedMass) -> str:
    for floor in mass.floors:
        if floor.level != 0:
            continue
        for alloc in floor.allocations:
            if alloc.double_height:
                return alloc.department
    if mass.floors:
        return mass.floors[0].programs[0] if mass.floors[0].programs else ""
    return ""


def _aligned_voids(
    mass: SolvedMass,
    align_length: float,
    *,
    shift_x: float | None = None,
) -> list[tuple[float, float, float, float]]:
    """
    Void rectangles for the DH column and ground punch.

    Width uses the mass-wide shared flush. Length stays in the ground plate's
    frame so a shorter wrapping story's end-flush cannot slide the void onto
    a neighboring ground program (Art/Media beside the gym).
    """
    shared_x = _mass_shared_shift_x(mass) if shift_x is None else float(shift_x)
    shift_y = 0.0
    if mass.floors:
        ground_pieces = _program_rects(mass.floors[0])
        if ground_pieces:
            _ignored_x, shift_y = _alignment_deltas(ground_pieces, align_length)
    out: list[tuple[float, float, float, float]] = []
    for floor in mass.floors:
        if floor.level == 0 or not floor.voids:
            continue
        for void in floor.voids:
            w = float(void.width_ft or 0)
            h = float(void.length_ft or 0)
            if w <= 0 or h <= 0:
                continue
            out.append(
                (
                    float(void.x_ft or 0) + shared_x,
                    float(void.y_ft or 0) + shift_y,
                    w,
                    h,
                )
            )
    return out


def _aligned_floor_programs(
    floor: FloorPlate,
    align_length: float,
    *,
    shift_x: float,
) -> list[tuple[str, tuple[float, float, float, float], float]]:
    """
    Align one floor's programs.

    Floors that wrap a void include void dummies so the leftover L keeps its
    relative hole; void dummies are dropped from the returned program list.
    """
    pieces = _program_rects(floor)
    dummies = _void_dummies(floor)
    if dummies:
        aligned = _align_pieces_to_side(pieces + dummies, align_length, shift_x=shift_x)
        return [(dept, rect, gsf) for dept, rect, gsf in aligned if dept != "void"]
    return _align_pieces_to_side(pieces, align_length, shift_x=shift_x)


def iter_mass_solid_boxes(
    mass: SolvedMass,
    origin_x: float,
    story_height_ft: float,
) -> list[dict[str, Any]]:
    """
    Axis-aligned program/void solids for one mass (world X length, Y width, Z up).

    Same placement rules as Rhino export and the browser preview.
    Each box: x0,y0,z0,x1,y1,z1 plus mass/department/level metadata.
    """
    if not mass.floors:
        return []
    height = float(story_height_ft)
    align_length = float(mass.floors[0].length_ft or 0)
    shared_x = _mass_shared_shift_x(mass)
    voids = _aligned_voids(mass, align_length, shift_x=shared_x)
    void_dept = _double_height_department(mass)
    boxes: list[dict[str, Any]] = []

    def _add_box(
        *,
        dept: str,
        rect: tuple[float, float, float, float],
        z0: float,
        dz: float,
        level: int,
        role: str,
        part: int = 0,
    ) -> None:
        local_x, local_y, w, h = rect
        if w <= 0 or h <= 0 or dz <= 0:
            return
        # Local x/width -> world Y; local y/length -> world X.
        boxes.append(
            {
                "id": f"{mass.id}_L{level}_{dept}_{role}_{part}",
                "mass_id": mass.id,
                "mass": mass.name,
                "department": dept,
                "level": level,
                "role": role,
                "x0": origin_x + local_y,
                "y0": local_x,
                "z0": z0,
                "x1": origin_x + local_y + h,
                "y1": local_x + w,
                "z1": z0 + dz,
            }
        )

    for floor in mass.floors:
        z0 = floor.level * height
        pieces = _aligned_floor_programs(floor, align_length, shift_x=shared_x)
        double_height = {a.department: a.double_height for a in floor.allocations}
        if floor.level == 0 and voids and void_dept:
            pieces = _punch_voids(pieces, voids, void_dept)
        for i, (dept, rect, _gsf) in enumerate(pieces):
            stories = 1
            if double_height.get(dept) and floor.level == 0 and not voids:
                stories = 2
            _add_box(
                dept=dept,
                rect=rect,
                z0=z0,
                dz=height * stories,
                level=floor.level,
                role="program",
                part=i + 1,
            )

    if voids and void_dept:
        for i, rect in enumerate(voids):
            _add_box(
                dept=void_dept,
                rect=rect,
                z0=0.0,
                dz=height * 2,
                level=0,
                role="void_column",
                part=i + 1,
            )
    return boxes


def iter_study_solid_boxes(
    result: MassingStudyResult,
    story_height_ft: float,
) -> list[dict[str, Any]]:
    """All program/void solids for a study, masses laid in a row with gaps."""
    boxes: list[dict[str, Any]] = []
    origin_x = 0.0
    masses = _place_order(result)
    for i, mass in enumerate(masses):
        boxes.extend(iter_mass_solid_boxes(mass, origin_x, story_height_ft))
        if mass.floors:
            origin_x += float(mass.floors[0].length_ft or 0)
            if i < len(masses) - 1:
                origin_x += MASS_GAP_FT
    return boxes


def colliding_solid_pairs(
    boxes: list[dict[str, Any]],
    *,
    eps: float = 0.05,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Pairs of solids whose AABBs overlap by more than eps on every axis."""
    hits: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            if (
                min(float(a["x1"]), float(b["x1"])) - max(float(a["x0"]), float(b["x0"])) > eps
                and min(float(a["y1"]), float(b["y1"])) - max(float(a["y0"]), float(b["y0"])) > eps
                and min(float(a["z1"]), float(b["z1"])) - max(float(a["z0"]), float(b["z0"])) > eps
            ):
                hits.append((a, b))
    return hits


def _punch_voids(
    pieces: list[tuple[str, tuple[float, float, float, float], float]],
    voids: list[tuple[float, float, float, float]],
    void_dept: str,
) -> list[tuple[str, tuple[float, float, float, float], float]]:
    """Drop the double-height footprint out of the ground plate."""
    punched: list[tuple[str, tuple[float, float, float, float], float]] = []
    for dept, rect, gsf in pieces:
        if dept != void_dept:
            punched.append((dept, rect, gsf))
            continue
        remain = [rect]
        for hole in voids:
            nxt: list[tuple[float, float, float, float]] = []
            for part in remain:
                nxt.extend(_subtract_rect(part, hole))
            remain = nxt
        for part in remain:
            punched.append((dept, part, gsf))
    return punched


def _subtract_rect(
    rect: tuple[float, float, float, float],
    hole: tuple[float, float, float, float],
) -> list[tuple[float, float, float, float]]:
    x, y, w, h = rect
    hx, hy, hw, hh = hole
    x0 = max(x, hx)
    y0 = max(y, hy)
    x1 = min(x + w, hx + hw)
    y1 = min(y + h, hy + hh)
    if x1 - x0 <= 1e-6 or y1 - y0 <= 1e-6:
        return [rect]
    pieces: list[tuple[float, float, float, float]] = []
    if x0 - x > 1e-6:
        pieces.append((x, y, x0 - x, h))
    if x + w - x1 > 1e-6:
        pieces.append((x1, y, x + w - x1, h))
    mid_w = x1 - x0
    if y0 - y > 1e-6:
        pieces.append((x0, y, mid_w, y0 - y))
    if y + h - y1 > 1e-6:
        pieces.append((x0, y1, mid_w, y + h - y1))
    return [p for p in pieces if p[2] > 1e-6 and p[3] > 1e-6]


def _write_void_volumes(
    model: Any,
    layers: Any,
    mass: SolvedMass,
    origin_x: float,
    height: float,
    colors: dict[str, tuple[int, int, int, int]],
    voids: list[tuple[float, float, float, float]],
    void_dept: str,
    fallback: tuple[int, int, int, int],
) -> int:
    written = 0
    color = colors.get(void_dept, fallback)
    layer = layers.child(_layer_token(mass.name), _layer_token(void_dept), color)
    for i, rect in enumerate(voids):
        ext = _extrude_rect(rect, origin_x, 0.0, height * 2)
        if ext is None:
            continue
        x, y, w, h = rect
        label = f"{void_dept} / {mass.name} L0"
        if len(voids) > 1:
            label = f"{label} part {i + 1}"
        _add(
            model,
            ext,
            layer,
            label,
            {
                "role": "program",
                "mass_id": mass.id,
                "department": void_dept,
                "level": "0",
                "gsf": f"{w * h:.1f}",
                "width_ft": f"{w:.2f}",
                "length_ft": f"{h:.2f}",
                "story_height_ft": f"{height * 2:g}",
                "double_height": "true",
                "origin_x_ft": f"{origin_x:.2f}",
                "voids": "0",
            },
        )
        written += 1
    return written


def _extrude_rect(
    rect: tuple[float, float, float, float],
    origin_x: float,
    z0: float,
    height: float,
) -> Any | None:
    import rhino3dm

    x, y, w, h = rect
    if w <= 0 or h <= 0 or height <= 0:
        return None
    # Local x/width -> world Y; local y/length -> world X.
    box = rhino3dm.Box(
        rhino3dm.BoundingBox(
            origin_x + y,
            x,
            0,
            origin_x + y + h,
            x + w,
            height,
        )
    )
    return _seat(rhino3dm.Extrusion.CreateBoxExtrusion(box, True), z0)


def _extrude_loop(
    loop: list[tuple[float, float]],
    origin_x: float,
    z0: float,
    height: float,
) -> Any | None:
    import rhino3dm

    if len(loop) < 3 or height <= 0:
        return None
    pts = [
        rhino3dm.Point3d(origin_x + y, x, 0)
        for x, y in loop
    ]
    first = pts[0]
    if pts[-1].X != first.X or pts[-1].Y != first.Y or pts[-1].Z != first.Z:
        pts.append(first)
    poly = rhino3dm.Polyline(pts)
    if not poly.IsClosed:
        return None
    ext = rhino3dm.Extrusion.Create(poly.ToPolylineCurve(), height, True)
    return _seat(ext, z0)


def _seat(ext: Any, z0: float) -> Any | None:
    """Put the solid on its story, even if the extrusion ran downward."""
    import rhino3dm

    if ext is None:
        return None
    box = ext.GetBoundingBox()
    shift = z0 - box.Min.Z
    if abs(shift) > 1e-6:
        ext.Translate(rhino3dm.Vector3d(0, 0, shift))
    return ext


def _add(model: Any, geometry: Any, layer_index: int, name: str, strings: dict[str, str]) -> None:
    import rhino3dm

    attr = rhino3dm.ObjectAttributes()
    attr.Name = name
    attr.LayerIndex = layer_index
    for key, value in strings.items():
        attr.SetUserString(key, value)
    model.Objects.AddExtrusion(geometry, attr)


class _LayerIndex:
    def __init__(self, model: Any) -> None:
        self.model = model
        self._parents: dict[str, Any] = {}
        self._children: dict[tuple[str, str], int] = {}

    def child(
        self,
        parent_name: str,
        name: str,
        color: tuple[int, int, int, int],
        visible: bool = True,
    ) -> int:
        import rhino3dm

        key = (parent_name, name)
        if key in self._children:
            return self._children[key]

        parent = self._parents.get(parent_name)
        if parent is None:
            parent_layer = rhino3dm.Layer()
            parent_layer.Name = parent_name
            parent_layer.Color = (90, 90, 90, 255)
            parent_layer.Visible = True
            parent_index = self.model.Layers.Add(parent_layer)
            parent = self.model.Layers[parent_index]
            self._parents[parent_name] = parent

        layer = rhino3dm.Layer()
        layer.Name = name
        layer.ParentLayerId = parent.Id
        layer.Color = color
        layer.Visible = visible
        index = self.model.Layers.Add(layer)
        self._children[key] = index
        return index
