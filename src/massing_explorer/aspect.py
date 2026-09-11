"""
Length:width mass ratios are orientation-agnostic.

Architects treat 3:5 the same as 5:3 — only the proportion matters, not which
axis is called length. Ratio bands work the same way: a footprint passes if
either directed aspect (L/W or W/L) falls in the stated band.
"""

from __future__ import annotations


def normalize_band(lo: float, hi: float) -> tuple[float, float]:
    if hi < lo:
        return hi, lo
    return lo, hi


def aspect_in_band(
    aspect: float,
    lo: float,
    hi: float,
    *,
    tol: float = 0.0,
    transposable: bool = True,
) -> bool:
    """True if aspect (or its transpose) sits in [lo, hi] within tol."""
    if aspect <= 0:
        return False
    lo, hi = normalize_band(lo, hi)
    low, high = lo - tol, hi + tol
    if low <= aspect <= high:
        return True
    if not transposable:
        return False
    return low <= (1.0 / aspect) <= high


def aspect_target_distance(actual: float, want: float) -> float:
    """0 = match in either orientation; 1 = far. Used for preference scoring."""
    if actual <= 0 or want <= 0:
        return 1.0
    d_direct = abs(actual - want) / max(want, 0.15)
    inv = 1.0 / want
    d_flip = abs(actual - inv) / max(inv, 0.15)
    return min(1.0, min(d_direct, d_flip))


def aspect_band_distance(actual: float, lo: float, hi: float) -> float:
    """0 inside the band (either orientation); grows outside."""
    if actual <= 0:
        return 1.0
    lo, hi = normalize_band(lo, hi)
    span = max(hi - lo, 0.15)

    def _one(a: float) -> float:
        if a < lo:
            return min(1.0, (lo - a) / span)
        if a > hi:
            return min(1.0, (a - hi) / span)
        return 0.0

    return min(_one(actual), _one(1.0 / actual))
