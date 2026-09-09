"""
Bradley–Terry taste among schemes that already fit.

Utility is a weighted sum of measured traits. A written brief is not a
sample. Weights stay empty until the user compares two legal drawings.
"""

from __future__ import annotations

import math
import re
from typing import Any

from .traits import TRAIT_NAMES


def utility(weights: dict[str, float], traits: dict[str, float]) -> float:
    return sum(float(weights.get(name, 0.0)) * float(traits.get(name, 0.0)) for name in _names(weights, traits))


def chance_a_beats_b(traits_a: dict[str, float], traits_b: dict[str, float], weights: dict[str, float]) -> float:
    gap = utility(weights, traits_a) - utility(weights, traits_b)
    return 1.0 / (1.0 + math.exp(-gap))


def fit_weights(
    comparisons: list[dict[str, Any]],
    schemes: dict[str, dict[str, float]],
    steps: int = 40,
    rate: float = 0.35,
    l2: float = 0.15,
) -> dict[str, float]:
    """
    Fit weights from pairwise choices.

    Each comparison is {"a": id, "b": id, "winner": "a"|"b"}. Schemes that
    are missing, or not feasible, are ignored.
    """
    names = list(TRAIT_NAMES)
    weights = {name: 0.0 for name in names}
    usable = []
    for item in comparisons:
        a = schemes.get(item.get("a"))
        b = schemes.get(item.get("b"))
        winner = item.get("winner")
        if not a or not b or winner not in {"a", "b"}:
            continue
        if not a.get("fits") or not b.get("fits"):
            continue
        usable.append((a["traits"], b["traits"], winner))
    if not usable:
        return weights

    for _ in range(steps):
        grad = {name: -l2 * weights[name] for name in names}
        for traits_a, traits_b, winner in usable:
            pa = chance_a_beats_b(traits_a, traits_b, weights)
            target = 1.0 if winner == "a" else 0.0
            error = target - pa
            for name in names:
                diff = float(traits_a.get(name, 0.0)) - float(traits_b.get(name, 0.0))
                grad[name] += error * diff
        for name in names:
            weights[name] += rate * grad[name]
    return {name: round(weights[name], 4) for name in names}


def information(probability: float) -> float:
    p = min(1.0, max(0.0, float(probability)))
    return p * (1.0 - p)


def next_pair(
    schemes: list[dict[str, Any]],
    weights: dict[str, float],
    comparisons: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    Pick two feasible schemes.

    Prefer an ambiguous pair. If a legal typology has never been shown,
    connect it with one already compared scheme so the ratings stay on one scale.
    """
    legal = [s for s in schemes if s.get("fits") and s.get("traits")]
    if len(legal) < 2:
        return None
    compared = {item.get("a") for item in comparisons} | {item.get("b") for item in comparisons}
    by_id = {s["id"]: s for s in legal}

    unseen = [s for s in legal if s["id"] not in compared]
    seen = [s for s in legal if s["id"] in compared]
    if unseen and seen:
        fresh = unseen[0]
        other = max(seen, key=lambda s: abs(utility(weights, s["traits"]) - utility(weights, fresh["traits"])))
        return _pair(fresh, other, "connecting")
    if unseen and len(unseen) >= 2:
        return _pair(unseen[0], unseen[1], "connecting")

    best = None
    best_score = -1.0
    for i, left in enumerate(legal):
        for right in legal[i + 1 :]:
            if _already(comparisons, left["id"], right["id"]):
                continue
            pa = chance_a_beats_b(left["traits"], right["traits"], weights)
            score = information(pa)
            if score > best_score:
                best_score = score
                best = _pair(left, right, "ambiguous" if score >= 0.2 else "obvious")
    return best


def parse_choice(text: str) -> str | None:
    """A comparison answer only. A new brief is not a choice."""
    raw = (text or "").strip().lower()
    if not raw or len(raw) > 80:
        return None
    if re.search(r"\b(mass|length|story|stories|floor|meter|metres|must|limit)\b", raw):
        return None
    if re.fullmatch(r"(a|first|left|1|one)", raw):
        return "a"
    if re.fullmatch(r"(b|second|right|2|two)", raw):
        return "b"
    match = re.fullmatch(
        r"(?:i\s+)?(?:prefer|choose|like|pick|take)\s+(?:the\s+)?(first|second|a|b|left|right)(?:\s+one)?",
        raw,
    )
    if not match:
        return None
    return "a" if match.group(1) in {"first", "a", "left"} else "b"


def take_choice(session: Any, text: str) -> dict[str, Any] | None:
    """Record a choice between the pending legal pair and update the weights."""
    learning = dict(session.constraints.get("preference_learning") or {})
    pair = learning.get("pending_pair")
    if not pair or not pair.get("a") or not pair.get("b"):
        return None
    side = parse_choice(text)
    if side is None:
        return None
    schemes = list((session.constraints.get("try_loop") or {}).get("schemes") or [])
    by_id = {item["id"]: item for item in schemes if item.get("fits")}
    if pair["a"] not in by_id or pair["b"] not in by_id:
        return None
    comparisons = list(learning.get("comparisons") or [])
    comparisons.append({"a": pair["a"], "b": pair["b"], "winner": side})
    weights = fit_weights(comparisons, by_id)
    nxt = next_pair(schemes, weights, comparisons)
    note = describe_weights(weights)
    updated = {
        "weights": weights,
        "comparisons": comparisons,
        "pending_pair": nxt,
        "note": note,
    }
    session.constraints["preference_learning"] = updated
    from .try_loop import reweight_particles

    ranked = reweight_particles(session, weights)
    if hasattr(session, "save"):
        session.save()
    chosen = by_id[pair[side]]
    other = by_id[pair["b" if side == "a" else "a"]]
    reply = (
        f"You chose {_scheme_line(chosen)} over {_scheme_line(other)}. "
        f"{note} Requirements and caps are unchanged."
    )
    if ranked.get("ok"):
        reply += (
            f" Legal schemes were reweighted; the highest is {ranked.get('kept')} "
            f"(effective sample size {ranked.get('effective_sample_size')})."
        )
    if nxt:
        reply += " Next pair: " + _scheme_line(by_id[nxt["a"]]) + " or " + _scheme_line(by_id[nxt["b"]]) + "."
    else:
        reply += " No further informative pair among the legal schemes."
    return {"ok": True, "reply": reply, "learning": updated}


def _scheme_line(scheme: dict[str, Any]) -> str:
    lengths = scheme.get("lengths") or []
    length = ", ".join(str(v) for v in lengths) if lengths else "unmeasured"
    return f"{scheme.get('id')} ({scheme.get('reason') or 'scheme'}, lengths {length} ft)"


def describe_weights(weights: dict[str, float]) -> str:
    ranked = sorted(weights.items(), key=lambda item: abs(item[1]), reverse=True)
    parts = []
    for name, value in ranked:
        if abs(value) < 0.05:
            continue
        direction = "more" if value > 0 else "less"
        parts.append(f"{direction} {name.replace('_', ' ')}")
    if not parts:
        return "No compared taste yet. Stated preferences still rank the schemes."
    return "From the comparisons, the favored drawings have " + ", ".join(parts) + "."


def _pair(left: dict[str, Any], right: dict[str, Any], kind: str) -> dict[str, Any]:
    return {
        "a": left["id"],
        "b": right["id"],
        "kind": kind,
        "a_typology": left.get("typology"),
        "b_typology": right.get("typology"),
    }


def _already(comparisons: list[dict[str, Any]], a: str, b: str) -> bool:
    for item in comparisons:
        ids = {item.get("a"), item.get("b")}
        if ids == {a, b}:
            return True
    return False


def _names(weights: dict[str, float], traits: dict[str, float]) -> list[str]:
    seen = list(TRAIT_NAMES)
    for name in list(weights) + list(traits):
        if name not in seen:
            seen.append(name)
    return seen
