"""
COVER probe encoding — the strategy feature space for novelty and BO.

Product language still names nine axes. Internally, program organization is a
pairwise same-mass block (not a SHA scalar), followed by the other eight
scalars. Exact feet are not coordinates; they are filled by realize(s).

Courtyard / podium / perpendicular wings stay unsupported and are not sample
targets; topology only encodes drawable kinds (independent / paired, plus
L-leftover when the void layout already exists).

Distance today is isotropic RBF (shared ℓ). Per-axis length scales (ARD) can
plug in later without renaming these axes.
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

    No invented feet. No unsupported courtyard code path.
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
    stories = [float(v) for v in (geom.get("stories") or {}).values()]
    if not stories and strategy.get("masses"):
        stories = [float(m.get("stories") or 0) for m in strategy["masses"]]

    mean_st = sum(stories) / max(len(stories), 1)
    artic = 0.0
    if len(stories) > 1:
        artic = (sum((s - mean_st) ** 2 for s in stories) / len(stories)) ** 0.5

    sizes = [len(depts or []) for depts in partition.values()]
    if not sizes and strategy.get("masses"):
        sizes = [len(m.get("departments") or []) for m in strategy["masses"]]
    total = sum(sizes) or 1
    # Even distribution → 1; one mass holds everything → 0.
    balance = 1.0 - (max(sizes) / total if sizes else 0.0)

    kind = str(topo.get("kind") or "independent_bars")
    if kind == "paired_bars":
        topology = 1.0
    elif topo.get("l_leftover"):
        topology = 0.35  # drawable L around a void, not a courtyard
    else:
        topology = 0.0

    loading = 1.0 if (geom.get("loading") or "double") == "double" else 0.0

    env = str(geom.get("envelope") or "balanced")
    if env == "compact":
        geometric = 0.0
    elif env == "elongated":
        geometric = 1.0
    else:
        geometric = 0.5

    pins = vertical.get("pins") or {}
    dh = vertical.get("double_height") or []
    vertical_org = min(
        1.0,
        0.65 * min(1.0, float(len(pins)) / 6.0)
        + 0.35 * (1.0 if dh else 0.0),
    )

    n_mass = float(program.get("mass_count") or len(sizes) or len(stories) or 0)

    return pairwise_partition_block(partition) + [
        min(1.0, n_mass / 8.0),
        max(0.0, min(1.0, balance)),
        topology,
        loading,
        min(1.0, mean_st / 5.0),
        min(1.0, artic / 3.0),
        vertical_org,
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
