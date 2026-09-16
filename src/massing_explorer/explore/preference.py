"""
Bradley–Terry taste among archive cells that already fit.

A written brief is not a sample. Weights stay empty until the user compares
two legal drawings. "Why B?" is not implemented as extra coordinates.
"""

from __future__ import annotations

import math
import re
from typing import Any

# Soft LEARN axes. Hard gate stays requirements + limitations only.
TRAIT_NAMES = (
    "program_coherence",
    "preference_alignment",
    "performance_efficiency",
    "robustness",
)

# Cap A/B questions; stop earlier when weights stabilize or remaining pairs are uninformative.
MAX_LEARN_COMPARISONS = 8
MIN_LEARN_COMPARISONS = 3
LEARN_INFO_FLOOR = 0.12
WEIGHT_STABLE_L1 = 0.08

# Reject near-twin drawings: need a clear architectural difference to choose.
MIN_PAIR_DIVERSITY = 4.0


def utility(weights: dict[str, float], traits: dict[str, Any]) -> float:
    return sum(float(weights.get(name, 0.0)) * float(traits.get(name, 0.0) or 0.0) for name in TRAIT_NAMES)


def chance_a_beats_b(traits_a: dict[str, Any], traits_b: dict[str, Any], weights: dict[str, float]) -> float:
    gap = utility(weights, traits_a) - utility(weights, traits_b)
    return 1.0 / (1.0 + math.exp(-gap))


def stated_weight(entry: dict[str, Any]) -> float:
    """Pre-LEARN ranking among legal cells — same objective as search/realize."""
    from .saturate import architectural_reward

    return architectural_reward(entry.get("performance") or {}, None)


def taste_weight(entry: dict[str, Any], weights: dict[str, float] | None) -> float:
    if weights and any(abs(float(v)) > 1e-9 for v in weights.values()):
        from .saturate import architectural_reward

        return architectural_reward(entry.get("performance") or {}, weights)
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
    *,
    prev_weights: dict[str, float] | None = None,
) -> dict[str, Any] | None:
    if len(comparisons) >= MAX_LEARN_COMPARISONS:
        return None
    legal = [s for s in schemes if s.get("fits") and s.get("traits")]
    if len(legal) < 2:
        return None

    # Adaptive stop: enough evidence and either stable weights or no informative pair.
    if len(comparisons) >= MIN_LEARN_COMPARISONS:
        best_info = 0.0
        for i, left in enumerate(legal):
            for right in legal[i + 1 :]:
                if _already(comparisons, left["id"], right["id"]):
                    continue
                if _pair_diversity(left, right) < MIN_PAIR_DIVERSITY:
                    continue
                pa = chance_a_beats_b(left["traits"], right["traits"], weights)
                best_info = max(best_info, information(pa))
        stable = False
        if prev_weights is not None:
            drift = sum(
                abs(float(weights.get(n, 0.0) or 0.0) - float(prev_weights.get(n, 0.0) or 0.0))
                for n in TRAIT_NAMES
            )
            stable = drift < WEIGHT_STABLE_L1
        if best_info < LEARN_INFO_FLOOR and (stable or len(comparisons) >= MIN_LEARN_COMPARISONS + 1):
            return None
    compared = {item.get("a") for item in comparisons} | {item.get("b") for item in comparisons}
    unseen = [s for s in legal if s["id"] not in compared]
    seen = [s for s in legal if s["id"] in compared]

    if unseen and seen:
        # Prefer a fresh scheme vs an already-seen one, but only if visibly different.
        best = None
        best_div = -1.0
        for fresh in unseen:
            for other in seen:
                d = _pair_diversity(fresh, other)
                if d < MIN_PAIR_DIVERSITY:
                    continue
                if d > best_div:
                    best_div = d
                    best = _pair(fresh, other, "connecting")
        if best:
            return best

    if len(unseen) >= 2:
        best = None
        best_div = -1.0
        best_score = -1.0
        for i, left in enumerate(unseen):
            for right in unseen[i + 1 :]:
                d = _pair_diversity(left, right)
                if d < MIN_PAIR_DIVERSITY:
                    continue
                pa = chance_a_beats_b(left["traits"], right["traits"], weights)
                score = d + 0.5 * information(pa)
                if d > best_div + 1e-9 or (abs(d - best_div) < 1e-9 and score > best_score):
                    best_div = d
                    best_score = score
                    best = _pair(
                        left,
                        right,
                        "contrast" if d >= MIN_PAIR_DIVERSITY + 2 else "connecting",
                    )
        if best:
            return best

    best = None
    best_score = -1.0
    for i, left in enumerate(legal):
        for right in legal[i + 1 :]:
            if _already(comparisons, left["id"], right["id"]):
                continue
            d = _pair_diversity(left, right)
            if d < MIN_PAIR_DIVERSITY:
                continue
            pa = chance_a_beats_b(left["traits"], right["traits"], weights)
            # Weight diversity more than BT ambiguity so A/B is chooseable.
            score = d * (1.0 + 0.25 * information(pa))
            if score > best_score:
                best_score = score
                best = _pair(left, right, "ambiguous" if information(pa) >= 0.2 else "contrast")
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
    labels = {
        "program_coherence": "program coherence",
        "preference_alignment": "preference alignment",
        "performance_efficiency": "performance efficiency",
        "robustness": "robustness",
    }
    for name, value in ranked:
        if abs(value) < 0.05:
            continue
        label = labels.get(name, name.replace("_", " "))
        parts.append(("less " if value < 0 else "more ") + label)
        if len(parts) == 3:
            break
    if not parts:
        return "No pairwise taste yet. Stated preferences still rank."
    return "Learned taste favors " + ", ".join(parts) + "."


def take_choice(session: Any, text: str) -> dict[str, Any] | None:
    side = parse_choice(text)
    if side is None:
        return None
    return apply_choice(session, side)


def apply_choice(session: Any, side: str) -> dict[str, Any] | None:
    """Record A/B winner ('a' or 'b'), refit LEARN weights, restore the winner."""
    side = str(side or "").strip().lower()
    if side not in {"a", "b"}:
        return None
    learning = dict((session.constraints.get("explore") or {}).get("learning") or {})
    pair = learning.get("pending_pair")
    if not pair or not pair.get("a") or not pair.get("b"):
        return None
    archive = (session.constraints.get("explore") or {}).get("archive") or {}
    schemes = schemes_from_archive(archive)
    by_id = {s["id"]: s for s in schemes}
    if pair["a"] not in by_id or pair["b"] not in by_id:
        return None
    comparisons = list(learning.get("comparisons") or [])
    comparisons.append({"a": pair["a"], "b": pair["b"], "winner": side})
    prev_weights = dict(learning.get("weights") or {})
    weights = fit_weights(comparisons, by_id)
    # Provisional: if the winner's traits barely move the fit, note a missing feature.
    missing_feature = _missing_feature_note(
        by_id[pair["a"]], by_id[pair["b"]], side, prev_weights, weights
    )
    nxt = next_pair(schemes, weights, comparisons, prev_weights=prev_weights)
    note = describe_weights(weights)
    if missing_feature:
        note = f"{note} {missing_feature}"
    if nxt is None:
        note = (
            f"{note} LEARN complete ({len(comparisons)}/{MAX_LEARN_COMPARISONS} comparisons)."
        )
    learning = {
        "weights": weights,
        "comparisons": comparisons,
        "pending_pair": nxt,
        "note": note,
        "max_comparisons": MAX_LEARN_COMPARISONS,
        "provisional": True,
        "missing_features": list(learning.get("missing_features") or [])
        + ([missing_feature] if missing_feature else []),
    }
    store = dict(session.constraints.get("explore") or {})
    store["learning"] = learning
    session.constraints["explore"] = store
    from .archive import restore_entry

    chosen = by_id[pair[side]]
    restore_entry(session, chosen["entry"])
    store["kept_cell"] = chosen["id"]
    session.constraints["explore"] = store

    # Close the loop: a short targeted refine with the new weights, keep alternatives.
    refine_report = _learn_targeted_refine(session, archive, weights)
    if refine_report.get("ran"):
        store["refine"] = refine_report
        learning["refine_after_choice"] = {
            "tuned": refine_report.get("tuned"),
            "improved": refine_report.get("improved"),
            "note": refine_report.get("note"),
        }
        store["learning"] = learning
        session.constraints["explore"] = store

    if hasattr(session, "save"):
        session.save()
    reply = (
        f"You chose {'A' if side == 'a' else 'B'} "
        f"({chosen['id'][:48]}…) over the other. {note} "
        "Requirements and caps are unchanged."
    )
    if refine_report.get("ran"):
        reply += f" Targeted refine: {refine_report.get('note') or 'ran'}."
    if nxt:
        remaining = MAX_LEARN_COMPARISONS - len(comparisons)
        reply += f" Next pair ready ({nxt.get('kind') or 'pair'}; {remaining} left)."
    else:
        reply += " No further A/B questions."
    return {
        "ok": True,
        "reply": reply,
        "learning": learning,
        "winner": side,
        "kept_cell": chosen["id"],
        "next_pair": nxt,
        "refine": refine_report,
    }


def _missing_feature_note(
    scheme_a: dict[str, Any],
    scheme_b: dict[str, Any],
    winner: str,
    prev: dict[str, float],
    nxt: dict[str, float],
) -> str | None:
    """When the choice barely moves axis weights, record that taste may be off-axis."""
    drift = sum(abs(float(nxt.get(k, 0) or 0) - float(prev.get(k, 0) or 0)) for k in TRAIT_NAMES)
    if drift >= 0.05:
        return None
    win = scheme_a if winner == "a" else scheme_b
    lose = scheme_b if winner == "a" else scheme_a
    # Surface a concrete geometric difference the axes may not capture.
    wa = _story_signature(win.get("entry") or win)
    la = _story_signature(lose.get("entry") or lose)
    if wa and la and wa != la:
        return (
            "Recorded off-axis note: preferred story pattern "
            f"{wa} over {la} (weights barely moved — taste may need a new feature)."
        )
    pa = (win.get("entry") or win).get("partition") or win.get("partition")
    pb = (lose.get("entry") or lose).get("partition") or lose.get("partition")
    if pa and pb and pa != pb:
        return (
            "Recorded off-axis note: preferred a different program organization "
            "(weights barely moved — taste may need a new feature)."
        )
    return (
        "Recorded off-axis note: choice not well explained by current soft axes "
        "(weights barely moved)."
    )


def _learn_targeted_refine(
    session: Any,
    archive: dict[str, Any],
    weights: dict[str, float],
) -> dict[str, Any]:
    """Small post-choice refine; keeps other legal cells in the archive."""
    try:
        from .controller import _refine
        from .saturate import REFINE_CAP

        # Temporarily shrink refine budget for a light loop close.
        explore = dict(session.constraints.get("explore") or {})
        budget = dict(explore.get("budget") or {})
        prev = budget.get("refine")
        budget["refine"] = min(4, int(prev or REFINE_CAP))
        explore["budget"] = budget
        # Ensure learning weights are what refine reads.
        learn = dict(explore.get("learning") or {})
        learn["weights"] = weights
        explore["learning"] = learn
        session.constraints["explore"] = explore
        report = _refine(session, archive)
        if prev is not None:
            budget["refine"] = prev
        else:
            budget.pop("refine", None)
        explore["budget"] = budget
        session.constraints["explore"] = explore
        return report if isinstance(report, dict) else {"ran": False}
    except Exception as exc:
        return {"ran": False, "reason": str(exc)}



def _pair(left: dict[str, Any], right: dict[str, Any], kind: str) -> dict[str, Any]:
    return {
        "a": left["id"],
        "b": right["id"],
        "kind": kind,
        "diversity": round(_pair_diversity(left, right), 3),
    }


def _story_signature(entry: dict[str, Any]) -> tuple[int, ...]:
    stories = entry.get("stories") or {}
    if stories:
        return tuple(sorted(int(v) for v in stories.values()))
    plates = entry.get("plates") or []
    if plates:
        return tuple(sorted(int(p.get("stories") or 0) for p in plates))
    geom = ((entry.get("strategy") or {}).get("G") or {}).get("stories") or {}
    if geom:
        return tuple(sorted(int(v) for v in geom.values()))
    return ()


def _silhouette(entry: dict[str, Any]) -> list[tuple[int, int, int]]:
    """Coarse visual fingerprint: (stories, width_bin, length_bin) per mass."""
    plates = list(entry.get("plates") or [])
    rows: list[tuple[int, int, int]] = []
    if plates:
        for p in plates:
            try:
                stories = int(p.get("stories") or 0)
                width = int(round(float(p.get("width_ft") or 0) / 10.0) * 10)
                length = int(round(float(p.get("length_ft") or 0) / 20.0) * 20)
            except (TypeError, ValueError):
                continue
            if width > 0 and length > 0:
                rows.append((stories, width, length))
    else:
        snap = entry.get("snapshot") or {}
        stories = entry.get("stories") or snap.get("stories") or {}
        widths = snap.get("widths") or {}
        lengths = list((entry.get("performance") or {}).get("lengths") or [])
        for i, mass in enumerate(snap.get("masses") or []):
            if not isinstance(mass, dict):
                continue
            mid = str(mass.get("id") or "")
            try:
                w = int(round(float(widths.get(mid) or 0) / 10.0) * 10)
                st = int(stories.get(mid) or mass.get("story_count") or 0)
                ln = int(round(float(lengths[i] if i < len(lengths) else 0) / 20.0) * 20)
            except (TypeError, ValueError):
                continue
            if w > 0 and ln > 0:
                rows.append((st, w, ln))
    rows.sort()
    return rows


def _probe_l1(entry_a: dict[str, Any], entry_b: dict[str, Any]) -> float:
    from .axes import encode_strategy

    def prep(entry: dict[str, Any]) -> dict[str, Any]:
        strategy = dict(entry.get("strategy") or {})
        geom = dict(strategy.get("G") or {})
        if not geom.get("stories") and entry.get("stories"):
            geom["stories"] = dict(entry["stories"])
            strategy["G"] = geom
        return strategy

    va = encode_strategy(prep(entry_a))
    vb = encode_strategy(prep(entry_b))
    return sum(abs(a - b) for a, b in zip(va, vb))


def _pair_diversity(left: dict[str, Any], right: dict[str, Any]) -> float:
    """How different two legal archive schemes are (must be chooseable on sight)."""
    ea = left.get("entry") or {}
    eb = right.get("entry") or {}
    score = 0.0
    if ea.get("partition") != eb.get("partition"):
        score += 3.0
    ta = ((ea.get("strategy") or {}).get("T") or {}).get("kind")
    tb = ((eb.get("strategy") or {}).get("T") or {}).get("kind")
    if ta != tb:
        score += 3.0
    ga = ((ea.get("strategy") or {}).get("G") or {}).get("envelope")
    gb = ((eb.get("strategy") or {}).get("G") or {}).get("envelope")
    if ga != gb:
        score += 1.5
    la = ((ea.get("strategy") or {}).get("G") or {}).get("loading")
    lb = ((eb.get("strategy") or {}).get("G") or {}).get("loading")
    if la != lb:
        score += 1.5

    sa = _story_signature(ea)
    sb = _story_signature(eb)
    if sa != sb:
        score += 2.5
        if len(sa) != len(sb):
            score += 1.5
        elif sa and sb:
            n = min(len(sa), len(sb))
            score += min(2.0, 0.5 * sum(abs(sa[i] - sb[i]) for i in range(n)))

    sil_a = _silhouette(ea)
    sil_b = _silhouette(eb)
    if sil_a and sil_b and sil_a != sil_b:
        n = max(len(sil_a), len(sil_b))
        matched = sum(1 for i in range(min(len(sil_a), len(sil_b))) if sil_a[i] == sil_b[i])
        score += 2.0 * (1.0 - matched / n)
        tot_a = sum(p[2] for p in sil_a) or 1
        tot_b = sum(p[2] for p in sil_b) or 1
        rel = abs(tot_a - tot_b) / max(tot_a, tot_b)
        score += min(2.0, 3.0 * rel)

    probe = _probe_l1(ea, eb)
    score += min(3.0, probe)

    traits_a = left.get("traits") or {}
    traits_b = right.get("traits") or {}
    for name in TRAIT_NAMES:
        score += abs(float(traits_a.get(name, 0.0) or 0.0) - float(traits_b.get(name, 0.0) or 0.0))
    return score


def _already(comparisons: list[dict[str, Any]], left: str, right: str) -> bool:
    pair = frozenset({left, right})
    return any(frozenset({c.get("a"), c.get("b")}) == pair for c in comparisons)
