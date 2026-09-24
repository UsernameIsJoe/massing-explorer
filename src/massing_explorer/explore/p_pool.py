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

from itertools import zip_longest
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
# While a feasible P still yields new legal/quality depth, bias COVER deepen —
# but leave room for discovery of seated-but-never-legal orgs (was 0.82).
DEEPEN_FRAC_WHEN_UNSATURATED = 0.72
EXPAND_FRAC = 1.0 - DEEPEN_FRAC
# First legal hit → one-shot G/L/story fan-out (kickstart depth).
GEOM_FANOUT_CAP = 8
# Smart depth: only distinct configs count; saturate after consecutive non-progress.
DEPTH_CONFIG_CAP = 64
DEPTH_QUALITY_EPS = 1e-4
# After this many distinct configs with no new legal and no quality gain → pause deepen.
DEPTH_STALE_DISTINCT = 3
# Need at least this many distinct configs before saturation can fire.
DEPTH_MIN_BEFORE_SATURATE = 4
# Discovery probes for unresolved P (experimental floor — not claimed optimal).
PROBE_FLOOR = 5  # align with cover.PER_PARTITION_STORY_FLOOR
NEAR_MISS_BATCH = 2
NEAR_MISS_DIST = 0.35
PROBE_FLAT_PAUSE = 2
PROBE_DIST_EPS = 1e-4

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
        # First legal for this organization → queue a one-shot geom fan-out.
        if int(entry.get("legal_hits") or 0) == 1 and not entry.get("geom_fanout_done"):
            entry["geom_fanout_pending"] = True
        # New legal re-opens depth even if a prior wave had stalled.
        entry["depth_saturated"] = False
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
    if entry.get("status") == STATUS_FEASIBLE:
        if entry.get("probe_configs") and not entry.get("depth_configs"):
            entry["depth_configs"] = list(entry.get("probe_configs") or [])[:DEPTH_CONFIG_CAP]
        _record_depth_progress(entry, session, perf)
    elif entry.get("status") == STATUS_UNRESOLVED:
        _record_probe_progress(entry, session, perf)
    return entry


def depth_config_key(session: Any) -> str:
    """
    Distinct strategy configuration for depth accounting.

    Stories × loading × envelope × plate — not feet, not cache identity.
    """
    masses = list(getattr(session, "masses", None) or [])
    stories = tuple(
        (str(m.id), int(m.story_count or 1)) for m in sorted(masses, key=lambda m: str(m.id))
    )
    c = getattr(session, "constraints", None) or {}
    loading = str(c.get("loading") or "double")
    envelope = str(c.get("cover_envelope") or "balanced")
    plate = str(c.get("cover_plate_profile") or "uniform")
    return f"S{stories}|L{loading}|E{envelope}|PL{plate}"


def _record_depth_progress(
    entry: dict[str, Any],
    session: Any,
    perf: dict[str, Any],
) -> None:
    """
    Track distinct configs on a feasible P; saturate when depth stops paying.

    Breadth admission is unchanged — this only gates further *deepen* bias.
    """
    if entry.get("status") != STATUS_FEASIBLE:
        return
    cfg = depth_config_key(session)
    seen = [str(x) for x in (entry.get("depth_configs") or []) if x]
    is_new = cfg not in seen
    if not is_new:
        # Repeats / cache hits do not count toward progress or saturation.
        return
    seen.append(cfg)
    entry["depth_configs"] = seen[-DEPTH_CONFIG_CAP:]

    progressed = False
    if perf.get("fits_limitations"):
        progressed = True
        entry["depth_legal_configs"] = int(entry.get("depth_legal_configs") or 0) + 1
    try:
        from .saturate import architectural_reward

        reward = float(architectural_reward(perf))
    except Exception:
        reward = 0.0
    best = entry.get("best_reward")
    if best is None or reward > float(best) + DEPTH_QUALITY_EPS:
        progressed = True
        entry["best_reward"] = round(reward, 4)

    explore = (getattr(session, "constraints", None) or {}).get("explore") or {}
    in_fanout = bool(explore.get("_in_geom_fanout"))

    if progressed:
        entry["depth_stale_distinct"] = 0
        entry["depth_saturated"] = False
        return
    if in_fanout:
        # Kickstart fan-out explores neighbors; do not saturate mid-batch.
        return
    entry["depth_stale_distinct"] = int(entry.get("depth_stale_distinct") or 0) + 1
    if (
        len(seen) >= DEPTH_MIN_BEFORE_SATURATE
        and int(entry.get("depth_stale_distinct") or 0) >= DEPTH_STALE_DISTINCT
    ):
        entry["depth_saturated"] = True


def p_depth_unsaturated(entry: dict[str, Any] | None) -> bool:
    """True when this org should still receive deepen samples."""
    if not entry or entry.get("status") != STATUS_FEASIBLE:
        return False
    return not bool(entry.get("depth_saturated"))


def unsaturated_feasible_count(archive: dict[str, Any] | None) -> int:
    if not archive:
        return 0
    return sum(
        1
        for e in (ensure_p_pool(archive).get("entries") or {}).values()
        if p_depth_unsaturated(e)
    )


def _record_probe_progress(
    entry: dict[str, Any],
    session: Any,
    perf: dict[str, Any],
) -> None:
    """
    Distinct discovery probes for unresolved P.

    Improving feasibility distance earns near-miss credit; flat results pause
    the org after the probe floor without marking it impossible.
    """
    cfg = depth_config_key(session)
    seen = [str(x) for x in (entry.get("probe_configs") or []) if x]
    is_new = cfg not in seen
    if is_new:
        seen.append(cfg)
        entry["probe_configs"] = seen[-DEPTH_CONFIG_CAP:]
    kinds = _failure_kinds(perf)
    if kinds:
        entry["last_failure_kinds"] = kinds[:8]

    raw_dist = perf.get("feasibility_distance")
    try:
        dist = float(raw_dist) if raw_dist is not None else None
    except (TypeError, ValueError):
        dist = None
    if dist is not None:
        best = entry.get("best_distance")
        try:
            best_f = float(best) if best is not None else None
        except (TypeError, ValueError):
            best_f = None
        improved = best_f is None or dist < best_f - PROBE_DIST_EPS
        if improved:
            entry["best_distance"] = round(dist, 4)
            entry["probe_flat"] = 0
            entry["probe_paused"] = False
            if dist <= NEAR_MISS_DIST:
                entry["near_miss_credit"] = max(
                    int(entry.get("near_miss_credit") or 0), NEAR_MISS_BATCH
                )
        elif is_new:
            entry["probe_flat"] = int(entry.get("probe_flat") or 0) + 1
            if (
                len(seen) >= PROBE_FLOOR
                and int(entry.get("probe_flat") or 0) >= PROBE_FLAT_PAUSE
                and int(entry.get("near_miss_credit") or 0) <= 0
            ):
                entry["probe_paused"] = True
    if is_new and int(entry.get("near_miss_credit") or 0) > 0:
        entry["near_miss_credit"] = int(entry.get("near_miss_credit") or 0) - 1


def groups_mass_count(groups: list[Any] | None) -> int:
    if not groups:
        return 0
    n = 0
    for g in groups:
        depts = g.get("departments") if isinstance(g, dict) else g
        if depts:
            n += 1
    return n


def probe_config_count(entry: dict[str, Any] | None) -> int:
    if not entry:
        return 0
    return len([x for x in (entry.get("probe_configs") or []) if x])


def p_needs_discovery_probe(entry: dict[str, Any] | None) -> bool:
    """Unresolved org still owed floor probes or near-miss follow-ups."""
    if not entry or entry.get("status") != STATUS_UNRESOLVED:
        return False
    if entry.get("probe_paused"):
        return False
    if int(entry.get("near_miss_credit") or 0) > 0:
        return True
    return probe_config_count(entry) < PROBE_FLOOR


def discovery_partition_indices(
    plan_partitions: list[dict[str, Any]],
    archive: dict[str, Any],
) -> list[int]:
    """Plan indices for unresolved P that still need protected probes."""
    pool = ensure_p_pool(archive)
    out: list[int] = []
    for i, item in enumerate(plan_partitions):
        key = partition_key(item.get("groups") or [])
        entry = (pool.get("entries") or {}).get(key)
        if p_needs_discovery_probe(entry):
            out.append(i)
    return out


def balance_indices_by_mass_count(
    indices: list[int],
    plan_partitions: list[dict[str, Any]],
    session: Any,
) -> list[int]:
    """
    Round-robin across allowed |P| so 3-mass and 4-mass both get real probes.
    """
    if not indices:
        return []
    bounds = required_mass_bounds(session)
    buckets: dict[int, list[int]] = {}
    for i in indices:
        n = groups_mass_count((plan_partitions[i] or {}).get("groups"))
        buckets.setdefault(n, []).append(i)
    if bounds:
        order = list(range(int(bounds[0]), int(bounds[1]) + 1))
    else:
        order = sorted(buckets.keys())
    out: list[int] = []
    # Prefer buckets that exist; cycle until all indices placed once.
    cursors = {k: 0 for k in buckets}
    while len(out) < len(indices):
        progressed = False
        for k in order:
            bag = buckets.get(k) or []
            c = cursors.get(k, 0)
            if c < len(bag):
                out.append(bag[c])
                cursors[k] = c + 1
                progressed = True
        if not progressed:
            # Mass counts outside bounds still get a turn.
            for k, bag in buckets.items():
                c = cursors.get(k, 0)
                if c < len(bag):
                    out.append(bag[c])
                    cursors[k] = c + 1
                    progressed = True
            if not progressed:
                break
    return out


def allocate_cover_step(
    step: int,
    archive: dict[str, Any] | None,
    *,
    discovery_needed: int = 0,
    deepen_needed: int = 0,
) -> tuple[int, int, int]:
    """
    Split one COVER step into discovery | deepen | expand (new P samples).

    Discovery and deepen run in parallel shares so neither waits for the other
    pool to finish. Expand keeps current breadth admission behavior.
    """
    step = max(0, int(step))
    if step <= 0:
        return 0, 0, 0
    need_d = max(0, int(discovery_needed))
    need_z = max(0, int(deepen_needed))
    if need_d and need_z:
        # Discovery gets half the step so seated unresolved orgs are not
        # starved once the first school-bar goes feasible.
        discovery = max(1, (step + 1) // 2)
        deepen = max(1, step // 4)
        expand = max(0, step - discovery - deepen)
    elif need_d:
        discovery = max(1, int(round(step * 0.45)))
        deepen = 0
        expand = max(0, step - discovery)
    elif need_z:
        deepen, expand = deepen_vs_expand_counts(step, archive)
        discovery = 0
    else:
        deepen, expand = deepen_vs_expand_counts(step, archive)
        discovery = 0
    # Cap discovery by how many probes are actually owed.
    if need_d > 0:
        discovery = min(discovery, max(need_d, 1))
    total = discovery + deepen + expand
    if total < step:
        expand += step - total
    elif total > step:
        overflow = total - step
        take = min(overflow, expand)
        expand -= take
        overflow -= take
        if overflow > 0:
            take = min(overflow, deepen)
            deepen -= take
            overflow -= take
        if overflow > 0:
            discovery = max(0, discovery - overflow)
    return discovery, deepen, expand


def informed_probe_recipe(
    groups: list[Any],
    session: Any,
    entry: dict[str, Any] | None,
    *,
    slot: int,
) -> dict[str, Any]:
    """
    Constraint-informed (stories bias, loading, envelope, plate) for probe slot.

    Uses demand, soft story preference, length/width pressure, and last failures.
    """
    profile = demand_profile(groups, session)
    constraints = getattr(session, "constraints", None) or {}
    pref = constraints.get("preferred_stories")
    kinds = {
        str(k).split(":")[0]
        for k in (
            (entry or {}).get("last_failure_kinds")
            or (entry or {}).get("failure_kinds")
            or []
        )
    }
    has_dh = bool(getattr(session, "double_height_rooms", None))
    length_pressure = bool(
        kinds
        & {
            "site_total_length",
            "site_length",
            "site_width",
            "max_edge",
            "max_building_length",
        }
    ) or bool(
        constraints.get("max_edge_ft")
        or constraints.get("max_building_length_ft")
        or constraints.get("max_total_length_ft")
    )
    prefer_tall = float(profile.get("prefer_tall") or 0.0) >= 0.45 or has_dh
    # Envelope
    if length_pressure or "site_total_length" in kinds or "site_length" in kinds:
        envelopes = ("compact", "balanced", "elongated")
    elif prefer_tall:
        envelopes = ("balanced", "elongated", "compact")
    else:
        envelopes = ("balanced", "compact", "elongated")
    # Loading: double packs area; single if length failures persist.
    if length_pressure and slot % 2 == 1:
        loading = "single"
    else:
        loading = "double" if slot % 3 != 2 else "single"
    if constraints.get("loading_required"):
        loading = str(constraints.get("loading") or loading)
    envelope = envelopes[slot % len(envelopes)]
    plate = "step" if prefer_tall and slot % 2 == 1 else "uniform"
    return {
        "prefer_tall": prefer_tall,
        "preferred_stories": pref,
        "loading": loading,
        "envelope": envelope,
        "plate_profile": plate,
        "length_pressure": length_pressure,
        "kinds": sorted(kinds),
    }


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

    # Local moves unload overloaded ground masses; CSP picks carry the
    # shortlist's relationship-feature coverage. Interleave so neither source
    # can swallow the whole batch.
    local = local_partition_candidates(session, limit=max(8, want * 3))
    local = _rank_local_by_demand(session, archive, local)
    for from_csp, from_local in zip_longest(csp_leftovers or [], local):
        if len(admitted) >= want:
            break
        if from_csp is not None:
            try_add(from_csp, "expand_csp")
        if len(admitted) >= want:
            break
        if from_local is not None:
            try_add(from_local, "expand_local")

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


def feasible_partition_indices(
    plan_partitions: list[dict[str, Any]],
    archive: dict[str, Any],
) -> list[int]:
    """Indices whose organization already has at least one legal realization."""
    pool = ensure_p_pool(archive)
    out: list[int] = []
    for i, item in enumerate(plan_partitions):
        key = partition_key(item.get("groups") or [])
        entry = (pool.get("entries") or {}).get(key)
        if entry and entry.get("status") == STATUS_FEASIBLE:
            out.append(i)
    return out


def unsaturated_feasible_partition_indices(
    plan_partitions: list[dict[str, Any]],
    archive: dict[str, Any],
) -> list[int]:
    """Feasible P that still produce depth returns (not depth-saturated)."""
    pool = ensure_p_pool(archive)
    out: list[int] = []
    for i, item in enumerate(plan_partitions):
        key = partition_key(item.get("groups") or [])
        entry = (pool.get("entries") or {}).get(key)
        if p_depth_unsaturated(entry):
            out.append(i)
    return out


def session_p_is_feasible(session: Any, archive: dict[str, Any] | None = None) -> bool:
    """True when the session's current organization is marked feasible."""
    if archive is None:
        explore = (getattr(session, "constraints", None) or {}).get("explore") or {}
        archive = explore.get("archive") if isinstance(explore.get("archive"), dict) else {}
    groups = [
        {
            "id": m.id,
            "name": m.name,
            "departments": list(m.departments or []),
            "story_count": int(m.story_count or 2),
        }
        for m in (getattr(session, "masses", None) or [])
    ]
    if not groups:
        return False
    entry = (ensure_p_pool(archive).get("entries") or {}).get(partition_key(groups))
    return bool(entry and entry.get("status") == STATUS_FEASIBLE)


def session_p_depth_unsaturated(
    session: Any, archive: dict[str, Any] | None = None
) -> bool:
    """True when current P is feasible and depth has not saturated."""
    if archive is None:
        explore = (getattr(session, "constraints", None) or {}).get("explore") or {}
        archive = explore.get("archive") if isinstance(explore.get("archive"), dict) else {}
    groups = [
        {
            "id": m.id,
            "name": m.name,
            "departments": list(m.departments or []),
            "story_count": int(m.story_count or 2),
        }
        for m in (getattr(session, "masses", None) or [])
    ]
    if not groups:
        return False
    entry = (ensure_p_pool(archive).get("entries") or {}).get(partition_key(groups))
    return p_depth_unsaturated(entry)


def feasible_p_count(archive: dict[str, Any] | None) -> int:
    if not archive:
        return 0
    entries = (ensure_p_pool(archive).get("entries") or {}).values()
    return sum(1 for e in entries if e.get("status") == STATUS_FEASIBLE)


def deepen_vs_expand_counts(
    step: int, archive: dict[str, Any] | None = None
) -> tuple[int, int]:
    """
    How many samples of a COVER step go to deepen vs newly admitted P.

    Breadth admission rules stay as-is. Only the deepen *share* rises while
    some feasible P still have unsaturated depth.
    """
    step = max(0, int(step))
    frac = (
        DEEPEN_FRAC_WHEN_UNSATURATED
        if unsaturated_feasible_count(archive) >= 1
        else DEEPEN_FRAC
    )
    deepen = int(round(step * frac))
    expand = step - deepen
    if step > 0 and deepen == 0:
        deepen, expand = 1, max(0, step - 1)
    return deepen, expand


def _geom_fanout_actions(session: Any) -> list[dict[str, Any]]:
    """Nearby story / loading / envelope moves — no regrouping."""
    actions: list[dict[str, Any]] = []
    lock = (getattr(session, "constraints", None) or {}).get("story_lock") or {}
    cap = max(1, int((getattr(session, "constraints", None) or {}).get("max_stories") or 4))
    for mass in getattr(session, "masses", None) or []:
        if mass.id in lock:
            continue
        current = int(mass.story_count or 2)
        for stories in (current - 1, current + 1):
            if 1 <= stories <= cap and stories != current:
                actions.append({"op": "SET_STORIES", "mass": mass.id, "stories": stories})
    constraints = getattr(session, "constraints", None) or {}
    if not constraints.get("loading_required"):
        loading = str(constraints.get("loading") or "double")
        other = "single" if loading != "single" else "double"
        actions.append({"op": "SET_LOADING", "loading": other})
    current_env = str(constraints.get("cover_envelope") or "balanced")
    for env in ("balanced", "compact", "elongated"):
        if env != current_env:
            actions.append({"op": "SET_ENVELOPE", "envelope": env})
    return actions[:GEOM_FANOUT_CAP]


def run_geom_fanout_if_pending(
    session: Any,
    archive: dict[str, Any],
    *,
    reason: str = "geom_fanout",
) -> int:
    """
    One-shot: after the first legal hit on a P, evaluate nearby G/L/story variants.

    Does not change P. Clears stale width locks so realize can refill feet.
    """
    explore = (getattr(session, "constraints", None) or {}).setdefault("explore", {})
    if explore.get("_in_geom_fanout"):
        return 0
    groups = [
        {
            "id": m.id,
            "name": m.name,
            "departments": list(m.departments or []),
            "story_count": int(m.story_count or 2),
        }
        for m in (getattr(session, "masses", None) or [])
    ]
    if not groups:
        return 0
    pool = ensure_p_pool(archive)
    entry = (pool.get("entries") or {}).get(partition_key(groups))
    if not entry or not entry.pop("geom_fanout_pending", False):
        return 0
    entry["geom_fanout_done"] = True
    actions = _geom_fanout_actions(session)
    if not actions:
        return 0

    from . import archive as archive_mod
    from .actions import apply_action
    from .realize import realize

    explore["_in_geom_fanout"] = True
    ran = 0
    budget = archive.get("_attempt_budget")
    try:
        budget_i = int(budget) if budget is not None else None
    except (TypeError, ValueError):
        budget_i = None
    try:
        snap = archive_mod.capture(session)
        for action in actions:
            if budget_i is not None and int(archive.get("attempts") or 0) >= budget_i:
                break
            archive_mod.restore_snapshot(session, snap)
            for mass in session.masses or []:
                session.constraints.pop(f"{mass.id}_width_ft", None)
            explore.pop("realize_cache", None)
            try:
                out = apply_action(session, action)
            except Exception:
                continue
            if not out.get("ok"):
                continue
            result, perf = realize(session)
            op = str(action.get("op") or "geom")
            detail = action.get("stories") or action.get("loading") or action.get("envelope") or ""
            archive_mod.insert(
                archive,
                session,
                result,
                perf,
                reason=f"{reason}: {op} {detail}".strip(),
            )
            ran += 1
        archive_mod.restore_snapshot(session, snap)
    finally:
        explore.pop("_in_geom_fanout", None)
    if ran:
        pool.setdefault("events", []).append(
            {"type": "geom_fanout", "key": partition_key(groups), "n": ran}
        )
    return ran


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
    """
    Next picks from the *same* ordered CSP stream the shortlist admitted.

    Expansion reuses the shortlist's relationship-feature coverage with what
    the pool already holds marked as covered, so growth reaches organizations
    the pool is missing rather than the rank tail of ones it has.
    """
    from .csp import ordered_partition_candidates

    want = max(0, int(limit))
    if want <= 0:
        return []
    return ordered_partition_candidates(
        session,
        cap=min(P_POOL_SOFT_MAX, max(COVER_PARTITION_MAX, want)),
        exclude=lambda groups: partition_key(groups) in known_keys,
    )[:want]
