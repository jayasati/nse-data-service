"""
ORB + VWAP benchmark engine (FEATURE_CHECKLIST Phase 3, Week 11, task 11.5).

A deliberately simple Opening Range Breakout to serve as the **benchmark** every
future strategy must beat. Per session:

  1. Opening range = high/low of the first `opening_range_bars` 5-min bars
     (3 bars = 09:15–09:30).
  2. After 09:30, go LONG when a bar's high breaks the OR high AND its close is
     above session VWAP (the filter). Long-only.
  3. Fill on the NEXT bar's open (no same-bar fill — anti-lookahead).
  4. ATR-based stop: SL = entry − atr_mult × ATR (ATR over the last
     `atr_length` 5-min bars known at the signal bar). Target = entry +
     rr_target × risk.
  5. SL-first within a bar (conservative tie-break). One trade per session;
     force-exit at the session's last bar close if neither level is hit.
"""

from __future__ import annotations

import math
import sqlite3
from typing import cast

import pandas as pd

from ..._core.types import Signal, Trade
from .bars import read_intraday_5m
from .config import OrbVwapConfig


def run_backtest_for_symbol(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    cfg: OrbVwapConfig,
) -> tuple[list[Signal], list[Trade]]:
    df = read_intraday_5m(conn, symbol, start_date=start_date, end_date=end_date)
    if df.empty:
        return [], []

    signals: list[Signal] = []
    trades: list[Trade] = []
    for _, day in df.groupby("session", sort=True):
        s, t = _run_session(day.reset_index(), cfg)
        signals.extend(s)
        trades.extend(t)
    return signals, trades


def _run_session(d: pd.DataFrame, cfg: OrbVwapConfig):
    """One trading session. `d` is a ts-reset frame ordered ascending."""
    n = len(d)
    orb = cfg.opening_range_bars
    if n <= orb:                       # need OR + at least one post-OR bar
        return [], []

    high = d["high"].to_numpy(dtype=float)
    low = d["low"].to_numpy(dtype=float)
    close = d["close"].to_numpy(dtype=float)
    open_ = d["open"].to_numpy(dtype=float)
    ts = d["ts"].to_numpy()

    or_high = float(high[:orb].max())
    vwap = _session_vwap(d)
    atr = _atr(high, low, close, cfg.atr_length)

    for i in range(orb, n - 1):        # leave a bar for the next-bar fill
        if high[i] > or_high and close[i] > vwap[i] and not math.isnan(atr[i]) and atr[i] > 0:
            entry = float(open_[i + 1])
            sl = entry - cfg.atr_mult * atr[i]
            risk = entry - sl
            if risk <= 0:
                continue
            target = entry + cfg.rr_target * risk
            qty = max(1, int(cfg.notional_per_trade // entry))

            exit_idx, exit_price, reason = _resolve_exit(
                low, high, close, i + 1, n, sl, target,
            )
            sig = Signal(direction="LONG", setup_ts=int(ts[i]),
                         rr=cfg.rr_target, armed=True)
            trade = Trade(
                direction="LONG", setup_ts=int(ts[i]), entry_ts=int(ts[i + 1]),
                entry_price=entry, sl=sl, target=target,
                exit_ts=int(ts[exit_idx]), exit_price=exit_price,
                exit_reason=reason, rr_at_entry=cfg.rr_target, qty=qty,
                signal_tags="orb_vwap",
            )
            return [sig], [trade]       # one trade per session
    return [], []


def _resolve_exit(low, high, close, start, n, sl, target):
    """Scan bars [start, n) for SL/target (SL-first); else EOD last close."""
    for j in range(start, n):
        if low[j] <= sl:
            return j, sl, "STOP"
        if high[j] >= target:
            return j, target, "TARGET"
    last = n - 1
    return last, float(close[last]), "EOD"


def _session_vwap(d: pd.DataFrame):
    tp = (d["high"] + d["low"] + d["close"]) / 3.0
    vol = d["volume"].clip(lower=0)
    cum_pv = (tp * vol).cumsum()
    cum_v = vol.cumsum().replace(0, pd.NA)
    return cast(pd.Series, (cum_pv / cum_v).ffill().fillna(tp)).to_numpy(dtype=float)


def _atr(high, low, close, length: int):
    """ATR known at bar i: mean true range over the `length` bars ending at i."""
    n = len(high)
    tr = [float("nan")] * n
    for i in range(n):
        if i == 0:
            tr[i] = high[i] - low[i]
        else:
            tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]),
                        abs(low[i] - close[i - 1]))
    atr = [float("nan")] * n
    for i in range(n):
        if i + 1 >= length:
            atr[i] = sum(tr[i - length + 1: i + 1]) / length
    return atr
