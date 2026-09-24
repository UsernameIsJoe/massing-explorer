"""
COVER: stratified multi-axis coarse map of the feasible design space.

Job: fill behavior cells across P × story pattern × topology × loading ×
envelope before BO exploits. Not a one-at-a-time story sweep.

Adaptive budget:
  start 40 → measure new cells *and* feature-space novelty → if still
  discovering, +10 or +20 → stop when stagnant → hard max ~100–120.
  Illegal cells are still samples. Zero legal regions does not skip COVER.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterator

from .csp import describe_csp
from .partitions import (
    COVER_PARTITION_MAX,
    P_EXPAND_BATCH,
    P_POOL_SOFT_MAX,
    apply_partition,
    cover_partition_budget,
    partition_signature,
)
from .p_pool import (
    active_partition_indices,
    allocate_cover_step,
    balance_indices_by_mass_count,
    bias_story_index_order,
    csp_leftover_partitions,
    demand_profile,
    discovery_partition_indices,
    expand_p_pool,
    feasible_partition_indices,
    informed_probe_recipe,
    partition_key,
    persist_pool_to_session,
    p_pool_summary,
    seed_p_pool,
    should_expand_p_pool,
    unsaturated_feasible_partition_indices,
)
from .strategy import grouping_is_required, partition_id
from .topology import paired_bars_drawable, pairing_proposals, stated_frontage_ft, topology_is_required

COVER_START = 40
COVER_STEP_SMALL = 10
COVER_STEP_LARGE = 20
COVER_MAX = 120
# Minimum distinct story joints each seated partition gets in the stratified
# pool before deep P0 axis sweeps consume remaining slots.
PER_PARTITION_STORY_FLOOR = 8
# Stop when a batch adds no new cells and few novel feature encodings.
COVER_STAGNANT_FRAC = 0.08
# COVER search pool size is adaptive (12–20); UI presentation stays separate.

ENVELOPES = ("balanced", "compact", "elongated")  # elongated → search low_rise
LOADINGS = ("double", "single")
TOPOLOGIES = ("independent", "paired")
# Floor-plate profiles COVER will try. "step" = ≤2 types, biggest→smallest.
PLATE_PROFILES = ("uniform", "step")
STEP_PLATE_TAPER = 0.80


@dataclass
class CoverSample:
    """One joint point in the design space."""

    partition_index: int = 0  # into plan.partitions
    stories: tuple[int, ...] = ()
    topology: str = "independent"
    loading: str = "double"
    envelope: str = "balanced"
    plate_profile: str = "uniform"  # uniform | step
    geom_rank: int = 0  # unused; realize fills widths
    label: str = ""


@dataclass
class CoverPlan:
    partitions: list[dict[str, Any]] = field(default_factory=list)
    stated_signature: frozenset | None = None
    story_patterns: list[tuple[int, ...]] = field(default_factory=list)
    # Per mass-count libraries so 3-mass and 4-mass seats are not pad/truncated
    # from a single stated-n pattern list.
    story_patterns_by_n: dict[int, list[tuple[int, ...]]] = field(default_factory=dict)
    topologies: list[str] = field(default_factory=list)
    loadings: list[str] = field(default_factory=list)
    envelopes: list[str] = field(default_factory=list)
    plate_profiles: list[str] = field(default_factory=list)
    samples: list[CoverSample] = field(default_factory=list)


def region_signature(session: Any) -> str:
    """Region id for adaptive stopping (= behavior cell, incl. envelope)."""
    from .strategy import cell_key

    return cell_key(session)

def school_critical_story_patterns(n: int, cap: int) -> list[tuple[int, ...]]:
    """
    One-tall / low stacks used by shortlist yield replay.

    These are the configs that often make alternate motif orgs legal when the
    shared mid-heavy library alone does not.
    """
    if n <= 0:
        return []
    cap = max(1, int(cap))
    high = min(cap, 3)
    out: list[tuple[int, ...]] = []
    # One-tall / low stacks first — these unlock alternate orgs more often
    # than flat mid stacks under the school prior.
    for i in range(n):
        out.append(tuple(high if j == i else 1 for j in range(n)))
        out.append(tuple(high if j == i else 2 for j in range(n)))
    out.append(tuple([2] * n))
    out.append(tuple([1] * n))
    # Dedup preserve order
    seen: set[tuple[int, ...]] = set()
    uniq: list[tuple[int, ...]] = []
    for pat in out:
        clipped = tuple(max(1, min(cap, int(v))) for v in pat)
        if clipped not in seen:
            seen.add(clipped)
            uniq.append(clipped)
    return uniq


def story_pattern_library(
    n: int,
    cap: int,
    *,
    locks: dict[str, int] | None = None,
    mass_ids: list[str] | None = None,
) -> list[tuple[int, ...]]:
    """Joint story vectors — not single-mass increments."""
    if n <= 0:
        return []
    cap = max(1, int(cap))
    mid = min(2, cap)
    high = cap
    low = 1
    raw: list[tuple[int, ...]] = []

    def add(vec: list[int]) -> None:
        clipped = [max(1, min(cap, int(v))) for v in vec]
        if locks and mass_ids:
            for i, mid_id in enumerate(mass_ids):
                if mid_id in locks and i < len(clipped):
                    clipped[i] = int(locks[mid_id])
        raw.append(tuple(clipped))

    # School-critical one-tall / all-low stacks first so the per-P seed and
    # start-40 floor evaluate the patterns that unlock alternate orgs.
    for pat in school_critical_story_patterns(n, cap):
        add(list(pat))
    add([mid] * n)
    add([low] * n)
    add([high] * n)
    add([low if i % 2 == 0 else high for i in range(n)])
    add([high if i % 2 == 0 else low for i in range(n)])
    add([low + (i * (high - low)) // max(1, n - 1) for i in range(n)])
    add([high - (i * (high - low)) // max(1, n - 1) for i in range(n)])
    # One tall, rest mid
    for tall_i in range(min(n, 4)):
        vec = [mid] * n
        vec[tall_i] = high
        add(vec)
    # Two tall opposite ends
    if n >= 2:
        vec = [mid] * n
        vec[0] = high
        vec[-1] = high
        add(vec)
    if n >= 3:
        add([low, mid, high] + [mid] * (n - 3))
        add([high, mid, low] + [mid] * (n - 3))
    # Small height × mass counts: enumerate the full joint grid so COVER
    # is not starved when P/T are locked (product must reach start=40).
    # Cap at 3 masses — 4×3 already yields 81 patterns and starves other axes.
    if n <= 3 and cap <= 4:
        from itertools import product

        for vec in product(range(1, cap + 1), repeat=n):
            add(list(vec))
    # Deduplicate, preserve order
    out: list[tuple[int, ...]] = []
    seen: set[tuple[int, ...]] = set()
    for pat in raw:
        if pat not in seen:
            seen.add(pat)
            out.append(pat)
    return out


def _partition_mass_count(plan: CoverPlan, i_p: int) -> int:
    groups = (plan.partitions[i_p] or {}).get("groups") or []
    return max(1, len([g for g in groups if g.get("departments")]))


def _patterns_for_partition(plan: CoverPlan, i_p: int) -> list[tuple[int, ...]]:
    n = _partition_mass_count(plan, i_p)
    by_n = plan.story_patterns_by_n or {}
    if n in by_n and by_n[n]:
        return by_n[n]
    return list(plan.story_patterns or [(2,) * n])


def build_cover_plan(session: Any, *, pool_size: int = COVER_MAX) -> CoverPlan:
    """Build a stratified pool of joint samples (larger than start budget)."""
    plan = CoverPlan()
    locked_p = grouping_is_required(session)
    stated_groups = [
        {
            "id": m.id,
            "name": m.name,
            "departments": list(m.departments),
            "story_count": int(m.story_count),
        }
        for m in session.masses
    ]
    plan.stated_signature = partition_signature(stated_groups)
    plan.partitions = [{"groups": stated_groups, "reason": "stated P"}]
    if not locked_p:
        # One CSP pass at COVER max; budget may trim further.
        report = describe_csp(session, cap=COVER_PARTITION_MAX)
        budget = cover_partition_budget(
            session, feasible_count=int(report.get("feasible_count") or 0)
        )
        for item in list(report.get("chosen") or [])[:budget]:
            sig = partition_signature(item["groups"])
            if sig == plan.stated_signature:
                continue
            plan.partitions.append(item)
        if len(plan.partitions) > max(1, budget):
            plan.partitions = plan.partitions[: max(1, budget)]

    # Persist the COVER P-pool for MCTS / later search (not the UI 5-shortlist).
    pool_payload = [
        {
            "reason": p.get("reason") or "",
            "groups": [
                {
                    "id": g.get("id"),
                    "name": g.get("name"),
                    "departments": list(g.get("departments") or []),
                    "story_count": int(g.get("story_count") or 2),
                }
                for g in (p.get("groups") or [])
            ],
        }
        for p in plan.partitions
    ]
    session.constraints["cover_partition_pool"] = pool_payload
    session.constraints["cover_partition_budget"] = len(plan.partitions)
    # Story patterns per mass count present in the plan (stated + shortlist).
    cap = max(1, int(session.constraints.get("max_stories") or 4))
    locks = dict(session.constraints.get("story_lock") or {})
    mass_ids = [m.id for m in session.masses]
    stated_n = len(session.masses)
    ns = {stated_n}
    for part in plan.partitions:
        ns.add(len(part.get("groups") or []))
    by_n: dict[int, list[tuple[int, ...]]] = {}
    for nn in sorted(n for n in ns if n > 0):
        # Only bake locks into the stated-n library when P is locked — after
        # apply_partition mass ids/order change for alternate orgs.
        use_locks = locked_p and nn == stated_n and locks
        by_n[nn] = story_pattern_library(
            nn,
            cap,
            locks=locks if use_locks else None,
            mass_ids=mass_ids if use_locks else None,
        )
    plan.story_patterns_by_n = by_n
    plan.story_patterns = by_n.get(stated_n) or next(iter(by_n.values()), [])
    if not plan.story_patterns:
        plan.story_patterns = [tuple(int(m.story_count) for m in session.masses)]

    plan.topologies = ["independent"]
    if not topology_is_required(session) and paired_bars_drawable(session):
        plan.topologies.append("paired")
    elif session.pairings:
        plan.topologies = ["paired"]

    if session.constraints.get("loading_required"):
        plan.loadings = [str(session.constraints.get("loading") or "double")]
    else:
        plan.loadings = list(LOADINGS)

    plan.envelopes = list(ENVELOPES)
    plan.plate_profiles = list(PLATE_PROFILES)

    raw = list(_stratified_samples(plan, pool_size=pool_size, session=session))
    plan.samples = _order_by_diversity(_canonicalize_samples(raw), pool_size=pool_size)
    return plan


def _append_partition_samples(
    plan: CoverPlan,
    *,
    partition_indices: list[int],
    n: int,
    session: Any,
) -> list[CoverSample]:
    """Demand-biased samples for specific P indices (expansion deepen/explore)."""
    if n <= 0 or not partition_indices or not plan.partitions:
        return []
    t_n = max(1, len(plan.topologies))
    l_n = max(1, len(plan.loadings))
    e_n = max(1, len(plan.envelopes))
    profiles = list(plan.plate_profiles or PLATE_PROFILES)
    pl_n = max(1, len(profiles))
    out: list[CoverSample] = []
    seen: set[tuple] = set()
    cursor = 0
    while len(out) < n and cursor < n * 40:
        i_p = partition_indices[cursor % len(partition_indices)]
        groups = (plan.partitions[i_p] or {}).get("groups") or []
        pats = _patterns_for_partition(plan, i_p)
        s_n = max(1, len(pats))
        n_mass = max(1, len([g for g in groups if g.get("departments")]))
        cap_stories = max((max(p) if p else 1) for p in pats) if pats else 2
        critical = set(school_critical_story_patterns(n_mass, cap_stories))
        crit_order = [i for i, p in enumerate(pats) if p in critical]
        biased = bias_story_index_order(
            pats,
            demand_profile(groups, session),
            preferred_stories=(session.constraints or {}).get("preferred_stories"),
        ) or list(range(s_n))
        order: list[int] = []
        for i in crit_order + biased:
            if i not in order:
                order.append(i)
        i_s = order[(cursor // max(1, len(partition_indices))) % len(order)]
        i_t = (cursor // 3) % t_n
        i_l = (cursor // 5) % l_n
        i_e = (cursor // 7) % e_n
        i_pl = (cursor // 11) % pl_n
        stories = pats[i_s % s_n]
        profile = profiles[i_pl % pl_n]
        key = (i_p, stories, plan.topologies[i_t], plan.loadings[i_l], plan.envelopes[i_e], profile)
        cursor += 1
        if key in seen:
            continue
        seen.add(key)
        out.append(
            CoverSample(
                partition_index=i_p,
                stories=stories,
                topology=plan.topologies[i_t],
                loading=plan.loadings[i_l],
                envelope=plan.envelopes[i_e],
                plate_profile=profile,
                label=f"P{i_p}+expand{len(out)}",
            )
        )
    return _canonicalize_samples(out)


def _append_informed_probe_samples(
    plan: CoverPlan,
    *,
    partition_indices: list[int],
    n: int,
    session: Any,
    archive: dict[str, Any],
) -> list[CoverSample]:
    """
    Protected discovery probes: distinct, constraint-informed configs per P.

    Varies stories/loading/envelope/plate using demand, caps, and last failures.
    """
    if n <= 0 or not partition_indices or not plan.partitions:
        return []
    from .p_pool import ensure_p_pool, partition_key as pkey

    pool = ensure_p_pool(archive)
    t_n = max(1, len(plan.topologies))
    loadings = list(plan.loadings or LOADINGS)
    envelopes = list(plan.envelopes or ENVELOPES)
    profiles = list(plan.plate_profiles or PLATE_PROFILES)
    out: list[CoverSample] = []
    seen: set[tuple] = set()
    slot = 0
    guard = 0
    while len(out) < n and guard < n * 50:
        guard += 1
        i_p = partition_indices[slot % len(partition_indices)]
        part = plan.partitions[i_p] or {}
        groups = part.get("groups") or []
        pats = _patterns_for_partition(plan, i_p)
        s_n = max(1, len(pats))
        entry = (pool.get("entries") or {}).get(pkey(groups))
        recipe = informed_probe_recipe(groups, session, entry, slot=slot)
        order = bias_story_index_order(
            pats,
            demand_profile(groups, session),
            preferred_stories=recipe.get("preferred_stories"),
        ) or list(range(s_n))
        # Deliberate spread: pick different story ranks for successive slots.
        i_s = order[min(slot // max(1, len(partition_indices)), len(order) - 1) % len(order)]
        if recipe.get("prefer_tall") and order:
            i_s = order[min(slot % max(1, len(order) // 2 + 1), len(order) - 1)]
        stories = pats[i_s % s_n]
        loading = str(recipe.get("loading") or "double")
        if loading not in loadings and loadings:
            loading = loadings[0]
        envelope = str(recipe.get("envelope") or "balanced")
        if envelope not in envelopes and envelopes:
            envelope = envelopes[slot % len(envelopes)]
        plate = str(recipe.get("plate_profile") or "uniform")
        if plate not in profiles and profiles:
            plate = profiles[0]
        topology = plan.topologies[min(slot, t_n - 1) % t_n]
        key = (i_p, stories, topology, loading, envelope, plate)
        slot += 1
        if key in seen:
            # Nudge story index to force distinctness.
            i_s2 = order[(i_s + slot) % len(order)] if order else 0
            stories = pats[i_s2 % s_n]
            key = (i_p, stories, topology, loading, envelope, plate)
            if key in seen:
                continue
        seen.add(key)
        out.append(
            CoverSample(
                partition_index=i_p,
                stories=stories,
                topology=topology,
                loading=loading,
                envelope=envelope,
                plate_profile=plate,
                label=f"P{i_p}+probe{len(out)}",
            )
        )
    return _canonicalize_samples(out)


def _queue_discovery_and_deepen(
    plan: CoverPlan,
    archive: dict[str, Any],
    session: Any,
    *,
    step: int,
    new_idx: list[int] | None = None,
) -> list[CoverSample]:
    """Parallel discovery probes + feasible deepen + optional expand samples."""
    discovery_idx = balance_indices_by_mass_count(
        discovery_partition_indices(plan.partitions, archive),
        plan.partitions,
        session,
    )
    deepen_idx = unsaturated_feasible_partition_indices(plan.partitions, archive)
    if not deepen_idx:
        deepen_idx = feasible_partition_indices(plan.partitions, archive)
    # How many distinct probes are still owed (approx).
    from .p_pool import PROBE_FLOOR, ensure_p_pool, partition_key as pkey, probe_config_count

    pool = ensure_p_pool(archive)
    owed = 0
    for i in discovery_idx:
        groups = (plan.partitions[i] or {}).get("groups") or []
        entry = (pool.get("entries") or {}).get(pkey(groups))
        if entry and int(entry.get("near_miss_credit") or 0) > 0:
            owed += int(entry.get("near_miss_credit") or 0)
        else:
            owed += max(0, PROBE_FLOOR - probe_config_count(entry))
    discovery_n, deepen_n, expand_n = allocate_cover_step(
        step,
        archive,
        discovery_needed=owed,
        deepen_needed=len(deepen_idx),
    )
    extra: list[CoverSample] = []
    if discovery_n and discovery_idx:
        extra.extend(
            _append_informed_probe_samples(
                plan,
                partition_indices=discovery_idx,
                n=discovery_n,
                session=session,
                archive=archive,
            )
        )
    if deepen_n and deepen_idx:
        extra.extend(
            _append_partition_samples(
                plan, partition_indices=deepen_idx, n=deepen_n, session=session
            )
        )
    expand_targets = list(new_idx or [])
    if not expand_targets:
        expand_targets = active_partition_indices(plan.partitions, archive)
    if expand_n and expand_targets:
        extra.extend(
            _append_partition_samples(
                plan, partition_indices=expand_targets, n=expand_n, session=session
            )
        )
    return extra


def _stratified_samples(
    plan: CoverPlan, *, pool_size: int, session: Any
) -> Iterator[CoverSample]:
    """Latin-ish cover of the axis product; stated combo first."""
    p_n = max(1, len(plan.partitions))
    t_n = max(1, len(plan.topologies))
    l_n = max(1, len(plan.loadings))
    e_n = max(1, len(plan.envelopes))
    profiles = list(plan.plate_profiles or PLATE_PROFILES)
    pl_n = max(1, len(profiles))
    pref_stories = session.constraints.get("preferred_stories")

    def patterns(i_p: int) -> list[tuple[int, ...]]:
        return _patterns_for_partition(plan, i_p)

    def make(
        i_p: int,
        i_s: int,
        i_t: int,
        i_l: int,
        i_e: int,
        i_pl: int = 0,
    ) -> CoverSample:
        pats = patterns(i_p)
        s_n = max(1, len(pats))
        stories = pats[i_s % s_n]
        profile = profiles[i_pl % pl_n]
        return CoverSample(
            partition_index=i_p % p_n,
            stories=stories,
            topology=plan.topologies[i_t % t_n],
            loading=plan.loadings[i_l % l_n],
            envelope=plan.envelopes[i_e % e_n],
            plate_profile=profile,
            label=(
                f"P{i_p % p_n}+S{i_s % s_n}+T{plan.topologies[i_t % t_n]}"
                f"+L{plan.loadings[i_l % l_n]}+G{plan.envelopes[i_e % e_n]}"
                f"+PL{profile}"
            ),
        )

    seen: set[tuple] = set()
    ordered: list[CoverSample] = []

    def push(sample: CoverSample) -> None:
        key = (
            sample.partition_index,
            sample.stories,
            sample.topology,
            sample.loading,
            sample.envelope,
            sample.plate_profile,
        )
        if key in seen:
            return
        seen.add(key)
        ordered.append(sample)

    stated_stories = tuple(int(m.story_count) for m in session.masses) or patterns(0)[0]
    pref = session.constraints.get("preferred_stories")
    if pref is not None:
        want = max(1, int(round(float(pref))))
        locks = dict(session.constraints.get("story_lock") or {})
        stated_stories = tuple(
            int(locks[m.id]) if m.id in locks else max(int(m.story_count), want)
            for m in session.masses
        ) or stated_stories
    push(
        CoverSample(
            partition_index=0,
            stories=stated_stories,
            topology="paired" if session.pairings else "independent",
            loading=str(session.constraints.get("loading") or "double"),
            envelope="balanced",
            plate_profile="uniform",
            label="stated",
        )
    )
    push(
        CoverSample(
            partition_index=0,
            stories=stated_stories,
            topology="paired" if session.pairings else "independent",
            loading=str(session.constraints.get("loading") or "double"),
            envelope="balanced",
            plate_profile="step",
            label="stated+step",
        )
    )

    # One seed per partition (story index 0 of that P's library).
    for i_p in range(p_n):
        push(make(i_p, 0, 0, 0, 0, 0))

    # Per-P evaluation floor: each seated org gets several story joints (and a
    # couple loadings) before deep P0 axis sweeps consume the pool. Pin
    # school-critical stacks first — demand bias alone prefers mid-heavy means
    # and skips the one-tall/low patterns that unlock alternate orgs.
    floor = max(1, int(PER_PARTITION_STORY_FLOOR))
    for i_p in range(p_n):
        pats = patterns(i_p)
        groups = (plan.partitions[i_p] or {}).get("groups") or []
        n_mass = max(1, len([g for g in groups if g.get("departments")]))
        cap_stories = max((max(p) if p else 1) for p in pats) if pats else 2
        critical = set(school_critical_story_patterns(n_mass, cap_stories))
        # Prefer rest-1 one-talls (every tall index) before rest-2 / flat stacks
        # so the floor covers the school-sweep unlock set, not just tall@0.
        def _unlock_key(idx: int) -> tuple[int, int]:
            pat = pats[idx]
            spread = max(pat) - min(pat) if pat else 0
            if spread < 2:
                return (2, idx)
            if min(pat) <= 1:
                return (0, idx)
            return (1, idx)

        crit_order = sorted(
            (i for i, p in enumerate(pats) if p in critical),
            key=_unlock_key,
        )
        biased = bias_story_index_order(
            pats,
            demand_profile(groups, session),
            preferred_stories=pref_stories,
        ) or list(range(len(pats)))
        s_order: list[int] = []
        for i in crit_order + biased:
            if i not in s_order:
                s_order.append(i)
        for rank, i_s in enumerate(s_order[:floor]):
            push(make(i_p, i_s, 0, 0, 0, 0))
            if rank < 2 and l_n > 1:
                push(make(i_p, i_s, 0, 1, 0, 0))
            if rank < 1 and e_n > 1:
                push(make(i_p, i_s, 0, 0, 1, 0))

    for i_t in range(t_n):
        push(make(0, 0, i_t, 0, 0, 0))
    for i_l in range(l_n):
        push(make(0, 0, 0, i_l, 0, 0))
    for i_e in range(e_n):
        push(make(0, 0, 0, 0, i_e, 0))
    for i_pl in range(pl_n):
        push(make(0, 0, 0, 0, 0, i_pl))
    # Demand-biased story axis for P0 (stated); other P already floored above.
    p0_pats = patterns(0)
    p0_groups = (plan.partitions[0] or {}).get("groups") or []
    story_order = bias_story_index_order(
        p0_pats,
        demand_profile(p0_groups, session),
        preferred_stories=pref_stories,
    ) or list(range(len(p0_pats)))
    story_axis_cap = min(len(p0_pats), max(8, pool_size // max(1, e_n * l_n * pl_n)))
    for i_s in story_order[:story_axis_cap]:
        push(make(0, i_s, 0, 0, 0, 0))

    if len(ordered) < pool_size:
        for i_e in range(e_n):
            for i_l in range(l_n):
                for i_pl in range(pl_n):
                    for i_t in range(t_n):
                        for i_p in range(p_n):
                            pats = patterns(i_p)
                            groups = (plan.partitions[i_p] or {}).get("groups") or []
                            s_order = bias_story_index_order(
                                pats,
                                demand_profile(groups, session),
                                preferred_stories=pref_stories,
                            ) or list(range(len(pats)))
                            for i_s in s_order:
                                if len(ordered) >= pool_size:
                                    return iter(ordered[:pool_size])
                                push(make(i_p, i_s, i_t, i_l, i_e, i_pl))

    return iter(ordered[:pool_size])


def _canonicalize_samples(samples: list[CoverSample]) -> list[CoverSample]:
    """Drop instructions that realize the same strategy. Step on 1-story is uniform."""
    out: list[CoverSample] = []
    seen: set[tuple] = set()
    for sample in samples:
        plate = sample.plate_profile
        stories = tuple(int(s) for s in (sample.stories or ()))
        if plate == "step" and stories and max(stories) < 2:
            plate = "uniform"
        key = (
            sample.partition_index,
            stories,
            sample.topology,
            sample.loading,
            sample.envelope,
            plate,
        )
        if key in seen:
            continue
        seen.add(key)
        if plate == sample.plate_profile and stories == tuple(sample.stories or ()):
            out.append(sample)
            continue
        out.append(
            CoverSample(
                partition_index=sample.partition_index,
                stories=stories,
                topology=sample.topology,
                loading=sample.loading,
                envelope=sample.envelope,
                plate_profile=plate,
                label=sample.label,
            )
        )
    return out


def _sample_distance(a: CoverSample, b: CoverSample) -> float:
    dist = 0.0
    if a.partition_index != b.partition_index:
        dist += 4.0
    if a.topology != b.topology:
        dist += 2.0
    if a.loading != b.loading:
        dist += 1.0
    if a.envelope != b.envelope:
        dist += 1.5
    if a.plate_profile != b.plate_profile:
        dist += 1.0
    left = tuple(int(s) for s in (a.stories or ()))
    right = tuple(int(s) for s in (b.stories or ()))
    n = max(len(left), len(right), 1)
    dist += sum(
        abs((left[i] if i < len(left) else 0) - (right[i] if i < len(right) else 0))
        for i in range(n)
    )
    return dist


def _stories_are_school_critical(stories: tuple[int, ...] | list[int] | None) -> bool:
    """True for one-tall / low unlock stacks — not flat all-2s / all-1s."""
    pat = tuple(int(s) for s in (stories or ()))
    if not pat:
        return False
    if max(pat) - min(pat) < 2:
        return False
    return pat in set(school_critical_story_patterns(len(pat), max(pat) if pat else 2))


def _order_by_diversity(samples: list[CoverSample], *, pool_size: int) -> list[CoverSample]:
    """
    Greedy farthest-point order with a per-partition floor up front.

    The stated sample stays first. Then each seated P gets a school-critical
    story sample (when present) before mid-heavy seeds, so start-40 actually
    evaluates the stacks that unlock alternate orgs. Remaining floor slots and
    farthest-point fill the rest.
    """
    if len(samples) <= 2:
        return samples[:pool_size]
    chosen = [samples[0]]
    rest = list(samples[1:])
    by_p: dict[int, list[CoverSample]] = defaultdict(list)
    for sample in rest:
        by_p[sample.partition_index].append(sample)
    # Phase A: articulated school stacks per P, preferring distinct tall-mass
    # positions so start-40 is not stuck on tall-at-index-0 for every org.
    for i_p in sorted(by_p.keys()):
        if len(chosen) >= pool_size:
            break
        bag = by_p[i_p]
        n_mass = 0
        for s in bag:
            n_mass = max(n_mass, len(tuple(s.stories or ())))
        prefer_order = [
            (n_mass - 1 - i_p + k) % n_mass for k in range(max(1, n_mass))
        ]
        peaks_used: set[int] = set()
        target = min(2, max(1, n_mass))
        while len(peaks_used) < target and len(chosen) < pool_size:
            pick = None
            for want_peak in prefer_order:
                if want_peak in peaks_used:
                    continue
                for j, s in enumerate(bag):
                    if not _stories_are_school_critical(s.stories):
                        continue
                    stories = tuple(int(x) for x in (s.stories or ()))
                    peak = max(range(len(stories)), key=lambda k: stories[k])
                    if peak != want_peak:
                        continue
                    pick = j
                    peaks_used.add(peak)
                    break
                if pick is not None:
                    break
            if pick is None:
                break
            sample = bag.pop(pick)
            if sample in rest:
                rest.remove(sample)
            chosen.append(sample)
    floor = max(1, int(PER_PARTITION_STORY_FLOOR))
    for depth in range(floor):
        for i_p in sorted(by_p.keys()):
            bag = by_p[i_p]
            if depth >= len(bag) or len(chosen) >= pool_size:
                continue
            sample = bag[depth]
            if sample not in rest:
                continue
            chosen.append(sample)
            rest.remove(sample)
    while rest and len(chosen) < pool_size:
        nxt = max(
            rest, key=lambda sample: min(_sample_distance(sample, kept) for kept in chosen)
        )
        chosen.append(nxt)
        rest.remove(nxt)
    return chosen



def _apply_plate_profile(session: Any, profile: str) -> None:
    """uniform = equal plates; step = ≤2-type setback on multi-story masses."""
    from ..tools import clear_floor_steps, set_floor_taper

    profile = str(profile or "uniform").strip().lower()
    clear_floor_steps(session)
    if profile != "step":
        return
    for mass in session.masses:
        if int(mass.story_count) < 2:
            continue
        set_floor_taper(session, mass.id, STEP_PLATE_TAPER)



def apply_cover_sample(
    session: Any,
    plan: CoverPlan,
    sample: CoverSample,
    *,
    origin: dict[str, Any],
    search_cache: dict[str, Any] | None = None,
) -> str:
    """Apply one joint sample onto the session. Returns reason string.

    Widths are left for realize(s). search_cache is unused (kept for callers).
    """
    from ..tools import clear_pairings, pair_masses
    from . import archive as archive_mod

    del search_cache
    archive_mod.restore_snapshot(session, origin)

    part = plan.partitions[sample.partition_index % len(plan.partitions)]
    groups = part.get("groups") or []
    if groups and sample.partition_index > 0:
        apply_partition(session, groups)

    # Origin snapshot may carry stated feet. After any (P, S) sample, realize
    # must refill widths — stale locks cause ratio_band / site_length misses
    # on otherwise legal organizations (53c checkpoint).
    for mass in session.masses or []:
        session.constraints.pop(f"{mass.id}_width_ft", None)
    explore = session.constraints.get("explore")
    if isinstance(explore, dict):
        explore.pop("realize_cache", None)

    n = len(session.masses)
    locks = dict(session.constraints.get("story_lock") or {})
    if n <= 0:
        return sample.label or "empty"
    stories = list(sample.stories)
    if len(stories) < n:
        mid = stories[len(stories) // 2] if stories else 2
        stories = stories + [mid] * (n - len(stories))
    elif len(stories) > n:
        stories = stories[:n]
    cap = max(1, int(session.constraints.get("max_stories") or 4))
    for i, mass in enumerate(session.masses):
        if mass.id in locks:
            mass.story_count = int(locks[mass.id])
        else:
            mass.story_count = max(1, min(cap, int(stories[i])))

    if not session.constraints.get("loading_required"):
        session.constraints["loading"] = sample.loading
    session.constraints["cover_envelope"] = sample.envelope
    session.constraints["cover_plate_profile"] = str(
        getattr(sample, "plate_profile", None) or "uniform"
    )
    session.constraints.pop("cover_geom_rank", None)
    _apply_plate_profile(session, getattr(sample, "plate_profile", None) or "uniform")

    if topology_is_required(session):
        # Keep the brief's pairing. COVER must not rewrite a required T.
        pass
    else:
        clear_pairings(session)
        if sample.topology == "paired" and paired_bars_drawable(session):
            frontage = stated_frontage_ft(session)
            proposals = pairing_proposals(session, cap=1)
            if frontage and proposals:
                pair_masses(session, proposals[0], float(frontage), length_is_cap=True)

    return sample.label or "cover sample"


def run_cover(
    session: Any,
    archive: dict[str, Any],
    *,
    evaluate,
    start: int = COVER_START,
    step_small: int = COVER_STEP_SMALL,
    step_large: int = COVER_STEP_LARGE,
    max_attempts: int = COVER_MAX,
) -> dict[str, Any]:
    """
    Adaptive COVER loop.

    Generate `start` samples, then while new *legal* regions or novel feature
    encodings appear add `step_small` or `step_large`, until stagnant
    or `max_attempts`. New illegal cell labels alone do not extend COVER.
    """
    from . import archive as archive_mod
    from .bayes import encode_strategy
    from .saturate import encodings_from_archive, feature_is_novel
    from .strategy import cell_key, read_strategy

    origin = archive_mod.capture(session)
    archive["stated_partition"] = partition_id(session)
    # Cap nested geom_fanout inserts so COVER cannot overshoot max_attempts.
    archive["_attempt_budget"] = int(max_attempts)
    plan = build_cover_plan(session, pool_size=max_attempts)
    seed_p_pool(archive, plan.partitions, source="initial")
    persist_pool_to_session(session, archive)
    archive["cover_plan"] = {
        "partitions": len(plan.partitions),
        "partition_reasons": [p.get("reason") or "" for p in plan.partitions],
        "partition_sizes": [len(p.get("groups") or []) for p in plan.partitions],
        "story_patterns": len(plan.story_patterns),
        "topologies": list(plan.topologies),
        "loadings": list(plan.loadings),
        "envelopes": list(plan.envelopes),
        "plate_profiles": list(plan.plate_profiles),
        "pool": len(plan.samples),
        "p_pool": p_pool_summary(archive),
        "csp_feasible": int(
            (session.constraints.get("explore") or {}).get("csp", {}).get("feasible")
            or 0
        ),
    }
    # Fresh CSP stats for honesty: shortlist vs full feasible / truncated enum.
    try:
        from .csp import describe_csp

        csp_meta = describe_csp(session, cap=1)
        archive["cover_plan"]["csp_feasible"] = int(csp_meta.get("feasible_count") or 0)
        archive["cover_plan"]["csp_truncated"] = bool(csp_meta.get("truncated"))
        feas = max(1, int(csp_meta.get("feasible_count") or 1))
        archive["cover_plan"]["partition_coverage"] = round(
            len(plan.partitions) / feas, 4
        )
    except Exception:
        archive["cover_plan"]["csp_truncated"] = False
        archive["cover_plan"]["partition_coverage"] = None

    legal_regions: set[str] = set()
    all_regions: set[str] = set()
    cursor = 0
    batches: list[dict[str, Any]] = []
    stagnant = False

    evaluate(session, archive, "COVER: stated scheme")
    origin_sig = region_signature(session)
    all_regions.add(origin_sig)
    origin_key = cell_key(session)
    origin_entry = (archive.get("cells") or {}).get(origin_key)
    if origin_entry and origin_entry.get("fits_limitations"):
        legal_regions.add(origin_sig)
    known_feat = encodings_from_archive(archive)
    solved_keys = {origin_key}

    def run_batch(n: int, tag: str) -> dict[str, Any]:
        nonlocal cursor, known_feat
        before_legal = len(legal_regions)
        before_regions = len(all_regions)
        before_attempts = int(archive.get("attempts") or 0)
        took = 0
        skipped = 0
        new_feat = 0
        while (
            took < n
            and cursor < len(plan.samples)
            and int(archive.get("attempts") or 0) < max_attempts
        ):
            sample = plan.samples[cursor]
            cursor += 1
            reason = apply_cover_sample(session, plan, sample, origin=origin)
            key = cell_key(session)
            if key in solved_keys:
                skipped += 1
                continue
            solved_keys.add(key)
            evaluate(session, archive, f"COVER {tag}: {reason}")
            sig = region_signature(session)
            all_regions.add(sig)
            key = cell_key(session)
            entry = (archive.get("cells") or {}).get(key)
            feat = encode_strategy(read_strategy(session))
            if feature_is_novel(feat, known_feat):
                new_feat += 1
            known_feat.append(feat)
            if entry and entry.get("fits_limitations"):
                legal_regions.add(sig)
            took += 1
        new_legal = len(legal_regions) - before_legal
        new_regions = len(all_regions) - before_regions
        attempts = int(archive.get("attempts") or 0) - before_attempts
        return {
            "tag": tag,
            "requested": n,
            "ran": took,
            "skipped_noop": skipped,
            "new_legal_regions": new_legal,
            "new_regions": new_regions,
            "new_feature": new_feat,
            "legal_regions": len(legal_regions),
            "attempts_delta": attempts,
        }

    first = min(start, len(plan.samples), max_attempts)
    batches.append(run_batch(first, "start"))

    while int(archive.get("attempts") or 0) < max_attempts and cursor < len(plan.samples):
        last = batches[-1]
        new_r = int(last.get("new_legal_regions") or 0)
        new_cells = int(last.get("new_regions") or 0)
        new_f = int(last.get("new_feature") or 0)
        ran = max(1, int(last.get("ran") or 1))
        frac = new_r / ran
        cell_frac = new_cells / ran
        feat_frac = new_f / ran
        # Stop when the batch adds neither legal regions nor feature novelty.
        # Bare new cell labels alone are not progress.
        if new_r == 0 and feat_frac < COVER_STAGNANT_FRAC:
            # Before declaring stagnant, try expanding the P pool once.
            if (
                should_expand_p_pool(archive, session)
                and len(plan.partitions) < P_POOL_SOFT_MAX
            ):
                known = {
                    partition_key(p.get("groups") or [])
                    for p in plan.partitions
                }
                leftovers = csp_leftover_partitions(session, known, limit=P_EXPAND_BATCH * 3)
                admitted = expand_p_pool(
                    session, archive, n=P_EXPAND_BATCH, csp_leftovers=leftovers
                )
                if admitted:
                    old_n = len(plan.partitions)
                    plan.partitions.extend(admitted)
                    new_idx = list(range(old_n, len(plan.partitions)))
                    remaining = max_attempts - int(archive.get("attempts") or 0)
                    step = min(step_small, remaining)
                    extra = _queue_discovery_and_deepen(
                        plan, archive, session, step=step, new_idx=new_idx
                    )
                    plan.samples.extend(extra)
                    archive["cover_plan"]["partitions"] = len(plan.partitions)
                    archive["cover_plan"]["p_pool"] = p_pool_summary(archive)
                    batches.append(run_batch(min(len(extra), remaining), "p_expand"))
                    continue
            stagnant = True
            break
        step = (
            step_large
            if frac >= 0.25 or feat_frac >= 0.25 or (new_r > 0 and cell_frac >= 0.25)
            else step_small
        )
        remaining = max_attempts - int(archive.get("attempts") or 0)
        if remaining <= 0:
            break
        # Opportunistic P expansion while still discovering.
        queued = False
        if (
            should_expand_p_pool(archive, session)
            and len(plan.partitions) < P_POOL_SOFT_MAX
            and int(archive.get("attempts") or 0) >= start
        ):
            known = {partition_key(p.get("groups") or []) for p in plan.partitions}
            leftovers = csp_leftover_partitions(session, known, limit=P_EXPAND_BATCH * 2)
            admitted = expand_p_pool(
                session, archive, n=P_EXPAND_BATCH, csp_leftovers=leftovers
            )
            if admitted:
                old_n = len(plan.partitions)
                plan.partitions.extend(admitted)
                new_idx = list(range(old_n, len(plan.partitions)))
                plan.samples.extend(
                    _queue_discovery_and_deepen(
                        plan,
                        archive,
                        session,
                        step=min(step, remaining),
                        new_idx=new_idx,
                    )
                )
                archive["cover_plan"]["partitions"] = len(plan.partitions)
                archive["cover_plan"]["p_pool"] = p_pool_summary(archive)
                queued = True
        if not queued:
            # Still run discovery alongside deepen even when no new P is admitted.
            plan.samples.extend(
                _queue_discovery_and_deepen(
                    plan, archive, session, step=min(step, remaining), new_idx=None
                )
            )
        batches.append(run_batch(min(step, remaining), f"expand+{step}"))

    archive_mod.restore_snapshot(session, origin)
    archive.pop("_attempt_budget", None)
    pool_exhausted = cursor >= len(plan.samples) and len(plan.samples) < max_attempts
    incomplete = ((not stagnant) and int(archive.get("attempts") or 0) >= max_attempts) or (
        pool_exhausted and not stagnant and int(batches[-1].get("new_regions") or 0) > 0
        if batches
        else pool_exhausted
    )
    persist_pool_to_session(session, archive)
    if archive.get("cover_plan"):
        archive["cover_plan"]["p_pool"] = p_pool_summary(archive)
    report = {
        "ran": True,
        "start": start,
        "max": max_attempts,
        "attempts": int(archive.get("attempts") or 0),
        "legal_regions": len(legal_regions),
        "regions_seen": len(all_regions),
        "batches": batches,
        "stagnant": stagnant,
        "incomplete": incomplete,
        "pool_exhausted": pool_exhausted,
        "samples_planned": len(plan.samples),
        "samples_used": cursor,
        "p_pool": p_pool_summary(archive),
        "csp_truncated": bool((archive.get("cover_plan") or {}).get("csp_truncated")),
        "csp_feasible": (archive.get("cover_plan") or {}).get("csp_feasible"),
        "partition_coverage": (archive.get("cover_plan") or {}).get("partition_coverage"),
        "note": (
            "COVER samples a stratified subset of the strategy product; "
            "the P pool may expand mid-run when outcomes repeat or gaps remain. "
            "incomplete means the adaptive budget stopped before saturation."
            + (
                " CSP enumeration was truncated."
                if (archive.get("cover_plan") or {}).get("csp_truncated")
                else ""
            )
        ),
    }
    archive["cover"] = report
    if incomplete:
        archive["cover_incomplete"] = True
    return report
