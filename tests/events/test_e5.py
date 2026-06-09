"""E5 tests: earnings odds aggregation, format gating, historical backfill."""
from __future__ import annotations

import pytest

from nse_data.events import backfill
from nse_data.signals import earnings_odds as eo
from nse_data.storage import db as dbmod


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.open_db(str(tmp_path / "t.db"))
    dbmod.apply_migrations(c, migrations_dir="migrations")
    yield c
    c.close()


def _signal(conn, *, direction, ret_1d, detected_at="2026-04-30T15:30:00", symbol="ACME"):
    sid = int(conn.execute(
        "INSERT INTO signals (symbol, signal_type, detected_at, price, direction, dispatched) "
        "VALUES (?, 'earnings_direction', ?, 100, ?, 1)",
        (symbol, detected_at, direction),
    ).lastrowid)
    conn.execute(
        "INSERT INTO signal_outcomes (signal_id, symbol, detected_at, ret_1d) VALUES (?, ?, ?, ?)",
        (sid, symbol, detected_at, ret_1d),
    )
    conn.commit()
    return sid


# --------------------------------------------------------------------------- #
# odds aggregation
# --------------------------------------------------------------------------- #

def test_odds_direction_adjusted_win_rate(conn):
    # long +3% = win; long -1% = loss
    _signal(conn, direction="long", ret_1d=3.0)
    _signal(conn, direction="long", ret_1d=-1.0)
    # short -2% (price fell) = win for a short; short +1% = loss
    _signal(conn, direction="short", ret_1d=-2.0)
    _signal(conn, direction="short", ret_1d=1.0)
    odds = eo.compute_odds(conn)
    assert odds["n"] == 4
    assert odds["wins"] == 2          # one long win + one short win
    assert odds["win_rate"] == 50.0
    # avg adj return = (3 -1 +2 -1)/4 = 0.75
    assert odds["avg_return_pct"] == 0.75
    # PF = gains(3+2) / losses(1+1) = 2.5
    assert odds["profit_factor"] == 2.5


def test_odds_filter_by_direction(conn):
    _signal(conn, direction="short", ret_1d=-4.0)   # short win (+4 adjusted)
    _signal(conn, direction="long", ret_1d=-4.0)    # long loss
    short = eo.compute_odds(conn, direction="short")
    assert short["n"] == 1 and short["win_rate"] == 100.0 and short["avg_return_pct"] == 4.0


def test_odds_empty(conn):
    odds = eo.compute_odds(conn)
    assert odds["n"] == 0 and odds["win_rate"] is None


# --------------------------------------------------------------------------- #
# format gating
# --------------------------------------------------------------------------- #

def test_format_odds_gates_on_min_samples():
    thin = {"n": 5, "wins": 4, "win_rate": 80.0, "avg_return_pct": 2.0, "profit_factor": 3.0}
    assert eo.format_odds(thin) is None
    rich = {"n": 40, "wins": 26, "win_rate": 65.0, "avg_return_pct": 2.1, "profit_factor": 1.8}
    line = eo.format_odds(rich)
    assert line is not None
    assert "win 65.0%" in line and "n=40" in line and "PF 1.8" in line


def test_dispatcher_odds_line_appears_with_enough_history(conn):
    from nse_data.bot.dispatcher import _earnings_odds_line
    for _ in range(25):
        _signal(conn, direction="long", ret_1d=2.0)
    line = _earnings_odds_line(conn, "long")
    assert line is not None and "win 100.0%" in line and "n=25" in line


# --------------------------------------------------------------------------- #
# backfill
# --------------------------------------------------------------------------- #

def _bhav(conn, symbol, date, close):
    conn.execute(
        "INSERT INTO raw_bhavcopy_cm (date, symbol, series, close, volume) "
        "VALUES (?, ?, 'EQ', ?, 1000)", (date, symbol, close),
    )


def _result(conn, symbol, filing_date, fp):
    conn.execute(
        "INSERT INTO raw_financial_results (fingerprint, symbol, period, filing_date, created_at) "
        "VALUES (?, ?, 'Quarterly', ?, 1)", (fp, symbol, filing_date),
    )


def test_backfill_reconstructs_reaction_and_outcome(conn):
    _result(conn, "ACME", "2026-04-30", "f1")
    _bhav(conn, "ACME", "2026-04-29", 100.0)   # prior close
    _bhav(conn, "ACME", "2026-04-30", 110.0)   # reaction day (+10% -> long)
    _bhav(conn, "ACME", "2026-05-01", 115.0)   # T+1 (ret_1d from 110 = +4.55%)
    _bhav(conn, "ACME", "2026-05-04", 120.0)
    conn.commit()

    rep = backfill.backfill_earnings_reactions(conn)
    assert rep["backfilled"] == 1 and rep["skipped_flat"] == 0
    row = conn.execute(
        "SELECT direction, price FROM signals WHERE signal_type='earnings_direction'"
    ).fetchone()
    assert row == ("long", 110.0)
    ret_1d = conn.execute("SELECT ret_1d FROM signal_outcomes").fetchone()[0]
    assert ret_1d == pytest.approx(4.55, abs=0.05)

    # idempotent on re-run
    rep2 = backfill.backfill_earnings_reactions(conn)
    assert rep2["backfilled"] == 0 and rep2["skipped_already_present"] == 1


def test_backfill_skips_flat_and_missing(conn):
    # flat reaction (<1.5%)
    _result(conn, "FLAT", "2026-04-30", "f2")
    _bhav(conn, "FLAT", "2026-04-29", 100.0)
    _bhav(conn, "FLAT", "2026-04-30", 100.5)
    # no price data at all
    _result(conn, "NODATA", "2026-04-30", "f3")
    conn.commit()
    rep = backfill.backfill_earnings_reactions(conn)
    assert rep["backfilled"] == 0
    assert rep["skipped_flat"] == 1
    assert rep["skipped_no_price_data"] == 1


def test_backfill_short_direction(conn):
    _result(conn, "DROP", "2026-04-30", "f4")
    _bhav(conn, "DROP", "2026-04-29", 100.0)
    _bhav(conn, "DROP", "2026-04-30", 92.0)    # -8% -> short
    _bhav(conn, "DROP", "2026-05-01", 90.0)
    conn.commit()
    backfill.backfill_earnings_reactions(conn)
    d = conn.execute(
        "SELECT direction FROM signals WHERE symbol='DROP'").fetchone()[0]
    assert d == "short"
    # short odds: price fell further T+1 (90<92) -> a win (positive adjusted)
    odds = eo.compute_odds(conn, direction="short")
    assert odds["wins"] == 1
