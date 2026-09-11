"""
Lightweight massing mesh for in-browser 3D preview.

Same placement and program-split logic as Rhino export: each department on a
floor is its own box, colored by department. Double-height volumes span two
storeys (or fill the void column). Masses sit in a row with a gap.
X = frontage/length, Y = width, Z = up.
"""

from __future__ import annotations

from typing import Any

from .massing_models import MassingStudyResult, SolvedMass
from .rhino_export import (
    MASS_GAP_FT,
    _align_pieces_to_side,
    _aligned_voids,
    _double_height_department,
    _place_order,
    _program_rects,
    _punch_voids,
    story_height_from_config,
)

# Longer than the Rhino 8-swatch set so every spreadsheet department stays distinct.
_PREVIEW_PALETTE = (
    "#4C78A8",
    "#F58518",
    "#54A24B",
    "#E45756",
    "#B279A2",
    "#72B7B2",
    "#EECA3B",
    "#9D755D",
    "#6B7F9C",
    "#D4A5A5",
    "#7D9B76",
    "#C9A66B",
    "#8E7CC3",
    "#5E9A9A",
    "#B86B6B",
    "#A67C52",
)


def _department_hex_colors(result: MassingStudyResult) -> dict[str, str]:
    seen: list[str] = []
    for mass in result.masses:
        for name in list(mass.departments) + [
            a.department for f in mass.floors for a in f.allocations
        ] + [p for f in mass.floors for p in f.programs]:
            if name and name not in seen:
                seen.append(name)
    return {name: _PREVIEW_PALETTE[i % len(_PREVIEW_PALETTE)] for i, name in enumerate(seen)}


def preview_mesh(
    result: MassingStudyResult,
    story_height_ft: float | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    height = float(story_height_ft) if story_height_ft else story_height_from_config(config)
    masses = _place_order(result)
    colors = _department_hex_colors(result)
    fallback = _PREVIEW_PALETTE[0]

    boxes: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    origin_x = 0.0
    max_y = 0.0
    max_z = 0.0

    for i, mass in enumerate(masses):
        ground = mass.floors[0] if mass.floors else None
        span = float(ground.length_ft) if ground else 0.0
        align_length = span
        voids = _aligned_voids(mass, align_length)
        void_dept = _double_height_department(mass)
        mass_top = 0.0

        for floor in mass.floors:
            z0 = floor.level * height
            pieces = _align_pieces_to_side(_program_rects(floor), align_length)
            double_height = {
                a.department: a.double_height for a in floor.allocations
            }
            if floor.level == 0 and voids and void_dept:
                pieces = _punch_voids(pieces, voids, void_dept)

            counts: dict[str, int] = {}
            for dept, _rect, _gsf in pieces:
                counts[dept] = counts.get(dept, 0) + 1
            seen: dict[str, int] = {}

            for dept, rect, gsf in pieces:
                stories = 1
                if double_height.get(dept) and floor.level == 0 and not voids:
                    stories = 2
                local_x, local_y, w, h = rect  # width axis, length axis
                if w <= 0 or h <= 0:
                    continue
                dz = height * stories
                seen[dept] = seen.get(dept, 0) + 1
                part = f"_p{seen[dept]}" if counts[dept] > 1 else ""
                cx = origin_x + local_y + h / 2.0
                cy = local_x + w / 2.0
                cz = z0 + dz / 2.0
                boxes.append(
                    {
                        "id": f"{mass.id}_L{floor.level}_{dept}{part}",
                        "mass": mass.name,
                        "mass_id": mass.id,
                        "department": dept,
                        "level": floor.level,
                        "x": round(cx, 3),
                        "y": round(cy, 3),
                        "z": round(cz, 3),
                        "dx": round(h, 3),
                        "dy": round(w, 3),
                        "dz": round(dz, 3),
                        "color": colors.get(dept, fallback),
                        "double_height": stories > 1,
                        "gsf": round(float(gsf or w * h), 1),
                    }
                )
                max_y = max(max_y, local_x + w)
                mass_top = max(mass_top, z0 + dz)

        # Double-height void column (upper floor wraps an open hole).
        if voids and void_dept:
            for vi, rect in enumerate(voids):
                local_x, local_y, w, h = rect
                if w <= 0 or h <= 0:
                    continue
                dz = height * 2
                cx = origin_x + local_y + h / 2.0
                cy = local_x + w / 2.0
                cz = dz / 2.0
                boxes.append(
                    {
                        "id": f"{mass.id}_DH_{void_dept}_{vi + 1}",
                        "mass": mass.name,
                        "mass_id": mass.id,
                        "department": void_dept,
                        "level": 0,
                        "x": round(cx, 3),
                        "y": round(cy, 3),
                        "z": round(cz, 3),
                        "dx": round(h, 3),
                        "dy": round(w, 3),
                        "dz": round(dz, 3),
                        "color": colors.get(void_dept, fallback),
                        "double_height": True,
                        "gsf": round(w * h, 1),
                    }
                )
                max_y = max(max_y, local_x + w)
                mass_top = max(mass_top, dz)

        max_z = max(max_z, mass_top)
        if ground:
            labels.append(
                {
                    "text": mass.name,
                    "x": round(origin_x + span / 2.0, 3),
                    "y": round(ground.width_ft / 2.0, 3),
                    "z": round(mass_top + 4.0, 3),
                }
            )
        origin_x += span
        if i < len(masses) - 1:
            origin_x += MASS_GAP_FT

    legend_departments = [
        {"name": name, "color": colors[name]}
        for name in sorted(colors.keys())
    ]
    legend_masses = [
        {
            "id": m.id,
            "name": m.name,
            "departments": list(m.departments),
            "colors": [colors.get(d, fallback) for d in m.departments],
            "stories": len(m.floors),
            "double_height": any(
                a.double_height for f in m.floors for a in f.allocations
            )
            or bool(_aligned_voids(m, m.floors[0].length_ft if m.floors else 0)),
        }
        for m in masses
    ]

    failed = [
        {"check": str(c.check), "message": str(c.message)}
        for c in result.validation
        if not c.passed
    ]
    return {
        "study_id": result.study_id,
        "story_height_ft": height,
        "units": "ft",
        "boxes": boxes,
        "labels": labels,
        "legend": {
            "departments": legend_departments,
            "masses": legend_masses,
        },
        "extent": {
            "x": round(origin_x, 2),
            "y": round(max_y or max((_mass_width(m) for m in masses), default=0.0), 2),
            "z": round(max_z, 2),
        },
        "all_checks_passed": not failed,
        "failed_checks": failed,
        "masses": [
            {
                "id": m.id,
                "name": m.name,
                "departments": m.departments,
                "stories": len(m.floors),
                "length_ft": round(m.floors[0].length_ft, 1) if m.floors else 0,
                "width_ft": round(m.floors[0].width_ft, 1) if m.floors else 0,
                "target_gsf": round(m.target_gsf, 0),
                "actual_gsf": round(m.actual_gsf, 0),
                "fit_pass": m.fit_pass,
            }
            for m in masses
        ],
    }


def _mass_width(mass: SolvedMass) -> float:
    if not mass.floors:
        return 0.0
    return max(f.width_ft for f in mass.floors)


_MASS_ENVELOPE_PALETTE = (
    "#6B7F9C",
    "#9D755D",
    "#7D9B76",
    "#B86B6B",
    "#8E7CC3",
    "#5E9A9A",
    "#C9A66B",
    "#A67C52",
)


def scheme_envelope_mesh(
    scheme: dict[str, Any],
    *,
    story_height_ft: float = 14.0,
    rank: int | None = None,
) -> dict[str, Any]:
    """
    Lightweight mass envelopes for sample-pool thumbnails.

    Uses each scheme mass's length × width × stories — no department split.
    Same axis convention as preview_mesh (X length, Y width, Z up).
    """
    height = float(story_height_ft) if story_height_ft > 0 else 14.0
    boxes: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    origin_x = 0.0
    max_y = 0.0
    max_z = 0.0
    mass_summaries: list[dict[str, Any]] = []

    masses = list(scheme.get("masses") or [])
    for i, m in enumerate(masses):
        length = float(m.get("length_ft") or 0.0)
        width = float(m.get("width_ft") or 0.0)
        stories = max(1, int(m.get("stories") or m.get("story_count") or 1))
        mass_id = str(m.get("mass_id") or m.get("id") or f"mass_{i}")
        mass_name = str(m.get("mass_name") or m.get("name") or mass_id)
        if length <= 0 or width <= 0:
            continue
        dz = height * stories
        color = _MASS_ENVELOPE_PALETTE[i % len(_MASS_ENVELOPE_PALETTE)]
        boxes.append(
            {
                "id": f"{mass_id}_envelope",
                "mass": mass_name,
                "mass_id": mass_id,
                "department": mass_name,
                "level": 0,
                "x": round(origin_x + length / 2.0, 3),
                "y": round(width / 2.0, 3),
                "z": round(dz / 2.0, 3),
                "dx": round(length, 3),
                "dy": round(width, 3),
                "dz": round(dz, 3),
                "color": color,
                "double_height": False,
                "gsf": round(length * width * stories, 1),
            }
        )
        labels.append(
            {
                "text": mass_name,
                "x": round(origin_x + length / 2.0, 3),
                "y": round(width / 2.0, 3),
                "z": round(dz + 4.0, 3),
            }
        )
        mass_summaries.append(
            {
                "id": mass_id,
                "name": mass_name,
                "departments": [],
                "stories": stories,
                "length_ft": round(length, 1),
                "width_ft": round(width, 1),
                "target_gsf": round(length * width * stories, 0),
                "actual_gsf": round(length * width * stories, 0),
                "fit_pass": True,
            }
        )
        max_y = max(max_y, width)
        max_z = max(max_z, dz)
        origin_x += length
        if i < len(masses) - 1:
            origin_x += MASS_GAP_FT

    return {
        "study_id": scheme.get("study_id") or "",
        "rank": rank,
        "envelope": True,
        "story_height_ft": height,
        "units": "ft",
        "boxes": boxes,
        "labels": labels,
        "legend": {
            "departments": [
                {"name": s["name"], "color": boxes[i]["color"]}
                for i, s in enumerate(mass_summaries)
                if i < len(boxes)
            ],
            "masses": [
                {
                    "id": s["id"],
                    "name": s["name"],
                    "departments": [],
                    "colors": [boxes[i]["color"]] if i < len(boxes) else [],
                    "stories": s["stories"],
                    "double_height": False,
                }
                for i, s in enumerate(mass_summaries)
            ],
        },
        "extent": {
            "x": round(origin_x, 2),
            "y": round(max_y, 2),
            "z": round(max_z, 2),
        },
        "all_checks_passed": bool(scheme.get("verified", True)),
        "failed_checks": list(scheme.get("failed_checks") or []),
        "masses": mass_summaries,
        "total_length_ft": scheme.get("total_length_ft"),
    }
