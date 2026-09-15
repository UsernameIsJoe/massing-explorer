"""
Distance-to-feasibility for illegal COVER samples.

Hard limitations only. Soft prefs (preferred stories / preferred ratio) do
not enter this vector. Illegal search_reward is graded by this distance;
accepted schemes use architectural_reward.
"""

from __future__ import annotations

import re
from typing import Any

from .strategy import idea_key_from_entry

FRONTIER_MAX = 12
FRONTIER_DISTANCE = 0.35

DISTANCE_WEIGHTS = {
    "edge_overrun": 0.40,
    "min_edge_short": 0.10,
    "ratio_hard": 0.10,
    "split": 0.15,
    "stories": 0.15,
    "required_width": 0.05,
    "other": 0.05,
}

_EMPTY = {
    "edge_overrun": 0.0,
    "min_edge_short": 0.0,
    "ratio_hard": 0.0,
    "split": 0.0,
    "stories": 0.0,
    "required_width": 0.0,
    "other": 0.0,
}

_NUM = r"([\d.,]+)"
_VS_MAX = re.compile(
    rf"{_NUM}\s*ft(?:\s+at L\d+)?\s+vs\s+(?:max|allowed)\s+{_NUM}",
    re.I,
)
_VS_MIN = re.compile(rf"{_NUM}\s*ft\s+vs min\s+{_NUM}", re.I)
_VS_REQ = re.compile(rf"{_NUM}\s*ft\s+vs required\s+{_NUM}", re.I)
_RATIO = re.compile(
    rf"length/width\s+{_NUM}.*allowed\s+{_NUM}\s*[–-]\s*{_NUM}",
    re.I,
)

_EDGE_KINDS = {
    "site_length",
    "site_width",
    "site_total_length",
    "site_total_width",
    "pairing_length",
}
_OTHER_KINDS = {
    "step_align",
    "step_stack",
    "step_plate_types",
    "step_void",
    "step_cantilever",
    "edge_sum",
    "pairing_length",
}


def empty_violations() -> dict[str, float]:
    return dict(_EMPTY)


def _kind(check: str) -> str:
    return str(check or "").split(":")[0]


def _num(raw: str) -> float | None:
    try:
        return float(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _overrun_frac(actual: float, cap: float) -> float:
    if cap <= 0:
        return 1.0 if actual > 0 else 0.0
    return min(1.0, max(0.0, (actual - cap) / cap))


def _shortfall_frac(actual: float, floor: float) -> float:
    if floor <= 0:
        return 0.0
    return min(1.0, max(0.0, (floor - actual) / floor))


def violations_from_checks(
    checks: list[Any],
    *,
    awkward: bool = False,
    session: Any = None,
) -> dict[str, float]:
    """Parse failed ValidationCheck rows into a [0, 1] violation vector."""
    out = empty_violations()
    edge = 0.0
    short = 0.0
    ratio = 0.0
    req = 0.0
    other = 0.0
    split = 1.0 if awkward else 0.0
    stories = 0.0

    role = ""
    if session is not None:
        role = str((getattr(session, "constraints", None) or {}).get("ratio_band_role") or "")

    for check in checks or []:
        passed = bool(getattr(check, "passed", False))
        if passed:
            continue
        name = _kind(getattr(check, "check", "") or "")
        msg = str(getattr(check, "message", "") or "")
        if name.startswith("program_split") or name == "program_split":
            split = 1.0
            continue
        if name in _EDGE_KINDS or name.startswith("site_"):
            hit = _VS_MAX.search(msg)
            if hit:
                actual, cap = _num(hit.group(1)), _num(hit.group(2))
                if actual is not None and cap is not None:
                    edge = max(edge, _overrun_frac(actual, cap))
                    continue
            edge = max(edge, 1.0)
            continue
        if name.startswith("min_edge"):
            hit = _VS_MIN.search(msg)
            if hit:
                actual, floor = _num(hit.group(1)), _num(hit.group(2))
                if actual is not None and floor is not None:
                    short = max(short, _shortfall_frac(actual, floor))
                    continue
            short = max(short, 1.0)
            continue
        if name.startswith("ratio_band"):
            if role == "preference":
                continue
            hit = _RATIO.search(msg)
            if hit:
                aspect = _num(hit.group(1))
                lo = _num(hit.group(2))
                hi = _num(hit.group(3))
                if aspect and lo is not None and hi is not None:
                    from ..aspect import aspect_band_distance

                    ratio = max(ratio, float(aspect_band_distance(aspect, lo, hi)))
                    continue
            ratio = max(ratio, 1.0)
            continue
        if name.startswith("required_width"):
            hit = _VS_REQ.search(msg)
            if hit:
                actual, want = _num(hit.group(1)), _num(hit.group(2))
                if actual is not None and want is not None and want > 0:
                    req = max(req, min(1.0, abs(actual - want) / want))
                    continue
            req = max(req, 1.0)
            continue
        if name.startswith("max_stories") or name == "stories":
            stories = 1.0
            continue
        if name in _OTHER_KINDS or name.startswith("step_") or name.startswith("edge_sum"):
            other = 1.0

    out["edge_overrun"] = round(edge, 4)
    out["min_edge_short"] = round(short, 4)
    out["ratio_hard"] = round(ratio, 4)
    out["split"] = round(split, 4)
    out["stories"] = round(stories, 4)
    out["required_width"] = round(req, 4)
    out["other"] = round(other, 4)
    return out


def violations_from_result(
    result: Any,
    session: Any = None,
    *,
    awkward: bool = False,
) -> dict[str, float]:
    failed = [
        c
        for c in (getattr(result, "validation", None) or [])
        if not getattr(c, "passed", True)
    ]
    if awkward:
        return violations_from_checks(failed, awkward=True, session=session)
    return violations_from_checks(failed, session=session)


def feasibility_distance(violations: dict[str, float] | None) -> float:
    """Weighted distance in [0, 1]. 0 = no hard-limit miss."""
    vec = violations or empty_violations()
    total = 0.0
    for key, weight in DISTANCE_WEIGHTS.items():
        total += weight * float(vec.get(key) or 0.0)
    return round(min(1.0, max(0.0, total)), 4)


def feasibility_distance_of(entry: dict[str, Any] | None) -> float:
    if not entry:
        return 1.0
    if entry.get("fits_limitations"):
        return 0.0
    perf = entry.get("performance") or {}
    if perf.get("fits_limitations"):
        return 0.0
    raw = perf.get("feasibility_distance")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    fails = int(perf.get("limit_fails") or perf.get("failed_checks") or 1)
    return min(1.0, 0.2 * max(1, fails))


def idea_of(entry: dict[str, Any] | None) -> str:
    if not entry:
        return ""
    return str(entry.get("idea") or idea_key_from_entry(entry) or "")


def attach_feasibility(
    vector: dict[str, Any],
    result: Any,
    session: Any = None,
    *,
    awkward: bool = False,
) -> dict[str, Any]:
    """Write violations + feasibility_distance onto a performance vector."""
    legal = bool(vector.get("fits_limitations"))
    if legal:
        vector["violations"] = empty_violations()
        vector["feasibility_distance"] = 0.0
        return vector
    viol = violations_from_result(result, session, awkward=awkward)
    if awkward:
        viol["split"] = 1.0
    owners: dict[str, list[str]] = {}
    for check in getattr(result, "validation", None) or []:
        if getattr(check, "passed", True):
            continue
        raw = str(getattr(check, "check", "") or "")
        kind = _kind(raw)
        parts = raw.split(":")
        mass_id = parts[1] if len(parts) > 1 else ""
        if not mass_id and session is not None:
            msg = str(getattr(check, "message", "") or "")
            for mass in getattr(session, "masses", None) or []:
                if mass.id in msg or (mass.name and mass.name in msg):
                    mass_id = mass.id
                    break
        bucket = None
        if kind in _EDGE_KINDS or kind.startswith("site_"):
            bucket = "edge_overrun"
        elif kind.startswith("program_split"):
            bucket = "split"
        elif kind.startswith("max_stories") or kind == "stories":
            bucket = "stories"
        elif kind.startswith("ratio_band"):
            bucket = "ratio_hard"
        elif kind.startswith("min_edge"):
            bucket = "min_edge_short"
        if bucket and mass_id:
            owners.setdefault(bucket, [])
            if mass_id not in owners[bucket]:
                owners[bucket].append(mass_id)
    if owners:
        viol["owners"] = owners
    vector["violations"] = viol
    vector["feasibility_distance"] = feasibility_distance(viol)
    return vector
