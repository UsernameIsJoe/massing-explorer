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
from .axes import PROBE_AXIS_NAMES, encode_named, encode_strategy  # noqa: F401
from .mcts import action_key, catalog_actions, cover_roots
from .saturate import BO_CAP, BO_MIN, Saturation, encodings_from_archive, feature_is_novel, read_explore_budget, search_reward
from .strategy import cell_key, read_strategy

# Shared isotropic ℓ for now. ARD can replace later without renaming axes.
LENGTHSCALE = 0.75
SIGNAL = 0.6
NOISE = 0.08


def evaluation_reward(performance: dict[str, Any], weights: dict[str, float] | None = None) -> float:
    """Feasibility first. LEARN taste steers; it cannot rescue a miss. Not search.py quality."""
    return search_reward(performance, weights)


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


def run_bayes(
    session: Any,
    archive: dict[str, Any] | None = None,
    budget: int | None = None,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Sequential EI proposals from COVER-informed candidates. Restore origin after."""
    archive = archive if archive is not None else archive_mod.load_archive(session)
    if not session.masses:
        return _empty("No masses, so Bayesian optimization did not run.")
    origin = archive_mod.capture(session)
    cfg = read_explore_budget(session)
    cap = int(budget) if budget is not None else int(cfg["bo"])
    cap = max(1, min(cap, BO_CAP))
    observed = _observations(archive, weights)
    gp = GaussianProcess()
    if len(observed) >= 2:
        gp.fit([row[0] for row in observed], [row[1] for row in observed])
    candidates = _candidates(session, archive, origin, weights=weights)
    picked: list[dict[str, Any]] = []
    spent = 0
    seen_cells = set((archive.get("cells") or {}).keys())
    known_feat = encodings_from_archive(archive)
    sat = Saturation(window=3, min_steps=min(BO_MIN, cap))
    best = max((row[1] for row in observed), default=0.0)

    while spent < cap:
        ranked = _rank(gp, observed, candidates, used={p.get("key") for p in picked})
        if not ranked:
            break
        item = ranked[0]
        action = item["action"]
        archive_mod.restore_snapshot(session, item.get("origin") or origin)
        try:
            out = apply_action(session, action)
        except Exception as exc:
            picked.append({"op": _label(action), "kind": "illegal", "ei": item["ei"], "reason": str(exc), "key": item["key"]})
            sat.observe(False)
            if sat.stop():
                break
            continue
        if not out.get("ok"):
            kind = "unsupported" if "cannot realize" in str(out.get("reason") or "").lower() else "illegal"
            picked.append({"op": _label(action), "kind": kind, "ei": item["ei"], "reason": out.get("reason"), "key": item["key"]})
            sat.observe(False)
            if sat.stop():
                break
            continue
        if getattr(session, "program", None) is None:
            picked.append({"op": _label(action), "kind": "applied", "ei": item["ei"], "reward": 0.6, "key": item["key"]})
            spent += 1
            sat.observe(True)
            continue
        from .realize import realize

        result, performance = realize(session)
        key = cell_key(session)
        feat = encode_strategy(read_strategy(session))
        if key in seen_cells:
            picked.append({"op": _label(action), "kind": "duplicate", "ei": item["ei"], "reason": "Cell already in the archive.", "key": item["key"]})
            sat.observe(False)
            if sat.stop():
                break
            continue
        reward = evaluation_reward(performance, weights)
        archive_mod.insert(archive, session, result, performance, reason=f"bayes {_label(action)}")
        seen_cells.add(key)
        observed.append((feat, reward))
        if len(observed) >= 2:
            gp.fit([row[0] for row in observed], [row[1] for row in observed])
        gained = reward > best + 1e-6 or feature_is_novel(feat, known_feat)
        if reward > best:
            best = reward
        known_feat.append(feat)
        picked.append(
            {
                "op": _label(action),
                "kind": "applied",
                "ei": round(item["ei"], 4),
                "reward": reward,
                "mean": round(item["mean"], 4),
                "std": round(item["std"], 4),
                "key": item["key"],
            }
        )
        spent += 1
        sat.observe(gained)
        # Grow the candidate pool around discoveries instead of a one-shot catalog.
        if gained and out.get("ok"):
            disc_snap = archive_mod.capture(session)
            used_keys = {c["key"] for c in candidates} | {p.get("key") for p in picked}
            for extra in _candidates_from_snap(
                session, disc_snap, tag=f"disc{spent}", origin_restore=origin
            ):
                if extra["key"] not in used_keys:
                    candidates.append(extra)
                    used_keys.add(extra["key"])
        if sat.stop():
            break

    archive_mod.restore_snapshot(session, origin)
    next_op = next((p["op"] for p in picked if p.get("kind") == "applied"), None)
    last_ranked = _rank(gp, observed, candidates, used={p.get("key") for p in picked})
    return {
        "ran": True,
        "budget": cap,
        "spent": spent,
        "observed": len(observed),
        "saturated": sat.stop(),
        "candidates": [
            {
                "op": c["label"],
                "ei": round(c["ei"], 4),
                "mean": round(c["mean"], 4),
                "std": round(c["std"], 4),
            }
            for c in last_ranked[:8]
        ],
        "picked": picked,
        "next": next_op,
        "note": (
            f"Bayesian optimization spent {spent} of {cap} sequential evaluations "
            f"(GP refit after each"
            f"{'; stopped on saturation' if sat.stop() else ''}). "
            "It is a budget manager, not a generator. It does not sit on feet."
        ),
    }


def _candidates(
    session: Any,
    archive: dict[str, Any],
    origin: dict[str, Any],
    *,
    weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    starts = [{"snap": origin, "stories": None, "tag": "origin"}]
    for meta in cover_roots(session, archive, weights=weights, cap=3):
        snap = meta.get("snapshot")
        if snap:
            starts.append({"snap": snap, "stories": meta.get("stories"), "tag": meta.get("label") or "elite"})
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for start in starts:
        for item in _candidates_from_snap(
            session,
            start["snap"],
            tag=start["tag"],
            stories=start.get("stories"),
            origin_restore=origin,
        ):
            if item["key"] in seen:
                continue
            seen.add(item["key"])
            out.append(item)
    archive_mod.restore_snapshot(session, origin)
    return out


def _candidates_from_snap(
    session: Any,
    snap: dict[str, Any],
    *,
    tag: str,
    stories: dict[str, Any] | None = None,
    origin_restore: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Catalog neighbors from one restored snapshot."""
    archive_mod.restore_snapshot(session, snap, stories)
    actions = [a for a in catalog_actions(session, include_unsupported=False) if a.get("op") != "COURTYARD"]
    held = archive_mod.capture(session)
    out: list[dict[str, Any]] = []
    for action in actions:
        key = f"{tag}|{action_key(action)}"
        archive_mod.restore_snapshot(session, held)
        try:
            applied = apply_action(session, action)
        except Exception:
            continue
        if not applied.get("ok"):
            continue
        feat = encode_strategy(read_strategy(session))
        out.append(
            {
                "action": action,
                "x": feat,
                "label": _label(action),
                "key": key,
                "origin": snap,
            }
        )
    if origin_restore is not None:
        archive_mod.restore_snapshot(session, origin_restore)
    return out


def _observations(archive: dict[str, Any], weights: dict[str, float] | None = None) -> list[tuple[list[float], float]]:
    rows = []
    for entry in (archive.get("cells") or {}).values():
        rows.append(
            (encode_strategy(entry.get("strategy")), evaluation_reward(entry.get("performance") or {}, weights))
        )
    return rows


def _rank(
    gp: GaussianProcess,
    observed: list[tuple[list[float], float]],
    candidates: list[dict[str, Any]],
    used: set[Any] | None = None,
) -> list[dict[str, Any]]:
    best = max((row[1] for row in observed), default=0.0)
    known = [row[0] for row in observed]
    used = used or set()
    ranked = []
    for item in candidates:
        if item.get("key") in used:
            continue
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
                "key": item.get("key"),
                "origin": item.get("origin"),
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
    extra = action.get("mass") or action.get("loading") or action.get("envelope") or ""
    if action.get("stories") is not None:
        extra = f"{action.get('mass')} {action.get('stories')}fl"
    if action.get("delta_ft") is not None:
        extra = f"{action.get('mass')} {action.get('delta_ft'):+g}ft"
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
        "saturated": False,
        "candidates": [],
        "picked": [],
        "next": None,
        "note": note,
    }
