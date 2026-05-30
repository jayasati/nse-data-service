"""Setup detection for the Williams %R + MACD daily swing strategy.

Two entry paths, OR'd together:

### Basic path (long)
1. **Hook from oversold:** there exists `k ∈ [1, willr_hook_lookback]` such that
   `willr[t-k] <= willr_oversold` AND `willr` has stayed strictly above
   `willr_oversold` from bar `t-k+1` through bar `t`.
2. **MACD filter:** `macd_hist[t] > 0` AND `macd[t] > macd_signal[t]`.
   (If `require_fresh_macd_cross=True`, also require `macd[t-1] ≤ macd_signal[t-1]`.)
3. **Setup:** entry at next bar's open; SL = `min(low[t-swing_lookback+1..t]) - tick`;
   target = `entry + rr_target × (entry - sl)`. Tag `signal_tags = "basic"`.

### Divergence path (long)
1. `detect_bullish_divergence` returns a Divergence at scan bar `t`.
2. No MACD filter required.
3. Same SL/target math. Tag `signal_tags = "divergence"`.

If both paths fire the same bar, tag `"basic+divergence"`.

### Short = mirror.

This module returns a `Setup` dataclass; the engine decides how/when to fill.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd

from .config import MacdWillrDailyConfig
from .divergence import detect_bearish_divergence, detect_bullish_divergence


Direction = Literal["LONG", "SHORT"]


@dataclass(frozen=True)
class Setup:
    """A daily-swing setup awaiting next-bar fill. The engine reads `entry_at`
    as the close of bar `t`; the fill happens at the open of bar `t+1`."""
    direction: Direction
    setup_index: int            # bar index `t` in the dataframe
    sl: float
    target: float
    rr: float                   # rr_target — fixed at construction
    signal_tags: str            # 'basic' | 'divergence' | 'basic+divergence'


# ----------------------------------------------------------- helpers

def _willr_hook(
    willr: pd.Series, t: int, level: float, lookback: int, direction: Direction,
) -> bool:
    """Did willr recently cross `level` from the wrong side?

    LONG: was <= level at some t-k, and is now strictly > level continuously.
    SHORT: mirror.
    """
    if t < 1:
        return False
    cur = willr.iloc[t]
    if pd.isna(cur):
        return False
    if direction == "LONG":
        if not (cur > level):
            return False
        # walk back; bar at t-k must have been <= level, and bars t-k+1..t > level
        for k in range(1, min(lookback, t) + 1):
            past = willr.iloc[t - k]
            if pd.isna(past):
                return False
            if past <= level:
                # Check that all intermediate bars stayed strictly above level
                for j in range(t - k + 1, t + 1):
                    if not (willr.iloc[j] > level):
                        return False
                return True
        return False
    else:
        if not (cur < level):
            return False
        for k in range(1, min(lookback, t) + 1):
            past = willr.iloc[t - k]
            if pd.isna(past):
                return False
            if past >= level:
                for j in range(t - k + 1, t + 1):
                    if not (willr.iloc[j] < level):
                        return False
                return True
        return False


def _macd_filter(
    df: pd.DataFrame, t: int, require_fresh_cross: bool, direction: Direction,
) -> bool:
    """Long: hist > 0 AND macd > signal. Short: hist < 0 AND macd < signal.
    With require_fresh_cross, also require the crossing happened at t-1 → t."""
    macd        = df["macd"].iloc[t]
    macd_signal = df["macd_signal"].iloc[t]
    hist        = df["macd_hist"].iloc[t]
    if pd.isna(macd) or pd.isna(macd_signal) or pd.isna(hist):
        return False
    if direction == "LONG":
        if not (hist > 0 and macd > macd_signal):
            return False
        if require_fresh_cross:
            if t < 1 or pd.isna(df["macd_hist"].iloc[t - 1]):
                return False
            return df["macd_hist"].iloc[t - 1] <= 0
        return True
    else:
        if not (hist < 0 and macd < macd_signal):
            return False
        if require_fresh_cross:
            if t < 1 or pd.isna(df["macd_hist"].iloc[t - 1]):
                return False
            return df["macd_hist"].iloc[t - 1] >= 0
        return True


def _sl_target_long(df: pd.DataFrame, t: int, cfg: MacdWillrDailyConfig,
                    entry_ref: float) -> tuple[float, float] | None:
    """Compute SL (lowest low in last `swing_lookback` bars - tick) and target.
    Returns None if SL >= entry_ref (no risk = no trade)."""
    lookback = max(1, cfg.swing_lookback)
    start = max(0, t - lookback + 1)
    sl = float(df["low"].iloc[start: t + 1].min()) - cfg.tick
    if sl >= entry_ref:
        return None
    target = entry_ref + cfg.rr_target * (entry_ref - sl)
    return sl, target


def _sl_target_short(df: pd.DataFrame, t: int, cfg: MacdWillrDailyConfig,
                     entry_ref: float) -> tuple[float, float] | None:
    lookback = max(1, cfg.swing_lookback)
    start = max(0, t - lookback + 1)
    sl = float(df["high"].iloc[start: t + 1].max()) + cfg.tick
    if sl <= entry_ref:
        return None
    target = entry_ref - cfg.rr_target * (sl - entry_ref)
    return sl, target


# ----------------------------------------------------------- public API

def detect_long_setup(
    df: pd.DataFrame, t: int, cfg: MacdWillrDailyConfig,
) -> Optional[Setup]:
    """Inspect bar `t` (last bar of the window) for a long setup. Fill is the
    engine's job on bar t+1.

    Anti-lookahead: only reads df.iloc[:t+1]. Divergence detector enforces
    its own pivot anti-lookahead.
    """
    if t < cfg.willr_length:        # warm-up
        return None
    if t + 1 >= len(df):            # need a next bar to fill on
        return None

    willr = df["willr"]
    if pd.isna(willr.iloc[t]):
        return None

    entry_ref = float(df["close"].iloc[t])  # approximation; engine uses next bar's open

    basic = (
        _willr_hook(willr, t, cfg.willr_oversold, cfg.willr_hook_lookback, "LONG")
        and _macd_filter(df, t, cfg.require_fresh_macd_cross, "LONG")
    )
    div = (
        cfg.use_divergence
        and detect_bullish_divergence(
            df, scan_index=t,
            pivot_window=cfg.pivot_window,
            lookback_bars=cfg.divergence_lookback,
        ) is not None
    )

    if not (basic or div):
        return None

    sltarget = _sl_target_long(df, t, cfg, entry_ref)
    if sltarget is None:
        return None
    sl, target = sltarget

    tags = "basic+divergence" if (basic and div) else ("basic" if basic else "divergence")
    rr = (target - entry_ref) / (entry_ref - sl)
    return Setup(direction="LONG", setup_index=t, sl=sl, target=target,
                 rr=rr, signal_tags=tags)


def detect_short_setup(
    df: pd.DataFrame, t: int, cfg: MacdWillrDailyConfig,
) -> Optional[Setup]:
    """Mirror of long. Spec wording is in long's docstring."""
    if t < cfg.willr_length:
        return None
    if t + 1 >= len(df):
        return None

    willr = df["willr"]
    if pd.isna(willr.iloc[t]):
        return None

    entry_ref = float(df["close"].iloc[t])

    basic = (
        _willr_hook(willr, t, cfg.willr_overbought, cfg.willr_hook_lookback, "SHORT")
        and _macd_filter(df, t, cfg.require_fresh_macd_cross, "SHORT")
    )
    div = (
        cfg.use_divergence
        and detect_bearish_divergence(
            df, scan_index=t,
            pivot_window=cfg.pivot_window,
            lookback_bars=cfg.divergence_lookback,
        ) is not None
    )

    if not (basic or div):
        return None

    sltarget = _sl_target_short(df, t, cfg, entry_ref)
    if sltarget is None:
        return None
    sl, target = sltarget

    tags = "basic+divergence" if (basic and div) else ("basic" if basic else "divergence")
    rr = (entry_ref - target) / (sl - entry_ref)
    return Setup(direction="SHORT", setup_index=t, sl=sl, target=target,
                 rr=rr, signal_tags=tags)
