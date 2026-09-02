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
    else:
        result = {"ok": False, "error": f"Unknown tool: {name}"}

    return json.dumps(result, indent=2)
