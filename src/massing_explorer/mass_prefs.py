"""
Per-mass preferences: how each wing should look, under the musts.

A sentence like "mass A should be a thin 3-floor building, mass B a 1:1
box of 2 floors" is not a requirement. Stage one tries those shapes, and
legal neighbors, so stage two can rank how the masses work together.
"""

from __future__ import annotations

import math
import re
from typing import Any

_ORD = {
    "a": 1,
    "b": 2,
    "c": 3,
    "d": 4,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
}
_SHAPE = (
    ("thin", ("thin", "slender", "narrow")),
    ("box", ("box", "square")),
    ("cube", ("cube",)),
)


def parse_mass_preferences(text: str) -> list[dict[str, Any]]:
    """Pull per-mass shape, story, and ratio preferences from ordinary language."""
    out: list[dict[str, Any]] = []
    pattern = re.compile(
        r"mass\s+([a-d]|one|two|three|four|\d+)\b"
        r"(.*?)(?=\bmass\s+(?:[a-d]|one|two|three|four|\d+)\b|$)",
        flags=re.I | re.S,
    )
    for match in pattern.finditer(text or ""):
        body = match.group(2)
        if not re.search(r"should|prefer|ideally|be a|be an", body, flags=re.I):
            continue
        pref = _pref_from_clause(match.group(1), body)
        if pref:
            out.append(pref)
    return out


def _pref_from_clause(ref: str, body: str) -> dict[str, Any] | None:
    low = body.lower()
    shape = None
    for name, words in _SHAPE:
        if any(re.search(rf"\b{word}\b", low) for word in words):
            shape = name
            break
    stories = None
    hit = re.search(r"(\d+)\s*(?:-| )?(?:floor|storey|story|stories)", low)
    if hit:
        stories = max(1, int(hit.group(1)))
    ratio = None
    ratio_hit = re.search(r"(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", body)
    if ratio_hit and float(ratio_hit.group(2)) > 0:
        ratio = float(ratio_hit.group(1)) / float(ratio_hit.group(2))
    size = "small" if re.search(r"\bsmall\b", low) else None
    if shape is None and stories is None and ratio is None and size is None:
        return None
    return {
        "ref": ref.lower(),
        "ordinal": _ORD.get(ref.lower()),
        "stories": stories,
        "shape": shape,
        "ratio": ratio,
        "size": size,
        "text": f"mass {ref}{body}".strip(),
    }


def bind_mass_preferences(
    prefs: list[dict[str, Any]],
    masses: list[Any],
) -> list[dict[str, Any]]:
    """Attach each preference to a real mass, in the order the masses were named."""
    bound: list[dict[str, Any]] = []
    used: set[str] = set()
    for pref in prefs:
        mass = None
        ordinal = pref.get("ordinal")
        if ordinal and 1 <= int(ordinal) <= len(masses):
            mass = masses[int(ordinal) - 1]
        if mass is None or mass.id in used:
            continue
        used.add(mass.id)
        item = dict(pref)
        item["mass_id"] = mass.id
        item["mass_name"] = mass.name
        bound.append(item)
    return bound


def apply_mass_shape(session: Any, mass_id: str, shape: str | None, ratio: float | None) -> None:
    key = f"{mass_id}_shape"
    ratio_key = f"{mass_id}_length_over_width"
    if shape:
        session.constraints[key] = shape
    else:
        session.constraints.pop(key, None)
    if ratio:
        session.constraints[ratio_key] = float(ratio)
    else:
        session.constraints.pop(ratio_key, None)


def clear_mass_shapes(session: Any) -> None:
    for key in list(session.constraints):
        if key.endswith("_shape") or key.endswith("_length_over_width"):
            session.constraints.pop(key, None)


def width_for_shape(
    shape: str | None,
    ratio: float | None,
    plate: float,
    functional: float,
) -> float | None:
    """
    A preferred shape sets the bar. It does not set the length to a cap.

    thin: a narrow classroom bar.
    box / cube / 1:1: a square plate.
    """
    if plate <= 0:
        return None
    if shape == "thin":
        return functional
    if shape in {"box", "cube"} or (ratio and abs(float(ratio) - 1.0) < 1e-6):
        return math.sqrt(plate)
    if ratio and float(ratio) > 0:
        return math.sqrt(plate / float(ratio))
    return None


def preference_distance(pref: dict[str, Any], mass: Any) -> float:
    """Lower is closer to what was asked for this mass."""
    if not mass.floors:
        return 5.0
    floor = mass.floors[0]
    miss = 0.0
    if pref.get("stories"):
        miss += abs(len(mass.floors) - int(pref["stories"]))
    aspect = floor.length_ft / floor.width_ft if floor.width_ft else 0.0
    shape = pref.get("shape")
    ratio = pref.get("ratio")
    if shape == "thin":
        miss += 0.0 if aspect >= 1.3 else (1.3 - aspect)
    elif shape in {"box", "cube"} or ratio:
        target = float(ratio or 1.0)
        miss += abs(aspect - target)
    if pref.get("size") == "small":
        miss += floor.area_sf / 100000.0
    return miss
