"""Week 18/19 contract: the minute snapshot write must not clobber the slower
writers' indicator_live columns (pre_event_*, psych_state, consecutive_*)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nse_data.indicators.live_snapshot import _ALL_COLUMNS, write_snapshots

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    for m in ("035_indicator_live.sql", "043_indicator_eod_set.sql",
              "044_intraday_supertrend_voldelta.sql", "070_pre_event_psychology.sql",
              "107_indicator_live_intraday_board.sql"):   # ltp, orb_*, rvol_5m, structure_5m
        c.executescript((MIGRATIONS_DIR / m).read_text())
    yield c
    c.close()


def _snapshot_row(symbol="ACME", **overrides):
    row: dict[str, object] = {c: None for c in _ALL_COLUMNS}
    row.update({"symbol": symbol, "updated_at": "2025-06-02T10:00:00+05:30",
                "vwap": 101.0, "rsi_5m": 58.0})
    row.update(overrides)
    return row


def test_minute_write_preserves_psych_and_pre_event_columns(conn):
    conn.execute(
        "INSERT INTO indicator_live (symbol, updated_at, psych_state, "
        "pre_event_state, days_to_event, pre_event_run_10d, consecutive_up_days) "
        "VALUES ('ACME', 'x', 'FOMO_EUPHORIA', 'BUY_RUMOR_IN_PLAY', 2, 11.0, 6)",
    )
    conn.commit()

    write_snapshots(conn, [_snapshot_row()])

    row = conn.execute(
        "SELECT vwap, rsi_5m, psych_state, pre_event_state, days_to_event, "
        "pre_event_run_10d, consecutive_up_days FROM indicator_live "
        "WHERE symbol='ACME'",
    ).fetchone()
    assert row == (101.0, 58.0, "FOMO_EUPHORIA", "BUY_RUMOR_IN_PLAY", 2, 11.0, 6)


def test_minute_write_still_updates_own_columns(conn):
    write_snapshots(conn, [_snapshot_row(vwap=99.0)])
    write_snapshots(conn, [_snapshot_row(vwap=102.5)])
    assert conn.execute(
        "SELECT vwap FROM indicator_live WHERE symbol='ACME'",
    ).fetchone()[0] == 102.5
    assert conn.execute("SELECT COUNT(*) FROM indicator_live").fetchone()[0] == 1
