"""
Read merged historical + live 5-minute OHLCV for one symbol.

Two sources, both keyed by UTC epoch seconds at bar start:

* **Historical** — 1-minute bars from `raw_intraday_candles` (broker backfill;
  populated by `scripts/backfill_intraday.py` against Kite Connect by default).
  These cover prior sessions; today's row arrives the next morning.

* **Live (today only)** — 1-minute bars synthesized on demand from
  `raw_equity_quotes` snapshots (the broad live feed). For each minute bucket
  we fold the snapshots inside it into a degenerate OHLC (open=first price,
  high/low=min/max, close=last; volume from cumulative-volume deltas).

The two streams are merged with day-level dedup — any day already present in
historical wins. The combined 1-minute series is then resampled to 5-minute
buckets aligned to the UTC minute (and therefore also to the IST clock, since
the IST offset is a whole multiple of 300 seconds).

The returned DataFrame is indexed by `ts` (epoch seconds, ascending) with the
columns `open`, `high`, `low`, `close`, `volume` — same shape that
`indicators/ohlcv.py:read_ohlcv` returns for the EOD path, so the orchestrator
and Indicator subclasses can stay source-agnostic.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable

import pandas as pd

from ..webcore.config import LIVE_INDEX

_OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")
_BUCKET_SECONDS = 300


def read_intraday_5m(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    since_ts: int | None = None,
) -> pd.DataFrame:
    """5-minute OHLCV for `symbol`, indexed by `ts` (UTC epoch) ascending.

    `since_ts`: if set, only bars at or after this epoch are returned. The
    orchestrator passes `last_computed_ts − min_history × 300` so rolling
    indicators have their warm-up window without pulling years of intraday.
    """
    hist = _read_historical_1m(conn, symbol, since_ts)
    live = _build_live_1m(conn, symbol, since_ts)
    merged = _merge_minute_bars(hist, live)
    if not merged:
        return _empty()
    return _resample_to_5m(merged)


def _read_historical_1m(
    conn: sqlite3.Connection, symbol: str, since_ts: int | None,
) -> list[dict]:
    if not _has_table(conn, "raw_intraday_candles"):
        return []
    sql = (
        "SELECT ts, open, high, low, close, volume "
        "FROM raw_intraday_candles "
        "WHERE symbol = ? AND interval = 'minute'"
    )
    params: list = [symbol]
    if since_ts is not None:
        sql += " AND ts >= ?"
        params.append(since_ts)
    sql += " ORDER BY ts ASC"
    return [
        {"ts": r[0], "open": r[1], "high": r[2], "low": r[3],
         "close": r[4], "volume": r[5] or 0}
        for r in conn.execute(sql, params).fetchall()
    ]


def _build_live_1m(
    conn: sqlite3.Connection, symbol: str, since_ts: int | None,
) -> list[dict]:
    """Synthesize 1-min OHLC candles from `raw_equity_quotes` snapshots."""
    if not _has_table(conn, "raw_equity_quotes"):
        return []
    sql = (
        "SELECT as_of, last_price, volume FROM raw_equity_quotes "
        "WHERE symbol = ? AND index_name = ? AND last_price IS NOT NULL"
    )
    params: list = [symbol, LIVE_INDEX]
    if since_ts is not None:
        sql += " AND as_of >= ?"
        params.append(since_ts)
    sql += " ORDER BY as_of ASC"
    rows = conn.execute(sql, params).fetchall()

    buckets: dict[int, dict] = {}
    prev_vol: int | None = None
    for as_of, price, vol in rows:
        # Live feed publishes cumulative session volume; per-minute volume is
        # the delta of consecutive snapshots. First sample gets 0 so we don't
        # mis-attribute the day-open total to the first minute.
        v_delta = (vol - prev_vol) if (
            prev_vol is not None and vol is not None and vol >= prev_vol
        ) else 0
        prev_vol = vol
        bucket = (as_of // 60) * 60
        b = buckets.get(bucket)
        if b is None:
            buckets[bucket] = {
                "ts": bucket,
                "open": price, "high": price, "low": price, "close": price,
                "volume": v_delta,
            }
        else:
            b["high"] = max(b["high"], price)
            b["low"]  = min(b["low"], price)
            b["close"] = price
            b["volume"] += v_delta
    return list(buckets.values())


def _merge_minute_bars(hist: list[dict], live: list[dict]) -> list[dict]:
    """Historical wins on any calendar day it covers — drop overlap from live."""
    if not hist:
        return sorted(live, key=lambda c: c["ts"])
    hist_days = {c["ts"] // 86400 for c in hist}
    merged = list(hist) + [c for c in live if c["ts"] // 86400 not in hist_days]
    merged.sort(key=lambda c: c["ts"])
    return merged


def _resample_to_5m(bars: Iterable[dict]) -> pd.DataFrame:
    df = pd.DataFrame(bars).set_index("ts")
    df.index = pd.to_datetime(df.index, unit="s", utc=True)
    out = (df
        .resample(f"{_BUCKET_SECONDS}s")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["close"]))
    # Back to integer epoch seconds — matches the storage column type and
    # avoids datetime/string churn through the rest of the pipeline.
    out.index = (out.index.astype("int64") // 1_000_000_000).astype("int64")
    out.index.name = "ts"
    return out


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,),
    ).fetchone() is not None


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=pd.Index(list(_OHLCV_COLUMNS))).rename_axis("ts")
