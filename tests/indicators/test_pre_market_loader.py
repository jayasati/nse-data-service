"""Pre-market loader: indicator_live seed + blacklist/quality publish."""

from __future__ import annotations

from datetime import datetime

from nse_data.indicators import pre_market_loader as pml
from nse_data.scheduler.market_hours import IST
from nse_data.storage.db import apply_migrations, open_db

from .conftest import FakeRedis, insert_bhavcopy

NOW = datetime(2025, 6, 2, 8, 45, 0, tzinfo=IST)   # pre-open, a Monday


def _add_surveillance(conn, table, symbols):
    conn.execute(f"CREATE TABLE {table} (symbol TEXT)")
    conn.executemany(f"INSERT INTO {table} (symbol) VALUES (?)", [(s,) for s in symbols])
    conn.commit()


# ------------------------------------------------------------- blacklist

def test_compute_blacklist_unions_surveillance(indicators_db):
    _add_surveillance(indicators_db, "raw_surveillance_gsm", ["AAA", "BBB"])
    _add_surveillance(indicators_db, "raw_surveillance_asm_st", ["BBB", "CCC"])
    # raw_surveillance_asm_lt intentionally absent -> guarded, not an error.
    assert pml.compute_blacklist(indicators_db) == {"AAA", "BBB", "CCC"}


def test_compute_blacklist_empty_when_no_tables(indicators_db):
    assert pml.compute_blacklist(indicators_db) == set()


# -------------------------------------------------------------- quality

def test_compute_quality_flags(indicators_db):
    insert_bhavcopy(indicators_db, "AAA", [100.0] * 5)
    insert_bhavcopy(indicators_db, "CCC", [100.0] * 250)
    indicators_db.execute(
        "INSERT INTO indicator_sma (symbol, date, sma_50, sma_200) "
        "VALUES ('CCC', '2025-05-30', 100, 95)"
    )
    indicators_db.commit()

    quality = pml.compute_quality(indicators_db, ["AAA", "CCC", "DDD"], blacklist={"AAA"})

    assert quality["AAA"] == {"daily_bars": 5, "has_sma200": 0, "blacklisted": 1}
    assert quality["CCC"] == {"daily_bars": 250, "has_sma200": 1, "blacklisted": 0}
    assert quality["DDD"] == {"daily_bars": 0, "has_sma200": 0, "blacklisted": 0}


# ------------------------------------------------------- redis publishing

def test_write_blacklist_to_redis():
    r = FakeRedis()
    r.sadd("blacklist:symbols", "STALE")          # prior run's member
    pml.write_blacklist_to_redis(r, {"AAA", "BBB"})
    assert r.smembers("blacklist:symbols") == {"AAA", "BBB"}   # DEL cleared STALE
    assert r.ttl("blacklist:symbols") == 6 * 3600


def test_write_quality_to_redis():
    r = FakeRedis()
    quality = {"CCC": {"daily_bars": 250, "has_sma200": 1, "blacklisted": 0}}
    pml.write_quality_to_redis(r, quality)
    assert r.hgetall("quality:CCC") == {"daily_bars": 250, "has_sma200": 1, "blacklisted": 0}
    assert r.ttl("quality:CCC") == 6 * 3600


# ----------------------------------------------------------- end to end

def test_run_pre_market_load_seeds_and_publishes(tmp_path):
    db_path = str(tmp_path / "pm.db")
    conn = open_db(db_path)
    apply_migrations(conn)

    # One tradable symbol with enough history to be classifiable.
    conn.execute("INSERT INTO raw_fno_list (symbol, series, fetched_at) VALUES ('HDFCBANK','EQ',0)")
    insert_bhavcopy(conn, "HDFCBANK", [100.0 + i for i in range(30)])
    conn.execute(
        "INSERT INTO indicator_sma (symbol, date, sma_50, sma_200) "
        "VALUES ('HDFCBANK', '2025-05-30', 120, 100)"
    )
    conn.commit()
    conn.close()

    r = FakeRedis()
    report = pml.run_pre_market_load(db_path, redis_client=r, now=NOW)

    assert report["symbols"] == 1
    assert report["seeded"] == 1
    assert report["redis_published"] is True

    check = open_db(db_path)
    row = check.execute(
        "SELECT symbol, trend_regime, atr_14_daily FROM indicator_live"
    ).fetchone()
    check.close()
    # Seeded from EOD: last close 129 > sma50 120 > sma200 100 -> strong_uptrend,
    # and daily ATR is populated even pre-open (no intraday needed).
    assert row[0] == "HDFCBANK"
    assert row[1] == "strong_uptrend"
    assert row[2] is not None
    # Quality hash published for the symbol.
    assert r.hgetall("quality:HDFCBANK")["daily_bars"] == 30
