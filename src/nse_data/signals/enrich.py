"""
Signal enrichment — attach the live indicator context to a signal (task 4.8).

When the detector fires, it wants the symbol's full live-indicator picture at
that instant: VWAP and its slope, RSI(5m), the trend/momentum tags, ATR. That
picture already exists — the live-snapshot job writes it every minute to the
Redis hash `ind:{symbol}` (hot path) and to the `indicator_live` table (source
of truth). This module just reads it back and normalises the types.

Read order:
  1. Redis `ind:{symbol}` — the minute-fresh hot copy. Values are stored as
     strings with None encoded as "" (see live_snapshot.flush_to_redis), so we
     parse numerics back to float and turn "" into None.
  2. `indicator_live` in SQLite — fallback when Redis is down/cold or `conn` is
     the only thing the caller has.

The returned dict is consumed by `feature_store.snapshot_features` to populate
the ML archive, and (Week 5) by the confidence scorer and Telegram formatter.
A symbol with no live row yields the same dict shape with every value None.
"""

from __future__ import annotations

import sqlite3

# The live-indicator fields carried on the context, in indicator_live order.
# `updated_at` is included so consumers can detect a stale snapshot.
_NUMERIC_FIELDS = ("vwap", "vwap_slope", "atr_14_daily", "atr_14_5m", "rsi_5m")
_TEXT_FIELDS = ("updated_at", "price_vs_vwap", "trend_regime", "momentum_state")
CONTEXT_FIELDS = (*_TEXT_FIELDS, *_NUMERIC_FIELDS)


def read_live_context(redis_client, symbol: str, conn: sqlite3.Connection | None = None) -> dict:
    """Live indicator context for `symbol` — Redis first, SQLite fallback.

    Always returns a dict keyed by CONTEXT_FIELDS; missing inputs are None.
    """
    ctx = _from_redis(redis_client, symbol)
    if ctx is not None:
        return ctx
    if conn is not None:
        ctx = _from_sqlite(conn, symbol)
        if ctx is not None:
            return ctx
    return {f: None for f in CONTEXT_FIELDS}


def _from_redis(redis_client, symbol: str) -> dict | None:
    """Parse the `ind:{symbol}` hash, or None if Redis is absent/empty/erroring."""
    if redis_client is None:
        return None
    try:
        raw = redis_client.hgetall(f"ind:{symbol}")
    except Exception:
        return None
    if not raw:
        return None

    ctx: dict = {}
    for f in _TEXT_FIELDS:
        value = raw.get(f)
        ctx[f] = value if value else None        # "" / missing -> None
    for f in _NUMERIC_FIELDS:
        ctx[f] = _to_float(raw.get(f))
    return ctx


def _from_sqlite(conn: sqlite3.Connection, symbol: str) -> dict | None:
    """Read the indicator_live row, or None if the table/row is absent."""
    if not _has_table(conn, "indicator_live"):
        return None
    cols = ",".join(CONTEXT_FIELDS)
    row = conn.execute(
        f"SELECT {cols} FROM indicator_live WHERE symbol = ?", (symbol,),
    ).fetchone()
    if row is None:
        return None
    return dict(zip(CONTEXT_FIELDS, row))


def _to_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,),
    ).fetchone() is not None
