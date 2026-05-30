"""Daily swing engine for the Williams %R + MACD strategy.

## Invariants (do not change without updating tests)

1. **Anti-lookahead.** At scan bar `t` the detectors see only `df.iloc[:t+1]`.
2. **Pivot anti-lookahead.** Enforced inside divergence.py — see its docstring.
3. **SL-first within a bar.** If a daily bar's [low, high] straddles both
   SL and target, exit = SL (conservative tie-break).
4. **Gap-through SL.** With `gap_fill="open"` (default), if `bar.open <= sl`
   for a long stop, exit at `bar.open` (worse fill, realistic). With
   `gap_fill="sl"`, exit at SL exactly (parity with intraday engine).
5. **Position persistence.** A position carries across calendar days. No
   force-exit at any time of day. Multi-day holds are normal.
6. **End-of-history exit.** If a position is still open when bars run out,
   exit at the last bar's close with reason `EOD_HISTORY`. Distinct from
   v1's `EOD_1515` so production rows remain unambiguous.
7. **Re-entry guard.** With `allow_reentry=False` (default), at most one
   open position per symbol at a time. After exit, the next bar is eligible
   for a new setup.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from ..._core.types import Direction, ExitReason, Signal, Trade
from .bars import read_daily_bars
from .config import MacdWillrDailyConfig
from .indicators import add_macd_willr
from .signals import Setup, detect_long_setup, detect_short_setup


# ----------------------------------------------------------- public API

def run_backtest_for_symbol(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    cfg: MacdWillrDailyConfig | None = None,
) -> tuple[list[Signal], list[Trade]]:
    cfg = cfg or MacdWillrDailyConfig()
    bars = read_daily_bars(conn, symbol, start_date=start_date, end_date=end_date)
    return run_backtest_on_bars(bars, cfg)


def run_backtest_on_bars(
    bars: pd.DataFrame,
    cfg: MacdWillrDailyConfig,
) -> tuple[list[Signal], list[Trade]]:
    """Engine core. `bars` is daily OHLCV indexed by 'YYYY-MM-DD' ascending.

    If the indicator columns already exist (willr/macd/macd_signal/macd_hist),
    indicator recomputation is skipped — useful in tests where we hand-craft
    small windows that wouldn't satisfy MACD's warm-up.
    """
    if bars.empty:
        return [], []

    has_inds = {"willr", "macd", "macd_signal", "macd_hist"}.issubset(bars.columns)
    df = bars.copy() if has_inds else add_macd_willr(bars, cfg)

    signals: list[Signal] = []
    trades: list[Trade] = []

    pos: Optional[_OpenPosition] = None
    indices = list(df.index)
    total = len(df)

    for t in range(total):
        date = indices[t]
        row = df.iloc[t]

        # 1) Exits first (an open position can close THIS bar)
        if pos is not None:
            exit_trade = _check_exit(pos, str(date), row, cfg)
            if exit_trade is not None:
                trades.append(exit_trade)
                pos = None

        # Re-entry gate: if a trade just exited and allow_reentry is False,
        # we still allow a new setup on the next bar (the engine naturally
        # advances). The user's spec for "one trade per symbol per day"
        # doesn't translate; swing trades are already days apart by nature.

        # 2) Try to fill a setup detected at bar t-1 (anti-lookahead: setup
        #    decisions used df up to t-1 only; fill happens at bar t open).
        # But because our setup contains entry_ref = close[t-1], we structure
        # the loop differently: scan THIS bar (t) for a setup whose fill is
        # bar t+1 open. So below.

        # 3) Detect new setup at bar t (fills next bar)
        if pos is None and t + 1 < total:
            setup = _scan_setup(df, t, cfg)
            if setup is not None:
                armed = setup.rr >= cfg.rr_min
                signals.append(Signal(
                    direction=setup.direction,
                    setup_ts=_date_to_epoch(str(date)),
                    rr=setup.rr,
                    armed=armed,
                ))
                if armed:
                    # Fill at next bar's open
                    fill_price = float(df.iloc[t + 1]["open"])
                    # Sanity: corporate-action gap filter
                    prev_close = float(df.iloc[t]["close"])
                    if _gap_ok(prev_close, fill_price, cfg):
                        # Recompute target/SL relative to the actual fill price
                        # (keeps R:R correct when open differs from prev close)
                        adjusted = _adjust_sl_target_to_fill(setup, fill_price, cfg)
                        if adjusted is not None:
                            pos = _OpenPosition(
                                direction=setup.direction,
                                setup_date=str(date),
                                entry_date=str(indices[t + 1]),
                                entry_price=fill_price,
                                sl=adjusted[0],
                                target=adjusted[1],
                                rr_at_entry=setup.rr,
                                signal_tags=setup.signal_tags,
                            )

    # End-of-history exit
    if pos is not None:
        last_date = str(indices[-1])
        last_close = float(df.iloc[-1]["close"])
        trades.append(_make_trade(pos, last_date, last_close, "EOD_HISTORY", cfg))

    return signals, trades


# ----------------------------------------------------------- internals

@dataclass(frozen=True)
class _OpenPosition:
    direction: Direction
    setup_date: str
    entry_date: str
    entry_price: float
    sl: float
    target: float
    rr_at_entry: float
    signal_tags: str | None


def _scan_setup(df: pd.DataFrame, t: int, cfg: MacdWillrDailyConfig) -> Optional[Setup]:
    """Try long first, then short. Only one direction can fire at a time
    (mutually exclusive: oversold hook vs. overbought hook)."""
    return detect_long_setup(df, t, cfg) or detect_short_setup(df, t, cfg)


def _check_exit(pos: _OpenPosition, bar_date: str, row,
                cfg: MacdWillrDailyConfig) -> Optional[Trade]:
    """SL-first, then target, then willr-extreme. Gap-fill rule for SL."""
    open_  = float(row["open"])
    high   = float(row["high"])
    low    = float(row["low"])
    close  = float(row["close"])
    willr  = row["willr"]

    if pos.direction == "LONG":
        # Gap-down through SL?
        if open_ <= pos.sl:
            exit_price = open_ if cfg.gap_fill == "open" else pos.sl
            return _make_trade(pos, bar_date, exit_price, "STOP", cfg)
        if low <= pos.sl:
            return _make_trade(pos, bar_date, pos.sl, "STOP", cfg)
        if high >= pos.target:
            return _make_trade(pos, bar_date, pos.target, "TARGET", cfg)
        # Willr crossed into overbought zone — exit at close (signal is computed
        # from close; we don't peek at next bar's open here, exit on this bar).
        if not pd.isna(willr) and willr >= cfg.willr_overbought:
            return _make_trade(pos, bar_date, close, "WILLR_EXTREME", cfg)
    else:
        # SHORT: gap-up through SL
        if open_ >= pos.sl:
            exit_price = open_ if cfg.gap_fill == "open" else pos.sl
            return _make_trade(pos, bar_date, exit_price, "STOP", cfg)
        if high >= pos.sl:
            return _make_trade(pos, bar_date, pos.sl, "STOP", cfg)
        if low <= pos.target:
            return _make_trade(pos, bar_date, pos.target, "TARGET", cfg)
        if not pd.isna(willr) and willr <= cfg.willr_oversold:
            return _make_trade(pos, bar_date, close, "WILLR_EXTREME", cfg)
    return None


def _adjust_sl_target_to_fill(
    setup: Setup, fill_price: float, cfg: MacdWillrDailyConfig,
) -> tuple[float, float] | None:
    """Keep the absolute SL fixed (it was anchored to the swing low/high) and
    recompute target so R:R is maintained against the actual fill price.
    Returns None if SL and fill_price are on the wrong side of each other."""
    if setup.direction == "LONG":
        if setup.sl >= fill_price:
            return None
        risk = fill_price - setup.sl
        target = fill_price + cfg.rr_target * risk
        return setup.sl, target
    else:
        if setup.sl <= fill_price:
            return None
        risk = setup.sl - fill_price
        target = fill_price - cfg.rr_target * risk
        return setup.sl, target


def _gap_ok(prev_close: float, this_open: float, cfg: MacdWillrDailyConfig) -> bool:
    """Skip corporate-action-sized gaps (likely splits/bonuses, not real moves)."""
    if prev_close <= 0:
        return False
    gap = abs(this_open - prev_close) / prev_close
    return gap <= cfg.max_gap_pct


def _make_trade(pos: _OpenPosition, exit_date: str, exit_price: float,
                reason: ExitReason, cfg: MacdWillrDailyConfig) -> Trade:
    qty = _qty_for(pos.entry_price, cfg.notional_per_trade)
    return Trade(
        direction=pos.direction,
        setup_ts=_date_to_epoch(pos.setup_date),
        entry_ts=_date_to_epoch(pos.entry_date),
        entry_price=pos.entry_price,
        sl=pos.sl,
        target=pos.target,
        exit_ts=_date_to_epoch(exit_date),
        exit_price=exit_price,
        exit_reason=reason,
        rr_at_entry=pos.rr_at_entry,
        qty=qty,
        signal_tags=pos.signal_tags,
    )


def _qty_for(entry_price: float, notional: float) -> int:
    if entry_price <= 0 or notional <= 0:
        return 1
    return max(1, math.floor(notional / entry_price))


def _date_to_epoch(date_str: str) -> int:
    """'YYYY-MM-DD' → UTC midnight epoch. Engine treats dates as integer days
    for the persistence layer (which expects epoch seconds)."""
    from datetime import datetime, timezone
    return int(datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc).timestamp())


__all__ = [
    "run_backtest_for_symbol", "run_backtest_on_bars",
    "_OpenPosition", "_check_exit",
]
