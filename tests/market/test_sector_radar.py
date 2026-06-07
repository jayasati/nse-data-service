"""Unit tests for market.sector_radar_job — RS helpers + DB pass."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from nse_data.market import sector_radar_job as sr
from nse_data.scheduler.market_hours import IST


# ---- pure RS helpers -------------------------------------------------------

def test_excess_return():
    assert sr.excess_return(1.5, 0.5) == 1.0
    assert sr.excess_return(-0.5, 0.5) == -1.0
    assert sr.excess_return(None, 0.5) is None


def test_rs_ratio_guards_flat_nifty():
    assert sr.rs_ratio(1.0, 0.5) == 2.0
    assert sr.rs_ratio(1.0, 0.01) is None    # nifty inside deadband -> unreliable
    assert sr.rs_ratio(1.0, None) is None


def test_rank_by_excess_best_first_nones_last():
    ranks = sr.rank_by_excess({"A": 2.0, "B": -1.0, "C": 0.5, "D": None})
    assert ranks["A"] == 1
    assert ranks["C"] == 2
    assert ranks["B"] == 3
    assert ranks["D"] == 4          # no data sorts last


def test_rs_trend():
    assert sr.rs_trend(1.0, 0.5) == "improving"
    assert sr.rs_trend(0.5, 1.0) == "deteriorating"
    assert sr.rs_trend(1.0, 1.01) == "flat"
    assert sr.rs_trend(None, 1.0) is None


# ---- DB pass ---------------------------------------------------------------

def _seed() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE raw_indices (index_symbol TEXT, as_of INTEGER, pct_change REAL,
                                  PRIMARY KEY(index_symbol, as_of));
        CREATE TABLE sector_state (
            sector_name TEXT NOT NULL, as_of TEXT NOT NULL, rs_ratio REAL,
            rs_rank INTEGER, rs_trend TEXT, volume_state TEXT, sector_return_pct REAL,
            PRIMARY KEY (sector_name, as_of)
        );
    """)
    base = 1_780_000_000
    # 30m ago and now. NIFTY 50 +0.5% now; METAL is the clear leader.
    for ts, nifty, metal, bank in [(base - 1800, 0.4, 0.6, 0.3), (base, 0.5, 2.0, -0.5)]:
        conn.execute("INSERT INTO raw_indices VALUES ('NIFTY 50', ?, ?)", (ts, nifty))
        conn.execute("INSERT INTO raw_indices VALUES ('NIFTY METAL', ?, ?)", (ts, metal))
        conn.execute("INSERT INTO raw_indices VALUES ('NIFTY BANK', ?, ?)", (ts, bank))
    conn.commit()
    return conn


def test_run_sector_pass_ranks_and_persists():
    conn = _seed()
    now = datetime(2026, 6, 5, 10, 0, tzinfo=IST)
    report = sr.run_sector_pass(conn, now=now)

    assert report["sectors"] == len(sr.SECTOR_INDICES)
    assert report["leader"] == "NIFTY METAL"     # excess +1.5 — biggest

    ranks = sr.latest_sector_ranks(conn)
    assert ranks["NIFTY METAL"]["rs_rank"] == 1
    assert ranks["NIFTY METAL"]["rs_trend"] == "improving"   # excess 0.2 -> 1.5
    # BANK went from +excess to negative excess -> worse than METAL
    assert ranks["NIFTY BANK"]["rs_rank"] > ranks["NIFTY METAL"]["rs_rank"]
    # sectors with no price data are still ranked (last), never crash
    assert ranks["NIFTY MEDIA"]["rs_rank"] >= 3


def test_latest_sector_ranks_empty_when_no_table():
    conn = sqlite3.connect(":memory:")
    assert sr.latest_sector_ranks(conn) == {}
