"""Signal detection for the BB(20,2) + EMA9 "gap hunt" strategy.

A *Setup* is a detected pattern that may produce a *Trade*. The engine arms
a Setup as pending and waits for the next bar to fill (anti-lookahead).

## BUY (long) preconditions, in order:

  1. Uptrend on the 30-min chart, measured at the signal bar `i`:
        ema9[i] > ema9[i-5]                              (rising over last 5 bars)
        AND close > ema9 for >=3 of the last 5 bars      (price-above-mean)

  2. Gap-down at bar `i`:
        bar[i].open <= bar[i-1].close * (1 - gap_pct)
     With cfg.gap_mode == "overnight" the prior bar must be from a different
     IST session day (last bar of D-1 closing into the first bar of D).
     With cfg.gap_mode == "any" any consecutive-bar drop qualifies.

  3. After the gap, lower-band distance exceeds upper-band distance:
        y[i] > x[i]

  4. Bar `i` pierces the lower BB AND rejects from it:
        bar[i].low  <= lower[i]
        bar[i].close > lower[i]

Once detected, the engine watches the NEXT bar (i+1). If bar[i+1].high crosses
trigger = bar[i+1].open as a bullish confirmation (open and close both above
bar[i].close — i.e. a strong rejection candle), entry fills at trigger price.

In practice the spec is "wait for a bullish confirmation candle, enter above
its high." For a clean MVP we collapse "rejection bar" and "confirmation bar"
into the same logic: the confirmation IS bar i+1 if it closes above its open;
entry trigger is bar[i+1].high + tick; SL is bar[i].low - tick; target is
upper[i] at the setup bar.

## SELL (short) preconditions: mirror of BUY.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd

from .config import BacktestConfig

Direction = Literal["LONG", "SHORT"]


@dataclass(frozen=True)
class Setup:
    direction: Direction
    setup_ts: int            # bar where pattern was detected (the gap+pierce bar)
    entry_trigger: float     # price at which a Position opens
    sl: float
    target: float
    rr: float                # |target - entry_trigger| / |entry_trigger - sl|


# ---------------------------------------------------------------- helpers

def _same_ist_day(ts_a: int, ts_b: int) -> bool:
    """Are two UTC epoch seconds in the same IST calendar day?"""
    ist_a = (ts_a + 19800) // 86400
    ist_b = (ts_b + 19800) // 86400
    return ist_a == ist_b


def _has_uptrend(window: pd.DataFrame, lookback: int, min_above: int) -> bool:
    """Last `lookback` bars ending at the signal bar. EMA9 rising AND price
    above EMA9 in at least `min_above` of those bars."""
    if len(window) < lookback + 1:
        return False
    ema_now = window["ema9"].iloc[-1]
    ema_then = window["ema9"].iloc[-1 - lookback]
    if pd.isna(ema_now) or pd.isna(ema_then) or ema_now <= ema_then:
        return False

    recent = window.iloc[-lookback:]
    above = int((recent["close"] > recent["ema9"]).sum())
    return above >= min_above


def _has_downtrend(window: pd.DataFrame, lookback: int, min_below: int) -> bool:
    if len(window) < lookback + 1:
        return False
    ema_now = window["ema9"].iloc[-1]
    ema_then = window["ema9"].iloc[-1 - lookback]
    if pd.isna(ema_now) or pd.isna(ema_then) or ema_now >= ema_then:
        return False

    recent = window.iloc[-lookback:]
    below = int((recent["close"] < recent["ema9"]).sum())
    return below >= min_below


def _gap_qualifies(
    prev_close: float, this_open: float, prev_ts: int, this_ts: int,
    cfg: BacktestConfig, direction: Direction,
) -> bool:
    """Direction-aware gap test honoring cfg.gap_mode."""
    if cfg.gap_mode == "overnight" and _same_ist_day(prev_ts, this_ts):
        return False
    if prev_close <= 0:
        return False
    move = (this_open - prev_close) / prev_close
    if direction == "LONG":
        return move <= -cfg.gap_pct           # gap DOWN >= threshold
    return move >= cfg.gap_pct                # gap UP for SHORT


def _row_indicators_valid(row: pd.Series) -> bool:
    for col in ("upper", "lower", "ema9", "x", "y"):
        if bool(pd.isna(row[col])):
            return False
    return True


# ---------------------------------------------------------------- detection

def detect_long_setup(window: pd.DataFrame, cfg: BacktestConfig) -> Optional[Setup]:
    """`window` = all bars up to and including the signal bar. The signal
    bar is the gap-down + pierce bar (NOT yet the confirmation/entry bar).

    Returns Setup or None. Engine handles the entry-trigger fill on the
    next bar (anti-lookahead).
    """
    # Need uptrend_lookback+1 bars: the lookback comparison uses ema[i] vs
    # ema[i-lookback], and the gap check needs bar[i-1] -> bar[i]. With
    # lookback=5 that's 6 bars total — the signal bar plus 5 trailing.
    if len(window) < cfg.uptrend_lookback + 1:
        return None

    sig = window.iloc[-1]
    prev = window.iloc[-2]

    if not _row_indicators_valid(sig):
        return None

    if not _has_uptrend(window, cfg.uptrend_lookback, cfg.min_closes_above_ema):
        return None

    sig_ts = int(window.index.values[-1])
    prev_ts = int(window.index.values[-2])
    if not _gap_qualifies(
        float(prev["close"]), float(sig["open"]),
        prev_ts, sig_ts, cfg, "LONG",
    ):
        return None

    if not (float(sig["y"]) > float(sig["x"])):
        return None

    pierced = float(sig["low"]) <= float(sig["lower"])
    rejected = float(sig["close"]) > float(sig["lower"])
    if not (pierced and rejected):
        return None

    # Entry trigger = signal bar high + tick (we cross above the rejection
    # candle on the NEXT bar). SL = signal bar low - tick. Target = upper BB.
    entry = float(sig["high"]) + cfg.tick
    sl    = float(sig["low"])  - cfg.tick
    target = float(sig["upper"])

    risk = entry - sl
    reward = target - entry
    if risk <= 0 or reward <= 0:
        return None
    rr = reward / risk

    return Setup(
        direction="LONG",
        setup_ts=sig_ts,
        entry_trigger=entry,
        sl=sl,
        target=target,
        rr=rr,
    )


def detect_short_setup(window: pd.DataFrame, cfg: BacktestConfig) -> Optional[Setup]:
    """Mirror of detect_long_setup."""
    # Need uptrend_lookback+1 bars: the lookback comparison uses ema[i] vs
    # ema[i-lookback], and the gap check needs bar[i-1] -> bar[i]. With
    # lookback=5 that's 6 bars total — the signal bar plus 5 trailing.
    if len(window) < cfg.uptrend_lookback + 1:
        return None

    sig = window.iloc[-1]
    prev = window.iloc[-2]

    if not _row_indicators_valid(sig):
        return None

    if not _has_downtrend(window, cfg.uptrend_lookback, cfg.min_closes_above_ema):
        return None

    sig_ts = int(window.index.values[-1])
    prev_ts = int(window.index.values[-2])
    if not _gap_qualifies(
        float(prev["close"]), float(sig["open"]),
        prev_ts, sig_ts, cfg, "SHORT",
    ):
        return None

    if not (float(sig["x"]) > float(sig["y"])):
        return None

    pierced = float(sig["high"]) >= float(sig["upper"])
    rejected = float(sig["close"]) < float(sig["upper"])
    if not (pierced and rejected):
        return None

    entry = float(sig["low"])  - cfg.tick    # we cross below on next bar
    sl    = float(sig["high"]) + cfg.tick
    target = float(sig["lower"])

    risk = sl - entry
    reward = entry - target
    if risk <= 0 or reward <= 0:
        return None
    rr = reward / risk

    return Setup(
        direction="SHORT",
        setup_ts=sig_ts,
        entry_trigger=entry,
        sl=sl,
        target=target,
        rr=rr,
    )


# Exported for tests
__all__ = ["Setup", "detect_long_setup", "detect_short_setup", "_same_ist_day"]
