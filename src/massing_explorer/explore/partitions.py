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


def local_partition_candidates(session: Any, *, limit: int = 4) -> list[dict[str, Any]]:
    """
    Nearby legal regroupings: move or swap unlocked departments.

    Extends search past the early COVER shortlist without re-enumerating Bell.
    Preserves keep-together glue, keep-apart, alone, and mass-count bounds.
    """
    if grouping_is_required(session):
        return []
    from .strategy import (
        required_alone,
        required_apart,
        required_mass_bounds,
        required_together,
    )

    masses = list(session.masses or [])
    if len(masses) < 2:
        return []
    home: dict[str, int] = {}
    blocks: list[list[str]] = []
    stories: list[int] = []
    for i, mass in enumerate(masses):
        depts = [str(d) for d in (mass.departments or []) if d]
        blocks.append(depts)
        stories.append(int(mass.story_count or 2))
        for d in depts:
            home[d] = i
    if len(home) < 2:
        return []

    # Glue keep-together into moveable units.
    parent = {d: d for d in home}

    def find(d: str) -> str:
        while parent[d] != d:
            parent[d] = parent[parent[d]]
            d = parent[d]
        return d

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for pair in required_together(session):
        members = [d for d in pair if d in home]
        for a, b in zip(members, members[1:]):
            union(a, b)
    alone = set(required_alone(session))
    units: dict[str, list[str]] = {}
    for d in home:
        units.setdefault(find(d), []).append(d)
    unit_list = list(units.values())

    apart_pairs = [frozenset(p) for p in required_apart(session)]
    for dept in alone:
        for other in home:
            if other != dept:
                apart_pairs.append(frozenset({dept, other}))
    bounds = required_mass_bounds(session)

    def legal(blocks_now: list[list[str]]) -> bool:
        nonempty = [b for b in blocks_now if b]
        if bounds:
            k_min, k_max = int(bounds[0]), int(bounds[1])
            if not (k_min <= len(nonempty) <= k_max):
                return False
        homes: dict[str, int] = {}
        for i, b in enumerate(nonempty):
            for d in b:
                homes[d] = i
        for pair in apart_pairs:
            members = [d for d in pair if d in homes]
            if len(members) >= 2 and len({homes[d] for d in members}) == 1:
                return False
        return True

    def to_groups(blocks_now: list[list[str]]) -> list[dict[str, Any]]:
        out = []
        for i, depts in enumerate(blocks_now):
            if not depts:
                continue
            if i < len(masses) and set(depts) & set(masses[i].departments or []):
                mid = masses[i].id
                name = masses[i].name
                st = stories[i]
            else:
                mid = f"m{len(out)}"
                name = f"Mass {len(out) + 1}"
                st = 2
            out.append(
                {
                    "id": mid,
                    "name": name,
                    "departments": list(depts),
                    "story_count": st,
                }
            )
        used: set[str] = set()
        for g in out:
            base = str(g["id"])
            nid = base
            n = 2
            while nid in used:
                nid = f"{base}_{n}"
                n += 1
            used.add(nid)
            g["id"] = nid
        return out

    stated = partition_signature(blocks)
    proposals: list[dict[str, Any]] = []
    seen: set[frozenset[frozenset[str]]] = set()

    def push(blocks_now: list[list[str]], reason: str) -> None:
        if not legal(blocks_now):
            return
        groups = to_groups(blocks_now)
        sig = partition_signature(groups)
        if sig == stated or sig in seen:
            return
        seen.add(sig)
        proposals.append({"groups": groups, "reason": reason})

    # Moves: relocate a glued unit onto another mass (or open a new mass if bounds allow).
    for unit in unit_list:
        src = home[unit[0]]
        for dst in range(len(blocks)):
            if dst == src:
                continue
            nxt = [list(b) for b in blocks]
            for d in unit:
                if d in nxt[src]:
                    nxt[src].remove(d)
            nxt[dst].extend(unit)
            push(nxt, f"move {','.join(unit)} → mass {dst}")
        # Open a new mass when allowed.
        if bounds is None or len([b for b in blocks if b]) < int(bounds[1]):
            nxt = [list(b) for b in blocks]
            for d in unit:
                if d in nxt[src]:
                    nxt[src].remove(d)
            nxt.append(list(unit))
            push(nxt, f"extract {','.join(unit)}")

    # Swaps: exchange two units on different masses.
    for i, left in enumerate(unit_list):
        for right in unit_list[i + 1 :]:
            a, b = home[left[0]], home[right[0]]
            if a == b:
                continue
            nxt = [list(x) for x in blocks]
            for d in left:
                if d in nxt[a]:
                    nxt[a].remove(d)
            for d in right:
                if d in nxt[b]:
                    nxt[b].remove(d)
            nxt[a].extend(right)
            nxt[b].extend(left)
            push(nxt, f"swap {','.join(left)} ↔ {','.join(right)}")

    return proposals[: max(0, int(limit))]


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
