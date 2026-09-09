"""
Discrete sample space for an already classified brief.

The coordinates are the levers the brief left open. A length cap is not a
coordinate. Requirements are not sampled. A point is legal only after the
solver says it fits; an illegal evaluation is an attempt, not coverage.

Stage one proposes from a designed subset until the remaining design cannot
open a new cell. Stage three treats each legal cell as a particle and
allocates local moves by weight, with a floor so a light elite is not deleted.
"""

from __future__ import annotations

import itertools
from typing import Any

# Full factorial only while the designed interaction set stays small.
MAX_INTERACTION = 12


def story_band(stories: int) -> str:
    if int(stories) <= 1:
        return "low"
    if int(stories) == 2:
        return "mid"
    return "high"


def shape_family(level: Any) -> str:
    if level in {"box", "cube"}:
        return "square"
    return "bar"


def norm_level(value: Any) -> Any:
    if value in {None, "", "bar", "-"}:
        return "bar"
    if value is True or value == "true":
        return True
    if value is False or value == "false":
        return False
    return value


def cell_key(point: dict[str, Any], factors: list[dict[str, Any]]) -> str:
    """Typology cell predicted from levers, before geometry is drawn."""
    loading = norm_level(point.get("loading", "double"))
    parts = [f"loading:{loading}"]
    for factor in factors:
        if factor["kind"] == "stories":
            parts.append(f"{factor['id']}:{story_band(int(point[factor['id']]))}")
        elif factor["kind"] == "shape":
            parts.append(f"{factor['id']}:{shape_family(point.get(factor['id']))}")
        elif factor["kind"] == "ratio":
            parts.append(f"{factor['id']}:{norm_level(point.get(factor['id']))}")
    return "|".join(parts)


def point_key(point: dict[str, Any], factors: list[dict[str, Any]]) -> tuple[Any, ...]:
    return tuple(norm_level(point.get(factor["id"])) for factor in factors)


def designed_points(
    factors: list[dict[str, Any]],
    baseline: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    A covering design, not a walk toward a cap.

    Margins first: every legal level of every open factor, others held at the
    stated preference. If the story factors are few, add their combinations so
    two story levers are not completely stuck together.
    """
    points = [dict(baseline)]
    for factor in factors:
        current = norm_level(baseline.get(factor["id"]))
        for level in factor["levels"]:
            if norm_level(level) == current:
                continue
            point = dict(baseline)
            point[factor["id"]] = level
            points.append(point)

    story = [f for f in factors if f["kind"] == "stories"]
    product = 1
    for factor in story:
        product *= max(1, len(factor["levels"]))
    if story and 1 < product <= MAX_INTERACTION:
        for combo in itertools.product(*[f["levels"] for f in story]):
            point = dict(baseline)
            for factor, level in zip(story, combo):
                point[factor["id"]] = level
            points.append(point)
    return _dedupe(points, factors)


def design_kind(factors: list[dict[str, Any]]) -> str:
    """What the designed set actually covers. A truncated product is not a full joint sample."""
    story = [f for f in factors if f["kind"] == "stories"]
    product = 1
    for factor in story:
        product *= max(1, len(factor["levels"]))
    if len(story) > 1 and product > MAX_INTERACTION:
        return "margins_only"
    if len(story) > 1:
        return "margins_and_story_interactions"
    return "margins"


def margins_missing(
    factors: list[dict[str, Any]],
    attempted: list[dict[str, Any]],
) -> list[tuple[str, Any]]:
    seen: dict[str, set[Any]] = {f["id"]: set() for f in factors}
    for point in attempted:
        for factor in factors:
            if factor["id"] in point:
                seen[factor["id"]].add(norm_level(point[factor["id"]]))
    missing = []
    for factor in factors:
        for level in factor["levels"]:
            if norm_level(level) not in seen[factor["id"]]:
                missing.append((factor["id"], level))
    return missing


def next_design_point(
    factors: list[dict[str, Any]],
    design: list[dict[str, Any]],
    attempted: list[dict[str, Any]],
    legal_cells: set[str],
    failed_cells: set[str],
) -> dict[str, Any] | None:
    """
    Next point in the designed space.

    Finish missing factor levels first. Then take an untried point whose
    predicted cell is not already legal and not already shown infeasible.
    None means the remaining design cannot open a new cell.
    """
    tried = {point_key(point, factors) for point in attempted}
    missing = {
        (factor_id, norm_level(level)) for factor_id, level in margins_missing(factors, attempted)
    }

    def unseen(point: dict[str, Any]) -> bool:
        if point_key(point, factors) in tried:
            return False
        cell = cell_key(point, factors)
        return cell not in legal_cells and cell not in failed_cells

    for point in design:
        if point_key(point, factors) in tried:
            continue
        if any(norm_level(point.get(factor_id)) == level for factor_id, level in missing):
            return point
    for point in design:
        if unseen(point):
            return point
    return None


def coverage_status(
    factors: list[dict[str, Any]],
    design: list[dict[str, Any]],
    attempted: list[dict[str, Any]],
    legal_cells: set[str],
    failed_cells: set[str],
    at_cap: bool,
) -> dict[str, Any]:
    missing = margins_missing(factors, attempted)
    remaining = next_design_point(factors, design, attempted, legal_cells, failed_cells)
    if at_cap and (missing or remaining is not None):
        stopped = "safety_cap"
        complete = False
    elif missing or remaining is not None:
        stopped = None
        complete = False
    else:
        stopped = "design_exhausted"
        complete = True
    kind = design_kind(factors)
    return {
        "designed": len(design),
        "design": kind,
        "attempted": len(attempted),
        "legal_cells": sorted(legal_cells),
        "failed_cells": sorted(failed_cells),
        "margins_missing": [{"factor": factor_id, "level": level} for factor_id, level in missing],
        "map_complete": complete,
        "stopped": stopped,
        "note": _note(stopped, len(legal_cells), len(design), len(missing), kind),
    }


def local_move_priority(
    seed_stories: dict[str, int],
    seed_shapes: dict[str, Any],
    candidate: dict[str, Any],
    weights: dict[str, float],
) -> float:
    """
    How much a one-step lever edit moves a trait the weights already favor.

    This is a proposal order, not a new dimension and not a score from the
    drawing. Empty weights keep the existing neighbor order.
    """
    if not weights or not any(abs(float(v)) > 1e-9 for v in weights.values()):
        return 0.0
    score = 0.0
    stories = {str(k): int(v) for k, v in (candidate.get("stories") or seed_stories).items()}
    before = [float(v) for v in seed_stories.values()]
    after = [float(stories.get(k, seed_stories[k])) for k in seed_stories]
    height = float(weights.get("height_variance") or 0.0)
    if height and before and after:
        score += height * (_spread_of(after) - _spread_of(before))
    likeness = float(weights.get("footprint_likeness") or 0.0)
    if likeness:
        score += likeness * (
            _family_likeness(candidate.get("shapes") or seed_shapes)
            - _family_likeness(seed_shapes)
        )
    return score


def _spread_of(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5


def _family_likeness(shapes: dict[str, Any]) -> float:
    families = ["square" if shape in {"box", "cube"} else "bar" for shape in shapes.values()]
    if len(families) < 2:
        return 1.0
    mode = max(set(families), key=families.count)
    return families.count(mode) / len(families)


def particle_weights(particles: list[dict[str, Any]]) -> list[float]:
    """Target weight on legal particles only. Infeasible weight is zero."""
    raw = []
    for particle in particles:
        if not particle.get("fits"):
            raw.append(0.0)
            continue
        weight = particle.get("weight")
        raw.append(max(0.0, float(weight if weight is not None else 0.0)))
    return raw


def effective_sample_size(weights: list[float]) -> float:
    total = sum(max(0.0, float(w)) for w in weights)
    if total <= 0:
        return 0.0
    return (total * total) / sum(max(0.0, float(w)) ** 2 for w in weights)


def allocate_local_tries(particles: list[dict[str, Any]], budget: int, floor: int = 1) -> dict[str, int]:
    """
    Residual allocation.

    Every kept legal particle gets `floor` tries. The remainder is drawn in
    proportion to target weight. A light elite is not given zero.
    """
    legal = [p for p in particles if p.get("fits") and p.get("id")]
    if not legal or budget <= 0:
        return {}
    floor = max(0, int(floor))
    reserved = min(budget, floor * len(legal))
    each = reserved // len(legal) if legal else 0
    counts = {p["id"]: each for p in legal}
    left = budget - sum(counts.values())
    weights = particle_weights(legal)
    total = sum(weights)
    if total <= 0 or left <= 0:
        return counts
    order = sorted(range(len(legal)), key=lambda i: weights[i], reverse=True)
    # Systematic step across the weight line, then dump any remainder on the heaviest.
    step = total / left
    cursor = step / 2.0
    running = 0.0
    index = 0
    for _ in range(left):
        while index < len(order) - 1 and running + weights[order[index]] <= cursor:
            running += weights[order[index]]
            index += 1
        counts[legal[order[index]]["id"]] += 1
        cursor += step
    return counts


def _dedupe(points: list[dict[str, Any]], factors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    out = []
    for point in points:
        key = point_key(point, factors)
        if key in seen:
            continue
        seen.add(key)
        out.append(point)
    return out


def _note(stopped: str | None, legal: int, designed: int, missing_margins: int, kind: str) -> str:
    scope = {
        "margins": "one-factor margins",
        "margins_and_story_interactions": "margins and story interactions",
        "margins_only": "one-factor margins only; the story product was too large to treat as fully joint",
    }.get(kind, kind)
    if stopped == "safety_cap":
        return (
            f"Safety cap hit before the designed space was covered "
            f"({legal} legal cells, {missing_margins} factor levels still untried, design={scope})."
        )
    if stopped == "design_exhausted":
        extra = ""
        if kind == "margins_only":
            extra = " This is not a full joint sample of the story levers."
        return (
            f"The remaining design cannot open a new cell "
            f"({legal} legal cells in a design of {designed}: {scope}). "
            "Further samples would repeat a cell already legal or already infeasible."
            + extra
        )
    return f"Designed space still has uncovered cells ({legal} legal so far, design={scope})."
