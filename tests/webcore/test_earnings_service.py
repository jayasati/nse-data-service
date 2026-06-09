"""EarningsService — odds buckets, reaction/upcoming shaping, Unavailable."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nse_data.webcore.errors import Unavailable
from nse_data.webcore.repositories.earnings import EarningsRepository
from nse_data.webcore.services.earnings import EarningsService

MIG = Path(__file__).resolve().parents[2] / "migrations"
_SCHEMA = ("036_signals.sql", "053_signal_horizon.sql", "057_signal_direction.sql",
           "056_earnings_setups.sql")


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for m in _SCHEMA:
        conn.executescript((MIG / m).read_text())
    conn.row_factory = sqlite3.Row
    return conn


def _signal(conn, *, direction, ret_1d, symbol="ACME", detected_at="2026-04-30T15:35:00"):
    sid = int(conn.execute(
        "INSERT INTO signals (symbol, signal_type, detected_at, price, price_change_pct, "
        "confidence, direction, dispatched) "
        "VALUES (?, 'earnings_direction', ?, 100, 4.0, 0.7, ?, 1)",
        (symbol, detected_at, direction),
    ).lastrowid)
    conn.execute(
        "INSERT INTO signal_outcomes (signal_id, symbol, detected_at, ret_1d) VALUES (?, ?, ?, ?)",
        (sid, symbol, detected_at, ret_1d),
    )
    conn.commit()
    return sid


def _svc(conn) -> EarningsService:
    return EarningsService(EarningsRepository(conn))


def test_odds_buckets_and_coverage(db):
    _signal(db, direction="long", ret_1d=3.0)    # long win
    _signal(db, direction="long", ret_1d=-2.0)   # long loss
    _signal(db, direction="short", ret_1d=-5.0)  # short win (price fell)
    out = _svc(db).odds()
    assert out["coverage"] == {"total": 3, "settled": 3, "longs": 2, "shorts": 1}
    assert out["overall"]["n"] == 3 and out["overall"]["wins"] == 2
    assert out["long"]["n"] == 2 and out["long"]["wins"] == 1
    assert out["short"]["win_rate"] == 100.0
    assert "min_samples" in out


def test_reactions_shape_and_direction_filter(db):
    _signal(db, direction="short", ret_1d=-5.0, symbol="DROP")
    rx = _svc(db).reactions(direction="short")
    assert rx["count"] == 1
    row = rx["reactions"][0]
    assert row["symbol"] == "DROP" and row["direction"] == "short"
    assert row["outcome_pct"] == 5.0 and row["win"] is True   # short + price fell = win
    # long filter excludes it
    assert _svc(db).reactions(direction="long")["count"] == 0


def test_upcoming_lists_setups(db):
    db.execute(
        "INSERT INTO earnings_setups (symbol, event_date, run_up_5d, run_up_class, "
        "implied_move_pct, pcr, fundamental_class, expectation_proxy_score, created_at) "
        "VALUES ('ACME', '2026-07-25', 9.0, 'BUY_RUMOR_IN_PLAY', 6.0, 0.8, 'STRONG_GROWTH', 0.7, 1)"
    )
    db.commit()
    up = _svc(db).upcoming()
    assert up["count"] == 1
    s = up["upcoming"][0]
    assert s["symbol"] == "ACME" and s["run_up_class"] == "BUY_RUMOR_IN_PLAY"
    assert s["implied_move_pct"] == 6.0


def test_unavailable_when_tables_missing():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with pytest.raises(Unavailable):
        _svc(conn).odds()


def test_empty_is_zeroes_not_error(db):
    out = _svc(db).odds()
    assert out["overall"]["n"] == 0 and out["overall"]["win_rate"] is None
    assert _svc(db).reactions()["count"] == 0
    assert _svc(db).upcoming()["count"] == 0
