"""Week 18.2: pre-event run-up risk (events/pre_event_risk.py)."""
from __future__ import annotations

import datetime as dt

import pytest

from nse_data.events import pre_event_risk as per
from nse_data.scheduler.market_hours import IST
from nse_data.storage import db as dbmod

NOW = dt.datetime(2025, 6, 2, 20, 20, tzinfo=IST)
TODAY = NOW.date()


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.open_db(str(tmp_path / "t.db"))
    dbmod.apply_migrations(c, migrations_dir="migrations")
    yield c
    c.close()


class FakeRedis:
    def __init__(self):
        self.hashes: dict[str, dict] = {}

    def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update(mapping)


def _seed_event(conn, symbol, expected_date, status="upcoming"):
    conn.execute(
        "INSERT OR REPLACE INTO pending_events "
        "(symbol, event_type, expected_date, source, confidence, status, created_at) "
        "VALUES (?, 'result', ?, 'board_meeting', 0.9, ?, 0)",
        (symbol, expected_date, status),
    )
    conn.commit()


def _seed_daily(conn, symbol, closes, start=dt.date(2025, 5, 12)):
    d = start
    for close in closes:
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
        conn.execute(
            "INSERT INTO raw_bhavcopy_cm (date, symbol, series, close, volume) "
            "VALUES (?, ?, 'EQ', ?, 1000)",
            (d.isoformat(), symbol, close),
        )
        d += dt.timedelta(days=1)
    conn.commit()


def test_parse_nse_date_handles_minute_precision():
    """raw_financial_results.filing_date drops the seconds ('01-Feb-2025 09:56');
    the cadence fallback + this module must still parse it."""
    from nse_data.events.calendar import _parse_nse_date

    assert _parse_nse_date("01-Feb-2025 09:56") == dt.date(2025, 2, 1)
    assert _parse_nse_date("01-May-2026 17:00:00") == dt.date(2026, 5, 1)
    assert _parse_nse_date("01-May-2026") == dt.date(2026, 5, 1)
    assert _parse_nse_date("2026-05-01") == dt.date(2026, 5, 1)
    assert _parse_nse_date("garbage") is None


# ---------------------------------------------------------------- pure bands

def test_classify_pre_event_run_bands():
    assert per.classify_pre_event_run(12.0) == "BUY_RUMOR_IN_PLAY"
    assert per.classify_pre_event_run(5.0) == "MILD_ANTICIPATION"
    assert per.classify_pre_event_run(0.0) == "NORMAL"
    assert per.classify_pre_event_run(-5.0) == "MILD_FEAR"
    assert per.classify_pre_event_run(-10.0) == "FEAR_PRICED"
    assert per.classify_pre_event_run(-20.0) == "SELL_RUMOR_IN_PLAY"
    assert per.classify_pre_event_run(None) is None


def test_band_edges():
    assert per.classify_pre_event_run(8.0) == "BUY_RUMOR_IN_PLAY"   # >= 8
    assert per.classify_pre_event_run(7.99) == "MILD_ANTICIPATION"
    assert per.classify_pre_event_run(-15.0) == "FEAR_PRICED"
    assert per.classify_pre_event_run(-15.01) == "SELL_RUMOR_IN_PLAY"


# ------------------------------------------------------------- upcoming map

def test_upcoming_result_events_window_and_nearest(conn):
    _seed_event(conn, "ACME", (TODAY + dt.timedelta(days=2)).isoformat())
    _seed_event(conn, "ACME", (TODAY + dt.timedelta(days=9)).isoformat())
    _seed_event(conn, "FAR", (TODAY + dt.timedelta(days=30)).isoformat())   # out of window
    _seed_event(conn, "DONE", (TODAY + dt.timedelta(days=3)).isoformat(), status="filed")

    events = per.upcoming_result_events(conn, TODAY)
    assert events == {"ACME": 2}   # nearest event wins, filed/expired excluded


# ----------------------------------------------------------------- the pass

def test_pass_classifies_and_persists(conn):
    # 11 sessions 100 → 112: run_10d = +12% → BUY_RUMOR_IN_PLAY.
    closes = [100 + i * 1.2 for i in range(11)]
    _seed_daily(conn, "ACME", closes)
    _seed_event(conn, "ACME", (TODAY + dt.timedelta(days=2)).isoformat())
    r = FakeRedis()

    report = per.run_pre_event_pass(conn, redis_client=r, now=NOW)
    assert report["events"] == 1
    assert report.get("BUY_RUMOR_IN_PLAY") == 1

    row = conn.execute(
        "SELECT pre_event_run_5d, pre_event_run_10d, days_to_event, pre_event_state "
        "FROM indicator_live WHERE symbol='ACME'",
    ).fetchone()
    assert row[3] == "BUY_RUMOR_IN_PLAY"
    assert row[2] == 2
    assert row[1] == pytest.approx(12.0, abs=0.1)

    assert r.hashes["ind:ACME"]["pre_event_state"] == "BUY_RUMOR_IN_PLAY"
    assert r.hashes["ind:ACME"]["days_to_event"] == "2"


def test_pass_clears_stale_states(conn):
    conn.execute(
        "INSERT INTO indicator_live (symbol, updated_at, pre_event_state, "
        "days_to_event, pre_event_run_10d) VALUES ('OLD', 'x', 'BUY_RUMOR_IN_PLAY', 1, 9.0)",
    )
    conn.commit()
    r = FakeRedis()

    report = per.run_pre_event_pass(conn, redis_client=r, now=NOW)
    assert report["cleared"] == 1
    row = conn.execute(
        "SELECT pre_event_state, days_to_event FROM indicator_live WHERE symbol='OLD'",
    ).fetchone()
    assert row == (None, None)
    assert r.hashes["ind:OLD"]["pre_event_state"] == ""


def test_pass_preserves_other_live_columns(conn):
    _seed_daily(conn, "ACME", [100.0] * 12)
    _seed_event(conn, "ACME", (TODAY + dt.timedelta(days=1)).isoformat())
    conn.execute(
        "INSERT INTO indicator_live (symbol, updated_at, vwap, rsi_5m) "
        "VALUES ('ACME', 'x', 101.5, 55.0)",
    )
    conn.commit()

    per.run_pre_event_pass(conn, now=NOW)
    row = conn.execute(
        "SELECT vwap, rsi_5m, pre_event_state FROM indicator_live WHERE symbol='ACME'",
    ).fetchone()
    assert row == (101.5, 55.0, "NORMAL")
