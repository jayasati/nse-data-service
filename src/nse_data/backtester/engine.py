"""Backtest engine — single-symbol bar-by-bar state machine.

## Invariants (do not change without updating tests/backtester/test_engine_*.py)

1. **Anti-lookahead.** At bar index `i` the detector sees only df.iloc[:i+1].
   A Setup detected at bar `i` may only fill on bar `i+1` or later.

2. **SL-first within a bar.** If a bar's [low, high] straddles both SL and
   target, exit = SL. This is the conservative convention — when we don't
   know the within-bar order, assume the worst. Documented; a 1-min finer
   simulation is a v2.

3. **One trade per symbol per session.** A new Setup is ignored if any trade
   already filled today in this symbol. (`cfg.allow_reentry=True` flips this.)

4. **Force exit at 15:15 IST.** If a position is open going into the 15:15
   bar, it exits at that bar's open price with reason `EOD_1515`.

5. **No new entries when bar_start_ist >= 14:45.** "no_entry_after=14:30"
   means bars starting at 14:45 and 15:15 cannot open a new position.

6. **Setup expiration.** A pending Setup that doesn't fill by end of the
   same IST session is discarded (no carry-over to next day).

## State machine, per bar i:

    a) session reset (IST day changed since i-1):
         clear pending Setup, reset trade-today flag, force-close any
         leaked open position with reason EOD_1515 at bar i open.
    b) if open position: check exits — SL first, target second; or
         force-close if bar_start == 15:15.
    c) if flat AND pending Setup AND bar_start_ist <= 14:30 AND
       (allow_reentry OR not already traded today):
         attempt fill — LONG fills if bar.high >= entry_trigger;
         SHORT fills if bar.low <= entry_trigger. Filled at entry_trigger.
    d) if flat AND no pending Setup AND bar_start_ist <= 14:30 AND
       (allow_reentry OR not already traded today):
         run detect_long_setup / detect_short_setup on df.iloc[:i+1].
         If detected, record signal; if rr >= rr_min, arm as pending.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd

from .bars import read_intraday_30m
from .config import BacktestConfig
from .indicators import add_bb_ema9
from .signals import Setup, detect_long_setup, detect_short_setup

Direction = Literal["LONG", "SHORT"]
ExitReason = Literal["TARGET", "STOP", "EOD_1515"]

_IST_OFFSET = 19800            # +5:30 in seconds


@dataclass(frozen=True)
class Signal:
    """A detected pattern, regardless of whether it became a trade."""
    direction: Direction
    setup_ts: int
    rr: float
    armed: bool                # passed rr_min AND not already-traded-today


@dataclass(frozen=True)
class Trade:
    direction: Direction
    setup_ts: int
    entry_ts: int
    entry_price: float
    sl: float
    target: float
    exit_ts: int
    exit_price: float
    exit_reason: ExitReason
    rr_at_entry: float
    qty: int = 1

    def pnl_raw(self) -> float:
        sign = 1.0 if self.direction == "LONG" else -1.0
        return (self.exit_price - self.entry_price) * self.qty * sign

    def pnl_leveraged(self, leverage: float) -> float:
        return self.pnl_raw() * leverage


# ------------------------------------------------------------- public API

def run_backtest_for_symbol(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    cfg: BacktestConfig | None = None,
) -> tuple[list[Signal], list[Trade]]:
    cfg = cfg or BacktestConfig()
    bars = read_intraday_30m(conn, symbol, start_date=start_date, end_date=end_date)
    return run_backtest_on_bars(bars, cfg)


def run_backtest_on_bars(
    bars: pd.DataFrame,
    cfg: BacktestConfig,
) -> tuple[list[Signal], list[Trade]]:
    """Engine core. `bars` is a 30-min OHLCV DataFrame from `read_intraday_30m`
    (or constructed in tests). If `bars` already has indicator columns
    (upper/lower/ema9/x/y), indicator recomputation is skipped — useful in
    tests where we hand-craft small windows that wouldn't satisfy BB(20)."""
    if bars.empty:
        return [], []

    has_bands = {"upper", "lower", "ema9"}.issubset(bars.columns)
    if has_bands:
        df = bars.copy()
        if "x" not in df.columns:
            df["x"] = df["upper"] - df["ema9"]
        if "y" not in df.columns:
            df["y"] = df["ema9"] - df["lower"]
    else:
        df = add_bb_ema9(bars, cfg)
    signals: list[Signal] = []
    trades: list[Trade] = []

    pending: Optional[Setup] = None
    pos: Optional[_OpenPosition] = None
    current_ist_day: Optional[int] = None
    traded_today = False

    no_entry_after_minutes = _hhmm_to_minutes(cfg.no_entry_after)
    force_exit_at_minutes = _hhmm_to_minutes(cfg.force_exit_at)

    timestamps = [int(x) for x in df.index.values.tolist()]
    rows = df.to_dict(orient="records")

    for i, (ts, row) in enumerate(zip(timestamps, rows)):
        bar_ist_day = _ist_day_index(ts)
        bar_ist_min = _ist_minutes(ts)

        # 1) Session reset
        if current_ist_day is None:
            current_ist_day = bar_ist_day
        elif bar_ist_day != current_ist_day:
            if pos is not None:
                trades.append(_force_close(pos, ts, float(row["open"]), "EOD_1515"))
                pos = None
            pending = None
            traded_today = False
            current_ist_day = bar_ist_day

        # 2) Exit logic for an open position
        if pos is not None:
            if bar_ist_min >= force_exit_at_minutes:
                trades.append(_force_close(pos, ts, float(row["open"]), "EOD_1515"))
                pos = None
            else:
                exit_trade = _check_exit(pos, ts, row)
                if exit_trade is not None:
                    trades.append(exit_trade)
                    pos = None

        can_enter = (
            pos is None
            and bar_ist_min <= no_entry_after_minutes
            and (cfg.allow_reentry or not traded_today)
        )

        # 3) Fill a pending setup
        if can_enter and pending is not None:
            filled = _try_fill(pending, ts, row)
            if filled is not None:
                pos = filled
                pending = None
                traded_today = True

        # 4) Detect new signal on this bar (anti-lookahead: only df up to i)
        if can_enter and pending is None and pos is None:
            window = df.iloc[: i + 1]
            setup = detect_long_setup(window, cfg) or detect_short_setup(window, cfg)
            if setup is not None:
                armed = setup.rr >= cfg.rr_min
                signals.append(Signal(
                    direction=setup.direction,
                    setup_ts=setup.setup_ts,
                    rr=setup.rr,
                    armed=armed,
                ))
                if armed:
                    pending = setup

    # End of history: if a position is still open (bars ran out before 15:15),
    # close at last bar's close so the trade is accounted for.
    if pos is not None and timestamps:
        last_ts = timestamps[-1]
        last_close = float(rows[-1]["close"])
        trades.append(_force_close(pos, last_ts, last_close, "EOD_1515"))

    return signals, trades


# ----------------------------------------------------------- internals

@dataclass(frozen=True)
class _OpenPosition:
    direction: Direction
    setup_ts: int
    entry_ts: int
    entry_price: float
    sl: float
    target: float
    rr_at_entry: float


def _try_fill(setup: Setup, bar_ts: int, row: dict) -> Optional[_OpenPosition]:
    """Long fills if bar.high crosses entry_trigger; short fills if bar.low does.

    Filled at entry_trigger (we assume liquidity at the trigger price; no
    slippage modelling in MVP).
    """
    if setup.direction == "LONG":
        if float(row["high"]) >= setup.entry_trigger:
            return _OpenPosition(
                direction="LONG",
                setup_ts=setup.setup_ts,
                entry_ts=bar_ts,
                entry_price=setup.entry_trigger,
                sl=setup.sl,
                target=setup.target,
                rr_at_entry=setup.rr,
            )
    else:
        if float(row["low"]) <= setup.entry_trigger:
            return _OpenPosition(
                direction="SHORT",
                setup_ts=setup.setup_ts,
                entry_ts=bar_ts,
                entry_price=setup.entry_trigger,
                sl=setup.sl,
                target=setup.target,
                rr_at_entry=setup.rr,
            )
    return None


def _check_exit(pos: _OpenPosition, bar_ts: int, row: dict) -> Optional[Trade]:
    """SL-first within a bar. Returns Trade if exited, None otherwise."""
    high = float(row["high"])
    low  = float(row["low"])

    if pos.direction == "LONG":
        hit_sl     = low  <= pos.sl
        hit_target = high >= pos.target
        if hit_sl:
            return _make_trade(pos, bar_ts, pos.sl, "STOP")
        if hit_target:
            return _make_trade(pos, bar_ts, pos.target, "TARGET")
    else:
        hit_sl     = high >= pos.sl
        hit_target = low  <= pos.target
        if hit_sl:
            return _make_trade(pos, bar_ts, pos.sl, "STOP")
        if hit_target:
            return _make_trade(pos, bar_ts, pos.target, "TARGET")
    return None


def _force_close(pos: _OpenPosition, bar_ts: int, price: float,
                 reason: ExitReason) -> Trade:
    return _make_trade(pos, bar_ts, price, reason)


def _make_trade(pos: _OpenPosition, exit_ts: int, exit_price: float,
                reason: ExitReason) -> Trade:
    return Trade(
        direction=pos.direction,
        setup_ts=pos.setup_ts,
        entry_ts=pos.entry_ts,
        entry_price=pos.entry_price,
        sl=pos.sl,
        target=pos.target,
        exit_ts=exit_ts,
        exit_price=exit_price,
        exit_reason=reason,
        rr_at_entry=pos.rr_at_entry,
    )


def _ist_day_index(ts: int) -> int:
    return (ts + _IST_OFFSET) // 86400


def _ist_minutes(ts: int) -> int:
    """Minutes past midnight IST."""
    seconds_of_day = (ts + _IST_OFFSET) % 86400
    return seconds_of_day // 60


def _hhmm_to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


# Mark some traded-today is gated by the engine state; expose helpers for tests.
__all__ = [
    "Signal", "Trade", "run_backtest_for_symbol", "run_backtest_on_bars",
    "_OpenPosition", "_check_exit", "_try_fill", "_ist_minutes",
]
