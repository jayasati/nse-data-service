"""Tests for the options-metrics pass (persistence + index GEX mirror)."""
from __future__ import annotations

import datetime as _dt
import sqlite3

from nse_data.options import job

_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
_AS_OF = int(_dt.datetime(2026, 6, 20, 12, 0, tzinfo=_IST).timestamp())   # expiry 30-Jun → 10d


def _db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE raw_option_chain (symbol TEXT, expiry TEXT, strike REAL, option_type TEXT, "
        "as_of INTEGER, underlying_value REAL, open_interest INTEGER, implied_volatility REAL);"
        "CREATE TABLE options_metrics (symbol TEXT, expiry TEXT, as_of INTEGER, spot REAL, "
        "max_pain REAL, pcr REAL, gex_total REAL, gex_sign TEXT, gex_flip_level REAL, "
        "computed_at INTEGER, PRIMARY KEY (symbol, expiry, as_of));"
        "CREATE TABLE market_state (as_of TEXT PRIMARY KEY, gex_total REAL, gex_sign TEXT, "
        "gex_flip_level REAL);")
    rows = []
    for strike in (90, 100, 110):
        # some rows carry underlying_value=0 (real-data quirk) — the job must still find spot
        rows.append(("NIFTY", "30-Jun-2026", strike, "CE", _AS_OF, 0.0, 1000, 20.0))
        rows.append(("NIFTY", "30-Jun-2026", strike, "PE", _AS_OF, 100.0, 1300, 20.0))
    conn.executemany("INSERT INTO raw_option_chain VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.execute("INSERT INTO market_state (as_of) VALUES ('2026-06-20T15:30:00')")
    conn.commit()
    return conn


def test_run_options_pass_persists_and_mirrors_index():
    conn = _db()
    rep = job.run_options_pass(conn)
    assert rep["written"] == 1
    row = conn.execute("SELECT spot, max_pain, pcr, gex_sign FROM options_metrics "
                       "WHERE symbol='NIFTY'").fetchone()
    assert row[0] == 100.0                       # spot found despite the underlying_value=0 rows
    assert row[1] == 100.0                       # max pain (all OI at 100)
    assert row[2] == 1.3                         # PCR = 1300/1000
    assert row[3] == "positive"                  # puts dominate → dealer long gamma
    # index GEX mirrored onto market_state (task 23.3)
    ms = conn.execute("SELECT gex_sign FROM market_state").fetchone()
    assert ms[0] == "positive"
    # PCR 1.3 is not > 1.3 → no extreme; a stronger put skew would fire pcr_extreme_high
    assert all(s["type"] != "max_pain_drift" for s in rep["signals"])     # spot == max pain


def test_compute_symbol_none_without_chain():
    conn = _db()
    assert job.compute_symbol(conn, "ABSENT") is None
