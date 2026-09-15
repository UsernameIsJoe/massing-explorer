"""
A strategy is a sequence of architectural decisions, not a width.

S = (P, T, V, G, D). Only fields the engine can store today are filled.
Courtyard and streets stay unsupported until layout can realize them.
"""

from __future__ import annotations

from typing import Any

from .topology import has_l_leftover, stated_frontage_ft, topology_is_required


def story_band(stories: int) -> str:
    if int(stories) <= 1:
        return "low"
    if int(stories) == 2:
        return "mid"
    return "high"


def grouping_is_required(session: Any) -> bool:
    """True only when the architect named a complete partition to freeze.

    mass_count, keep-together, and alone constrain P. They do not freeze it.
    brief_locked still blocks the chat tools from rewriting wings; COVER and
    the CSP may sample other organizations that obey those constraints.
    """
    return bool((getattr(session, "constraints", None) or {}).get("partition_locked"))


def p_constraints(session: Any) -> dict[str, Any] | None:
    """User-stated P constraints, not the synthesized starting grouping."""
    raw = (getattr(session, "constraints", None) or {}).get("p_constraints")
    return raw if isinstance(raw, dict) else None


def required_together(session: Any) -> list[frozenset[str]]:
    """Glue pairs the architect stated. Synthesized wings are not glue."""
    stated = p_constraints(session)
    if stated is not None:
        return _pair_list(stated.get("together"))
    pairs: list[frozenset[str]] = []
    briefing = (getattr(session, "constraints", None) or {}).get("briefing") or {}
    for clause in briefing.get("requirements") or []:
        if clause.get("lever") in {"same_mass", "keep_together"}:
            depts = [str(d) for d in (clause.get("departments") or [])]
            if len(depts) >= 2:
                pairs.append(frozenset(depts))
    return pairs


def required_alone(session: Any) -> list[str]:
    """Departments the architect said must be their own mass."""
    stated = p_constraints(session)
    if stated is not None:
        return [str(d) for d in (stated.get("alone") or []) if d]
    out: list[str] = []
    briefing = (getattr(session, "constraints", None) or {}).get("briefing") or {}
    for clause in briefing.get("requirements") or []:
        if clause.get("lever") != "alone":
            continue
        for dept in clause.get("departments") or []:
            name = str(dept)
            if name and name not in out:
                out.append(name)
    return out


def preferred_mass_count(session: Any) -> int | None:
    """Soft preferred |P| from the brief. Absent → no mass-count ranking bias."""
    stated = p_constraints(session)
    if stated is not None and stated.get("preferred_mass_count") is not None:
        try:
            n = int(stated["preferred_mass_count"])
        except (TypeError, ValueError):
            n = 0
        return n if n > 0 else None
    raw = (getattr(session, "constraints", None) or {}).get("preferred_mass_count")
    if raw is not None and raw != "":
        try:
            n = int(raw)
        except (TypeError, ValueError):
            n = 0
        if n > 0:
            return n
    briefing = (getattr(session, "constraints", None) or {}).get("briefing") or {}
    for clause in briefing.get("preferences") or []:
        if clause.get("lever") != "mass_count":
            continue
        value = clause.get("value")
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def required_mass_bounds(session: Any) -> tuple[int, int] | None:
    """Inclusive |P| range. A single count is (n, n)."""
    stated = p_constraints(session)
    if stated is not None:
        lo = stated.get("mass_count_min")
        hi = stated.get("mass_count_max")
        exact = stated.get("mass_count")
        if lo is None and hi is None and exact is None:
            return None
        try:
            lo_i = int(lo if lo is not None else (exact if exact is not None else hi))
            hi_i = int(hi if hi is not None else (exact if exact is not None else lo))
        except (TypeError, ValueError):
            return None
        if lo_i <= 0 or hi_i <= 0:
            return None
        return (min(lo_i, hi_i), max(lo_i, hi_i))
    count = required_mass_count(session)
    if count is None:
        return None
    return (int(count), int(count))


def _pair_list(raw: Any) -> list[frozenset[str]]:
    pairs: list[frozenset[str]] = []
    for item in raw or []:
        if isinstance(item, (list, tuple, set, frozenset)) and len(item) >= 2:
            pairs.append(frozenset(str(d) for d in item))
    return pairs


def required_mass_count(session: Any) -> int | None:
    """Stated mass count. Ranges like 3–4 use the high end (same as parse)."""
    briefing = session.constraints.get("briefing") or {}
    for clause in briefing.get("requirements") or []:
        if clause.get("lever") != "mass_count":
            continue
        value = clause.get("value")
        if value is None or value == "":
            continue
        if isinstance(value, (list, tuple)):
            nums = [int(v) for v in value if v is not None and v != ""]
            return max(nums) if nums else None
        return int(value)
    return None


def required_apart(session: Any) -> list[frozenset[str]]:
    """Keep-apart pairs constrain P; they do not lock a single grouping."""
    stated = p_constraints(session)
    if stated is not None:
        return _pair_list(stated.get("apart"))
    pairs: list[frozenset[str]] = []
    briefing = session.constraints.get("briefing") or {}
    for clause in briefing.get("requirements") or []:
        if clause.get("lever") == "keep_apart":
            depts = [str(d) for d in (clause.get("departments") or [])]
            if len(depts) >= 2:
                pairs.append(frozenset(depts[:2]))
    for item in session.constraints.get("keep_apart") or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            pairs.append(frozenset({str(item[0]), str(item[1])}))
    return pairs


def topology_of(session: Any) -> str:
    if session.pairings:
        return "paired_bars"
    return "independent_bars"


def read_strategy(session: Any) -> dict[str, Any]:
    """Snapshot of the current structured design state."""
    masses = []
    for mass in session.masses:
        masses.append(
            {
                "id": mass.id,
                "name": mass.name,
                "departments": list(mass.departments),
                "stories": int(mass.story_count),
                "story_band": story_band(mass.story_count),
            }
        )
    pins = {str(k): int(v) for k, v in (session.floor_pins or {}).items()}
    loading = session.constraints.get("loading") or "double"
    return {
        "P": {
            "mass_count": len(session.masses),
            "partition": {m["id"]: list(m["departments"]) for m in masses},
            "locked": grouping_is_required(session),
        },
        "T": {
            "kind": topology_of(session),
            "supported": True,
            "locked": topology_is_required(session),
            "l_leftover": has_l_leftover(session),
        },
        "V": {
            "pins": pins,
            "double_height": list(session.double_height_rooms or []),
            "story_lock": dict(session.constraints.get("story_lock") or {}),
        },
        "G": {
            "stories": {m["id"]: m["stories"] for m in masses},
            "loading": loading,
            "envelope": session.constraints.get("cover_envelope") or "balanced",
            "plate_profile": _plate_profile_of(session),
        },
        "D": {
            "max_building_length_ft": session.constraints.get("max_building_length_ft"),
            "max_total_length_ft": session.constraints.get("max_total_length_ft"),
            "max_building_width_ft": session.constraints.get("max_building_width_ft"),
            "frontage": stated_frontage_ft(session),
            "stated": stated_frontage_ft(session) is not None
            or session.constraints.get("max_building_width_ft") is not None
            or session.constraints.get("max_building_length_ft") is not None,
            "unsupported": ["streets", "neighbors", "topography"],
        },
        "masses": masses,
        "cell": cell_key(session),
    }


def _plate_profile_of(session: Any) -> str:
    if getattr(session, "floor_tapers", None) or getattr(session, "floor_steps", None):
        return "step"
    return "uniform"


def cell_key(session: Any) -> str:
    """Behavior cell = architectural idea (no exact feet, no geom ranks)."""
    return idea_key(session)


def partition_id(session: Any) -> str:
    chunks = []
    for mass in session.masses:
        chunks.append("+".join(sorted(mass.departments)))
    return " / ".join(sorted(chunks))


def idea_key(session: Any) -> str:
    """Architectural identity: P + T + V + loading + envelope + plate + exact stories.

    Exact widths are omitted so a repaired plate stays the same idea.
    Vertical organization is included because search can pin floors.
    Story counts are exact so 3 and 4 do not collapse into one band.
    """
    loading = session.constraints.get("loading") or "double"
    topo = topology_of(session)
    env = session.constraints.get("cover_envelope") or "balanced"
    plate = _plate_profile_of(session)
    parts = [
        f"P:{partition_id(session)}",
        f"T:{topo}",
        f"L:{loading}",
        f"E:{env}",
        f"PL:{plate}",
        _vertical_token(
            session.floor_pins or {},
            session.double_height_rooms or [],
            session.constraints.get("story_lock") or {},
        ),
    ]
    for mass in session.masses:
        parts.append(f"{mass.id}:{int(mass.story_count)}")
    return "|".join(parts)


def _vertical_token(pins: Any, double_height: Any, story_lock: Any) -> str:
    pin_bits = sorted(f"{k}:{int(v)}" for k, v in (pins or {}).items())
    dh_bits = sorted(str(x) for x in (double_height or []))
    lock_bits = sorted(f"{k}:{int(v)}" for k, v in (story_lock or {}).items())
    return f"V:{','.join(pin_bits)}/{','.join(dh_bits)}/{','.join(lock_bits)}"


def idea_key_from_entry(entry: dict[str, Any] | None) -> str:
    """Rebuild an idea key from an archive entry when `idea` was not stored."""
    if not entry:
        return ""
    if entry.get("idea"):
        return str(entry["idea"])
    strat = entry.get("strategy") or {}
    program = strat.get("P") or {}
    topo = (strat.get("T") or {}).get("kind") or "independent_bars"
    geom = strat.get("G") or {}
    loading = geom.get("loading") or "double"
    env = geom.get("envelope") or "balanced"
    plate = geom.get("plate_profile") or "uniform"
    partition = entry.get("partition") or ""
    if not partition:
        parts = []
        for depts in (program.get("partition") or {}).values():
            parts.append("+".join(sorted(str(d) for d in (depts or []))))
        partition = " / ".join(sorted(parts))
    stories = geom.get("stories") or entry.get("stories") or {}
    heights = []
    for mid, n in stories.items():
        try:
            heights.append(f"{mid}:{int(n)}")
        except (TypeError, ValueError):
            continue
    vertical = strat.get("V") or {}
    token = _vertical_token(
        vertical.get("pins") or {},
        vertical.get("double_height") or [],
        vertical.get("story_lock") or {},
    )
    return "|".join(
        [f"P:{partition}", f"T:{topo}", f"L:{loading}", f"E:{env}", f"PL:{plate}", token, *heights]
    )
