from __future__ import annotations

import json
from typing import Any

from .session import StudySession


def get_department_summary(session: StudySession) -> dict[str, Any]:
    """Return NFA and target GSF per department from the engine (not LLM math)."""
    departments = []
    for d in sorted(session.program.departments, key=lambda x: -x.nfa_sf):
        departments.append(
            {
                "name": d.name,
                "nfa_sf": round(d.nfa_sf, 1),
                "target_gsf": round(d.target_gsf, 1),
                "room_count": d.room_count,
            }
        )
    return {
        "units": "feet",
        "area_unit": "sf",
        "grossing": {
            "area_adjustment": session.program.grossing.area_adjustment,
            "grossing_factor": session.program.grossing.grossing_factor,
            "combined_multiplier": session.program.grossing.combined_multiplier,
        },
        "totals": {
            "nfa_sf": round(session.program.totals.get("nfa_sf", 0), 1),
            "target_gsf": round(session.program.totals.get("target_gsf", 0), 1),
        },
        "departments": departments,
    }


def get_grouping_summary(session: StudySession) -> dict[str, Any]:
    """Return current mass groupings and per-mass GSF from engine."""
    dept_map = {d.name: d for d in session.program.departments}
    masses = []
    for mass in session.masses:
        nfa = sum(dept_map[dep].nfa_sf for dep in mass.departments if dep in dept_map)
        target_gsf = sum(
            dept_map[dep].target_gsf for dep in mass.departments if dep in dept_map
        )
        avg_floor_plate = target_gsf / mass.story_count if mass.story_count > 0 else 0
        masses.append(
            {
                "id": mass.id,
                "name": mass.name,
                "departments": mass.departments,
                "story_count": mass.story_count,
                "nfa_sf": round(nfa, 1),
                "target_gsf": round(target_gsf, 1),
                "avg_floor_plate_sf": round(avg_floor_plate, 1),
                "notes": mass.notes,
            }
        )

    assigned = {dep for m in session.masses for dep in m.departments}
    unassigned = [d for d in session.department_names() if d not in assigned]

    return {
        "masses": masses,
        "unassigned_departments": unassigned,
        "constraints": session.constraints,
        "adjacency_notes": session.adjacency_notes,
        "double_height_rooms": session.double_height_rooms,
    }


def set_grouping(
    session: StudySession,
    masses: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Set conceptual mass groupings.

    Each mass: { "id": "academic", "name": "Academic Wing", "departments": ["CORE ACADEMIC", ...] }
    """
    from .study_state import MassGrouping

    known = set(session.department_names())
    new_masses: list[MassGrouping] = []
    errors: list[str] = []

    for i, m in enumerate(masses):
        mass_id = str(m.get("id", f"mass_{i + 1}"))
        name = str(m.get("name", mass_id))
        departments = [str(d) for d in m.get("departments", [])]
        story_count = int(m.get("story_count", 1))
        notes = str(m.get("notes", ""))

        for dep in departments:
            if dep not in known:
                errors.append(f"Unknown department: {dep}")

        new_masses.append(
            MassGrouping(
                id=mass_id,
                name=name,
                departments=departments,
                story_count=story_count,
                notes=notes,
            )
        )

    if errors:
        return {"ok": False, "errors": errors}

    session.masses = new_masses
    session.save()
    return {"ok": True, "grouping": get_grouping_summary(session)}


def set_story_count(
    session: StudySession,
    mass_id: str,
    story_count: int,
) -> dict[str, Any]:
    if story_count < 1:
        return {"ok": False, "error": "story_count must be >= 1"}

    for mass in session.masses:
        if mass.id == mass_id:
            mass.story_count = story_count
            session.save()
            return {"ok": True, "grouping": get_grouping_summary(session)}

    return {"ok": False, "error": f"Mass not found: {mass_id}"}


def set_constraint(
    session: StudySession,
    key: str,
    value: Any,
) -> dict[str, Any]:
    session.constraints[key] = value
    session.save()
    return {"ok": True, "constraints": session.constraints}


def add_adjacency_note(session: StudySession, note: str) -> dict[str, Any]:
    session.adjacency_notes.append(note)
    session.save()
    return {"ok": True, "adjacency_notes": session.adjacency_notes}


def mark_double_height(session: StudySession, room_name: str) -> dict[str, Any]:
    if room_name not in session.double_height_rooms:
        session.double_height_rooms.append(room_name)
        session.save()
    return {"ok": True, "double_height_rooms": session.double_height_rooms}


def pin_department_to_floor(
    session: StudySession,
    department: str,
    level: int,
) -> dict[str, Any]:
    """Force a department onto a specific level (0 = ground) when allocating."""
    known = session.department_names()
    if department not in known:
        matches = [d for d in known if department.lower() in d.lower()]
        if len(matches) != 1:
            return {
                "ok": False,
                "error": f"Unknown department: {department}",
                "known_departments": known,
            }
        department = matches[0]

    if level < 0:
        return {"ok": False, "error": "level must be >= 0 (0 = ground floor)"}

    mass = next((m for m in session.masses if department in m.departments), None)
    if mass and level >= mass.story_count:
        return {
            "ok": False,
            "error": (
                f"{mass.name} has {mass.story_count} stories, so level {level} "
                f"does not exist (top level is {mass.story_count - 1})"
            ),
        }

    session.floor_pins[department] = int(level)
    session.save()
    return {
        "ok": True,
        "floor_pins": session.floor_pins,
        "note": "Call solve_dimensions to re-allocate with this pin applied.",
    }


def unpin_department(session: StudySession, department: str) -> dict[str, Any]:
    removed = session.floor_pins.pop(department, None)
    if removed is None:
        matches = [d for d in session.floor_pins if department.lower() in d.lower()]
        if len(matches) == 1:
            session.floor_pins.pop(matches[0])
        else:
            return {"ok": False, "error": f"No pin for: {department}"}
    session.save()
    return {"ok": True, "floor_pins": session.floor_pins}


def pair_masses(
    session: StudySession,
    mass_ids: list[str],
    total_length_ft: float,
    pairing_id: str | None = None,
) -> dict[str, Any]:
    """
    Constrain two or more adjacent masses to a shared width inside a total length.

    Engine solves W = sum(floor plates) / total_length, then L_i = plate_i / W.
    """
    from .config import load_project_config
    from .solver import _mass_plate_area, solve_paired_masses
    from .study_state import MassPairing

    known = {m.id: m for m in session.masses}
    missing = [mid for mid in mass_ids if mid not in known]
    if missing:
        return {
            "ok": False,
            "error": f"Unknown mass id(s): {', '.join(missing)}",
            "known_mass_ids": list(known),
        }
    if len(mass_ids) < 2:
        return {"ok": False, "error": "Pairing needs at least two mass ids"}
    if total_length_ft <= 0:
        return {"ok": False, "error": "total_length_ft must be > 0"}

    pid = pairing_id or "+".join(mass_ids)
    session.pairings = [p for p in session.pairings if p.id != pid]
    session.pairings.append(
        MassPairing(
            id=pid,
            mass_ids=list(mass_ids),
            total_length_ft=float(total_length_ft),
        )
    )
    session.save()

    config = load_project_config(session.config_path or None)
    plates = [_mass_plate_area(session, known[mid], config) for mid in mass_ids]
    width, lengths = solve_paired_masses(plates, float(total_length_ft))

    return {
        "ok": True,
        "pairing_id": pid,
        "shared_width_ft": round(width, 1),
        "lengths_ft": {
            mid: round(length, 1) for mid, length in zip(mass_ids, lengths)
        },
        "floor_plates_sf": {
            mid: round(plate, 1) for mid, plate in zip(mass_ids, plates)
        },
        "note": "Call solve_dimensions to produce the full validated study.",
    }


def clear_pairings(session: StudySession) -> dict[str, Any]:
    session.pairings = []
    session.save()
    return {"ok": True, "pairings": []}


def resize_mass(
    session: StudySession,
    mass_id: str,
    width_ft: float | None = None,
    story_count: int | None = None,
) -> dict[str, Any]:
    """Change a fixed dimension or story count, then re-solve and re-validate."""
    known = {m.id for m in session.masses}
    if mass_id not in known:
        return {
            "ok": False,
            "error": f"Unknown mass id: {mass_id}",
            "known_mass_ids": sorted(known),
        }
    if width_ft is None and story_count is None:
        return {"ok": False, "error": "Provide width_ft and/or story_count"}

    changes: list[str] = []
    if width_ft is not None:
        if width_ft <= 0:
            return {"ok": False, "error": "width_ft must be > 0"}
        session.constraints[f"{mass_id}_width_ft"] = float(width_ft)
        changes.append(f"width -> {width_ft:g} ft")
    if story_count is not None:
        if story_count < 1:
            return {"ok": False, "error": "story_count must be >= 1"}
        for mass in session.masses:
            if mass.id == mass_id:
                mass.story_count = int(story_count)
        changes.append(f"stories -> {story_count}")

    session.save()
    solved = solve_dimensions(session)
    solved["changes"] = changes
    return solved


def solve_dimensions(session: StudySession) -> dict[str, Any]:
    """Solve footprints and validate GSF / anchor rooms. Engine only — no LLM math."""
    from .report import format_massing_report
    from .solver import solve_massing_study

    result = solve_massing_study(session)
    session.last_massing = result.to_dict()
    session.save()

    report = format_massing_report(result)
    report_path = session.study_dir / "massing_report.txt"
    report_path.write_text(report, encoding="utf-8")

    failed = [v.message for v in result.validation if not v.passed]

    # Failures first, and no raw per-mass dump: the LLM should quote the report
    # and the failure list rather than picking numbers out of nested JSON.
    return {
        "ok": True,
        "all_checks_passed": not failed,
        "failed_checks": failed,
        "instruction": (
            "Report every failed check to the user verbatim. Do not describe a "
            "failing mass as fitting."
            if failed
            else "All checks passed."
        ),
        "resize_suggestions": [s.to_dict() for s in result.resize_suggestions],
        "masses": [
            {
                "id": m.id,
                "name": m.name,
                "stories": len(m.floors),
                "width_ft": round(m.fixed_dim_ft, 1),
                "length_ft": round(m.floors[0].length_ft, 1) if m.floors else 0,
                "target_gsf": round(m.target_gsf),
                "actual_gsf": round(m.actual_gsf),
                "gsf_fit_pass": m.fit_pass,
                "pairing_id": m.pairing_id,
                "floors": [
                    {
                        "level": f.level,
                        "usable_sf": round(f.usable_area_sf),
                        "utilization_pct": round(f.utilization * 100),
                        "programs": {
                            a.department: round(a.gsf) for a in f.allocations
                        },
                    }
                    for f in m.floors
                ],
            }
            for m in result.masses
        ],
        "report_path": str(report_path),
        "summary": report,
    }


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_department_summary",
            "description": (
                "Get NFA and target GSF for every department. "
                "Always use this for area numbers — never calculate yourself."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_grouping_summary",
            "description": "Get current mass groupings, story counts, and per-mass GSF.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_grouping",
            "description": (
                "Assign departments to conceptual masses. "
                "Each department should appear in exactly one mass."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "masses": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "name": {"type": "string"},
                                "departments": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "story_count": {"type": "integer"},
                                "notes": {"type": "string"},
                            },
                            "required": ["id", "name", "departments"],
                        },
                    }
                },
                "required": ["masses"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_story_count",
            "description": "Set number of stories for a mass by mass id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mass_id": {"type": "string"},
                    "story_count": {"type": "integer"},
                },
                "required": ["mass_id", "story_count"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_constraint",
            "description": (
                "Set a planning constraint, e.g. max_width_ft, max_length_ft, "
                "academic_width_ft, max_total_length_ft."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {},
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_adjacency_note",
            "description": "Record an adjacency preference from the user.",
            "parameters": {
                "type": "object",
                "properties": {"note": {"type": "string"}},
                "required": ["note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_double_height",
            "description": "Mark a room as double-height (void on floor above).",
            "parameters": {
                "type": "object",
                "properties": {"room_name": {"type": "string"}},
                "required": ["room_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "solve_dimensions",
            "description": (
                "Solve footprint W×L for each mass from target GSF and story count. "
                "Validates GSF fit (±tolerance) and anchor room clear dims. "
                "Call after groupings and constraints are set. Never invent dimensions."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pair_masses",
            "description": (
                "Constrain two or more adjacent masses to share a width and fit a "
                "combined length, e.g. 'fit the academic and support wings in 280 ft'. "
                "The engine solves the shared width; never compute it yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mass_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "total_length_ft": {"type": "number"},
                    "pairing_id": {"type": "string"},
                },
                "required": ["mass_ids", "total_length_ft"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_pairings",
            "description": "Remove all shared-width pairings so masses solve independently.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pin_department_to_floor",
            "description": (
                "Force a department onto a specific level when allocating program "
                "to floors (0 = ground). Use when the user says something like "
                "'put the media center on the ground floor'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "department": {"type": "string"},
                    "level": {"type": "integer"},
                },
                "required": ["department", "level"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "unpin_department",
            "description": "Remove a department's floor pin so it allocates freely.",
            "parameters": {
                "type": "object",
                "properties": {"department": {"type": "string"}},
                "required": ["department"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resize_mass",
            "description": (
                "Change a mass width and/or story count, then re-solve and re-validate "
                "in one step. Use this to apply resize suggestions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mass_id": {"type": "string"},
                    "width_ft": {"type": "number"},
                    "story_count": {"type": "integer"},
                },
                "required": ["mass_id"],
            },
        },
    },
]


def execute_tool(session: StudySession, name: str, arguments: dict[str, Any]) -> str:
    if name == "get_department_summary":
        result = get_department_summary(session)
    elif name == "get_grouping_summary":
        result = get_grouping_summary(session)
    elif name == "set_grouping":
        result = set_grouping(session, arguments.get("masses", []))
    elif name == "set_story_count":
        result = set_story_count(
            session, arguments["mass_id"], int(arguments["story_count"])
        )
    elif name == "set_constraint":
        result = set_constraint(session, arguments["key"], arguments["value"])
    elif name == "add_adjacency_note":
        result = add_adjacency_note(session, arguments["note"])
    elif name == "mark_double_height":
        result = mark_double_height(session, arguments["room_name"])
    elif name == "solve_dimensions":
        result = solve_dimensions(session)
    elif name == "pair_masses":
        result = pair_masses(
            session,
            [str(m) for m in arguments.get("mass_ids", [])],
            float(arguments["total_length_ft"]),
            arguments.get("pairing_id"),
        )
    elif name == "clear_pairings":
        result = clear_pairings(session)
    elif name == "pin_department_to_floor":
        result = pin_department_to_floor(
            session, str(arguments["department"]), int(arguments["level"])
        )
    elif name == "unpin_department":
        result = unpin_department(session, str(arguments["department"]))
    elif name == "resize_mass":
        width = arguments.get("width_ft")
        stories = arguments.get("story_count")
        result = resize_mass(
            session,
            str(arguments["mass_id"]),
            float(width) if width is not None else None,
            int(stories) if stories is not None else None,
        )
    else:
        result = {"ok": False, "error": f"Unknown tool: {name}"}

    return json.dumps(result, indent=2)
