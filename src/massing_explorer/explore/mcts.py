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

SIM_CAP = 8
MAX_DEPTH = 2
EXPLORATION = 1.25
CATALOG_CAP = 6


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
    simulations: int = SIM_CAP,
) -> dict[str, Any]:
    """A few UCT simulations. Planner actions get the expansion prior."""
    archive = archive if archive is not None else archive_mod.load_archive(session)
    if not session.masses:
        return _empty_report("No masses, so MCTS did not run.")

    prior_actions = _planner_prior(session, client, plan)
    root_snap = archive_mod.capture(session)
    root = _Node()
    sims = max(1, min(int(simulations), SIM_CAP))

    for _ in range(sims):
        archive_mod.restore_snapshot(session, root_snap)
        path = [root]
        node = root
        while (
            node.children
            and all(child.visits > 0 for child in node.children)
            and len(path) < MAX_DEPTH
            and node.kind not in {"illegal", "unsupported"}
        ):
            node = max(node.children, key=lambda child: _puct(child, node.visits))
            _play(session, node)
            path.append(node)
            if node.kind in {"illegal", "unsupported"}:
                break

        if node.kind not in {"illegal", "unsupported"} and len(path) <= MAX_DEPTH:
            if not node.expanded:
                _expand(node, session, prior_actions if node is root else [], probe_unsupported=node is root)
                node.expanded = True
            waiting = [child for child in node.children if child.visits == 0]
            if waiting:
                child = max(waiting, key=lambda item: item.prior)
                _play(session, child)
                path.append(child)
                node = child

        reward = _score(session, archive, node)
        for item in path:
            item.visits += 1
            item.value += reward

    archive_mod.restore_snapshot(session, root_snap)
    tree = _flatten(root)
    best = _best_path(root)
    applied = sum(1 for n in tree if n.get("kind") == "applied")
    illegal = sum(1 for n in tree if n.get("kind") == "illegal")
    unsupported = sum(1 for n in tree if n.get("kind") == "unsupported")
    return {
        "ran": True,
        "simulations": sims,
        "depth": MAX_DEPTH,
        "nodes": tree,
        "best_path": best,
        "prior": [_label(a) for a in prior_actions],
        "applied": applied,
        "illegal": illegal,
        "unsupported": unsupported,
        "baseline": "search.py remains the enumeration baseline. MCTS does not sit on feet.",
        "note": (
            f"MCTS: {sims} simulation(s) over design actions "
            f"({applied} applied, {illegal} illegal, {unsupported} unsupported). "
            "Planner is the expansion prior. search.py remains the enumeration baseline."
        ),
    }


def catalog_actions(session: Any, include_unsupported: bool = True) -> list[dict[str, Any]]:
    """Deterministic legal-looking moves. No invented feet. Courtyard stays a probe."""
    from .topology import pairing_proposals, stated_frontage_ft, topology_is_required

    actions: list[dict[str, Any]] = []
    lock = session.constraints.get("story_lock") or {}
    cap = max(1, int(session.constraints.get("max_stories") or 4))
    for mass in session.masses or []:
        if mass.id in lock:
            continue
        current = int(mass.story_count or 2)
        for stories in (current - 1, current + 1):
            if 1 <= stories <= cap and stories != current:
                actions.append({"op": "SET_STORIES", "mass": mass.id, "stories": stories})
        if len(actions) >= CATALOG_CAP:
            break
    loading = str(session.constraints.get("loading") or "double")
    if not session.constraints.get("loading_required"):
        other = "single" if loading != "single" else "double"
        actions.append({"op": "SET_LOADING", "loading": other})
    pins = session.floor_pins or {}
    for mass in session.masses or []:
        for dept in mass.departments:
            if dept not in pins:
                actions.append({"op": "PIN_GROUND", "programs": [dept]})
                break
        if any(a.get("op") == "PIN_GROUND" for a in actions):
            break
    if not topology_is_required(session) and stated_frontage_ft(session):
        for ids in pairing_proposals(session, cap=1):
            actions.append({"op": "PAIR_MASSES", "masses": ids})
            break
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
        if len(out) >= CATALOG_CAP + 1:
            break
    return out


def action_key(action: dict[str, Any] | None) -> str:
    if not action:
        return "root"
    op = str(action.get("op") or "")
    bits = [op]
    for key in ("mass", "mass_id", "stories", "loading", "type"):
        if action.get(key) is not None:
            bits.append(f"{key}={action[key]}")
    if action.get("programs") or action.get("departments"):
        names = [str(x) for x in (action.get("programs") or action.get("departments") or [])]
        bits.append("p=" + ",".join(sorted(names)))
    if action.get("masses") or action.get("mass_ids"):
        bits.append("m=" + ",".join(str(x) for x in (action.get("masses") or action.get("mass_ids") or [])))
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


def _score(session: Any, archive: dict[str, Any], node: _Node) -> float:
    if node.kind in {"illegal", "unsupported", "pending"}:
        return 0.0
    if getattr(session, "program", None) is None:
        return 0.6 if node.kind in {"applied", "root"} else 0.0
    from ..solver import solve_massing_study

    result = solve_massing_study(session)
    performance = measure(result, session, archive=archive)
    archive_mod.insert(
        archive,
        session,
        result,
        performance,
        reason=f"mcts {_label(node.action)}" if node.action else "mcts root",
    )
    return _reward(performance)


def _reward(performance: dict[str, Any]) -> float:
    """Feasibility first. Not search.py's balanced/low_rise/compact sum."""
    if not performance.get("fits_limitations"):
        return 0.0
    feasible = 1.0 if performance.get("feasible") else 0.4
    pref = 1.0 - min(1.0, max(0.0, float(performance.get("preference_distance") or 0.0)))
    novelty = min(1.0, max(0.0, float(performance.get("novelty") or 0.0)))
    return round(0.55 * feasible + 0.30 * pref + 0.15 * novelty, 4)


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
    extra = action.get("mass") or action.get("loading") or ""
    if action.get("stories") is not None:
        extra = f"{action.get('mass')} {action.get('stories')}fl"
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
