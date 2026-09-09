"""
One strategy planner: at most five typed actions. The engine falsifies them.

The planner does not draw feet. Unsupported topologies are counted separately
from illegal moves that undo a must or invent a dimension.
"""

from __future__ import annotations

import json
from typing import Any

from .actions import SUPPORTED, UNSUPPORTED, apply_action
from .strategy import grouping_is_required, read_strategy

MAX_ACTIONS = 5
FEET_KEYS = (
    "width_ft",
    "length_ft",
    "total_length_ft",
    "gsf",
    "area_sf",
    "area_ft",
    "depth_ft",
)


def plan_context(session: Any, archive: dict[str, Any] | None = None) -> dict[str, Any]:
    """What the planner is allowed to see: brief, failures, empty cells."""
    from . import archive as archive_mod
    from .topology import describe_topology

    archive = archive or archive_mod.load_archive(session)
    briefing = session.constraints.get("briefing") or {}
    failed = []
    last = getattr(session, "last_massing", None) or {}
    for check in last.get("failed_checks") or []:
        failed.append(check)
    infeasible = [
        k for k, e in (archive.get("cells") or {}).items() if not e.get("fits_limitations")
    ]
    return {
        "locked_p": grouping_is_required(session),
        "strategy": read_strategy(session),
        "masses": [
            {
                "id": m.id,
                "name": m.name,
                "departments": list(m.departments),
                "stories": int(m.story_count),
            }
            for m in session.masses
        ],
        "requirements": list(briefing.get("requirements") or []),
        "limitations": list(briefing.get("limitations") or []),
        "preferences": list(briefing.get("preferences") or []),
        "archive": {
            "attempts": archive.get("attempts"),
            "legal": len(archive_mod.legal_cells(archive)),
            "infeasible": len(infeasible),
            "unsupported": archive.get("unsupported"),
            "note": archive.get("note"),
        },
        "failed_checks": failed,
        "empty_cells": _empty_cell_lines(session, archive),
        "topology": describe_topology(session),
        "allowed_ops": list(SUPPORTED),
        "unsupported_ops": list(UNSUPPORTED),
        "rule": (
            "At most five typed actions. Do not invent feet or GSF. "
            "Do not undo a required grouping. Do not propose COLOCATE, "
            "SPLIT_MASS, or KEEP_APART when P is open; the constraint solver "
            "owns partitions. PAIR_MASSES only with the stated frontage as a cap. "
            "Courtyard, podium, and perpendicular wings are unsupported."
        ),
    }


def _empty_cell_lines(session: Any, archive: dict[str, Any] | None) -> list[str]:
    try:
        from .explain import empty_cells

        return list((empty_cells(session, archive).get("sentences") or [])[:8])
    except Exception:
        return []


def parse_plan(data: dict[str, Any] | None, session: Any) -> dict[str, Any]:
    """Keep at most five actions. Drop invented sizes before the engine sees them."""
    dropped: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    raw = []
    if isinstance(data, dict):
        raw = list(data.get("actions") or data.get("moves") or [])
    why = ""
    if isinstance(data, dict):
        why = str(data.get("why") or data.get("reason") or "")
    for item in raw:
        if not isinstance(item, dict):
            dropped.append({"op": "UNKNOWN", "kind": "dropped", "reason": "Not an action object."})
            continue
        cleaned, drop_reason = _clean_action(item, session)
        if drop_reason:
            dropped.append(
                {
                    "op": str(item.get("op") or item.get("name") or "UNKNOWN").upper(),
                    "kind": "illegal" if "invent" in drop_reason.lower() or "foot" in drop_reason.lower() else "dropped",
                    "reason": drop_reason,
                    "action": item,
                }
            )
            continue
        if cleaned:
            kept.append(cleaned)
    extra = kept[MAX_ACTIONS:]
    for action in extra:
        dropped.append(
            {
                "op": action.get("op"),
                "kind": "dropped",
                "reason": "Over the five-action cap.",
                "action": action,
            }
        )
    return {"actions": kept[:MAX_ACTIONS], "dropped": dropped, "why": why}


def apply_plan(session: Any, actions: list[dict[str, Any]], evaluate: Any = None) -> dict[str, Any]:
    """Apply each action or record why the engine rejected it."""
    results: list[dict[str, Any]] = []
    applied = illegal = unsupported = 0
    for action in actions:
        out = apply_action(session, action)
        op = str(out.get("op") or action.get("op") or "")
        if out.get("ok"):
            kind = "applied"
            applied += 1
            if evaluate:
                evaluate(session, f"planner {op}")
        elif op in UNSUPPORTED or "cannot realize" in str(out.get("reason") or "").lower():
            kind = "unsupported"
            unsupported += 1
        else:
            kind = "illegal"
            illegal += 1
        results.append(
            {
                "op": op,
                "kind": kind,
                "ok": bool(out.get("ok")),
                "reason": out.get("reason") or "",
                "action": action,
            }
        )
    return {
        "ran": True,
        "applied": applied,
        "illegal": illegal,
        "unsupported": unsupported,
        "results": results,
        "note": _note(applied, illegal, unsupported),
    }


def run_planner(
    session: Any,
    client: Any = None,
    plan: dict[str, Any] | list | None = None,
    archive: dict[str, Any] | None = None,
    evaluate: Any = None,
) -> dict[str, Any]:
    """Ask the planner, or apply an injected plan. No client means skip."""
    if plan is None and client is None:
        return {"ran": False, "applied": 0, "illegal": 0, "unsupported": 0, "results": [], "note": "No planner this turn."}
    if isinstance(plan, list):
        parsed = parse_plan({"actions": plan}, session)
    elif isinstance(plan, dict) and (plan.get("actions") or plan.get("moves") or plan.get("dropped")):
        parsed = parse_plan(plan, session)
    elif client is not None:
        parsed = parse_plan(_request(client, session, archive), session)
    else:
        parsed = parse_plan(plan if isinstance(plan, dict) else {}, session)

    report = apply_plan(session, parsed["actions"], evaluate=evaluate)
    report["dropped"] = parsed["dropped"]
    report["why"] = parsed.get("why") or ""
    report["illegal"] = int(report["illegal"]) + sum(
        1 for d in parsed["dropped"] if d.get("kind") == "illegal"
    )
    report["note"] = _note(report["applied"], report["illegal"], report["unsupported"])
    if parsed["dropped"]:
        report["note"] += f" {len(parsed['dropped'])} proposal(s) dropped before apply."
    return report


def empty_cells_line(archive: dict[str, Any] | None) -> str:
    if not archive:
        return "No archive yet."
    return str(archive.get("note") or "Archive has no explanation yet.")


def _request(client: Any, session: Any, archive: dict[str, Any] | None) -> dict[str, Any] | None:
    from ..reading import _extract_json

    context = plan_context(session, archive)
    try:
        response = client.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You are the one strategy planner. Reply with JSON only: "
                        '{"actions": [{"op": "...", ...}], "why": "..."}. '
                        "At most five actions. Use only typed ops. Do not invent "
                        "dimensions. Do not undo a required grouping. Do not invent "
                        "P; the constraint solver enumerates partitions."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Propose up to five design actions for this study.\n"
                        + json.dumps(context, default=str)[:5000]
                    ),
                },
            ]
        )
        content = (response.get("message") or {}).get("content") or ""
    except Exception:
        return None
    return _extract_json(content)


def _clean_action(item: dict[str, Any], session: Any) -> tuple[dict[str, Any] | None, str]:
    op = str(item.get("op") or item.get("name") or "").upper()
    if not op:
        return None, "Missing op."
    invented = [k for k in FEET_KEYS if item.get(k) is not None]
    if op == "PAIR_MASSES":
        length = item.get("length_ft", item.get("total_length_ft"))
        stated = _stated_length(session, length)
        if length is not None and stated is None:
            return None, "Invented a pairing length. A cap is not a length to draw."
        cleaned = {
            "op": op,
            "masses": list(item.get("masses") or item.get("mass_ids") or []),
            "length_is_cap": True,
        }
        if stated is not None:
            cleaned["length_ft"] = stated
        elif session.constraints.get("max_total_length_ft"):
            cleaned["length_ft"] = float(session.constraints["max_total_length_ft"])
        return cleaned, ""
    if invented:
        return None, f"Invented size field(s): {', '.join(invented)}."
    cleaned = {"op": op}
    for key in ("programs", "departments", "from", "from_mass", "as", "name", "mass", "mass_id", "stories", "story_count", "type", "loading"):
        if key in item:
            cleaned[key] = item[key]
    return cleaned, ""


def _stated_length(session: Any, length: Any) -> float | None:
    if length is None:
        return None
    try:
        value = float(length)
    except (TypeError, ValueError):
        return None
    known = [
        session.constraints.get("max_total_length_ft"),
    ]
    for item in known:
        if item is None:
            continue
        if abs(float(item) - value) < 0.51:
            return float(item)
    return None


def _note(applied: int, illegal: int, unsupported: int) -> str:
    return (
        f"Planner: {applied} applied, {illegal} illegal, {unsupported} unsupported. "
        "The engine falsifies. The planner does not draw feet."
    )
