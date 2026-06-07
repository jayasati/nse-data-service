"""Daily-bar engine for the 52-week-high breakout strategy.

## Invariants (mirrors the macd_willr_daily engine; keep tests in sync)

1. **Anti-lookahead.** A setup at scan bar `t` uses the prior-window high
   (`high[t-lookback : t]`, excluding `t`) and the trailing volume average
   (also excluding `t`). The breakout is "today's high exceeds that prior high".
2. **Next-bar fill.** An armed setup at bar `t` fills at bar `t+1`'s open
   (no same-bar fill), skipping corporate-action-sized gaps.
3. **SL-first within a bar.** If a bar straddles both SL and target, exit = SL.
4. **Gap-through SL.** `gap_fill="open"` exits at the gapped open; `"sl"` at SL.
5. **Long-only.** A 52w-high breakout is a long setup.
6. **Bounded hold.** Exit at close after `max_hold_days` if neither level hits
   ('MAX_HOLD'); if bars run out while open, exit at the last close
   ('EOD_HISTORY').
7. **One position at a time** (re-entry after exit on a later bar).
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, cast

import pandas as pd

from ..._core.types import Signal, Trade
from ...strategies.macd_willr_daily.bars import read_daily_bars
from .config import Breakout52whConfig


# ----------------------------------------------------------- public API

def run_backtest_for_symbol(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    cfg: Breakout52whConfig | None = None,
) -> tuple[list[Signal], list[Trade]]:
    cfg = cfg or Breakout52whConfig()
    bars = read_daily_bars(conn, symbol, start_date=start_date, end_date=end_date)
    return run_backtest_on_bars(bars, cfg)


def run_backtest_on_bars(
    bars: pd.DataFrame,
    cfg: Breakout52whConfig,
) -> tuple[list[Signal], list[Trade]]:
    """Engine core. `bars` is daily OHLCV indexed by 'YYYY-MM-DD' ascending."""
    if bars.empty:
        return [], []

    df = _add_features(bars, cfg)
    signals: list[Signal] = []
    trades: list[Trade] = []

    pos: Optional[_OpenPosition] = None
    indices = list(df.index)
    total = len(df)

    for t in range(total):
        date = str(indices[t])
        row = df.iloc[t]

        # 1) Exits first (an open position can close on this bar).
        if pos is not None:
            exit_trade = _check_exit(pos, date, row, t, cfg)
            if exit_trade is not None:
                trades.append(exit_trade)
                pos = None

        # 2) Detect a new breakout setup at bar t (fills next bar's open).
        if pos is None and t + 1 < total:
            setup = _detect_breakout(df, t, cfg)
            if setup is not None:
                signals.append(Signal(
                    direction="LONG",
                    setup_ts=_date_to_epoch(date),
                    rr=setup.rr,
                    armed=setup.rr >= cfg.rr_min,
                ))
                if setup.rr >= cfg.rr_min:
                    pos = _fill_next_bar(df, t, setup, cfg)

    # End-of-history exit.
    if pos is not None:
        trades.append(_make_trade(
            pos, str(indices[-1]), float(df.iloc[-1]["close"]), "EOD_HISTORY", cfg,
        ))

    return signals, trades


# ----------------------------------------------------------- features

def _add_features(bars: pd.DataFrame, cfg: Breakout52whConfig) -> pd.DataFrame:
    """Attach prior-window 52w high, trailing volume average, and ATR(14).

    All three are shifted by one bar so a scan at `t` never sees bar `t` itself
    (anti-lookahead) — except ATR, which legitimately includes the setup bar's
    range (it sizes the bracket placed after that bar).
    """
    df = bars.copy()
    # Prior 52w high: rolling max of high over the window ENDING at t-1.
    df["hi_prior"] = cast(
        pd.Series,
        df["high"].rolling(cfg.lookback_52w, min_periods=cfg.min_history).max(),
    ).shift(1)
    # Trailing average volume over the window ending at t-1.
    df["vol_avg"] = cast(
        pd.Series,
        df["volume"].rolling(cfg.vol_lookback, min_periods=cfg.vol_lookback).mean(),
    ).shift(1)
    df["atr"] = _atr(df, cfg.atr_length)
    return df


def _atr(df: pd.DataFrame, length: int) -> pd.Series:
    """Wilder-style ATR via an EWM of the true range."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return cast(pd.Series, tr.ewm(alpha=1.0 / length, min_periods=length, adjust=False).mean())


# ----------------------------------------------------------- setup detection

@dataclass(frozen=True)
class _Setup:
    rr: float
    atr: float


def _detect_breakout(df: pd.DataFrame, t: int, cfg: Breakout52whConfig) -> Optional[_Setup]:
    """A new-52w-high breakout confirmed by volume, with a usable ATR."""
    row = df.iloc[t]
    hi_prior = row["hi_prior"]
    vol_avg = row["vol_avg"]
    atr = row["atr"]
    if pd.isna(hi_prior) or pd.isna(vol_avg) or pd.isna(atr) or atr <= 0:
        return None
    if float(row["high"]) <= float(hi_prior):          # not a new high
        return None
    if vol_avg <= 0 or float(row["volume"]) / float(vol_avg) < cfg.vol_ratio_min:
        return None
    # 1.5×ATR each side -> reward/risk is exactly 1.0 (1R target).
    return _Setup(rr=1.0, atr=float(atr))


def _fill_next_bar(
    df: pd.DataFrame, t: int, setup: _Setup, cfg: Breakout52whConfig,
) -> Optional["_OpenPosition"]:
    """Fill at bar t+1's open; size the ATR bracket off the fill price."""
    fill_price = float(df.iloc[t + 1]["open"])
    prev_close = float(df.iloc[t]["close"])
    if not _gap_ok(prev_close, fill_price, cfg):
        return None
    band = cfg.atr_mult * setup.atr
    return _OpenPosition(
        setup_date=str(df.index[t]),
        entry_date=str(df.index[t + 1]),
        entry_bar=t + 1,
        entry_price=fill_price,
        sl=fill_price - band,
        target=fill_price + band,
        rr_at_entry=setup.rr,
    )


# ----------------------------------------------------------- exits

@dataclass(frozen=True)
class _OpenPosition:
    setup_date: str
    entry_date: str
    entry_bar: int
    entry_price: float
    sl: float
    target: float
    rr_at_entry: float


def _check_exit(
    pos: "_OpenPosition", bar_date: str, row, t: int, cfg: Breakout52whConfig,
) -> Optional[Trade]:
    """SL-first, then target, then the max-hold timeout (long-only)."""
    open_ = float(row["open"])
    high = float(row["high"])
    low = float(row["low"])
    close = float(row["close"])

    if open_ <= pos.sl:                                  # gap-down through SL
        exit_price = open_ if cfg.gap_fill == "open" else pos.sl
        return _make_trade(pos, bar_date, exit_price, "STOP", cfg)
    if low <= pos.sl:
        return _make_trade(pos, bar_date, pos.sl, "STOP", cfg)
    if high >= pos.target:
        return _make_trade(pos, bar_date, pos.target, "TARGET", cfg)
    if t - pos.entry_bar >= cfg.max_hold_days:           # held long enough, give up
        return _make_trade(pos, bar_date, close, "MAX_HOLD", cfg)
    return None


# ----------------------------------------------------------- helpers

def _gap_ok(prev_close: float, this_open: float, cfg: Breakout52whConfig) -> bool:
    if prev_close <= 0:
        return False
    return abs(this_open - prev_close) / prev_close <= cfg.max_gap_pct


def _make_trade(
    pos: "_OpenPosition", exit_date: str, exit_price: float, reason: str,
    cfg: Breakout52whConfig,
) -> Trade:
    return Trade(
        direction="LONG",
        setup_ts=_date_to_epoch(pos.setup_date),
        entry_ts=_date_to_epoch(pos.entry_date),
        entry_price=pos.entry_price,
        sl=pos.sl,
        target=pos.target,
        exit_ts=_date_to_epoch(exit_date),
        exit_price=exit_price,
        exit_reason=reason,
        rr_at_entry=pos.rr_at_entry,
        qty=_qty_for(pos.entry_price, cfg.notional_per_trade),
        signal_tags=None,
    )


def _qty_for(entry_price: float, notional: float) -> int:
    if entry_price <= 0 or notional <= 0:
        return 1
    return max(1, math.floor(notional / entry_price))


def _date_to_epoch(date_str: str) -> int:
    return int(datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc).timestamp())


__all__ = ["run_backtest_for_symbol", "run_backtest_on_bars",
           "_OpenPosition", "_check_exit"]
