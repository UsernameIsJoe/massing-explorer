from __future__ import annotations

import json
import re
from typing import Any

from .session import StudySession


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")


def resolve_mass_id(session: StudySession, ref: str) -> tuple[str, dict[str, Any] | None]:
    """
    Map a loose mass reference onto a real mass id.

    The LLM routinely derives an id from the display name - calling a mass
    "academic_bar" when its id is "academic" - so exact-id matching alone
    rejected calls that were otherwise correct. Mirrors the tolerant matching
    `pin_department_to_floor` already does for department names. An ambiguous
    reference is an error rather than a guess.

    Returns (mass_id, error). Exactly one of the two is meaningful.
    """
    ids = [m.id for m in session.masses]
    if not ids:
        return "", {"ok": False, "error": "No masses defined. Set groupings first."}

    ref = (ref or "").strip()
    if not ref:
        return "", {"ok": False, "error": "mass_id is required", "known_mass_ids": ids}
    if ref in ids:
        return ref, None

    target = _slug(ref)
    for mass in session.masses:
        if target in (_slug(mass.id), _slug(mass.name)):
            return mass.id, None

    matches = sorted(
        {
            mass.id
            for mass in session.masses
            for candidate in (_slug(mass.id), _slug(mass.name))
            if candidate and (target in candidate or candidate in target)
        }
    )
    if len(matches) == 1:
        return matches[0], None

    return "", {
        "ok": False,
        "error": (
            f"Ambiguous mass reference '{ref}' - could be {matches}"
            if matches
            else f"Unknown mass '{ref}'. Known: {ids}"
        ),
        "known_mass_ids": ids,
    }


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

    mass_id, error = resolve_mass_id(session, mass_id)
    if error:
        return error

    for mass in session.masses:
        if mass.id == mass_id:
            mass.story_count = story_count
            session.save()
            return {"ok": True, "grouping": get_grouping_summary(session)}

    return {"ok": False, "error": f"Mass not found: {mass_id}"}


GLOBAL_WIDTH_KEYS = ("fixed_width_ft", "max_building_width_ft", "academic_width_ft")


def set_constraint(
    session: StudySession,
    key: str,
    value: Any,
) -> dict[str, Any]:
    # A per-mass width key is "<mass_id>_width_ft". Accepting one that matches
    # no mass stored a constraint that nothing ever read, so the study kept its
    # old width while the caller believed the new one had been applied.
    if key.endswith("_width_ft") and key not in GLOBAL_WIDTH_KEYS:
        valid = [f"{m.id}_width_ft" for m in session.masses]
        if key not in valid:
            return {
                "ok": False,
                "error": (
                    f"'{key}' does not match any mass, so it would be ignored. "
                    f"Mass ids are: {[m.id for m in session.masses]}."
                ),
                "valid_width_keys": valid + list(GLOBAL_WIDTH_KEYS),
            }

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


MIN_TAPER = 0.2
MAX_TAPER = 1.0


def set_floor_steps(
    session: StudySession,
    mass_id: str,
    weights: list[float],
) -> dict[str, Any]:
    """
    Step a mass by explicit per-level plate weights, e.g. [1, 0.8, 0.5].

    Weights are relative, not areas: the engine scales them so the plates still
    sum to the mass target GSF. Width stays constant (shared edge), plates stack
    biggest→smallest, and at most two distinct footprints are kept.
    """
    from .solver import canonicalize_step_weights

    mass_id, error = resolve_mass_id(session, mass_id)
    if error:
        return error
    if not weights:
        return {"ok": False, "error": "weights must not be empty"}
    try:
        values = [float(w) for w in weights]
    except (TypeError, ValueError):
        return {"ok": False, "error": "weights must all be numbers"}
    if any(w <= 0 for w in values):
        return {
            "ok": False,
            "error": "every weight must be > 0; a zero-area floor is not a floor",
        }

    mass = next(m for m in session.masses if m.id == mass_id)
    note = None
    if len(values) != mass.story_count:
        note = (
            f"{mass.name} has {mass.story_count} stories but {len(values)} weights "
            f"were given; the list is padded or trimmed to match. Call "
            f"set_story_count first if the height should change."
        )

    canonical = canonicalize_step_weights(values)
    if canonical != values:
        extra = (
            f"Normalized to biggest→smallest with ≤2 plate types: {canonical}."
        )
        note = f"{note} {extra}" if note else extra
    values = canonical

    session.floor_steps[mass_id] = values
    session.floor_tapers.pop(mass_id, None)
    session.save()
    return {
        "ok": True,
        "mass_id": mass_id,
        "weights": values,
        "note": note,
        "next_step": "Call solve_dimensions to see the stepped plates.",
    }


def set_floor_taper(
    session: StudySession,
    mass_id: str,
    ratio: float,
) -> dict[str, Any]:
    """
    Step a mass by a constant setback ratio: each level is `ratio` times the one
    below. Unlike explicit weights this is independent of story count, so a
    scheme search can still vary the height.
    """
    mass_id, error = resolve_mass_id(session, mass_id)
    if error:
        return error
    try:
        value = float(ratio)
    except (TypeError, ValueError):
        return {"ok": False, "error": "ratio must be a number"}
    if not (MIN_TAPER <= value <= MAX_TAPER):
        return {
            "ok": False,
            "error": (
                f"ratio must be between {MIN_TAPER} and {MAX_TAPER} "
                f"(1.0 = no setback). Got {value:g}."
            ),
        }

    session.floor_tapers[mass_id] = value
    session.floor_steps.pop(mass_id, None)
    session.save()
    return {
        "ok": True,
        "mass_id": mass_id,
        "ratio": value,
        "next_step": "Call solve_dimensions to see the stepped plates.",
    }


def clear_floor_steps(session: StudySession, mass_id: str = "") -> dict[str, Any]:
    """Return one mass, or all masses, to equal floor plates."""
    if mass_id:
        mass_id, error = resolve_mass_id(session, mass_id)
        if error:
            return error
        session.floor_steps.pop(mass_id, None)
        session.floor_tapers.pop(mass_id, None)
    else:
        session.floor_steps.clear()
        session.floor_tapers.clear()
    session.save()
    return {
        "ok": True,
        "stepped_masses": sorted({*session.floor_steps, *session.floor_tapers}),
    }


def search_site_schemes(
    session: StudySession,
    max_total_length_ft: float | None = None,
    max_length_ft: float | None = None,
    max_width_ft: float | None = None,
    max_stories: int | None = None,
    preference: str = "balanced",
    top_n: int = 3,
) -> dict[str, Any]:
    """
    Search story counts and widths that fit a site envelope.

    Unlike solve_dimensions (which checks a scheme you already chose), this
    finds schemes. Every option returned has been verified through the solver.
    """
    from .search import PREFERENCE_WEIGHTS, SiteEnvelope, search_schemes

    def _from_session(*keys: str):
        for key in keys:
            value = session.constraints.get(key)
            if value is not None:
                return float(value)
        return None

    if max_total_length_ft is None:
        max_total_length_ft = _from_session("max_total_length_ft")
    if max_length_ft is None:
        max_length_ft = _from_session("max_building_length_ft", "max_length_ft")
    if max_width_ft is None:
        max_width_ft = _from_session("max_building_width_ft", "max_width_ft")
    if max_stories is None:
        stored = session.constraints.get("max_stories")
        max_stories = int(stored) if stored is not None else 4

    if preference not in PREFERENCE_WEIGHTS:
        return {
            "ok": False,
            "error": f"Unknown preference: {preference}",
            "valid_preferences": sorted(PREFERENCE_WEIGHTS),
        }
    if not session.masses:
        return {"ok": False, "error": "Set groupings before searching for schemes."}

    envelope = SiteEnvelope(
        max_building_length_ft=max_length_ft,
        max_building_width_ft=max_width_ft,
        max_total_length_ft=max_total_length_ft,
        max_stories=max(1, int(max_stories)),
    )
    candidates, notes = search_schemes(
        session,
        envelope,
        preference=preference,
        top_n=max(1, int(top_n)),
        config_path=session.config_path or None,
    )

    session.last_search = [c.to_dict() for c in candidates]
    session.save()

    # Say plainly which limits were enforced. Without this the model happily
    # reports a 400 ft scheme as fitting a 300 ft site it never passed in.
    limits = {
        "max_total_length_ft": max_total_length_ft,
        "max_length_ft": max_length_ft,
        "max_width_ft": max_width_ft,
    }
    applied = {k: v for k, v in limits.items() if v is not None}
    missing = sorted(k for k, v in limits.items() if v is None)
    caution = None
    if missing:
        caution = (
            f"These limits were NOT applied and NOT checked: {missing}. Do not "
            f"tell the user a scheme respects any of them. If the user stated "
            f"one, call search_site_schemes again and pass it."
        )

    return {
        "ok": True,
        "envelope": envelope.to_dict(),
        "limits_applied": applied,
        "limits_not_checked": missing,
        "caution": caution,
        "preference": preference,
        "found": len(candidates),
        "notes": notes,
        "schemes": [
            {
                "index": i,
                "summary": c.summary(),
                "total_length_ft": round(c.total_length_ft, 1),
                "verified": c.verified,
                "masses": [
                    {
                        "mass_id": o.mass_id,
                        "name": o.mass_name,
                        "stories": o.stories,
                        "width_ft": round(o.width_ft, 1),
                        "length_ft": round(o.length_ft, 1),
                    }
                    for o in c.options
                ],
            }
            for i, c in enumerate(candidates)
        ],
        "instruction": (
            "Present these options to the user and ask which to apply, then call "
            "apply_scheme with its index."
            if candidates
            else "No scheme fits. Report the notes and suggest relaxing a limit."
        ),
    }


def apply_scheme(session: StudySession, index: int = 0) -> dict[str, Any]:
    """Apply one scheme from the last search, then re-solve and re-validate."""
    from .search import apply_scheme_from_dict

    if not session.last_search:
        return {"ok": False, "error": "No search results. Call search_site_schemes first."}
    if index < 0 or index >= len(session.last_search):
        return {
            "ok": False,
            "error": f"index must be 0..{len(session.last_search) - 1}",
        }

    applied = apply_scheme_from_dict(session, session.last_search[index])
    if not applied["ok"]:
        return {"ok": False, "error": "Scheme did not match any current mass."}

    solved = solve_dimensions(session)
    solved["changes"] = applied["applied"]
    return solved


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

    if level < -1:
        return {"ok": False, "error": "level must be >= -1 (-1 = top floor, 0 = ground)"}

    mass = next((m for m in session.masses if department in m.departments), None)
    if mass and level >= 0 and level >= mass.story_count:
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
        "note": (
            "Top-floor pin (-1) resolves to the mass's uppermost level at allocate time."
            if int(level) < 0
            else "Call solve_dimensions to re-allocate with this pin applied."
        ),
    }


def resolved_floor_pins(session: StudySession) -> dict[str, int]:
    """Absolute levels for allocate/solve. Relational 'top' (-1) follows mass height."""
    kinds = (session.constraints or {}).get("floor_pin_kinds") or {}
    out: dict[str, int] = {}
    for dept, raw in (session.floor_pins or {}).items():
        try:
            lvl = int(raw)
        except (TypeError, ValueError):
            continue
        if lvl < 0 or str(kinds.get(dept) or "") == "top":
            mass = next((m for m in session.masses if dept in m.departments), None)
            stories = int(getattr(mass, "story_count", 1) or 1) if mass else 1
            out[dept] = max(0, stories - 1)
        else:
            out[dept] = lvl
    return out


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
    length_is_cap: bool = False,
) -> dict[str, Any]:
    """
    Place two or more masses together along a frontage.

    When length_is_cap is false, they share W = sum(plates) / total_length.
    When it is true, total_length is only a maximum. Width comes from how the
    bar should work, and the length must stay under the cap.
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
            length_is_cap=bool(length_is_cap),
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


def _pairing_containing(session: StudySession, mass_id: str):
    for pairing in session.pairings:
        if mass_id in pairing.mass_ids:
            return pairing
    return None


def resize_mass(
    session: StudySession,
    mass_id: str,
    width_ft: float | None = None,
    story_count: int | None = None,
) -> dict[str, Any]:
    """Change a fixed dimension or story count, then re-solve and re-validate.

    Paired masses share a width derived from the pairing length. An independent
    width on one member is ignored by the solver, so this writes the change
    onto the pairing instead: new_length = sum(plates) / requested_width.
    Story changes still apply to the named mass only.
    """
    mass_id, error = resolve_mass_id(session, mass_id)
    if error:
        return error
    if width_ft is None and story_count is None:
        return {"ok": False, "error": "Provide width_ft and/or story_count"}

    changes: list[str] = []
    pairing = _pairing_containing(session, mass_id)

    if story_count is not None:
        if story_count < 1:
            return {"ok": False, "error": "story_count must be >= 1"}
        for mass in session.masses:
            if mass.id == mass_id:
                mass.story_count = int(story_count)
        changes.append(f"stories -> {story_count}")

    if width_ft is not None:
        if width_ft <= 0:
            return {"ok": False, "error": "width_ft must be > 0"}
        if pairing is not None:
            from .config import load_project_config
            from .solver import _mass_plate_area

            config = load_project_config(session.config_path or None)
            by_id = {m.id: m for m in session.masses}
            members = [by_id[mid] for mid in pairing.mass_ids if mid in by_id]
            plates = [_mass_plate_area(session, m, config) for m in members]
            new_len = sum(plates) / float(width_ft)
            pairing.total_length_ft = new_len
            for mid in pairing.mass_ids:
                session.constraints.pop(f"{mid}_width_ft", None)
            changes.append(
                f"pairing {pairing.id} frontage -> {new_len:.1f} ft "
                f"(shared width {width_ft:g} ft)"
            )
        else:
            session.constraints[f"{mass_id}_width_ft"] = float(width_ft)
            changes.append(f"width -> {width_ft:g} ft")

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

    # "All checks passed" is misleading when the limit the user cares about was
    # never recorded, so spell out which site limits are actually in force.
    site_keys = (
        "max_total_length_ft",
        "max_building_length_ft",
        "max_building_width_ft",
    )
    active = {k: session.constraints[k] for k in site_keys if session.constraints.get(k)}
    inactive = [k for k in site_keys if k not in active]
    caution = None
    if inactive and not failed:
        caution = (
            f"No limit is set for {inactive}, so nothing was checked against "
            f"them. If the user stated one of these, call set_constraint with it "
            f"and solve again before saying the scheme fits the site."
        )

    # Failures first, and no raw per-mass dump: the LLM should quote the report
    # and the failure list rather than picking numbers out of nested JSON.
    return {
        "ok": True,
        "all_checks_passed": not failed,
        "failed_checks": failed,
        "site_limits_active": active,
        "site_limits_not_set": inactive,
        "caution": caution,
        "total_ground_length_ft": round(
            sum(m.floors[0].length_ft for m in result.masses if m.floors), 1
        ),
        "instruction": (
            (
                "A length limit on this study is a cap, not a target. "
                "Do not tell the user the bars are that long on purpose, and "
                "do not propose setting the length to the cap. Report every "
                "failed check verbatim."
                if failed and session.constraints.get("length_limit_is_cap")
                else (
                    "Report every failed check to the user verbatim. Do not "
                    "describe a failing mass as fitting."
                    if failed
                    else "All checks passed."
                )
            )
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
            "name": "apply_brief",
            "description": (
                "Parse a user brief (stay-together, site length/width, max stories) "
                "and let the ENGINE group departments and search for a fitting "
                "scheme. Pass the user's message verbatim. Do not invent groupings "
                "with set_grouping when this tool can run."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The user's message, copied verbatim",
                    }
                },
                "required": ["text"],
            },
        },
    },
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
                "Override mass groupings. Prefer apply_brief — the engine groups "
                "from stay-together notes and operational families. Only use this "
                "when the user explicitly asks to regroup by hand."
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
            "name": "set_floor_steps",
            "description": (
                "Make a mass stepped / terraced with explicit per-level plate "
                "weights, e.g. [1, 0.8, 0.5] for a base with two setbacks. "
                "Weights are relative, not areas - the engine rescales them so "
                "the plates still total the mass target GSF. Use this when the "
                "user describes specific levels ('top floor half the base')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mass_id": {"type": "string"},
                    "weights": {"type": "array", "items": {"type": "number"}},
                },
                "required": ["mass_id", "weights"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_floor_taper",
            "description": (
                "Make a mass stepped with a constant setback ratio - each level "
                "is `ratio` times the plate below (0.8 = each floor 80% of the "
                "one under it, 1.0 = no setback). Prefer this over "
                "set_floor_steps when the user describes a general shape "
                "('step it back as it rises', 'wedding cake', 'ziggurat'), "
                "because it survives a change of story count."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mass_id": {"type": "string"},
                    "ratio": {"type": "number"},
                },
                "required": ["mass_id", "ratio"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_floor_steps",
            "description": (
                "Return a mass to equal floor plates. Omit mass_id to un-step "
                "every mass."
            ),
            "parameters": {
                "type": "object",
                "properties": {"mass_id": {"type": "string"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_site_schemes",
            "description": (
                "Find story counts and widths that FIT a site envelope. Use this "
                "when the user gives site limits and asks what would work, rather "
                "than stating widths themselves. solve_dimensions only checks a "
                "scheme; this one searches for schemes. Every option returned is "
                "already verified by the solver. Preference is one of: balanced, "
                "low_rise, compact."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "max_total_length_ft": {"type": "number"},
                    "max_length_ft": {"type": "number"},
                    "max_width_ft": {"type": "number"},
                    "max_stories": {"type": "integer"},
                    "preference": {
                        "type": "string",
                        "enum": ["balanced", "low_rise", "compact"],
                    },
                    "top_n": {"type": "integer"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_scheme",
            "description": (
                "Apply one scheme from the last search_site_schemes result by its "
                "index, then re-solve and re-validate."
            ),
            "parameters": {
                "type": "object",
                "properties": {"index": {"type": "integer"}},
                "required": ["index"],
            },
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
                "Change a mass story count and/or width, then re-solve. "
                "If the mass is paired, width is not independent: the engine "
                "updates the pairing length so all members still share one width."
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
    if name == "apply_brief":
        from .brief import apply_brief as run_brief

        result = run_brief(
            session, str(arguments.get("text") or arguments.get("brief") or "")
        )
    elif name == "get_department_summary":
        result = get_department_summary(session)
    elif name == "get_grouping_summary":
        result = get_grouping_summary(session)
    elif name == "set_grouping":
        if session.brief_locked:
            result = {
                "ok": False,
                "error": (
                    "The user brief already set the wings. Do not call "
                    "set_grouping. Report the masses already on the study."
                ),
                "grouping": get_grouping_summary(session),
            }
        else:
            result = set_grouping(session, arguments.get("masses", []))
    elif name == "set_story_count":
        lock = session.constraints.get("story_lock") or {}
        mid, err = resolve_mass_id(session, str(arguments.get("mass_id") or ""))
        if err:
            result = err
        elif session.brief_locked and mid in lock:
            result = {
                "ok": False,
                "error": (
                    f"{mid} story count is set by the brief "
                    f"({lock[mid]}). Do not change it."
                ),
            }
        else:
            result = set_story_count(
                session, arguments["mass_id"], int(arguments["story_count"])
            )
    elif name == "set_constraint":
        key = str(arguments.get("key") or "")
        blocked = session.brief_locked and (
            key in session.constraints
            or key.endswith("_width_ft")
            or key in {"max_stories", "preferred_width_ft", "length_over_width"}
        )
        if blocked:
            result = {
                "ok": False,
                "error": (
                    f"{key} is already set by the brief. Do not override it."
                ),
            }
        else:
            result = set_constraint(session, arguments["key"], arguments["value"])
    elif name == "add_adjacency_note":
        result = add_adjacency_note(session, arguments["note"])
    elif name == "mark_double_height":
        result = mark_double_height(session, arguments["room_name"])
    elif name == "solve_dimensions":
        # solve_dimensions reads widths off the session. Silently dropping
        # arguments let the model believe it had applied widths it never set,
        # and then report those invented numbers as solved.
        if arguments:
            result = {
                "ok": False,
                "error": (
                    "solve_dimensions takes no arguments; it solves whatever is "
                    f"already on the study. Ignored: {sorted(arguments)}."
                ),
                "next_step": (
                    "To fix a width, call set_constraint with key "
                    "'<mass_id>_width_ft' (e.g. 'academic_width_ft') for each "
                    "mass, then call solve_dimensions again. To have the engine "
                    "choose widths for you, call search_site_schemes instead."
                ),
            }
        else:
            result = solve_dimensions(session)
    elif name == "pair_masses":
        if session.brief_locked and not session.pairings:
            result = {
                "ok": False,
                "error": (
                    "The brief did not pair these masses. Do not pair them "
                    "or treat the length cap as a length to fill."
                ),
            }
        elif session.brief_locked and any(
            getattr(p, "length_is_cap", False) for p in session.pairings
        ):
            result = {
                "ok": False,
                "error": (
                    "Those wings are already paired under a length cap from "
                    "the brief. Do not replace that with a length to fill."
                ),
                "pairings": [p.to_dict() for p in session.pairings],
            }
        else:
            result = pair_masses(
                session,
                [str(m) for m in arguments.get("mass_ids", [])],
                float(arguments["total_length_ft"]),
                arguments.get("pairing_id"),
                length_is_cap=bool(arguments.get("length_is_cap", False)),
            )
    elif name == "clear_pairings":
        if session.brief_locked and session.pairings:
            result = {
                "ok": False,
                "error": "The brief pairing is already set. Do not clear it.",
                "pairings": [p.to_dict() for p in session.pairings],
            }
        else:
            result = clear_pairings(session)
    elif name == "set_floor_steps":
        # Small models reach for a synonym of "weights" about as often as the
        # real name; they all mean the same list, so accept them.
        weights = next(
            (
                arguments[key]
                for key in ("weights", "steps", "step_weights", "ratios", "fractions")
                if arguments.get(key)
            ),
            [],
        )
        result = set_floor_steps(session, str(arguments["mass_id"]), list(weights))
    elif name == "set_floor_taper":
        result = set_floor_taper(
            session, str(arguments["mass_id"]), arguments.get("ratio")
        )
    elif name == "clear_floor_steps":
        result = clear_floor_steps(session, str(arguments.get("mass_id", "")))
    elif name == "search_site_schemes":
        if session.brief_locked:
            result = {
                "ok": False,
                "error": (
                    "The brief is already applied. Do not search for a new "
                    "scheme or treat the length cap as a site length to fill."
                ),
                "grouping": get_grouping_summary(session),
            }
        else:
            result = search_site_schemes(
                session,
                max_total_length_ft=arguments.get("max_total_length_ft"),
                max_length_ft=arguments.get("max_length_ft"),
                max_width_ft=arguments.get("max_width_ft"),
                max_stories=(
                    int(arguments["max_stories"])
                    if arguments.get("max_stories") is not None
                    else None
                ),
                preference=str(arguments.get("preference", "balanced")),
                top_n=int(arguments.get("top_n", 3)),
            )
    elif name == "apply_scheme":
        if session.brief_locked:
            result = {
                "ok": False,
                "error": "The brief is already applied. Do not replace it with a scheme.",
            }
        else:
            result = apply_scheme(session, int(arguments.get("index", 0)))
    elif name == "pin_department_to_floor":
        if session.brief_locked:
            result = {
                "ok": False,
                "error": "Floor placement comes from the brief. Do not re-pin it.",
            }
        else:
            result = pin_department_to_floor(
                session, str(arguments["department"]), int(arguments["level"])
            )
    elif name == "unpin_department":
        if session.brief_locked and session.floor_pins:
            result = {
                "ok": False,
                "error": "Floor pins come from the brief. Do not unpin them.",
            }
        else:
            result = unpin_department(session, str(arguments["department"]))
    elif name == "resize_mass":
        if session.brief_locked:
            result = {
                "ok": False,
                "error": "Mass size comes from the brief. Do not resize it.",
            }
        else:
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
