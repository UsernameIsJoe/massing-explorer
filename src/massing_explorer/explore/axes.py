"""
COVER probe encoding — the strategy feature space for novelty and BO.

Product language still names nine axes. Internally, program organization is a
pairwise same-mass block (not a SHA scalar), followed by normalized scalars.
Exact feet are not coordinates; they are filled by realize(s).

Heights keep mass ownership (order by mass id). Pins keep department + level.
Plate profile is part of geometric character.
"""

from __future__ import annotations

from typing import Any

# Ordered product names. encode_named keeps this map; encode_strategy is longer.
PROBE_AXIS_NAMES = (
    "program_organization",
    "mass_count",
    "distribution_balance",
    "topology",
    "loading",
    "mean_height",
    "height_articulation",
    "vertical_organization",
    "geometric_character",
)

# Pad to C(12, 2) so every GP row shares a length.
P_BLOCK_DEPTS = 12
P_BLOCK_SIZE = P_BLOCK_DEPTS * (P_BLOCK_DEPTS - 1) // 2
STRATEGY_DIM = P_BLOCK_SIZE + (len(PROBE_AXIS_NAMES) - 1)


def encode_strategy(strategy: dict[str, Any] | None) -> list[float]:
    """
    Strategy vector: pairwise P-block + eight normalized scalars.

    Story and pin axes are ownership-sensitive so swapping which mass is tall,
    or which department is pinned where, changes the encoding.
    """
    strategy = strategy or {}
    program = strategy.get("P") or {}
    topo = strategy.get("T") or {}
    vertical = strategy.get("V") or {}
    geom = strategy.get("G") or {}
    partition = program.get("partition") or {}
    if not partition and strategy.get("masses"):
        partition = {
            str(m.get("id") or i): list(m.get("departments") or [])
            for i, m in enumerate(strategy["masses"])
        }
    story_map = dict(geom.get("stories") or {})
    if not story_map and strategy.get("masses"):
        story_map = {
            str(m.get("id") or i): float(m.get("stories") or 0)
            for i, m in enumerate(strategy["masses"])
        }
    # Ownership-sensitive story signatures (sorted mass ids).
    mass_ids = sorted(str(k) for k in story_map.keys())
    stories_ord = [float(story_map.get(mid) or 0.0) for mid in mass_ids]
    if not stories_ord and strategy.get("masses"):
        stories_ord = [float(m.get("stories") or 0) for m in strategy["masses"]]
    n_st = max(len(stories_ord), 1)
    # Weighted moments distinguish [3,1] from [1,3] for the same mass order.
    weight_sum = sum(range(1, n_st + 1)) or 1
    mean_st = sum(s * (i + 1) for i, s in enumerate(stories_ord)) / weight_sum
    artic = sum(s * ((i + 1) ** 2) for i, s in enumerate(stories_ord)) / (n_st * 25.0)

    sizes = [len(depts or []) for depts in partition.values()]
    if not sizes and strategy.get("masses"):
        sizes = [len(m.get("departments") or []) for m in strategy["masses"]]
    total = sum(sizes) or 1
    balance = 1.0 - (max(sizes) / total if sizes else 0.0)

    kind = str(topo.get("kind") or "independent_bars")
    if kind == "paired_bars":
        topology = 1.0
    elif topo.get("l_leftover"):
        topology = 0.35
    else:
        topology = 0.0

    loading = 1.0 if (geom.get("loading") or "double") == "double" else 0.0

    env = str(geom.get("envelope") or "balanced")
    if env == "compact":
        env_v = 0.0
    elif env == "elongated":
        env_v = 1.0
    else:
        env_v = 0.5
    plate = 1.0 if str(geom.get("plate_profile") or "uniform") == "step" else 0.0
    geometric = 0.7 * env_v + 0.3 * plate

    pins = vertical.get("pins") or {}
    dh = vertical.get("double_height") or []
    pin_items = sorted((str(k), int(v)) for k, v in pins.items())
    vertical_org = 0.0
    for i, (dept, lvl) in enumerate(pin_items[:6]):
        h = (sum(ord(c) for c in dept) % 97) / 97.0
        # -1 / top sentinel encodes as uppermost cue (1.0), not ground.
        lvl_n = 1.0 if int(lvl) < 0 else min(1.0, (int(lvl) + 1) / 5.0)
        vertical_org += (0.55 * h + 0.45 * lvl_n) / 6.0
    if dh:
        vertical_org = min(1.0, vertical_org + 0.12)

    n_mass = float(program.get("mass_count") or len(sizes) or len(stories_ord) or 0)

    return pairwise_partition_block(partition) + [
        min(1.0, n_mass / 8.0),
        max(0.0, min(1.0, balance)),
        topology,
        loading,
        min(1.0, mean_st / 5.0),
        min(1.0, artic),
        min(1.0, vertical_org),
        geometric,
    ]


def encode_named(strategy: dict[str, Any] | None) -> dict[str, float]:
    """Nine product axes. program_organization is a compact id of the P-block."""
    vals = encode_strategy(strategy)
    block = vals[:P_BLOCK_SIZE]
    scalars = vals[P_BLOCK_SIZE:]
    named = {"program_organization": round(_p_block_id(block), 4)}
    for i, name in enumerate(PROBE_AXIS_NAMES[1:]):
        named[name] = round(scalars[i], 4) if i < len(scalars) else 0.0
    return named


def pairwise_partition_block(partition: dict[str, Any] | None) -> list[float]:
    """
    Same-mass bits for each department pair i<j, padded to C(12, 2).

    Canonical order is sorted department names on the study. 1 if the pair
    shares a mass, else 0. Unused department slots stay 0.
    """
    names = _department_names(partition)
    owner: dict[str, str] = {}
    for mass_id, depts in (partition or {}).items():
        mid = str(mass_id)
        for dept in depts or []:
            owner[str(dept)] = mid
    bits: list[float] = []
    n = P_BLOCK_DEPTS
    for i in range(n):
        for j in range(i + 1, n):
            if i >= len(names) or j >= len(names):
                bits.append(0.0)
                continue
            a = owner.get(names[i])
            b = owner.get(names[j])
            bits.append(1.0 if a and b and a == b else 0.0)
    return bits


def _department_names(partition: dict[str, Any] | None) -> list[str]:
    names = {str(d) for depts in (partition or {}).values() for d in (depts or [])}
    return sorted(names)[:P_BLOCK_DEPTS]


def _p_block_id(block: list[float]) -> float:
    """Stable [0, 1] summary of the pairwise bits (radar / debugging)."""
    acc = 0
    width = min(24, len(block))
    for i in range(width):
        if block[i] >= 0.5:
            acc |= 1 << i
    denom = float((1 << width) - 1) if width else 1.0
    return acc / denom if denom else 0.0
