"""
Drawable topologies and stated site.

T is only what layout can draw: independent bars, paired bars, and L leftover
around a void. Courtyard, podium, and perpendicular wings stay named and
unsupported. D is the stated rectangle and frontage. Streets wait.
"""

from __future__ import annotations

from typing import Any

from ..group import _family_of

UNSUPPORTED_T = ("courtyard", "podium", "perpendicular_wings")
UNSUPPORTED_D = ("streets", "neighbors", "topography")
PAIRING_CAP = 2


def stated_frontage_ft(session: Any) -> float | None:
    """Site frontage the engine can check. A per-bar length cap is not this."""
    value = session.constraints.get("max_total_length_ft")
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def topology_is_required(session: Any) -> bool:
    """True when the brief already paired masses. T is not sampled."""
    return bool(session.constraints.get("topology_locked"))


def paired_bars_drawable(session: Any) -> bool:
    return len(session.masses or []) >= 2 and stated_frontage_ft(session) is not None


def has_l_leftover(session: Any) -> bool:
    """Layout already wraps a double-height void. That is not a courtyard."""
    if session.double_height_rooms:
        return True
    last = getattr(session, "last_massing", None) or {}
    for mass in last.get("masses") or []:
        for floor in mass.get("floors") or []:
            if floor.get("voids"):
                return True
    return False


def pairing_proposals(session: Any, cap: int = PAIRING_CAP) -> list[list[str]]:
    """A few drawable pairings. Do not invent a courtyard or a length."""
    masses = list(session.masses or [])
    if len(masses) < 2:
        return []
    by_family: dict[str, str] = {}
    for mass in masses:
        for dept in mass.departments:
            fam = _family_of(dept)
            by_family.setdefault(fam, mass.id)
    out: list[list[str]] = []
    academic = by_family.get("academic")
    public = by_family.get("athletics") or by_family.get("dining")
    if academic and public and academic != public:
        out.append([academic, public])
    arts = by_family.get("arts")
    if academic and arts and academic != arts:
        pair = [academic, arts]
        if pair not in out:
            out.append(pair)
    if not out:
        out.append([masses[0].id, masses[1].id])
    return out[:cap]


def describe_topology(session: Any) -> dict[str, Any]:
    """Catalog for COVER, the planner, and the Phase 7 board."""
    frontage = stated_frontage_ft(session)
    locked = topology_is_required(session)
    current = "paired_bars" if session.pairings else "independent_bars"
    paired_status = "stated" if current == "paired_bars" and locked else (
        "drawable" if paired_bars_drawable(session) and not locked else (
            "locked" if locked else "missing_d"
        )
    )
    l_status = "drawable" if has_l_leftover(session) else "idle"
    site = {
        "stated": frontage is not None
        or session.constraints.get("max_building_width_ft") is not None
        or session.constraints.get("max_building_length_ft") is not None,
        "frontage_ft": frontage,
        "max_building_length_ft": session.constraints.get("max_building_length_ft"),
        "max_width_ft": session.constraints.get("max_building_width_ft"),
        "unsupported": list(UNSUPPORTED_D),
    }
    if locked:
        note = "T was stated. Other topologies were not sampled."
    elif frontage is None:
        note = (
            "Paired bars need a stated site frontage. D is only the stated "
            "rectangle. Courtyard stays unsupported."
        )
    else:
        note = (
            f"Drawable T: independent bars and paired bars under {frontage:g} ft "
            "(cap). Courtyard, podium, and perpendicular wings are unsupported."
        )
    return {
        "ran": True,
        "locked": locked,
        "current": current,
        "l_leftover": has_l_leftover(session),
        "drawable": [
            {"kind": "independent_bars", "status": "stated" if current == "independent_bars" else "available"},
            {"kind": "paired_bars", "status": paired_status},
            {"kind": "l_leftover", "status": l_status},
        ],
        "unsupported": list(UNSUPPORTED_T),
        "site": site,
        "proposals": pairing_proposals(session) if (not locked and frontage) else [],
        "note": note,
    }
