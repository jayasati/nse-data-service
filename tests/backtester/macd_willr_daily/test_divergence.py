"""Swing pivot detection + bullish/bearish divergence."""

from __future__ import annotations

import pandas as pd

from nse_data.backtester.strategies.macd_willr_daily.divergence import (
    detect_bearish_divergence,
    detect_bullish_divergence,
    detect_swing_pivots,
)


def _df(highs: list[float], lows: list[float], willrs: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "open":   [(h + l) / 2 for h, l in zip(highs, lows)],
        "high":   highs,
        "low":    lows,
        "close":  [(h + l) / 2 for h, l in zip(highs, lows)],
        "volume": [100] * len(highs),
        "willr":  willrs,
    })


# ============================================================ pivot detection

def test_swing_low_detected_at_correct_index_after_window_passes():
    # 13 bars: low at index 5 is the swing low
    # window=3 — confirmation requires scan_index >= 5 + 3 = 8
    lows  = [100, 99, 98, 97, 96, 90, 92, 94, 96, 98, 100, 102, 104]
    highs = [101 + i for i in range(13)]
    willrs = [-50.0] * 13
    df = _df(highs, lows, willrs)

    # Before confirmation: scan_index=7, pivot at i=5 needs scan_index>=8
    pivots_early = detect_swing_pivots(df, scan_index=7, window=3, kind="low")
    assert pivots_early == [], "should not see unconfirmed pivot"

    # At confirmation: scan_index=8
    pivots_now = detect_swing_pivots(df, scan_index=8, window=3, kind="low")
    assert len(pivots_now) == 1
    assert pivots_now[0].index == 5
    assert pivots_now[0].price == 90
    assert pivots_now[0].kind == "low"


def test_swing_high_detection_symmetric_to_low():
    highs = [100, 101, 102, 103, 104, 110, 108, 106, 104, 102, 100, 98, 96]
    lows  = [99 - i * 0.1 for i in range(13)]
    willrs = [-30.0] * 13
    df = _df(highs, lows, willrs)

    pivots = detect_swing_pivots(df, scan_index=8, window=3, kind="high")
    assert len(pivots) == 1
    assert pivots[0].index == 5
    assert pivots[0].price == 110


def test_pivots_with_nan_willr_are_skipped():
    """Early bars where willr hasn't warmed up yet should not yield pivots."""
    lows = [100, 99, 98, 97, 96, 90, 92, 94, 96, 98, 100, 102, 104]
    highs = [101 + i for i in range(13)]
    willrs = [float("nan")] * 6 + [-50.0] * 7   # NaN until i=5; pivot at i=5 has NaN willr
    df = _df(highs, lows, willrs)

    pivots = detect_swing_pivots(df, scan_index=10, window=3, kind="low")
    # The pivot at i=5 has willr=NaN -> skipped
    assert pivots == []


def test_short_history_returns_empty():
    df = _df([100, 101, 102], [99, 98, 97], [-50, -50, -50])
    assert detect_swing_pivots(df, scan_index=2, window=3, kind="low") == []


# ============================================================ divergence

def _build_two_low_series(
    *, ll1_index: int, ll2_index: int,
    ll1_price: float, ll2_price: float,
    ll1_willr: float, ll2_willr: float,
    total_bars: int,
) -> pd.DataFrame:
    """Build a price series with two distinct swing lows at given indices.
    Outside the immediate pivot windows the price is flat-high so the pivots
    stand out unambiguously."""
    lows  = [110.0] * total_bars
    highs = [115.0] * total_bars
    willrs = [-30.0] * total_bars
    # Carve the first valley
    lows[ll1_index] = ll1_price
    willrs[ll1_index] = ll1_willr
    # And the second
    lows[ll2_index] = ll2_price
    willrs[ll2_index] = ll2_willr
    return _df(highs, lows, willrs)


def test_bullish_divergence_detected_when_price_lower_low_willr_higher_low():
    # Two valleys: LL1 at i=10 (price 100, willr -90), LL2 at i=25 (price 95, willr -85)
    # Price LL: 95 < 100. Willr HL: -85 > -90. → Bullish divergence.
    df = _build_two_low_series(
        ll1_index=10, ll2_index=25,
        ll1_price=100.0, ll2_price=95.0,
        ll1_willr=-90.0, ll2_willr=-85.0,
        total_bars=40,
    )

    div = detect_bullish_divergence(
        df, scan_index=35, pivot_window=3, lookback_bars=60,
    )
    assert div is not None
    assert div.direction == "LONG"
    assert div.pivot1.index == 10
    assert div.pivot2.index == 25
    assert div.pivot2.price < div.pivot1.price
    assert div.pivot2.willr > div.pivot1.willr


def test_bullish_divergence_rejected_when_willr_also_made_lower_low():
    # Both price AND willr made lower lows — NOT a divergence (just a downtrend).
    df = _build_two_low_series(
        ll1_index=10, ll2_index=25,
        ll1_price=100.0, ll2_price=95.0,
        ll1_willr=-85.0, ll2_willr=-95.0,    # willr LOWER, not higher
        total_bars=40,
    )

    div = detect_bullish_divergence(df, scan_index=35, pivot_window=3, lookback_bars=60)
    assert div is None


def test_bearish_divergence_detected():
    # Two peaks: HH1 at i=10 (price 120, willr -10), HH2 at i=25 (price 125, willr -15)
    # Price HH: 125 > 120. Willr LH: -15 < -10. → Bearish divergence.
    highs = [105.0] * 40
    lows  = [100.0] * 40
    willrs = [-30.0] * 40
    highs[10] = 120.0; willrs[10] = -10.0
    highs[25] = 125.0; willrs[25] = -15.0
    df = _df(highs, lows, willrs)

    div = detect_bearish_divergence(
        df, scan_index=35, pivot_window=3, lookback_bars=60,
    )
    assert div is not None
    assert div.direction == "SHORT"
    assert div.pivot1.index == 10
    assert div.pivot2.index == 25


def test_divergence_rejected_when_pivots_outside_lookback_window():
    df = _build_two_low_series(
        ll1_index=10, ll2_index=25,
        ll1_price=100.0, ll2_price=95.0,
        ll1_willr=-90.0, ll2_willr=-85.0,
        total_bars=40,
    )
    # Distance = 25 - 10 = 15 bars. Set lookback to 10 → reject.
    div = detect_bullish_divergence(
        df, scan_index=35, pivot_window=3, lookback_bars=10,
    )
    assert div is None


# ============================================================ anti-lookahead

def test_divergence_anti_lookahead():
    """Critical invariant. A future pivot must not influence the present scan.

    Build a series where bar i=20 IS a swing low (price 95, willr -85) that
    would form a divergence with the earlier swing low at i=10 (100, -90).
    Scan at t=22 (pivot_window=3): bar 20 needs i+3=23 <= t. 23 > 22, so the
    pivot at 20 is NOT yet confirmed. → No divergence.
    Then scan at t=23 — now confirmed. → Divergence appears.
    """
    df = _build_two_low_series(
        ll1_index=10, ll2_index=20,
        ll1_price=100.0, ll2_price=95.0,
        ll1_willr=-90.0, ll2_willr=-85.0,
        total_bars=30,
    )

    not_yet = detect_bullish_divergence(
        df, scan_index=22, pivot_window=3, lookback_bars=60,
    )
    confirmed = detect_bullish_divergence(
        df, scan_index=23, pivot_window=3, lookback_bars=60,
    )

    assert not_yet is None, "pivot at i=20 must NOT be visible at scan_index=22"
    assert confirmed is not None, "pivot at i=20 is confirmed at scan_index=23"
    assert confirmed.pivot2.index == 20
