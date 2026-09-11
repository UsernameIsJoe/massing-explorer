"""
MCTS over typed design actions. The planner is the expansion prior.

The tree sits around planner + engine. It does not sit on feet.
search.py remains the enumeration baseline for experiments; its weighted
sum is not the MCTS reward.
"""

from __future__ import annotations

import math
from typing import Any

from . import archive as archive_mod
from .actions import UNSUPPORTED, apply_action
from .performance import measure
from .planner import parse_plan
from .saturate import MCTS_DEPTH, MCTS_ROOTS, MCTS_SIMS, Saturation, read_explore_budget
from .strategy import grouping_is_required

# Production caps. Session explore_budget may lower them.
SIM_CAP = MCTS_SIMS
MAX_DEPTH = MCTS_DEPTH
EXPLORATION = 1.25
CATALOG_CAP = 18
ROOT_CAP = MCTS_ROOTS


class _Node:
    def __init__(self, action: dict[str, Any] | None = None, prior: float = 1.0, parent: _Node | None = None):
        self.action = action
        self.prior = float(prior)
        self.parent = parent
        self.children: list[_Node] = []
        self.visits = 0
        self.value = 0.0
        self.kind = "root" if action is None else "pending"
        self.reason = ""
        self.expanded = False
        self.from_planner = False

    @property
    def q(self) -> float:
        if self.visits <= 0:
            return 0.0
        return self.value / self.visits


def run_mcts(
    session: Any,
    archive: dict[str, Any] | None = None,
    client: Any = None,
    plan: Any = None,
    evaluate: Any = None,
    simulations: int | None = None,
    weights: dict[str, float] | None = None,
    max_depth: int | None = None,
    root_cap: int | None = None,
) -> dict[str, Any]:
    """UCT from several diverse COVER elites, not one current scheme."""
    archive = archive if archive is not None else archive_mod.load_archive(session)
    if not session.masses:
        return _empty_report("No masses, so MCTS did not run.")

    budget = read_explore_budget(session)
    cap = SIM_CAP
    sims_budget = int(simulations) if simulations is not None else int(budget["mcts_sims"])
    sims_budget = max(1, min(sims_budget, cap))
    depth = int(max_depth) if max_depth is not None else int(budget["mcts_depth"])
    depth = max(1, min(depth, MAX_DEPTH))
    n_roots = int(root_cap) if root_cap is not None else int(budget["mcts_roots"])
    n_roots = max(1, min(n_roots, ROOT_CAP))

    prior_actions = _planner_prior(session, client, plan)
    current_snap = archive_mod.capture(session)
    roots_meta = cover_roots(session, archive, weights=weights, cap=n_roots)
    forest = _Node()
    forest.kind = "forest"
    trees: list[dict[str, Any]] = []
    best_reward = -1.0
    known_cells = set((archive.get("cells") or {}).keys())
    total_sims = 0
    applied = illegal = unsupported = 0

    per_root = max(1, sims_budget // max(1, len(roots_meta)))
    extra = sims_budget - per_root * len(roots_meta)

    for i, meta in enumerate(roots_meta):
        snap = meta.get("snapshot") or current_snap
        archive_mod.restore_snapshot(session, snap, meta.get("stories"))
        root = _Node()
        root.kind = "cover_root"
        root.reason = str(meta.get("label") or "cover elite")
        forest.children.append(root)
        allot = per_root + (1 if i < extra else 0)
        # Each COVER elite gets its own search. Do not skip remaining roots
        # after the first basin goes idle.
        root_sat = Saturation(window=min(8, max(3, allot // 2)), min_steps=max(2, allot // 2))
        ran, stats = _simulate_tree(
            session,
            archive,
            root,
            prior_actions,
            simulations=allot,
            depth=depth,
            saturation=root_sat,
            known_cells=known_cells,
            best_reward=best_reward,
            weights=weights,
        )
        total_sims += ran
        applied += stats["applied"]
        illegal += stats["illegal"]
        unsupported += stats["unsupported"]
        best_reward = max(best_reward, stats["best_reward"])
        known_cells |= stats["cells"]
        trees.append(
            {
                "label": root.reason,
                "simulations": ran,
                "best_reward": stats["best_reward"],
                "saturated": root_sat.stop(),
                "best_path": _best_path(root),
            }
        )

    archive_mod.restore_snapshot(session, current_snap)
    tree = _flatten(forest)
    best = []
    if trees:
        best = (
            max(
                trees,
                key=lambda t: (float(t.get("best_reward") or 0.0), len(t.get("best_path") or [])),
            ).get("best_path")
            or []
        )
    return {
        "ran": True,
        "simulations": total_sims,
        "depth": depth,
        "roots": [t["label"] for t in trees],
        "root_reports": trees,
        "saturated": bool(trees) and all(t.get("saturated") for t in trees),
        "nodes": tree,
        "best_path": best,
        "prior": [_label(a) for a in prior_actions],
        "applied": applied,
        "illegal": illegal,
        "unsupported": unsupported,
        "baseline": "search.py remains the enumeration baseline. MCTS does not sit on feet.",
        "note": (
            f"MCTS: {total_sims} simulation(s) from {len(trees)} COVER root(s), "
            f"depth {depth} ({applied} applied, {illegal} illegal, {unsupported} unsupported)"
            f"{'; stopped on saturation' if (trees and all(t.get('saturated') for t in trees)) else ''}. "
            "Planner is the expansion prior. search.py remains the enumeration baseline."
        ),
    }


def _simulate_tree(
    session: Any,
    archive: dict[str, Any],
    root: _Node,
    prior_actions: list[dict[str, Any]],
    *,
    simulations: int,
    depth: int,
    saturation: Saturation,
    known_cells: set[str],
    best_reward: float,
    weights: dict[str, float] | None = None,
) -> tuple[int, dict[str, Any]]:
    from .strategy import cell_key

    root_snap = archive_mod.capture(session)
    ran = 0
    local_best = best_reward
    local_cells = set(known_cells)
    applied = illegal = unsupported = 0
    gained_any = False
    for _ in range(max(1, simulations)):
        if saturation.stop() and ran >= 1:
            break
        archive_mod.restore_snapshot(session, root_snap)
        path = [root]
        node = root
        while (
            node.children
            and all(child.visits > 0 for child in node.children)
            and len(path) < depth
            and node.kind not in {"illegal", "unsupported"}
        ):
            node = max(node.children, key=lambda child: _puct(child, node.visits))
            _play(session, node)
            path.append(node)
            if node.kind in {"illegal", "unsupported"}:
                break

        if node.kind not in {"illegal", "unsupported"} and len(path) <= depth:
            if not node.expanded:
                _expand(
                    node,
                    session,
                    prior_actions if node is root else [],
                    probe_unsupported=node is root,
                )
                node.expanded = True
            waiting = [child for child in node.children if child.visits == 0]
            if waiting:
                child = max(waiting, key=lambda item: item.prior)
                _play(session, child)
                path.append(child)
                node = child

        reward = _score(session, archive, node, weights)
        for item in path:
            item.visits += 1
            item.value += reward
        ran += 1
        if node.kind == "applied":
            applied += 1
        elif node.kind == "illegal":
            illegal += 1
        elif node.kind == "unsupported":
            unsupported += 1
        gained = False
        try:
            key = cell_key(session)
        except Exception:
            key = ""
        if key and key not in local_cells:
            local_cells.add(key)
            gained = True
        if reward > local_best + 1e-6:
            local_best = reward
            gained = True
        if gained:
            gained_any = True
        saturation.observe(gained)
    archive_mod.restore_snapshot(session, root_snap)
    return ran, {
        "applied": applied,
        "illegal": illegal,
        "unsupported": unsupported,
        "best_reward": local_best,
        "cells": local_cells,
        "gained": gained_any,
    }


def cover_roots(
    session: Any,
    archive: dict[str, Any],
    *,
    weights: dict[str, float] | None = None,
    cap: int = ROOT_CAP,
) -> list[dict[str, Any]]:
    """Legal COVER elites first, then a few near-feasible frontier starts."""
    from .archive import frontier_entries, legal_cells
    from .feasibility import feasibility_distance_of, idea_of
    from .preference import taste_weight

    legal = legal_cells(archive)
    frontier = frontier_entries(archive)
    current = {
        "snapshot": archive_mod.capture(session),
        "stories": {m.id: m.story_count for m in session.masses},
        "label": "current",
        "entry": None,
    }
    picked: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    seen_ideas: set[str] = set()

    def _append(entry: dict[str, Any], label: str) -> None:
        snap = entry.get("snapshot") or {}
        if not snap:
            return
        picked.append(
            {
                "snapshot": snap,
                "stories": entry.get("stories") or snap.get("stories") or {},
                "label": label,
                "entry": entry,
            }
        )

    if legal:
        ranked = sorted(legal, key=lambda e: taste_weight(e, weights), reverse=True)
        n_front = min(len(frontier), 2 if cap > 1 else 0)
        legal_slots = max(1, cap - n_front)
        for entry in ranked:
            strat = entry.get("strategy") or {}
            axis = (
                entry.get("partition"),
                ((strat.get("T") or {}).get("kind")),
                ((strat.get("G") or {}).get("envelope") or (strat.get("G") or {}).get("loading")),
            )
            if axis in seen and len(picked) >= 2:
                continue
            seen.add(axis)
            seen_ideas.add(idea_of(entry))
            _append(entry, str(entry.get("reason") or entry.get("cell") or "elite"))
            if len(picked) >= legal_slots:
                break
    for entry in sorted(frontier, key=feasibility_distance_of):
        if len(picked) >= cap:
            break
        idea = idea_of(entry)
        if idea and idea in seen_ideas:
            continue
        seen_ideas.add(idea)
        dist = feasibility_distance_of(entry)
        _append(entry, f"frontier d={dist:.3f}")
    return picked or [current]


def catalog_actions(session: Any, include_unsupported: bool = True) -> list[dict[str, Any]]:
    """Local typed neighbors. Nearby width is a step, not an invented target."""
    from .topology import pairing_proposals, stated_frontage_ft, topology_is_required

    actions: list[dict[str, Any]] = []
    lock = session.constraints.get("story_lock") or {}
    cap = max(1, int(session.constraints.get("max_stories") or 4))
    paired_ids = {mid for p in (session.pairings or []) for mid in (p.mass_ids or [])}
    for mass in session.masses or []:
        if mass.id in lock:
            continue
        current = int(mass.story_count or 2)
        for stories in (current - 1, current + 1):
            if 1 <= stories <= cap and stories != current:
                actions.append({"op": "SET_STORIES", "mass": mass.id, "stories": stories})
        if mass.id not in paired_ids:
            for delta in (-10.0, 10.0):
                actions.append({"op": "SET_WIDTH", "mass": mass.id, "delta_ft": delta})
    loading = str(session.constraints.get("loading") or "double")
    if not session.constraints.get("loading_required"):
        other = "single" if loading != "single" else "double"
        actions.append({"op": "SET_LOADING", "loading": other})
    current_env = str(session.constraints.get("cover_envelope") or "balanced")
    for env in ("balanced", "compact", "elongated"):
        if env != current_env:
            actions.append({"op": "SET_ENVELOPE", "envelope": env})
    pins = session.floor_pins or {}
    for mass in session.masses or []:
        for dept in mass.departments:
            if dept not in pins:
                actions.append({"op": "PIN_GROUND", "programs": [dept]})
                break
        if any(a.get("op") == "PIN_GROUND" for a in actions):
            break
    if not topology_is_required(session):
        if stated_frontage_ft(session) and not session.pairings:
            for ids in pairing_proposals(session, cap=1):
                actions.append({"op": "PAIR_MASSES", "masses": ids})
                break
        elif session.pairings:
            actions.append({"op": "CLEAR_PAIRINGS"})
    if not grouping_is_required(session):
        try:
            from .partitions import enumerate_partitions, partition_signature

            stated = partition_signature([list(m.departments) for m in session.masses])
            added = 0
            for item in enumerate_partitions(session, cap=3):
                groups = item.get("groups") or []
                if partition_signature(groups) == stated:
                    continue
                actions.append({"op": "APPLY_PARTITION", "groups": groups})
                added += 1
                if added >= 2:
                    break
        except Exception:
            pass
    if include_unsupported:
        actions.append({"op": "COURTYARD"})
    seen: set[str] = set()
    out = []
    for action in actions:
        key = action_key(action)
        if key in seen:
            continue
        seen.add(key)
        out.append(action)
        if len(out) >= CATALOG_CAP + (1 if include_unsupported else 0):
            break
    return out


def action_key(action: dict[str, Any] | None) -> str:
    if not action:
        return "root"
    op = str(action.get("op") or "")
    bits = [op]
    for key in ("mass", "mass_id", "stories", "loading", "type", "delta_ft", "envelope"):
        if action.get(key) is not None:
            bits.append(f"{key}={action[key]}")
    if action.get("programs") or action.get("departments"):
        names = [str(x) for x in (action.get("programs") or action.get("departments") or [])]
        bits.append("p=" + ",".join(sorted(names)))
    if action.get("masses") or action.get("mass_ids"):
        bits.append("m=" + ",".join(str(x) for x in (action.get("masses") or action.get("mass_ids") or [])))
    if action.get("groups"):
        bits.append("g=" + "|".join(",".join(str(x) for x in g) for g in action.get("groups") or []))
    return "|".join(bits)


def _planner_prior(session: Any, client: Any, plan: Any) -> list[dict[str, Any]]:
    if plan is None and client is None:
        return []
    if isinstance(plan, list):
        parsed = parse_plan({"actions": plan}, session)
    elif isinstance(plan, dict) and (plan.get("actions") or plan.get("moves")):
        parsed = parse_plan(plan, session)
    elif client is not None:
        from .planner import _request

        parsed = parse_plan(_request(client, session, None), session)
    else:
        parsed = parse_plan({}, session)
    return list(parsed.get("actions") or [])


def _expand(
    node: _Node,
    session: Any,
    prior_actions: list[dict[str, Any]],
    probe_unsupported: bool = False,
) -> None:
    catalog = catalog_actions(session, include_unsupported=probe_unsupported)
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for action in list(prior_actions) + catalog:
        key = action_key(action)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(action)
    if not ordered:
        return
    prior_keys = {action_key(a) for a in prior_actions}
    prior_n = sum(1 for a in ordered if action_key(a) in prior_keys) or 1
    rest_n = sum(1 for a in ordered if action_key(a) not in prior_keys) or 1
    share = 0.7 if prior_actions else 0.0
    for action in ordered:
        key = action_key(action)
        if key in prior_keys:
            prior = share / prior_n
        else:
            prior = (1.0 - share) / rest_n if prior_actions else 1.0 / len(ordered)
        child = _Node(action=action, prior=prior, parent=node)
        child.from_planner = key in prior_keys
        node.children.append(child)


def _play(session: Any, node: _Node) -> None:
    if not node.action or node.kind in {"applied", "illegal", "unsupported"}:
        if node.kind == "applied" and node.action:
            try:
                apply_action(session, node.action)
            except Exception:
                return
        return
    try:
        out = apply_action(session, node.action)
    except Exception as exc:
        node.kind = "illegal"
        node.reason = str(exc)
        return
    op = str(out.get("op") or node.action.get("op") or "")
    node.reason = str(out.get("reason") or "")
    if out.get("ok"):
        node.kind = "applied"
    elif op in UNSUPPORTED or "cannot realize" in node.reason.lower():
        node.kind = "unsupported"
    else:
        node.kind = "illegal"


def _score(session: Any, archive: dict[str, Any], node: _Node, weights: dict[str, float] | None = None) -> float:
    if node.kind in {"illegal", "unsupported", "pending"}:
        return 0.0
    if getattr(session, "program", None) is None:
        return 0.6 if node.kind in {"applied", "root", "cover_root"} else 0.0
    from ..solver import solve_massing_study
    from .saturate import search_reward

    result = solve_massing_study(session)
    performance = measure(result, session, archive=archive)
    archive_mod.insert(
        archive,
        session,
        result,
        performance,
        reason=f"mcts {_label(node.action)}" if node.action else "mcts root",
    )
    return search_reward(performance, weights)


def _reward(performance: dict[str, Any], weights: dict[str, float] | None = None) -> float:
    from .saturate import search_reward

    return search_reward(performance, weights)


def _puct(node: _Node, parent_visits: int) -> float:
    if node.visits <= 0:
        return 1000.0 + node.prior
    return node.q + EXPLORATION * node.prior * math.sqrt(max(1, parent_visits)) / (1 + node.visits)


def _flatten(root: _Node) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def walk(node: _Node, parent_id: int | None, depth: int) -> None:
        idx = len(out)
        out.append(
            {
                "id": idx,
                "parent": parent_id,
                "depth": depth,
                "op": _label(node.action),
                "kind": node.kind,
                "reason": node.reason,
                "visits": node.visits,
                "q": round(node.q, 4),
                "prior": round(node.prior, 4),
                "planner_prior": bool(node.from_planner),
            }
        )
        for child in node.children:
            walk(child, idx, depth + 1)

    walk(root, None, 0)
    return out


def _best_path(root: _Node) -> list[dict[str, Any]]:
    path = []
    node = root
    while node.children:
        applied = [c for c in node.children if c.visits > 0 and c.kind == "applied"]
        pool = applied or [c for c in node.children if c.visits > 0]
        if not pool:
            break
        node = max(pool, key=lambda c: (c.q, c.visits, c.prior))
        path.append({"op": _label(node.action), "kind": node.kind, "q": round(node.q, 4), "visits": node.visits})
    return path


def _label(action: dict[str, Any] | None) -> str:
    if not action:
        return "root"
    op = str(action.get("op") or "ACTION")
    extra = action.get("mass") or action.get("loading") or action.get("envelope") or ""
    if action.get("stories") is not None:
        extra = f"{action.get('mass')} {action.get('stories')}fl"
    if action.get("delta_ft") is not None:
        extra = f"{action.get('mass')} {action.get('delta_ft'):+g}ft"
    if action.get("programs"):
        extra = ",".join(str(x) for x in action["programs"][:2])
    if action.get("masses"):
        extra = "+".join(str(x) for x in action["masses"][:2])
    extra = str(extra)
    return f"{op} {extra}".strip()


def _empty_report(note: str) -> dict[str, Any]:
    return {
        "ran": False,
        "simulations": 0,
        "saturated": False,
        "roots": [],
        "root_reports": [],
        "depth": MAX_DEPTH,
        "nodes": [],
        "best_path": [],
        "prior": [],
        "applied": 0,
        "illegal": 0,
        "unsupported": 0,
        "baseline": "search.py remains the enumeration baseline. MCTS does not sit on feet.",
        "note": note,
    }
