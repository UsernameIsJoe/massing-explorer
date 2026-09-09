"""
Bradley–Terry taste among archive cells that already fit.

A written brief is not a sample. Weights stay empty until the user compares
two legal drawings. "Why B?" is not implemented as extra coordinates.
"""

from __future__ import annotations

import math
import re
from typing import Any

TRAIT_NAMES = ("spread", "height_variance", "footprint_likeness", "street_edge")


def utility(weights: dict[str, float], traits: dict[str, Any]) -> float:
    return sum(float(weights.get(name, 0.0)) * float(traits.get(name, 0.0) or 0.0) for name in TRAIT_NAMES)


def chance_a_beats_b(traits_a: dict[str, Any], traits_b: dict[str, Any], weights: dict[str, float]) -> float:
    gap = utility(weights, traits_a) - utility(weights, traits_b)
    return 1.0 / (1.0 + math.exp(-gap))


def stated_weight(entry: dict[str, Any]) -> float:
    perf = entry.get("performance") or {}
    fail = 1.0 / (1.0 + float(perf.get("failed_checks") or 0.0))
    pref = 1.0 - min(1.0, max(0.0, float(perf.get("preference_distance") or 0.0)))
    return fail * (0.65 + 0.35 * pref)


def taste_weight(entry: dict[str, Any], weights: dict[str, float] | None) -> float:
    if weights and any(abs(float(v)) > 1e-9 for v in weights.values()):
        return utility(weights, entry.get("performance") or {})
    return stated_weight(entry)


def fit_weights(
    comparisons: list[dict[str, Any]],
    schemes: dict[str, dict[str, Any]],
    steps: int = 40,
    rate: float = 0.35,
    l2: float = 0.15,
) -> dict[str, float]:
    weights = {name: 0.0 for name in TRAIT_NAMES}
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
        grad = {name: -l2 * weights[name] for name in TRAIT_NAMES}
        for traits_a, traits_b, winner in usable:
            pa = chance_a_beats_b(traits_a, traits_b, weights)
            error = (1.0 if winner == "a" else 0.0) - pa
            for name in TRAIT_NAMES:
                diff = float(traits_a.get(name, 0.0) or 0.0) - float(traits_b.get(name, 0.0) or 0.0)
                grad[name] += error * diff
        for name in TRAIT_NAMES:
            weights[name] += rate * grad[name]
    return {name: round(weights[name], 4) for name in TRAIT_NAMES}


def information(probability: float) -> float:
    p = min(1.0, max(0.0, float(probability)))
    return p * (1.0 - p)


def next_pair(
    schemes: list[dict[str, Any]],
    weights: dict[str, float],
    comparisons: list[dict[str, Any]],
) -> dict[str, Any] | None:
    legal = [s for s in schemes if s.get("fits") and s.get("traits")]
    if len(legal) < 2:
        return None
    compared = {item.get("a") for item in comparisons} | {item.get("b") for item in comparisons}
    unseen = [s for s in legal if s["id"] not in compared]
    seen = [s for s in legal if s["id"] in compared]
    if unseen and seen:
        fresh = unseen[0]
        other = max(seen, key=lambda s: abs(utility(weights, s["traits"]) - utility(weights, fresh["traits"])))
        return _pair(fresh, other, "connecting")
    if len(unseen) >= 2:
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


def schemes_from_archive(archive: dict[str, Any]) -> list[dict[str, Any]]:
    """Feasible archive cells only. A cap break is not an A/B option."""
    out = []
    for i, entry in enumerate((archive.get("cells") or {}).values()):
        if not entry.get("fits_limitations"):
            continue
        perf = entry.get("performance") or {}
        if perf.get("feasible") is False:
            continue
        if int(perf.get("failed_checks") or 0) > 0:
            continue
        traits = dict(perf)
        traits.pop("lengths", None)
        out.append(
            {
                "id": entry.get("cell") or f"cell-{i}",
                "fits": True,
                "traits": traits,
                "reason": entry.get("reason") or "",
                "entry": entry,
            }
        )
    return out


def describe_weights(weights: dict[str, float]) -> str:
    ranked = sorted(weights.items(), key=lambda item: abs(item[1]), reverse=True)
    parts = []
    for name, value in ranked:
        if abs(value) < 0.05:
            continue
        label = name.replace("_", " ")
        if name == "height_variance":
            parts.append(("less " if value < 0 else "more ") + "height variance")
        elif name == "spread":
            parts.append(("less " if value < 0 else "more ") + "spread")
        elif name == "footprint_likeness":
            parts.append(("less " if value < 0 else "more ") + "alike footprints")
        elif name == "street_edge":
            parts.append(("weaker " if value < 0 else "stronger ") + "street edge")
        else:
            parts.append(f"{label} {value:+.2f}")
        if len(parts) == 3:
            break
    if not parts:
        return "No pairwise taste yet. Stated preferences still rank."
    return "Learned taste favors " + ", ".join(parts) + "."


def take_choice(session: Any, text: str) -> dict[str, Any] | None:
    learning = dict((session.constraints.get("explore") or {}).get("learning") or {})
    pair = learning.get("pending_pair")
    if not pair or not pair.get("a") or not pair.get("b"):
        return None
    side = parse_choice(text)
    if side is None:
        return None
    archive = (session.constraints.get("explore") or {}).get("archive") or {}
    schemes = schemes_from_archive(archive)
    by_id = {s["id"]: s for s in schemes}
    if pair["a"] not in by_id or pair["b"] not in by_id:
        return None
    comparisons = list(learning.get("comparisons") or [])
    comparisons.append({"a": pair["a"], "b": pair["b"], "winner": side})
    weights = fit_weights(comparisons, by_id)
    nxt = next_pair(schemes, weights, comparisons)
    note = describe_weights(weights)
    learning = {
        "weights": weights,
        "comparisons": comparisons,
        "pending_pair": nxt,
        "note": note,
    }
    store = dict(session.constraints.get("explore") or {})
    store["learning"] = learning
    session.constraints["explore"] = store
    from .archive import restore_entry

    chosen = by_id[pair[side]]
    other = by_id[pair["b" if side == "a" else "a"]]
    restore_entry(session, chosen["entry"])
    store["kept_cell"] = chosen["id"]
    session.constraints["explore"] = store
    if hasattr(session, "save"):
        session.save()
    reply = (
        f"You chose {chosen['id']} over {other['id']}. {note} "
        "Requirements and caps are unchanged."
    )
    if nxt:
        reply += f" Next pair: {nxt['a']} or {nxt['b']} ({nxt.get('kind')})."
    else:
        reply += " No further informative pair among the legal cells."
    return {"ok": True, "reply": reply, "learning": learning}


def _pair(left: dict[str, Any], right: dict[str, Any], kind: str) -> dict[str, Any]:
    return {"a": left["id"], "b": right["id"], "kind": kind}


def _already(comparisons: list[dict[str, Any]], left: str, right: str) -> bool:
    pair = frozenset({left, right})
    return any(frozenset({c.get("a"), c.get("b")}) == pair for c in comparisons)
