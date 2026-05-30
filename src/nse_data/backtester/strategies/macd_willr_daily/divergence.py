"""Swing-pivot detection + bullish/bearish divergence between price and Williams %R.

## Pivot anti-lookahead invariant

A swing low at index `i` requires `low[i] == min(low[i-N..i+N])` — a window
that extends `N` bars into the future. At scan time `t`, a pivot at index
`i` is *confirmed* only when `i + N <= t`. Pivots with `i > t - N` are NOT
yet confirmed and MUST NOT influence any signal decision at `t`.

The detector functions take a `scan_index` parameter; pivots inside the
unconfirmed tail are filtered out. Tests pin this.

## Divergence patterns

* **Bullish divergence (long setup):** Two confirmed price swing lows
  (LL1 then LL2) where `price[LL2] < price[LL1]` (lower low in price)
  AND `willr[LL2] > willr[LL1]` (higher low in momentum). The two pivots
  must be within `divergence_lookback` bars of each other.

* **Bearish divergence (short setup):** Mirror — two confirmed swing
  highs (HH1, HH2) with `price[HH2] > price[HH1]` and
  `willr[HH2] < willr[HH1]`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd

PivotKind = Literal["low", "high"]


@dataclass(frozen=True)
class Pivot:
    """A confirmed swing pivot."""
    index: int          # bar index (in the original DataFrame)
    kind: PivotKind
    price: float        # the low (for kind='low') or high (for kind='high')
    willr: float


@dataclass(frozen=True)
class Divergence:
    """Two confirmed pivots that exhibit price-vs-willr divergence."""
    direction: Literal["LONG", "SHORT"]
    pivot1: Pivot       # the older pivot
    pivot2: Pivot       # the newer pivot


# ----------------------------------------------------------- pivot detection

def detect_swing_pivots(
    df: pd.DataFrame, *,
    scan_index: int,
    window: int,
    kind: PivotKind,
) -> list[Pivot]:
    """All confirmed pivots of `kind` at indices `i` with `i + window <= scan_index`.

    A "low" pivot at i satisfies `low[i] == min(low[i-window..i+window])`.
    The willr column is read at the same index.

    Returns pivots ordered by index ascending (oldest first).
    """
    if window < 1 or scan_index < 2 * window:
        return []

    # We iterate over candidate pivot indices i in [window, scan_index - window].
    # The +window bars after i must exist in df (they do, since scan_index is
    # within df), and they must be at or before scan_index (which they are
    # since i + window <= scan_index by construction).
    pivots: list[Pivot] = []
    col = df["low"] if kind == "low" else df["high"]
    values = col.to_numpy()
    willrs = df["willr"].to_numpy()
    last_i = scan_index - window

    for i in range(window, last_i + 1):
        ref = values[i]
        left_edge  = values[i - window]
        right_edge = values[i + window]
        # Strict V-shape: ref must be the extremum in the window AND strictly
        # better than both endpoints. This avoids flat-region false pivots
        # (which would yield infinitely many "swing lows" in a sideways tape).
        if kind == "low":
            if ref != values[i - window: i + window + 1].min():
                continue
            if not (ref < left_edge and ref < right_edge):
                continue
        else:
            if ref != values[i - window: i + window + 1].max():
                continue
            if not (ref > left_edge and ref > right_edge):
                continue
        # Skip if willr is NaN at the pivot (early bars before warm-up)
        w = willrs[i]
        if pd.isna(w):
            continue
        pivots.append(Pivot(index=i, kind=kind, price=float(ref), willr=float(w)))

    return pivots


# ----------------------------------------------------------- divergence

def detect_bullish_divergence(
    df: pd.DataFrame, *,
    scan_index: int,
    pivot_window: int,
    lookback_bars: int,
) -> Optional[Divergence]:
    """Two most recent confirmed swing lows; check for price-LL + willr-HL.

    Returns the divergence if found, else None. Uses ONLY the two most
    recent confirmed pivots (consecutive lows) — we don't search for any
    pair in the history, which would let small intermediate lows pollute.
    """
    pivots = detect_swing_pivots(df, scan_index=scan_index,
                                 window=pivot_window, kind="low")
    if len(pivots) < 2:
        return None
    p1, p2 = pivots[-2], pivots[-1]
    if p2.index - p1.index > lookback_bars:
        return None
    if not (p2.price < p1.price and p2.willr > p1.willr):
        return None
    return Divergence(direction="LONG", pivot1=p1, pivot2=p2)


def detect_bearish_divergence(
    df: pd.DataFrame, *,
    scan_index: int,
    pivot_window: int,
    lookback_bars: int,
) -> Optional[Divergence]:
    """Mirror of bullish — two recent swing highs, price-HH + willr-LH."""
    pivots = detect_swing_pivots(df, scan_index=scan_index,
                                 window=pivot_window, kind="high")
    if len(pivots) < 2:
        return None
    p1, p2 = pivots[-2], pivots[-1]
    if p2.index - p1.index > lookback_bars:
        return None
    if not (p2.price > p1.price and p2.willr < p1.willr):
        return None
    return Divergence(direction="SHORT", pivot1=p1, pivot2=p2)
