"""
Bayesian optimization as an evaluation-budget manager.

A Gaussian process predicts the reward of an unevaluated *strategy*, then
Expected Improvement picks which typed action to run through the engine next.
It is not an architectural generator. It does not sit on feet. Courtyard is
not a candidate. search.py's weighted sum is not the objective.
"""

from __future__ import annotations

import math
from typing import Any

from . import archive as archive_mod
from .actions import apply_action
from .mcts import action_key, catalog_actions
from .performance import measure
from .strategy import cell_key, read_strategy

BO_CAP = 4
LENGTHSCALE = 0.75
SIGNAL = 0.6
NOISE = 0.08


def encode_strategy(strategy: dict[str, Any] | None) -> list[float]:
    """Numeric sketch of S = (P, T, V, G). No widths, no invented feet."""
    strategy = strategy or {}
    program = strategy.get("P") or {}
    topo = strategy.get("T") or {}
    vertical = strategy.get("V") or {}
    geom = strategy.get("G") or {}
    stories = [float(v) for v in (geom.get("stories") or {}).values()]
    mean_st = sum(stories) / max(len(stories), 1)
    spread_st = 0.0
    if len(stories) > 1:
        spread_st = (sum((s - mean_st) ** 2 for s in stories) / len(stories)) ** 0.5
    return [
        float(program.get("mass_count") or 0) / 8.0,
        1.0 if program.get("locked") else 0.0,
        1.0 if topo.get("kind") == "paired_bars" else 0.0,
        1.0 if topo.get("l_leftover") else 0.0,
        1.0 if (geom.get("loading") or "double") == "double" else 0.0,
        mean_st / 5.0,
        min(1.0, spread_st / 3.0),
        min(1.0, float(len(vertical.get("pins") or {})) / 6.0),
        min(1.0, float(len(program.get("partition") or {})) / 8.0),
    ]


def evaluation_reward(performance: dict[str, Any]) -> float:
    """Feasibility first. Not search.py quality."""
    if not performance.get("fits_limitations"):
        return 0.0
    feasible = 1.0 if performance.get("feasible") else 0.4
    pref = 1.0 - min(1.0, max(0.0, float(performance.get("preference_distance") or 0.0)))
    novelty = min(1.0, max(0.0, float(performance.get("novelty") or 0.0)))
    return round(0.55 * feasible + 0.30 * pref + 0.15 * novelty, 4)


def expected_improvement(mean: float, std: float, best: float) -> float:
    """Standard EI for maximizing reward. High where the GP is unsure and promising."""
    if std <= 1e-9:
        return 0.0
    z = (mean - best) / std
    return (mean - best) * _phi(z) + std * _phi_pdf(z)


class GaussianProcess:
    """RBF GP. Small n; this is a budget manager, not a simulator."""

    def __init__(self, lengthscale: float = LENGTHSCALE, signal: float = SIGNAL, noise: float = NOISE):
        self.lengthscale = float(lengthscale)
        self.signal = float(signal)
        self.noise = float(noise)
        self.X: list[list[float]] = []
        self.y: list[float] = []
        self._L: list[list[float]] | None = None
        self._alpha: list[float] | None = None
        self._y_mean = 0.0

    def fit(self, X: list[list[float]], y: list[float]) -> None:
        self.X = [list(map(float, row)) for row in X]
        self.y = [float(v) for v in y]
        n = len(self.y)
        if n == 0:
            self._L = None
            return
        self._y_mean = sum(self.y) / n
        centered = [v - self._y_mean for v in self.y]
        k = [[self._k(self.X[i], self.X[j]) for j in range(n)] for i in range(n)]
        for i in range(n):
            k[i][i] += self.noise ** 2 + 1e-8
        self._L = _cholesky(k)
        self._alpha = _chol_solve(self._L, centered)

    def predict(self, z: list[float]) -> tuple[float, float]:
        if not self._L or not self.X:
            return 0.0, 1.0
        k_star = [self._k(x, z) for x in self.X]
        mean = self._y_mean + _dot(k_star, self._alpha or [])
        v = _forward_sub(self._L, k_star)
        var = self._k(z, z) - _dot(v, v)
        return mean, math.sqrt(max(var, 1e-12))

    def _k(self, a: list[float], b: list[float]) -> float:
        dist = sum((x - y) ** 2 for x, y in zip(a, b))
        return self.signal * math.exp(-0.5 * dist / (self.lengthscale ** 2))


def run_bayes(session: Any, archive: dict[str, Any] | None = None, budget: int = BO_CAP) -> dict[str, Any]:
    """Spend a small evaluation budget on high-EI legal actions. Restore the origin after."""
    archive = archive if archive is not None else archive_mod.load_archive(session)
    if not session.masses:
        return _empty("No masses, so Bayesian optimization did not run.")
    origin = archive_mod.capture(session)
    observed = _observations(archive)
    gp = GaussianProcess()
    if len(observed) >= 2:
        gp.fit([row[0] for row in observed], [row[1] for row in observed])
    candidates = _candidates(session, origin)
    picked: list[dict[str, Any]] = []
    ranked = _rank(gp, observed, candidates)
    spent = 0
    cap = max(1, min(int(budget), BO_CAP))
    seen_cells = set((archive.get("cells") or {}).keys())
    for item in ranked:
        if spent >= cap:
            break
        action = item["action"]
        archive_mod.restore_snapshot(session, origin)
        try:
            out = apply_action(session, action)
        except Exception as exc:
            picked.append({"op": _label(action), "kind": "illegal", "ei": item["ei"], "reason": str(exc)})
            continue
        if not out.get("ok"):
            kind = "unsupported" if "cannot realize" in str(out.get("reason") or "").lower() else "illegal"
            picked.append({"op": _label(action), "kind": kind, "ei": item["ei"], "reason": out.get("reason")})
            continue
        if getattr(session, "program", None) is None:
            picked.append({"op": _label(action), "kind": "applied", "ei": item["ei"], "reward": 0.6})
            spent += 1
            continue
        from ..solver import solve_massing_study

        result = solve_massing_study(session)
        performance = measure(result, session, archive=archive)
        key = cell_key(session)
        if key in seen_cells:
            picked.append({"op": _label(action), "kind": "duplicate", "ei": item["ei"], "reason": "Cell already in the archive."})
            continue
        reward = evaluation_reward(performance)
        archive_mod.insert(archive, session, result, performance, reason=f"bayes {_label(action)}")
        seen_cells.add(key)
        observed.append((encode_strategy(read_strategy(session)), reward))
        if len(observed) >= 2:
            gp.fit([row[0] for row in observed], [row[1] for row in observed])
        picked.append(
            {
                "op": _label(action),
                "kind": "applied",
                "ei": round(item["ei"], 4),
                "reward": reward,
                "mean": round(item["mean"], 4),
                "std": round(item["std"], 4),
            }
        )
        spent += 1
    archive_mod.restore_snapshot(session, origin)
    next_op = next((p["op"] for p in picked if p.get("kind") == "applied"), None)
    return {
        "ran": True,
        "budget": cap,
        "spent": spent,
        "observed": len(observed),
        "candidates": [
            {
                "op": c["label"],
                "ei": round(c["ei"], 4),
                "mean": round(c["mean"], 4),
                "std": round(c["std"], 4),
            }
            for c in ranked[:8]
        ],
        "picked": picked,
        "next": next_op,
        "note": (
            f"Bayesian optimization spent {spent} of {cap} evaluations "
            "(GP + expected improvement). It is a budget manager, not a generator. "
            "It does not sit on feet."
        ),
    }


def _candidates(session: Any, origin: dict[str, Any]) -> list[dict[str, Any]]:
    actions = [a for a in catalog_actions(session, include_unsupported=False) if a.get("op") != "COURTYARD"]
    out = []
    seen: set[str] = set()
    for action in actions:
        key = action_key(action)
        if key in seen:
            continue
        seen.add(key)
        archive_mod.restore_snapshot(session, origin)
        try:
            applied = apply_action(session, action)
        except Exception:
            continue
        if not applied.get("ok"):
            continue
        feat = encode_strategy(read_strategy(session))
        out.append({"action": action, "x": feat, "label": _label(action)})
    archive_mod.restore_snapshot(session, origin)
    return out


def _observations(archive: dict[str, Any]) -> list[tuple[list[float], float]]:
    rows = []
    for entry in (archive.get("cells") or {}).values():
        rows.append((encode_strategy(entry.get("strategy")), evaluation_reward(entry.get("performance") or {})))
    return rows


def _rank(gp: GaussianProcess, observed: list[tuple[list[float], float]], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best = max((row[1] for row in observed), default=0.0)
    known = [row[0] for row in observed]
    ranked = []
    for item in candidates:
        if any(_close(item["x"], x) for x in known):
            continue
        if gp._L:
            mean, std = gp.predict(item["x"])
        else:
            mean, std = best, 1.0
        ranked.append(
            {
                "action": item["action"],
                "label": item["label"],
                "ei": expected_improvement(mean, std, best),
                "mean": mean,
                "std": std,
            }
        )
    ranked.sort(key=lambda row: row["ei"], reverse=True)
    return ranked


def _close(a: list[float], b: list[float], tol: float = 1e-6) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def _label(action: dict[str, Any]) -> str:
    op = str(action.get("op") or "ACTION")
    extra = action.get("mass") or action.get("loading") or ""
    if action.get("stories") is not None:
        extra = f"{action.get('mass')} {action.get('stories')}fl"
    if action.get("programs"):
        extra = ",".join(str(x) for x in action["programs"][:2])
    if action.get("masses"):
        extra = "+".join(str(x) for x in action["masses"][:2])
    return f"{op} {extra}".strip()


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _phi_pdf(z: float) -> float:
    return math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _cholesky(a: list[list[float]]) -> list[list[float]]:
    n = len(a)
    L = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = sum(L[i][k] * L[j][k] for k in range(j))
            if i == j:
                L[i][j] = math.sqrt(max(a[i][i] - s, 1e-12))
            else:
                L[i][j] = (a[i][j] - s) / L[j][j]
    return L


def _forward_sub(L: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    y = [0.0] * n
    for i in range(n):
        y[i] = (b[i] - sum(L[i][j] * y[j] for j in range(i))) / L[i][i]
    return y


def _chol_solve(L: list[list[float]], b: list[float]) -> list[float]:
    y = _forward_sub(L, b)
    n = len(b)
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (y[i] - sum(L[j][i] * x[j] for j in range(i + 1, n))) / L[i][i]
    return x


def _empty(note: str) -> dict[str, Any]:
    return {
        "ran": False,
        "budget": 0,
        "spent": 0,
        "observed": 0,
        "candidates": [],
        "picked": [],
        "next": None,
        "note": note,
    }
