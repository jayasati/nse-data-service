"""indicator_live snapshot builder + writer + Redis mirror (live_snapshot.py)."""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from nse_data.indicators import live_snapshot
from nse_data.scheduler.market_hours import IST

from .conftest import FakeRedis, insert_bhavcopy, insert_intraday_candles

# A fixed trading day so epochs are deterministic. 2025-06-02 is a Monday.
NOW = datetime(2025, 6, 2, 11, 0, 0, tzinfo=IST)
SESSION_START = int(NOW.replace(hour=9, minute=15, second=0, microsecond=0).timestamp())


def _seed_full_symbol(conn: sqlite3.Connection, symbol: str = "HDFCBANK") -> None:
    """Seed every input build_snapshot reads, tuned to a strong-uptrend setup."""
    # 30 daily candles rising by 1 -> ATR(14) daily settles at 1.0.
    insert_bhavcopy(conn, symbol, [100.0 + i for i in range(30)])

    # 100 one-minute bars from the open, rising -> ~20 five-minute bars after
    # resample (enough for ATR(14) 5m); last close ~149.5 is the live price.
    insert_intraday_candles(
        conn, symbol,
        [100.0 + i * 0.5 for i in range(100)],
        start_ts=SESSION_START, bar_seconds=60,
    )

    # 7 session VWAP bars rising 104..110 -> positive slope, vwap=110.
    for i in range(7):
        conn.execute(
            "INSERT INTO indicator_vwap_5m (symbol, ts, vwap) VALUES (?, ?, ?)",
            (symbol, SESSION_START + i * 300, 104.0 + i),
        )
    # Latest RSI(5m) = 72 -> 'overbought'.
    for i, rsi in enumerate([60.0, 65.0, 72.0]):
        conn.execute(
            "INSERT INTO indicator_rsi_5m (symbol, ts, rsi_14) VALUES (?, ?, ?)",
            (symbol, SESSION_START + i * 300, rsi),
        )
    # SMA stack: price(149.5) > sma50(120) > sma200(100) -> strong_uptrend.
    conn.execute(
        "INSERT INTO indicator_sma (symbol, date, sma_20, sma_50, sma_200) "
        "VALUES (?, '2025-05-30', 130, 120, 100)",
        (symbol,),
    )
    conn.commit()


def test_build_snapshot_full(indicators_db):
    _seed_full_symbol(indicators_db)
    snap = live_snapshot.build_snapshot(indicators_db, "HDFCBANK", now=NOW)

    assert snap["symbol"] == "HDFCBANK"
    assert snap["updated_at"] == NOW.isoformat()
    assert snap["vwap"] == 110.0
    assert snap["vwap_slope"] == pytest.approx(1.0)        # 110 - 104 over 6 bars
    assert snap["price_vs_vwap"] == "above"                 # ~149.5 > 110
    assert snap["atr_14_daily"] == pytest.approx(1.0)
    assert snap["atr_14_5m"] is not None and snap["atr_14_5m"] > 0
    assert snap["rsi_5m"] == 72.0
    assert snap["trend_regime"] == "strong_uptrend"
    assert snap["momentum_state"] == "overbought"


def test_build_snapshot_missing_symbol_all_none(indicators_db):
    snap = live_snapshot.build_snapshot(indicators_db, "NOSUCH", now=NOW)
    assert snap["symbol"] == "NOSUCH"
    for field in (
        "vwap", "vwap_slope", "price_vs_vwap", "atr_14_daily",
        "atr_14_5m", "rsi_5m", "trend_regime", "momentum_state",
    ):
        assert snap[field] is None


def test_write_and_read_back(indicators_db):
    _seed_full_symbol(indicators_db)
    snap = live_snapshot.build_snapshot(indicators_db, "HDFCBANK", now=NOW)
    assert live_snapshot.write_snapshots(indicators_db, [snap]) == 1

    row = indicators_db.execute(
        "SELECT symbol, vwap, rsi_5m, trend_regime, momentum_state "
        "FROM indicator_live WHERE symbol = 'HDFCBANK'"
    ).fetchone()
    assert row == ("HDFCBANK", 110.0, 72.0, "strong_uptrend", "overbought")

    # PRIMARY KEY(symbol) -> a second write overwrites, not duplicates.
    live_snapshot.write_snapshots(indicators_db, [snap])
    assert indicators_db.execute("SELECT COUNT(*) FROM indicator_live").fetchone()[0] == 1


def test_flush_to_redis(indicators_db):
    _seed_full_symbol(indicators_db)
    snap = live_snapshot.build_snapshot(indicators_db, "HDFCBANK", now=NOW)
    r = FakeRedis()

    assert live_snapshot.flush_to_redis(r, [snap]) == 1
    h = r.hgetall("ind:HDFCBANK")
    assert h["trend_regime"] == "strong_uptrend"
    assert h["rsi_5m"] == 72.0
    assert "symbol" not in h                       # PK is in the key, not the hash
    assert r.ttl("ind:HDFCBANK") == 5 * 60         # task 3.7: 5-minute TTL


def test_flush_to_redis_nulls_become_empty_string(indicators_db):
    snap = live_snapshot.build_snapshot(indicators_db, "NOSUCH", now=NOW)
    r = FakeRedis()
    live_snapshot.flush_to_redis(r, [snap])
    h = r.hgetall("ind:NOSUCH")
    assert h["trend_regime"] == ""                 # None -> "" so no stale carry-over
    assert h["vwap"] == ""


def test_run_snapshot_pass_writes_and_reports(indicators_db):
    _seed_full_symbol(indicators_db)
    r = FakeRedis()
    report = live_snapshot.run_snapshot_pass(
        indicators_db, ["HDFCBANK"], redis_client=r, now=NOW,
    )
    assert report == {"snapshots": 1, "redis_flushed": 1}
    assert indicators_db.execute(
        "SELECT COUNT(*) FROM indicator_live"
    ).fetchone()[0] == 1


def test_run_snapshot_pass_skips_when_table_absent():
    bare = sqlite3.connect(":memory:")
    report = live_snapshot.run_snapshot_pass(bare, ["X"], now=NOW)
    assert report == {"skipped": "no_indicator_live_table"}
