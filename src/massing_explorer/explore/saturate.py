"""
Shared evaluation-budget policy: hard caps with saturation stop.

COVER / MCTS / BO / REFINE may spend up to a cap, but they stop earlier when
new evaluations stop adding archive cells, feature-space diversity, or a
better reward. Session `explore_budget` overrides production defaults for tests.
"""

from __future__ import annotations

import math
from typing import Any

# Production defaults. Tests should set session.constraints["explore_budget"].
MCTS_SIMS = 64
MCTS_DEPTH = 4
MCTS_ROOTS = 4
MCTS_MIN_SIMS = 16
BO_CAP = 12
BO_MIN = 4
REFINE_CAP = 12
REFINE_MIN = 4
FEATURE_NOVEL_DIST = 0.12


class Saturation:
    """Stop after `window` idle steps once `min_steps` have run."""

    def __init__(self, *, window: int = 6, min_steps: int = 4):
        self.window = max(1, int(window))
        self.min_steps = max(1, int(min_steps))
        self.steps = 0
        self.idle = 0

    def observe(self, gained: bool) -> None:
        self.steps += 1
        if gained:
            self.idle = 0
        else:
            self.idle += 1

    def stop(self) -> bool:
        return self.steps >= self.min_steps and self.idle >= self.window


def read_explore_budget(session: Any) -> dict[str, int]:
    cfg = dict((getattr(session, "constraints", None) or {}).get("explore_budget") or {})
    return {
        "mcts_sims": int(cfg.get("mcts_sims", MCTS_SIMS)),
        "mcts_depth": int(cfg.get("mcts_depth", MCTS_DEPTH)),
        "mcts_roots": int(cfg.get("mcts_roots", MCTS_ROOTS)),
        "bo": int(cfg.get("bo", BO_CAP)),
        "refine": int(cfg.get("refine", REFINE_CAP)),
    }


def feature_distance(a: list[float], b: list[float]) -> float:
    n = max(len(a), len(b), 1)
    aa = list(a) + [0.0] * (n - len(a))
    bb = list(b) + [0.0] * (n - len(b))
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(aa, bb)))


def feature_is_novel(
    encoding: list[float],
    known: list[list[float]],
    *,
    min_dist: float = FEATURE_NOVEL_DIST,
) -> bool:
    if not known:
        return True
    return all(feature_distance(encoding, row) >= min_dist for row in known)


def encodings_from_archive(archive: dict[str, Any]) -> list[list[float]]:
    from .bayes import encode_strategy

    out = []
    for entry in (archive.get("cells") or {}).values():
        out.append(encode_strategy(entry.get("strategy")))
    return out


def search_reward(performance: dict[str, Any] | None, weights: dict[str, float] | None = None) -> float:
    """Feasibility first. LEARN taste steers among legal schemes; it cannot rescue a miss."""
    performance = performance or {}
    if not performance.get("fits_limitations"):
        return 0.0
    feasible = 1.0 if performance.get("feasible") else 0.4
    coherence = float(performance.get("program_coherence") or 0.0)
    pref = float(
        performance.get("preference_alignment")
        if performance.get("preference_alignment") is not None
        else (1.0 - min(1.0, max(0.0, float(performance.get("preference_distance") or 0.0))))
    )
    efficiency = float(performance.get("performance_efficiency") or 0.0)
    robust = float(performance.get("robustness") or 0.0)
    novelty = min(1.0, max(0.0, float(performance.get("novelty") or 0.0)))
    awkward = min(1.0, max(0.0, float(performance.get("awkward_splits") or 0.0)))
    awkward_factor = max(0.05, (1.0 - awkward) ** 2)
    tasted = bool(weights and any(abs(float(v)) > 1e-9 for v in weights.values()))
    if not tasted:
        return round(
            (
                0.45 * feasible
                + 0.20 * pref
                + 0.15 * coherence
                + 0.10 * efficiency
                + 0.05 * robust
                + 0.05 * novelty
            )
            * awkward_factor,
            4,
        )
    from .preference import utility

    taste = 1.0 / (1.0 + math.exp(-utility(weights, performance)))
    return round(
        (
            0.40 * feasible
            + 0.15 * pref
            + 0.10 * coherence
            + 0.08 * efficiency
            + 0.07 * robust
            + 0.05 * novelty
            + 0.15 * taste
        )
        * awkward_factor,
        4,
    )
