"""
Nine COVER probe axes — the strategy feature space for novelty and BO.

These coordinates describe *what kind of strategy* a cell is. They are not
LEARN taste and not a quality score. Courtyard / podium / perpendicular wings
stay unsupported and are not sample targets; topology only encodes drawable
kinds (independent / paired, plus L-leftover when the void layout already exists).

Distance today is isotropic RBF (shared ℓ). Per-axis length scales (ARD) can
plug in later without renaming these axes.
"""

from __future__ import annotations

import hashlib
from typing import Any

# Ordered — index i is axis i in encode_strategy / GP X rows.
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


def encode_strategy(strategy: dict[str, Any] | None) -> list[float]:
    """
    Nine normalized coordinates in [0, 1] (approximately).

    No invented feet. No unsupported courtyard code path.
    """
    strategy = strategy or {}
    program = strategy.get("P") or {}
    topo = strategy.get("T") or {}
    vertical = strategy.get("V") or {}
    geom = strategy.get("G") or {}
    partition = program.get("partition") or {}
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

    return [
        _partition_fingerprint(partition),
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
    """Same vector as a name→value map (docs / debugging)."""
    vals = encode_strategy(strategy)
    return {name: round(vals[i], 4) for i, name in enumerate(PROBE_AXIS_NAMES)}


def _partition_fingerprint(partition: dict[str, Any]) -> float:
    """
    Stable [0,1] id for who-is-with-whom.

    Same grouping → same value. Different organizations → usually different.
    Not a quality score.
    """
    groups = []
    for depts in (partition or {}).values():
        groups.append(tuple(sorted(str(d) for d in (depts or []))))
    groups.sort()
    if not groups:
        return 0.0
    raw = "|".join(",".join(g) for g in groups)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    # Take 32 bits → [0,1)
    return int(digest[:8], 16) / float(0xFFFFFFFF)
