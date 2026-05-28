"""Retention sweeper: drops intraday rows older than the cutoff, leaves EOD alone."""

from __future__ import annotations

import time

from nse_data.indicators.retention import sweep_intraday


def test_sweep_deletes_old_intraday_keeps_recent(indicators_db):
    now = int(time.time())
    day = 86_400

    # Insert one row for each of: 60 days ago, 25 days ago, 1 day ago.
    rows = [
        ("TEST", now - 60 * day, 40.0),
        ("TEST", now - 25 * day, 45.0),
        ("TEST", now -  1 * day, 50.0),
    ]
    indicators_db.executemany(
        "INSERT INTO indicator_rsi_5m (symbol, ts, rsi_14) VALUES (?, ?, ?)", rows,
    )
    indicators_db.commit()

    deleted = sweep_intraday(indicators_db, retention_days=30)
    assert deleted["indicator_rsi_5m"] == 1   # only the 60-days-ago row

    remaining = indicators_db.execute(
        "SELECT ts FROM indicator_rsi_5m WHERE symbol=? ORDER BY ts", ("TEST",),
    ).fetchall()
    assert [r[0] for r in remaining] == [now - 25 * day, now - 1 * day]


def test_sweep_ignores_eod_tables(indicators_db):
    """EOD indicator tables use date strings, not epoch ts — not swept."""
    indicators_db.execute(
        "INSERT INTO indicator_sma (symbol, date, sma_20) VALUES (?, ?, ?)",
        ("TEST", "2020-01-01", 100.0),
    )
    indicators_db.commit()

    sweep_intraday(indicators_db, retention_days=30)
    count = indicators_db.execute(
        "SELECT COUNT(*) FROM indicator_sma WHERE symbol=?", ("TEST",),
    ).fetchone()[0]
    assert count == 1
