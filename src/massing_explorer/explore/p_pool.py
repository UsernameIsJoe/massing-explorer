"""
Expanding program-organization (P) pool.

COVER starts with a diverse 12–20 batch, then may admit more P options in
batches when outcomes repeat, failures persist, or organizational gaps remain.

Statuses describe the *organization* (department grouping), not one
story/width configuration:

  feasible   — at least one accepted realization (sticky)
  unresolved — tried, none accepted, not proven impossible
  impossible — organization-wide proof only (never “failed at two floors”)
"""

from __future__ import annotations

from typing import Any

from .partitions import (
    COVER_PARTITION_MAX,
    COVER_PARTITION_MIN,
    P_EXPAND_BATCH,
    P_POOL_SOFT_MAX,
    local_partition_candidates,
    partition_signature,
)
from .strategy import grouping_is_required, required_mass_bounds

DEEPEN_FRAC = 0.60
EXPAND_FRAC = 1.0 - DEEPEN_FRAC

STATUS_FEASIBLE = "feasible"
STATUS_UNRESOLVED = "unresolved"
STATUS_IMPOSSIBLE = "impossible"

PUBLIC_TOKENS = ("health", "physical", "dining", "food", "art", "music", "gym", "media")


def partition_key(groups: list[Any]) -> str:
    """Stable string key for a partition signature."""
    sig = partition_signature(groups)
    parts = ["+".join(sorted(block)) for block in sig]
    return "||".join(sorted(parts))


def ensure_p_pool(archive: dict[str, Any]) -> dict[str, Any]:
    pool = archive.get("p_pool")
    if not isinstance(pool, dict):
        pool = {"entries": {}, "expansions": [], "events": []}
        archive["p_pool"] = pool
    pool.setdefault("entries", {})
    pool.setdefault("expansions", [])
    pool.setdefault("events", [])
    return pool


def seed_p_pool(
    archive: dict[str, Any],
    partitions: list[dict[str, Any]],
    *,
    source: str = "initial",
) -> dict[str, Any]:
    """Register the first diverse batch (or any admitted set)."""
    pool = ensure_p_pool(archive)
    for item in partitions:
        groups = item.get("groups") or []
        if not groups:
            continue
        key = partition_key(groups)
        entries = pool["entries"]
        if key in entries:
            continue
        entries[key] = {
            "key": key,
            "groups": _copy_groups(groups),
            "reason": str(item.get("reason") or source),
            "source": source,
            "status": STATUS_UNRESOLVED,
            "attempts": 0,
            "legal_hits": 0,
            "best_reward": None,
            "failure_kinds": [],
            "ground_pressure": None,
        }
    return pool


def record_p_outcome(
    archive: dict[str, Any],
    session: Any,
    performance: dict[str, Any] | None,
    *,
    groups: list[Any] | None = None,
) -> dict[str, Any] | None:
    """
    Update status/stats after a realize for the session's (or given) P.

    Status is about the *organization* (department grouping), not one
    story/width configuration:
      feasible   — at least one legal realization (sticky; never downgraded)
      unresolved — tried, none accepted, not proven impossible
      impossible — organization-wide contradiction only
    A later legal result always overrides a prior impossible label.
    """
    pool = ensure_p_pool(archive)
    if groups is None:
        groups = [
            {
                "id": m.id,
                "name": m.name,
                "departments": list(m.departments or []),
                "story_count": int(m.story_count or 2),
            }
            for m in (session.masses or [])
        ]
    if not groups:
        return None
    key = partition_key(groups)
    entry = pool["entries"].get(key)
    if entry is None:
        seed_p_pool(archive, [{"groups": groups, "reason": "observed"}], source="observed")
        entry = pool["entries"].get(key)
    if entry is None:
        return None

    perf = performance or {}
    entry["attempts"] = int(entry.get("attempts") or 0) + 1
    kinds = _failure_kinds(perf)
    if kinds:
        prev = list(entry.get("failure_kinds") or [])
        for k in kinds:
            if k not in prev:
                prev.append(k)
        entry["failure_kinds"] = prev[:12]

    if perf.get("fits_limitations"):
        was_impossible = entry.get("status") == STATUS_IMPOSSIBLE
        entry["status"] = STATUS_FEASIBLE
        entry["legal_hits"] = int(entry.get("legal_hits") or 0) + 1
        entry.pop("impossible_reason", None)
        if was_impossible:
            pool["events"].append({"type": "feasible_override", "key": key})
        try:
            from .saturate import architectural_reward

            reward = float(architectural_reward(perf))
        except Exception:
            reward = 0.0
        best = entry.get("best_reward")
        if best is None or reward > float(best):
            entry["best_reward"] = round(reward, 4)
    elif entry.get("status") != STATUS_FEASIBLE:
        # Never downgrade a proven-feasible organization.
        proof = structural_impossible(session, groups)
        if proof:
            entry["status"] = STATUS_IMPOSSIBLE
            entry["impossible_reason"] = proof
            pool["events"].append({"type": "impossible", "key": key, "reason": proof})
        else:
            entry["status"] = STATUS_UNRESOLVED

    profile = demand_profile(groups, session)
    entry["ground_pressure"] = profile.get("ground_pressure")
    return entry


def structural_impossible(session: Any, groups: list[Any]) -> str | None:
    """
    Organization-wide hard proofs only.

    Current widths, story counts, or one failed realize never prove the
    whole grouping impossible — a 2-floor miss may still work at 3 floors.
    """
    nonempty = [g for g in groups if (g.get("departments") if isinstance(g, dict) else g)]
    if not nonempty:
        return "empty partition"
    bounds = required_mass_bounds(session)
    if bounds:
        k_min, k_max = int(bounds[0]), int(bounds[1])
        n = len(nonempty)
        if n < k_min or n > k_max:
            return f"|P|={n} outside required {k_min}–{k_max}"
    return None


def should_expand_p_pool(archive: dict[str, Any], session: Any) -> bool:
    """True when the active pool is stuck, repetitive, or organizationally thin."""
    if grouping_is_required(session):
        return False
    pool = ensure_p_pool(archive)
    entries = list((pool.get("entries") or {}).values())
    if len(entries) >= P_POOL_SOFT_MAX:
        return False
    if len(entries) < COVER_PARTITION_MIN:
        return True

    unresolved = [e for e in entries if e.get("status") == STATUS_UNRESOLVED]
    feasible = [e for e in entries if e.get("status") == STATUS_FEASIBLE]
    # Shared persistent failure across several unresolved P.
    kind_hits: dict[str, int] = {}
    for e in unresolved:
        for k in e.get("failure_kinds") or []:
            kind_hits[str(k)] = kind_hits.get(str(k), 0) + 1
    shared_fail = any(v >= 3 for v in kind_hits.values())

    # High ground pressure among tried P → need regroupings.
    pressure = [
        float(e.get("ground_pressure") or 0.0)
        for e in entries
        if e.get("ground_pressure") is not None
    ]
    overloaded = bool(pressure) and (sum(pressure) / len(pressure)) >= 0.55

    # Thin |P| coverage vs allowed bounds.
    sizes = set()
    for e in entries:
        groups = e.get("groups") or []
        sizes.add(len([g for g in groups if g.get("departments")]))
    bounds = required_mass_bounds(session)
    gap = False
    if bounds:
        for k in range(int(bounds[0]), int(bounds[1]) + 1):
            if k not in sizes:
                gap = True
                break

    # Few feasible relative to attempts → explore new organizations.
    total_attempts = sum(int(e.get("attempts") or 0) for e in entries)
    starved = total_attempts >= 8 and len(feasible) <= 1 and len(unresolved) >= 3

    return bool(shared_fail or overloaded or gap or starved)


def expand_p_pool(
    session: Any,
    archive: dict[str, Any],
    *,
    n: int = P_EXPAND_BATCH,
    csp_leftovers: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Admit up to n new P options from CSP leftovers and demand-driven local moves.
    """
    if grouping_is_required(session):
        return []
    pool = ensure_p_pool(archive)
    room = P_POOL_SOFT_MAX - len(pool.get("entries") or {})
    if room <= 0:
        return []
    want = min(max(0, int(n)), room, P_EXPAND_BATCH)
    if want <= 0:
        return []

    known = set(pool["entries"].keys())
    admitted: list[dict[str, Any]] = []

    def try_add(item: dict[str, Any], source: str) -> bool:
        groups = item.get("groups") or []
        if not groups:
            return False
        key = partition_key(groups)
        if key in known:
            return False
        if structural_impossible(session, groups):
            return False
        payload = {
            "groups": _copy_groups(groups),
            "reason": str(item.get("reason") or source),
        }
        seed_p_pool(archive, [payload], source=source)
        known.add(key)
        admitted.append(payload)
        return True

    # Prefer local moves that unload overloaded ground masses.
    local = local_partition_candidates(session, limit=max(8, want * 3))
    local = _rank_local_by_demand(session, archive, local)
    for item in local:
        if len(admitted) >= want:
            break
        try_add(item, "expand_local")

    for item in csp_leftovers or []:
        if len(admitted) >= want:
            break
        try_add(item, "expand_csp")

    if admitted:
        pool["expansions"].append(
            {
                "added": len(admitted),
                "keys": [partition_key(a["groups"]) for a in admitted],
                "pool_size": len(pool["entries"]),
            }
        )
        pool["events"].append(
            {"type": "expand", "added": len(admitted), "pool_size": len(pool["entries"])}
        )
        persist_pool_to_session(session, archive)
    return admitted


def demand_profile(groups: list[Any], session: Any = None) -> dict[str, float]:
    """Cheap program-demand signals for strategy pairing and expansion ranking."""
    blocks: list[list[str]] = []
    for g in groups:
        if isinstance(g, dict):
            blocks.append([str(d) for d in (g.get("departments") or []) if d])
        else:
            blocks.append([str(d) for d in g if d])
    blocks = [b for b in blocks if b]
    if not blocks:
        return {
            "ground_pressure": 0.0,
            "public_share": 0.0,
            "imbalance": 0.0,
            "n_masses": 0.0,
        }
    sizes = [len(b) for b in blocks]
    total = sum(sizes) or 1
    imbalance = max(sizes) / total
    # Ground pressure proxy: largest block share (overloaded mass).
    ground_pressure = imbalance
    public = 0
    all_depts = [d for b in blocks for d in b]
    for d in all_depts:
        low = d.lower()
        if any(tok in low for tok in PUBLIC_TOKENS):
            public += 1
    public_share = public / max(1, len(all_depts))
    # Prefer taller patterns when public share is high or one mass dominates.
    prefer_tall = max(ground_pressure, public_share)
    return {
        "ground_pressure": round(ground_pressure, 4),
        "public_share": round(public_share, 4),
        "imbalance": round(imbalance, 4),
        "prefer_tall": round(prefer_tall, 4),
        "n_masses": float(len(blocks)),
    }


def bias_story_index_order(
    patterns: list[tuple[int, ...]],
    profile: dict[str, float] | None,
    preferred_stories: float | int | None = None,
) -> list[int]:
    """Reorder story-pattern indices for a P's demand profile and soft story pref."""
    if not patterns:
        return []
    prefer_tall = float((profile or {}).get("prefer_tall") or 0.0)
    want: float | None
    try:
        want = float(preferred_stories) if preferred_stories is not None else None
    except (TypeError, ValueError):
        want = None
    if want is not None and want <= 0:
        want = None
    scored: list[tuple[float, int]] = []
    for i, pat in enumerate(patterns):
        if not pat:
            scored.append((0.0, i))
            continue
        mean = sum(pat) / len(pat)
        spread = max(pat) - min(pat)
        # High ground/public pressure → prefer higher mean / more articulation.
        score = prefer_tall * (mean / 4.0 + 0.15 * spread) + (1.0 - prefer_tall) * (
            1.0 - abs(mean - 2.0) / 3.0
        )
        # Soft "prefer N floors" from the brief — raise patterns near N without
        # locking every mass to N (gym/dining often need 1–2).
        if want is not None:
            score += 0.35 * (1.0 - min(1.0, abs(mean - want) / max(want, 1.0)))
            score += 0.25 * min(1.0, max(pat) / want)
        scored.append((score, i))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [i for _s, i in scored]


def persist_pool_to_session(session: Any, archive: dict[str, Any]) -> None:
    """Keep MCTS / later stages on the grown pool (not the UI 5)."""
    pool = ensure_p_pool(archive)
    payload = []
    for entry in (pool.get("entries") or {}).values():
        if entry.get("status") == STATUS_IMPOSSIBLE:
            continue
        groups = entry.get("groups") or []
        if not groups:
            continue
        payload.append(
            {
                "reason": str(entry.get("reason") or entry.get("source") or ""),
                "groups": _copy_groups(groups),
                "status": entry.get("status"),
            }
        )
    session.constraints["cover_partition_pool"] = payload
    session.constraints["cover_partition_budget"] = len(payload)


def p_pool_summary(archive: dict[str, Any]) -> dict[str, Any]:
    pool = ensure_p_pool(archive)
    entries = list((pool.get("entries") or {}).values())
    counts = {
        STATUS_FEASIBLE: 0,
        STATUS_UNRESOLVED: 0,
        STATUS_IMPOSSIBLE: 0,
    }
    for e in entries:
        st = str(e.get("status") or STATUS_UNRESOLVED)
        if st in counts:
            counts[st] += 1
    return {
        "size": len(entries),
        "soft_max": P_POOL_SOFT_MAX,
        "initial_band": [COVER_PARTITION_MIN, COVER_PARTITION_MAX],
        "statuses": counts,
        "expansions": len(pool.get("expansions") or []),
        "events": list(pool.get("events") or [])[-8:],
    }


def active_partition_indices(
    plan_partitions: list[dict[str, Any]],
    archive: dict[str, Any],
) -> list[int]:
    """Indices into plan.partitions that are still worth deepening."""
    pool = ensure_p_pool(archive)
    out: list[int] = []
    for i, item in enumerate(plan_partitions):
        key = partition_key(item.get("groups") or [])
        entry = (pool.get("entries") or {}).get(key)
        if entry and entry.get("status") == STATUS_IMPOSSIBLE:
            continue
        out.append(i)
    return out


def deepen_vs_expand_counts(step: int) -> tuple[int, int]:
    """How many samples of a COVER step go to deepen vs newly admitted P."""
    step = max(0, int(step))
    deepen = int(round(step * DEEPEN_FRAC))
    expand = step - deepen
    if step > 0 and deepen == 0:
        deepen, expand = 1, max(0, step - 1)
    return deepen, expand


def _failure_kinds(perf: dict[str, Any]) -> list[str]:
    kinds: list[str] = []
    for item in perf.get("failed_kinds") or []:
        kinds.append(str(item).split(":")[0])
    violations = perf.get("violations") or {}
    if isinstance(violations, dict):
        for k, v in violations.items():
            if k == "owners":
                continue
            try:
                if float(v or 0) > 1e-9:
                    kinds.append(str(k))
            except (TypeError, ValueError):
                continue
    # unique preserve order
    out: list[str] = []
    for k in kinds:
        if k and k not in out:
            out.append(k)
    return out


def _copy_groups(groups: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, g in enumerate(groups):
        if isinstance(g, dict):
            out.append(
                {
                    "id": g.get("id") or f"m{i}",
                    "name": g.get("name") or f"Mass {i + 1}",
                    "departments": list(g.get("departments") or []),
                    "story_count": int(g.get("story_count") or 2),
                }
            )
        else:
            out.append(
                {
                    "id": f"m{i}",
                    "name": f"Mass {i + 1}",
                    "departments": list(g),
                    "story_count": 2,
                }
            )
    return out


def _rank_local_by_demand(
    session: Any,
    archive: dict[str, Any],
    locals_: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Prefer moves that reduce ground pressure / imbalance."""
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in locals_:
        groups = item.get("groups") or []
        prof = demand_profile(groups, session)
        # Lower pressure is better for expansion candidates from an overloaded base.
        score = 1.0 - float(prof.get("ground_pressure") or 0.0)
        score += 0.15 * float(prof.get("n_masses") or 0.0) / 6.0
        scored.append((score, item))
    scored.sort(key=lambda t: -t[0])
    return [item for _s, item in scored]


def csp_leftover_partitions(
    session: Any,
    known_keys: set[str],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Extra CSP shortlist picks not yet in the pool (for expansion)."""
    from .csp import describe_csp

    # Ask for more than the initial band so leftovers exist.
    report = describe_csp(session, cap=min(P_POOL_SOFT_MAX, max(COVER_PARTITION_MAX + 10, limit + 10)))
    out: list[dict[str, Any]] = []
    for item in report.get("chosen") or []:
        groups = item.get("groups") or []
        key = partition_key(groups)
        if key in known_keys:
            continue
        out.append(item)
        if len(out) >= limit:
            break
    return out
