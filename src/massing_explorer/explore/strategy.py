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
    """True when the brief locked program organization. P is not sampled."""
    briefing = session.constraints.get("briefing") or {}
    for clause in briefing.get("requirements") or []:
        if clause.get("lever") in {"mass_count", "same_mass", "alone", "keep_together"}:
            return True
    return bool(session.brief_locked)


def required_together(session: Any) -> list[frozenset[str]]:
    pairs: list[frozenset[str]] = []
    briefing = session.constraints.get("briefing") or {}
    for clause in briefing.get("requirements") or []:
        if clause.get("lever") in {"same_mass", "keep_together"}:
            depts = [str(d) for d in (clause.get("departments") or [])]
            if len(depts) >= 2:
                pairs.append(frozenset(depts))
    return pairs


def required_mass_count(session: Any) -> int | None:
    briefing = session.constraints.get("briefing") or {}
    for clause in briefing.get("requirements") or []:
        if clause.get("lever") == "mass_count" and clause.get("value"):
            return int(clause["value"])
    return None


def required_apart(session: Any) -> list[frozenset[str]]:
    """Keep-apart pairs constrain P; they do not lock a single grouping."""
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


def cell_key(session: Any) -> str:
    """Behavior cell: organization, story band, loading, topology."""
    loading = session.constraints.get("loading") or "double"
    topo = topology_of(session)
    parts = [f"loading:{loading}", f"topo:{topo}"]
    for mass in session.masses:
        depts = ",".join(mass.departments)
        parts.append(f"{mass.id}:{story_band(mass.story_count)}:{depts}")
    return "|".join(parts)


def partition_id(session: Any) -> str:
    chunks = []
    for mass in session.masses:
        chunks.append("+".join(sorted(mass.departments)))
    return " / ".join(sorted(chunks))
