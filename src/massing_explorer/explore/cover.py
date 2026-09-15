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

from dataclasses import dataclass, field
from typing import Any, Iterator

from .csp import describe_csp
from .partitions import (
    COVER_PARTITION_MAX,
    apply_partition,
    cover_partition_budget,
    partition_signature,
)
from .strategy import grouping_is_required, partition_id
from .topology import paired_bars_drawable, pairing_proposals, stated_frontage_ft, topology_is_required

COVER_START = 40
COVER_STEP_SMALL = 10
COVER_STEP_LARGE = 20
COVER_MAX = 120
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
    topologies: list[str] = field(default_factory=list)
    loadings: list[str] = field(default_factory=list)
    envelopes: list[str] = field(default_factory=list)
    plate_profiles: list[str] = field(default_factory=list)
    samples: list[CoverSample] = field(default_factory=list)


def region_signature(session: Any) -> str:
    """Region id for adaptive stopping (= behavior cell, incl. envelope)."""
    from .strategy import cell_key

    return cell_key(session)

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
                if mid_id in locks:
                    clipped[i] = int(locks[mid_id])
        raw.append(tuple(clipped))

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
    # Story patterns sized to stated mass count; re-derived per partition on apply.
    cap = max(1, int(session.constraints.get("max_stories") or 4))
    locks = dict(session.constraints.get("story_lock") or {})
    mass_ids = [m.id for m in session.masses]
    n = len(session.masses)
    plan.story_patterns = story_pattern_library(n, cap, locks=locks, mass_ids=mass_ids)
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


def _stratified_samples(
    plan: CoverPlan, *, pool_size: int, session: Any
) -> Iterator[CoverSample]:
    """Latin-ish cover of the axis product; stated combo first."""
    p_n = max(1, len(plan.partitions))
    s_n = max(1, len(plan.story_patterns))
    t_n = max(1, len(plan.topologies))
    l_n = max(1, len(plan.loadings))
    e_n = max(1, len(plan.envelopes))
    profiles = list(plan.plate_profiles or PLATE_PROFILES)
    pl_n = max(1, len(profiles))

    def make(
        i_p: int,
        i_s: int,
        i_t: int,
        i_l: int,
        i_e: int,
        i_pl: int = 0,
    ) -> CoverSample:
        stories = plan.story_patterns[i_s % s_n]
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

    stated_stories = tuple(int(m.story_count) for m in session.masses) or plan.story_patterns[0]
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

    for i_p in range(p_n):
        push(make(i_p, 0, 0, 0, 0, 0))
    for i_t in range(t_n):
        push(make(0, 0, i_t, 0, 0, 0))
    for i_l in range(l_n):
        push(make(0, 0, 0, i_l, 0, 0))
    for i_e in range(e_n):
        push(make(0, 0, 0, 0, i_e, 0))
    for i_pl in range(pl_n):
        push(make(0, 0, 0, 0, 0, i_pl))
    story_axis_cap = min(s_n, max(8, pool_size // max(1, e_n * l_n * pl_n)))
    for i_s in range(story_axis_cap):
        push(make(0, i_s, 0, 0, 0, 0))

    if len(ordered) < pool_size:
        for i_e in range(e_n):
            for i_l in range(l_n):
                for i_pl in range(pl_n):
                    for i_t in range(t_n):
                        for i_p in range(p_n):
                            for i_s in range(s_n):
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


def _order_by_diversity(samples: list[CoverSample], *, pool_size: int) -> list[CoverSample]:
    """Greedy farthest-point order. The stated sample stays first."""
    if len(samples) <= 2:
        return samples[:pool_size]
    chosen = [samples[0]]
    rest = list(samples[1:])
    while rest and len(chosen) < pool_size:
        nxt = max(rest, key=lambda sample: min(_sample_distance(sample, kept) for kept in chosen))
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

    Generate `start` samples, then while new cells or novel feature
    encodings appear add `step_small` or `step_large`, until stagnant
    or `max_attempts`. Zero legal regions is not a stop condition.
    """
    from . import archive as archive_mod
    from .bayes import encode_strategy
    from .saturate import encodings_from_archive, feature_is_novel
    from .strategy import cell_key, read_strategy

    origin = archive_mod.capture(session)
    archive["stated_partition"] = partition_id(session)
    plan = build_cover_plan(session, pool_size=max_attempts)
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
    }

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
        # Illegal cells still count as samples. Stop only when the map is
        # no longer adding cells or encodings.
        if new_cells == 0 and feat_frac < COVER_STAGNANT_FRAC:
            stagnant = True
            break
        step = (
            step_large
            if frac >= 0.25 or feat_frac >= 0.25 or cell_frac >= 0.25
            else step_small
        )
        remaining = max_attempts - int(archive.get("attempts") or 0)
        if remaining <= 0:
            break
        batches.append(run_batch(min(step, remaining), f"expand+{step}"))

    archive_mod.restore_snapshot(session, origin)
    pool_exhausted = cursor >= len(plan.samples) and len(plan.samples) < max_attempts
    incomplete = ((not stagnant) and int(archive.get("attempts") or 0) >= max_attempts) or (
        pool_exhausted and not stagnant and int(batches[-1].get("new_regions") or 0) > 0
        if batches
        else pool_exhausted
    )
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
    }
    archive["cover"] = report
    if incomplete:
        archive["cover_incomplete"] = True
    return report
