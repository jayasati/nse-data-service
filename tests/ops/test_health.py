"""Unit tests for ops.health — timestamp parsing, schedule reasoning, verdicts."""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from nse_data.ops import health
from nse_data.scheduler.market_hours import IST


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=IST)


# ---- timestamp parsing -----------------------------------------------------

def test_parse_epoch_int():
    # 2026-05-22 13:30:00 IST
    got = health._parse_ts(1779434400)
    assert got is not None and got.tzinfo is not None
    assert got.astimezone(IST).date().isoformat() == "2026-05-22"


def test_parse_numeric_string():
    assert health._parse_ts("1779434400") == health._parse_ts(1779434400)


def test_parse_iso_date_anchored_to_eod():
    got = health._parse_ts("2026-05-22")
    assert (got.hour, got.minute, got.second) == (23, 59, 59)


def test_parse_nse_month_strings():
    a = health._parse_ts("22-May-2026")
    b = health._parse_ts("22-MAY-2026")
    assert a.date() == b.date() == dt("2026-05-22").date()


def test_parse_garbage_returns_none():
    assert health._parse_ts("not-a-date") is None
    assert health._parse_ts(None) is None


# ---- timestamp column detection -------------------------------------------

def test_detect_prefers_collection_over_date():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (date TEXT, as_of INTEGER, v REAL)")
    col, is_date = health.detect_ts_column(conn, "t")
    assert col == "as_of" and is_date is False


def test_detect_date_only_fallback():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (date TEXT, v REAL)")
    col, is_date = health.detect_ts_column(conn, "t")
    assert col == "date" and is_date is True


def test_safe_ident_rejects_injection():
    with pytest.raises(ValueError):
        health._safe_ident("t; DROP TABLE x")


# ---- last_expected_run -----------------------------------------------------
# Reference instants are IST. 2026-05-22 is a Friday (trading day);
# 2026-05-24 is a Sunday.

def test_market_hours_midsession_is_now():
    cfg = {"cadence": "5m", "market_hours_only": True}
    now = dt("2026-05-22T11:00:00")
    assert health.last_expected_run(cfg, now) == now


def test_market_hours_after_close_is_close():
    cfg = {"cadence": "5m", "market_hours_only": True}
    now = dt("2026-05-22T20:00:00")
    assert health.last_expected_run(cfg, now) == dt("2026-05-22T15:30:00")


def test_market_hours_weekend_walks_back_to_friday():
    cfg = {"cadence": "5m", "market_hours_only": True}
    now = dt("2026-05-24T11:00:00")  # Sunday
    assert health.last_expected_run(cfg, now) == dt("2026-05-22T15:30:00")


def test_active_window_after_window_uses_end():
    cfg = {"cadence": "10m", "active_hours": "08:00-19:00"}
    now = dt("2026-05-22T22:00:00")
    assert health.last_expected_run(cfg, now) == dt("2026-05-22T19:00:00")


def test_daily_before_settle_uses_previous_day():
    cfg = {"cadence": "daily", "run_at": "19:00"}
    # 19:10 is within the 30-min settle window -> today's run may not have
    # landed yet, so the most recent *settled* run is yesterday's 19:00.
    now = dt("2026-05-22T19:10:00")
    assert health.last_expected_run(cfg, now) == dt("2026-05-21T19:00:00")


def test_daily_after_settle_uses_today():
    cfg = {"cadence": "daily", "run_at": "19:00"}
    now = dt("2026-05-22T20:00:00")
    assert health.last_expected_run(cfg, now) == dt("2026-05-22T19:00:00")


def test_weekly_walks_back_to_matching_dow():
    cfg = {"cadence": "weekly", "run_at": "Sun 08:00"}
    now = dt("2026-05-22T12:00:00")  # Friday
    assert health.last_expected_run(cfg, now) == dt("2026-05-17T08:00:00")  # prev Sunday


# ---- verdicts --------------------------------------------------------------

def _conn_with(table_sql: str, *inserts: str) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(table_sql)
    for ins in inserts:
        conn.execute(ins)
    conn.commit()
    return conn


def test_status_ok_when_fresh():
    now = dt("2026-05-22T11:05:00")
    ts = int(dt("2026-05-22T11:00:00").timestamp())
    conn = _conn_with("CREATE TABLE raw_x (as_of INTEGER)",
                      f"INSERT INTO raw_x VALUES ({ts})")
    cfg = {"cadence": "5m", "market_hours_only": True, "enabled": True, "table": "raw_x"}
    h = health.collector_health(conn, "x", cfg, now)
    assert h["status"] == "ok"


def test_status_down_when_days_stale():
    now = dt("2026-05-22T11:05:00")
    ts = int(dt("2026-05-19T11:00:00").timestamp())  # 3 days old
    conn = _conn_with("CREATE TABLE raw_x (as_of INTEGER)",
                      f"INSERT INTO raw_x VALUES ({ts})")
    cfg = {"cadence": "5m", "market_hours_only": True, "enabled": True, "table": "raw_x"}
    h = health.collector_health(conn, "x", cfg, now)
    assert h["status"] == "down"


def test_status_no_table():
    conn = sqlite3.connect(":memory:")
    cfg = {"cadence": "5m", "enabled": True, "table": "raw_missing"}
    h = health.collector_health(conn, "x", cfg, dt("2026-05-22T11:00:00"))
    assert h["status"] == "no_table"


def test_status_empty():
    conn = _conn_with("CREATE TABLE raw_x (as_of INTEGER)")
    cfg = {"cadence": "5m", "enabled": True, "table": "raw_x"}
    h = health.collector_health(conn, "x", cfg, dt("2026-05-22T11:00:00"))
    assert h["status"] == "empty"


def test_status_disabled():
    conn = _conn_with("CREATE TABLE raw_x (as_of INTEGER)")
    cfg = {"cadence": "5m", "enabled": False, "table": "raw_x"}
    h = health.collector_health(conn, "x", cfg, dt("2026-05-22T11:00:00"))
    assert h["status"] == "disabled"


def test_build_report_sorts_worst_first_and_summarizes():
    now = dt("2026-05-22T11:05:00")
    fresh = int(dt("2026-05-22T11:00:00").timestamp())
    conn = _conn_with("CREATE TABLE raw_ok (as_of INTEGER)",
                      f"INSERT INTO raw_ok VALUES ({fresh})")
    endpoints = {
        "ok_one": {"cadence": "5m", "market_hours_only": True,
                   "enabled": True, "table": "raw_ok"},
        "missing": {"cadence": "5m", "enabled": True, "table": "raw_gone"},
    }
    report = health.build_report(conn, endpoints, now=now)
    assert report["total"] == 2
    assert report["summary"]["ok"] == 1
    assert report["summary"]["no_table"] == 1
    assert report["collectors"][0]["status"] == "no_table"  # worst first
