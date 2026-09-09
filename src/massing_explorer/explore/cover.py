"""
COVER: stratified multi-axis coarse map of the feasible design space.

Job: fill behavior cells across P × story pattern × topology × loading ×
envelope before BO exploits. Not a one-at-a-time story sweep.

Adaptive budget:
  start 40 → measure new regions → if still discovering, +10 or +20 → stop
  when stagnant → hard max ~100–120.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from .partitions import apply_partition, enumerate_partitions, partition_signature
from .strategy import grouping_is_required, partition_id
from .topology import paired_bars_drawable, pairing_proposals, stated_frontage_ft, topology_is_required

COVER_START = 40
COVER_STEP_SMALL = 10
COVER_STEP_LARGE = 20
COVER_MAX = 120
# Stop when a batch adds fewer than this fraction of new legal regions.
COVER_STAGNANT_FRAC = 0.08
COVER_PARTITION_CAP = 8

ENVELOPES = ("balanced", "compact", "elongated")  # elongated → search low_rise
LOADINGS = ("double", "single")
TOPOLOGIES = ("independent", "paired")


@dataclass
class CoverSample:
    """One joint point in the design space."""

    partition_index: int = 0  # into plan.partitions
    stories: tuple[int, ...] = ()
    topology: str = "independent"
    loading: str = "double"
    envelope: str = "balanced"
    label: str = ""


@dataclass
class CoverPlan:
    partitions: list[dict[str, Any]] = field(default_factory=list)
    stated_signature: frozenset | None = None
    story_patterns: list[tuple[int, ...]] = field(default_factory=list)
    topologies: list[str] = field(default_factory=list)
    loadings: list[str] = field(default_factory=list)
    envelopes: list[str] = field(default_factory=list)
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
    # Deduplicate, preserve order
    out: list[tuple[int, ...]] = []
    seen: set[tuple[int, ...]] = set()
    for pat in raw:
        if pat not in seen:
            seen.add(pat)
            out.append(pat)
    return out


def _search_preference(envelope: str) -> str:
    if envelope == "compact":
        return "compact"
    if envelope == "elongated":
        return "low_rise"
    return "balanced"


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
        for item in enumerate_partitions(session, cap=COVER_PARTITION_CAP):
            sig = partition_signature(item["groups"])
            if sig == plan.stated_signature:
                continue
            plan.partitions.append(item)

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

    plan.samples = list(_stratified_samples(plan, pool_size=pool_size, session=session))
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

    def make(i_p: int, i_s: int, i_t: int, i_l: int, i_e: int) -> CoverSample:
        stories = plan.story_patterns[i_s % s_n]
        return CoverSample(
            partition_index=i_p % p_n,
            stories=stories,
            topology=plan.topologies[i_t % t_n],
            loading=plan.loadings[i_l % l_n],
            envelope=plan.envelopes[i_e % e_n],
            label=(
                f"P{i_p % p_n}+S{i_s % s_n}+T{plan.topologies[i_t % t_n]}"
                f"+L{plan.loadings[i_l % l_n]}+G{plan.envelopes[i_e % e_n]}"
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
        )
        if key in seen:
            return
        seen.add(key)
        ordered.append(sample)

    # 1) Stated organization + mid stories + current loading + independent + balanced
    stated_stories = tuple(int(m.story_count) for m in session.masses) or plan.story_patterns[0]
    push(
        CoverSample(
            partition_index=0,
            stories=stated_stories,
            topology="paired" if session.pairings else "independent",
            loading=str(session.constraints.get("loading") or "double"),
            envelope="balanced",
            label="stated",
        )
    )

    # 2) Cover each axis level at least once against stated others
    for i_p in range(p_n):
        push(make(i_p, 0, 0, 0, 0))
    for i_s in range(s_n):
        push(make(0, i_s, 0, 0, 0))
    for i_t in range(t_n):
        push(make(0, 0, i_t, 0, 0))
    for i_l in range(l_n):
        push(make(0, 0, 0, i_l, 0))
    for i_e in range(e_n):
        push(make(0, 0, 0, 0, i_e))

    # 3) Diagonal / staggered joints to fill the pool
    i = 0
    while len(ordered) < pool_size and i < pool_size * 4:
        push(
            make(
                i % p_n,
                (i * 3) % s_n,
                (i * 5) % t_n,
                (i * 7) % l_n,
                (i * 11) % e_n,
            )
        )
        i += 1

    return iter(ordered[:pool_size])


def apply_cover_sample(
    session: Any,
    plan: CoverPlan,
    sample: CoverSample,
    *,
    origin: dict[str, Any],
    search_cache: dict[str, Any] | None = None,
) -> str:
    """Apply one joint sample onto the session. Returns reason string."""
    from ..tools import clear_pairings, pair_masses
    from . import archive as archive_mod

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

    clear_pairings(session)
    if sample.topology == "paired" and paired_bars_drawable(session):
        frontage = stated_frontage_ft(session)
        proposals = pairing_proposals(session, cap=1)
        if frontage and proposals:
            pair_masses(session, proposals[0], float(frontage), length_is_cap=True)

    _apply_envelope_geometry(session, sample.envelope, cache=search_cache)
    return sample.label or "cover sample"


def _apply_envelope_geometry(
    session: Any,
    envelope: str,
    *,
    cache: dict[str, Any] | None = None,
) -> None:
    """Pull one site-search candidate under the envelope preference, if any."""
    if not session.masses:
        return
    from ..search import SiteEnvelope, apply_scheme as apply_candidate, search_schemes

    preference = _search_preference(envelope)
    envelope_obj = SiteEnvelope(
        max_building_length_ft=session.constraints.get("max_building_length_ft"),
        max_building_width_ft=session.constraints.get("max_building_width_ft"),
        max_total_length_ft=session.constraints.get("max_total_length_ft"),
        max_stories=int(session.constraints.get("max_stories") or 4),
    )
    if not any(
        [
            envelope_obj.max_building_length_ft,
            envelope_obj.max_building_width_ft,
            envelope_obj.max_total_length_ft,
        ]
    ):
        return

    cache_key = (
        partition_id(session),
        tuple((m.id, int(m.story_count)) for m in session.masses),
        str(session.constraints.get("loading") or "double"),
        preference,
        bool(session.pairings),
    )
    store = cache if cache is not None else {}
    if cache_key in store:
        candidate = store[cache_key]
        if candidate is not None:
            apply_candidate(session, candidate, save=False)
        return

    try:
        candidates, _notes = search_schemes(
            session, envelope_obj, preference=preference, top_n=1
        )
    except Exception:
        store[cache_key] = None
        return
    candidate = candidates[0] if candidates else None
    store[cache_key] = candidate
    if candidate is not None:
        apply_candidate(session, candidate, save=False)


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

    Generate `start` samples, then while new legal regions appear add
    `step_small` or `step_large`, until stagnant or `max_attempts`.
    """
    from . import archive as archive_mod
    from .strategy import cell_key

    origin = archive_mod.capture(session)
    archive["stated_partition"] = partition_id(session)
    plan = build_cover_plan(session, pool_size=max_attempts)
    archive["cover_plan"] = {
        "partitions": len(plan.partitions),
        "story_patterns": len(plan.story_patterns),
        "topologies": list(plan.topologies),
        "loadings": list(plan.loadings),
        "envelopes": list(plan.envelopes),
        "pool": len(plan.samples),
    }

    legal_regions: set[str] = set()
    all_regions: set[str] = set()
    cursor = 0
    batches: list[dict[str, Any]] = []
    search_cache: dict[str, Any] = {}
    stagnant = False

    def run_batch(n: int, tag: str) -> dict[str, Any]:
        nonlocal cursor
        before_legal = len(legal_regions)
        before_attempts = int(archive.get("attempts") or 0)
        took = 0
        while (
            took < n
            and cursor < len(plan.samples)
            and int(archive.get("attempts") or 0) < max_attempts
        ):
            sample = plan.samples[cursor]
            cursor += 1
            reason = apply_cover_sample(
                session,
                plan,
                sample,
                origin=origin,
                search_cache=search_cache,
            )
            evaluate(session, archive, f"COVER {tag}: {reason}")
            sig = region_signature(session)
            all_regions.add(sig)
            key = cell_key(session)
            entry = (archive.get("cells") or {}).get(key)
            if entry and entry.get("fits_limitations"):
                legal_regions.add(sig)
            took += 1
        new_legal = len(legal_regions) - before_legal
        attempts = int(archive.get("attempts") or 0) - before_attempts
        return {
            "tag": tag,
            "requested": n,
            "ran": took,
            "new_legal_regions": new_legal,
            "legal_regions": len(legal_regions),
            "attempts_delta": attempts,
        }

    first = min(start, len(plan.samples), max_attempts)
    batches.append(run_batch(first, "start"))

    while int(archive.get("attempts") or 0) < max_attempts and cursor < len(plan.samples):
        last = batches[-1]
        new_r = int(last.get("new_legal_regions") or 0)
        ran = max(1, int(last.get("ran") or 1))
        frac = new_r / ran
        if new_r == 0 or frac < COVER_STAGNANT_FRAC:
            stagnant = True
            break
        step = step_large if frac >= 0.25 else step_small
        remaining = max_attempts - int(archive.get("attempts") or 0)
        if remaining <= 0:
            break
        batches.append(run_batch(min(step, remaining), f"expand+{step}"))

    archive_mod.restore_snapshot(session, origin)
    incomplete = (not stagnant) and int(archive.get("attempts") or 0) >= max_attempts
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
        "samples_planned": len(plan.samples),
        "samples_used": cursor,
    }
    archive["cover"] = report
    if incomplete:
        archive["cover_incomplete"] = True
    return report
