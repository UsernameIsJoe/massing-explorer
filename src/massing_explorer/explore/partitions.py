"""
Legal program partitions when P is open.

CSP enumerates the feasible set. Callers choose a role-specific shortlist size:
  - UI / report: UI_PARTITION_CAP (~5)
  - COVER search: adaptive 12–20 diverse partitions
"""

from __future__ import annotations

from typing import Any

from .csp import UI_PARTITION_CAP, describe_csp
from .strategy import grouping_is_required, required_mass_bounds

# COVER search pool — not a UI number.
COVER_PARTITION_MIN = 12
COVER_PARTITION_MAX = 20


def partition_signature(groups: list[list[str]] | list[dict[str, Any]]) -> frozenset[frozenset[str]]:
    out = []
    for group in groups:
        depts = group["departments"] if isinstance(group, dict) else group
        out.append(frozenset(str(d) for d in depts))
    return frozenset(out)


def cover_partition_budget(
    session: Any,
    *,
    feasible_count: int | None = None,
) -> int:
    """
    How many diverse partitions COVER should search.

    Presentation stays at UI_PARTITION_CAP. This budget is 12–20 when the CSP
    has enough feasible organizations; smaller when the feasible set is tiny.
    """
    if grouping_is_required(session):
        return 1
    bounds = required_mass_bounds(session)
    span = 0
    if bounds:
        span = max(0, int(bounds[1]) - int(bounds[0]))
    if feasible_count is None:
        report = describe_csp(session, cap=1)
        feasible_count = int(report.get("feasible_count") or 0)
    feasible_count = max(0, int(feasible_count))
    if feasible_count <= 0:
        return 0
    if feasible_count <= COVER_PARTITION_MIN:
        return feasible_count
    # Wider |P| range and larger feasible sets → more of the 12–20 band.
    target = COVER_PARTITION_MIN + min(
        COVER_PARTITION_MAX - COVER_PARTITION_MIN,
        span * 3 + (2 if feasible_count >= 80 else 0) + (2 if feasible_count >= 400 else 0),
    )
    return max(1, min(COVER_PARTITION_MAX, target, feasible_count))


def enumerate_partitions(session: Any, cap: int = UI_PARTITION_CAP) -> list[dict[str, Any]]:
    """Diverse legal P's from CSP, capped for the caller (UI or COVER)."""
    report = describe_csp(session, cap=cap)
    chosen = list(report.get("chosen") or [])
    if grouping_is_required(session):
        return chosen[:1]
    return chosen[:cap]


def cover_partition_candidates(session: Any, *, limit: int = 4) -> list[dict[str, Any]]:
    """
    APPLY_PARTITION candidates for MCTS / catalog.

    Prefer the COVER search pool persisted on the session; fall back to a
    fresh COVER-budget shortlist so MCTS is never stuck on the UI 5.
    """
    if grouping_is_required(session):
        return []
    stated = partition_signature(
        [list(m.departments) for m in (session.masses or [])]
    )
    pool = list(session.constraints.get("cover_partition_pool") or [])
    if not pool:
        budget = cover_partition_budget(session)
        pool = enumerate_partitions(session, cap=budget)
    out: list[dict[str, Any]] = []
    for item in pool:
        groups = item.get("groups") or []
        if not groups:
            continue
        if partition_signature(groups) == stated:
            continue
        out.append(item)
        if len(out) >= max(0, int(limit)):
            break
    return out


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
