"""
Legal program partitions when P is open.

The constraint solver enumerates distinct feasible organizations. The LLM
does not invent P. Locked briefs return only the stated grouping.
"""

from __future__ import annotations

from typing import Any

from .csp import PARTITION_CAP, describe_csp
from .strategy import grouping_is_required


def partition_signature(groups: list[list[str]] | list[dict[str, Any]]) -> frozenset[frozenset[str]]:
    out = []
    for group in groups:
        depts = group["departments"] if isinstance(group, dict) else group
        out.append(frozenset(str(d) for d in depts))
    return frozenset(out)


def enumerate_partitions(session: Any, cap: int = PARTITION_CAP) -> list[dict[str, Any]]:
    """Distinct legal P's from CSP, default first. Locked briefs return one grouping."""
    report = describe_csp(session, cap=cap)
    chosen = list(report.get("chosen") or [])
    if grouping_is_required(session):
        return chosen[:1]
    return chosen[:cap]


def apply_partition(session: Any, groups: list[dict[str, Any]]) -> None:
    """Write one partition onto the study. Drops widths and pairings for vanished masses."""
    from ..tools import set_grouping

    payload = [
        {
            "id": g["id"],
            "name": g["name"],
            "departments": list(g["departments"]),
            "story_count": int(g.get("story_count") or 2),
        }
        for g in groups
    ]
    set_grouping(session, payload)
    ids = {m.id for m in session.masses}
    for key in list(session.constraints):
        if not str(key).endswith("_width_ft"):
            continue
        if key in {"fixed_width_ft", "max_building_width_ft", "academic_width_ft"}:
            continue
        owner = str(key)[: -len("_width_ft")]
        if owner not in ids:
            session.constraints.pop(key, None)
    session.pairings = [
        p for p in (session.pairings or []) if all(mid in ids for mid in p.mass_ids)
    ]
    if session.floor_steps:
        session.floor_steps = {k: v for k, v in session.floor_steps.items() if k in ids}
    if session.floor_tapers:
        session.floor_tapers = {k: v for k, v in session.floor_tapers.items() if k in ids}
