"""Unit tests for market.regime_job — classifiers + DB pass."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from nse_data.market import regime_job as rj
from nse_data.scheduler.market_hours import IST


# ---- VIX state (task 7.3) --------------------------------------------------

def test_vix_state_bands():
    assert rj.classify_vix_state(11) == "low"
    assert rj.classify_vix_state(15) == "normal"
    assert rj.classify_vix_state(20) == "elevated"
    assert rj.classify_vix_state(25) == "high"
    assert rj.classify_vix_state(30) == "extreme"
    assert rj.classify_vix_state(None) is None


# ---- direction helpers -----------------------------------------------------

def test_nifty_direction_deadband():
    assert rj.nifty_direction(0.5) == "up"
    assert rj.nifty_direction(-0.5) == "down"
    assert rj.nifty_direction(0.05) == "flat"
    assert rj.nifty_direction(None) is None


def test_vix_direction_vs_prior():
    assert rj.vix_direction(15.0, 14.0) == "rising"
    assert rj.vix_direction(14.0, 15.0) == "falling"
    assert rj.vix_direction(15.0, 15.1) == "flat"
    assert rj.vix_direction(None, 15.0) is None


def test_gift_signal():
    assert rj.gift_signal(0.5) == "aligned_bull"
    assert rj.gift_signal(-0.5) == "aligned_bear"
    assert rj.gift_signal(0.1) == "neutral"
    assert rj.gift_signal(None) == "neutral"


# ---- overall regime (task 7.2) ---------------------------------------------

def test_panic_overrides_everything():
    regime, conf = rj.classify_regime(
        nifty_dir="up", vix_dir="falling", vix_level=26.0,
        ad_ratio=3.0, pct_above_vwap=90.0,
    )
    assert regime == "panic" and conf == 0.9


def test_risk_on():
    regime, conf = rj.classify_regime(
        nifty_dir="up", vix_dir="falling", vix_level=13.0,
        ad_ratio=2.0, pct_above_vwap=70.0, gift="aligned_bull",
    )
    assert regime == "risk_on" and conf == 1.0   # all factors bullish


def test_risk_off():
    regime, _ = rj.classify_regime(
        nifty_dir="down", vix_dir="rising", vix_level=20.0,
        ad_ratio=0.5, pct_above_vwap=20.0,
    )
    assert regime == "risk_off"


def test_neutral_when_mixed():
    regime, _ = rj.classify_regime(
        nifty_dir="up", vix_dir="rising", vix_level=16.0,
        ad_ratio=1.0, pct_above_vwap=50.0,
    )
    assert regime == "neutral"


def test_risk_on_requires_all_factors():
    # nifty up + vix falling but breadth weak -> not risk_on
    regime, _ = rj.classify_regime(
        nifty_dir="up", vix_dir="falling", vix_level=13.0,
        ad_ratio=1.0, pct_above_vwap=55.0,
    )
    assert regime == "neutral"


# ---- DB pass ---------------------------------------------------------------

def _seed_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE raw_india_vix (as_of INTEGER PRIMARY KEY, vix REAL);
        CREATE TABLE raw_indices (index_symbol TEXT, as_of INTEGER, pct_change REAL,
                                  PRIMARY KEY(index_symbol, as_of));
        CREATE TABLE raw_gift_nifty (as_of INTEGER PRIMARY KEY, curr_value REAL,
                                     close_value REAL, pct_change REAL);
        CREATE TABLE raw_advances_declines (as_of INTEGER PRIMARY KEY,
                                            advances INTEGER, declines INTEGER);
        CREATE TABLE indicator_live (symbol TEXT PRIMARY KEY, price_vs_vwap TEXT);
        CREATE TABLE raw_fii_dii (date TEXT, category TEXT, net_value REAL,
                                  PRIMARY KEY(date, category));
        CREATE TABLE market_state (
            as_of TEXT PRIMARY KEY, nifty_direction TEXT, nifty_return_pct REAL,
            vix_level REAL, vix_state TEXT, vix_direction TEXT,
            gift_nifty_signal TEXT, advance_decline_ratio REAL, pct_above_vwap REAL,
            fii_partial_day REAL, overall_regime TEXT, regime_confidence REAL,
            updated_at TEXT
        );
    """)
    # VIX falling: 13.0 now, 14.0 thirty+ minutes ago.
    base = 1_780_000_000
    conn.execute("INSERT INTO raw_india_vix VALUES (?, ?)", (base - 3600, 14.0))
    conn.execute("INSERT INTO raw_india_vix VALUES (?, ?)", (base, 13.0))
    conn.execute("INSERT INTO raw_indices VALUES ('NIFTY 50', ?, ?)", (base, 0.8))
    conn.execute("INSERT INTO raw_gift_nifty VALUES (?, ?, ?, ?)", (base, 101.0, 100.0, 1.0))
    conn.execute("INSERT INTO raw_advances_declines VALUES (?, ?, ?)", (base, 1800, 600))
    for i in range(10):  # 8/10 above VWAP = 80%
        conn.execute("INSERT INTO indicator_live VALUES (?, ?)",
                     (f"S{i}", "above" if i < 8 else "below"))
    conn.commit()
    return conn


def test_run_regime_pass_writes_risk_on():
    conn = _seed_db()
    now = datetime(2026, 6, 5, 10, 0, tzinfo=IST)
    state = rj.run_regime_pass(conn, now=now)

    assert state["overall_regime"] == "risk_on"
    assert state["vix_state"] == "normal"
    assert state["vix_direction"] == "falling"
    assert state["nifty_direction"] == "up"
    assert state["advance_decline_ratio"] == 3.0
    assert state["pct_above_vwap"] == 80.0

    # persisted
    stored = conn.execute(
        "SELECT overall_regime FROM market_state WHERE as_of = ?", (now.isoformat(),)
    ).fetchone()
    assert stored[0] == "risk_on"


def test_run_regime_pass_handles_empty_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE raw_india_vix (as_of INTEGER PRIMARY KEY, vix REAL);
        CREATE TABLE raw_indices (index_symbol TEXT, as_of INTEGER, pct_change REAL,
                                  PRIMARY KEY(index_symbol, as_of));
        CREATE TABLE raw_gift_nifty (as_of INTEGER PRIMARY KEY, curr_value REAL,
                                     close_value REAL, pct_change REAL);
        CREATE TABLE raw_advances_declines (as_of INTEGER PRIMARY KEY,
                                            advances INTEGER, declines INTEGER);
        CREATE TABLE indicator_live (symbol TEXT PRIMARY KEY, price_vs_vwap TEXT);
        CREATE TABLE raw_fii_dii (date TEXT, category TEXT, net_value REAL,
                                  PRIMARY KEY(date, category));
        CREATE TABLE market_state (
            as_of TEXT PRIMARY KEY, nifty_direction TEXT, nifty_return_pct REAL,
            vix_level REAL, vix_state TEXT, vix_direction TEXT,
            gift_nifty_signal TEXT, advance_decline_ratio REAL, pct_above_vwap REAL,
            fii_partial_day REAL, overall_regime TEXT, regime_confidence REAL,
            updated_at TEXT
        );
    """)
    now = datetime(2026, 6, 5, 10, 0, tzinfo=IST)
    state = rj.run_regime_pass(conn, now=now)
    # No inputs -> neutral, every factor None, no crash.
    assert state["overall_regime"] == "neutral"
    assert state["vix_level"] is None
    assert state["pct_above_vwap"] is None
