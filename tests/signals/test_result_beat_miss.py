"""Weeks 18.4/18.5: result_beat / result_miss signals."""
from __future__ import annotations

import datetime as dt
import json

import pytest

from nse_data.scheduler.market_hours import IST
from nse_data.signals import detect
from nse_data.signals.dedup import SignalDedup
from nse_data.storage import db as dbmod

NOW = dt.datetime(2025, 6, 2, 10, 0, tzinfo=IST)


# ---------------------------------------------------------------- pure rules

def test_result_beat_requires_both_legs():
    growth = {"yoy_revenue_pct": 20.0}
    positive = {"mgmt_tone": "positive"}
    assert detect.result_beat_check(growth, positive) is True
    assert detect.result_beat_check(growth, {"guidance": "raised"}) is True
    assert detect.result_beat_check(growth, {"mgmt_tone": None}) is False
    assert detect.result_beat_check({"yoy_revenue_pct": 10.0}, positive) is False
    assert detect.result_beat_check(growth, None) is False
    assert detect.result_beat_check(None, positive) is False


def test_result_miss_revenue_leg():
    assert detect.result_miss_check({"yoy_revenue_pct": -12.0}, None) is True
    assert detect.result_miss_check({"yoy_revenue_pct": -5.0}, None) is False


def test_result_miss_sentiment_leg_needs_corroboration():
    g = {"yoy_revenue_pct": 2.0, "yoy_pat_pct": -3.0}
    assert detect.result_miss_check(g, {"mgmt_tone": "negative"}) is True       # PAT down
    assert detect.result_miss_check(
        {"yoy_revenue_pct": 2.0}, {"mgmt_tone": "negative", "guidance": "cut"}) is True
    assert detect.result_miss_check(
        {"yoy_revenue_pct": 2.0, "yoy_pat_pct": 5.0}, {"mgmt_tone": "negative"}) is False
    assert detect.result_miss_check(g, {"mgmt_tone": "positive"}) is False


# ------------------------------------------------------------- detector pass

@pytest.fixture()
def conn(tmp_path):
    c = dbmod.open_db(str(tmp_path / "t.db"))
    dbmod.apply_migrations(c, migrations_dir="migrations")
    yield c
    c.close()


def _seed_listing(conn, symbol):
    for i in range(35):
        conn.execute(
            "INSERT INTO raw_bhavcopy_cm (date, symbol, series, close, volume) "
            "VALUES (?, ?, 'EQ', 100, 1000)",
            (f"2025-04-{i + 1:02d}" if i < 30 else f"2025-05-{i - 29:02d}", symbol),
        )
    conn.commit()


def _seed_financials(conn, symbol, *, growth, narrative=None,
                     broadcast="02-Jun-2025 09:45:00"):
    conn.execute(
        "INSERT INTO extracted_financials "
        "(symbol, period_ending, scope, revenue_cr, pat_cr, eps_basic, "
        " growth_json, narrative_json, broadcast_dt, extracted_at) "
        "VALUES (?, '2025-03-31', 'standalone', 1200.0, 150.0, 12.5, ?, ?, ?, ?)",
        (symbol, json.dumps(growth),
         json.dumps(narrative) if narrative else None,
         broadcast, int(NOW.timestamp()) - 60),
    )
    conn.commit()


def _signals(conn):
    return conn.execute(
        "SELECT symbol, signal_type, direction FROM signals",
    ).fetchall()


def test_beat_fires_long(conn):
    _seed_listing(conn, "ACME")
    _seed_financials(conn, "ACME", growth={"yoy_revenue_pct": 22.0},
                     narrative={"mgmt_tone": "positive"})
    fired = detect._detect_result_beat_miss(
        conn, None, SignalDedup(None), NOW.isoformat(), None, NOW,
    )
    assert fired == 1
    assert _signals(conn) == [("ACME", "result_beat", "long")]


def test_miss_fires_short(conn):
    _seed_listing(conn, "ACME")
    _seed_financials(conn, "ACME", growth={"yoy_revenue_pct": -14.0})
    fired = detect._detect_result_beat_miss(
        conn, None, SignalDedup(None), NOW.isoformat(), None, NOW,
    )
    assert fired == 1
    assert _signals(conn) == [("ACME", "result_miss", "short")]


def test_inline_result_fires_nothing(conn):
    _seed_listing(conn, "ACME")
    _seed_financials(conn, "ACME", growth={"yoy_revenue_pct": 5.0},
                     narrative={"mgmt_tone": "positive"})
    fired = detect._detect_result_beat_miss(
        conn, None, SignalDedup(None), NOW.isoformat(), None, NOW,
    )
    assert fired == 0 and _signals(conn) == []


def test_old_filing_does_not_fire(conn):
    """A nightly backfill stamps a fresh extracted_at on an OLD filing — the
    broadcast-recency guard must keep it silent."""
    _seed_listing(conn, "ACME")
    _seed_financials(conn, "ACME", growth={"yoy_revenue_pct": 30.0},
                     narrative={"mgmt_tone": "positive"},
                     broadcast="15-May-2025 17:00:00")
    fired = detect._detect_result_beat_miss(
        conn, None, SignalDedup(None), NOW.isoformat(), None, NOW,
    )
    assert fired == 0


def test_dedup_blocks_refire(conn):
    _seed_listing(conn, "ACME")
    _seed_financials(conn, "ACME", growth={"yoy_revenue_pct": 22.0},
                     narrative={"mgmt_tone": "positive"})
    dedup = SignalDedup(None)
    args = (conn, None, dedup, NOW.isoformat(), None, NOW)
    assert detect._detect_result_beat_miss(*args) == 1
    assert detect._detect_result_beat_miss(*args) == 0
