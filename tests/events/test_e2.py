"""E2 tests: expectation proxy, results calendar, pre-event flag (no API/Telegram)."""
from __future__ import annotations

import datetime as dt

import pytest

from nse_data.events import calendar as cal
from nse_data.events import expectation as exp
from nse_data.events import pre_screen as ps
from nse_data.storage import db as dbmod


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.open_db(str(tmp_path / "t.db"))
    dbmod.apply_migrations(c, migrations_dir="migrations")
    yield c
    c.close()


# --------------------------------------------------------------------------- #
# expectation.py (pure)
# --------------------------------------------------------------------------- #

def test_classify_runup_bands():
    assert exp.classify_runup(12) == "BUY_RUMOR_IN_PLAY"
    assert exp.classify_runup(5) == "MILD_ANTICIPATION"
    assert exp.classify_runup(0) == "NORMAL"
    assert exp.classify_runup(-5) == "MILD_FEAR"
    assert exp.classify_runup(-20) == "FEAR_PRICED"
    assert exp.classify_runup(None) == "UNKNOWN"


def test_classify_oi_buildup():
    assert exp.classify_oi_buildup(5, 2) == "LONG_BUILDUP"
    assert exp.classify_oi_buildup(5, -2) == "SHORT_BUILDUP"
    assert exp.classify_oi_buildup(0.2, 2) == "NEUTRAL"   # OI change too small
    assert exp.classify_oi_buildup(None, 2) == "NEUTRAL"


def test_implied_move_from_iv():
    # 40% IV, 5 days -> ~4.68%
    assert exp.implied_move_from_iv(40, 5) == pytest.approx(4.68, abs=0.05)
    assert exp.implied_move_from_iv(None, 5) is None
    assert exp.implied_move_from_iv(40, 0) is None


def test_classify_fundamental_trend():
    assert exp.classify_fundamental_trend({"yoy_pat_pct": 30}) == "STRONG_GROWTH"
    assert exp.classify_fundamental_trend({"yoy_pat_pct": 8}) == "GROWTH"
    assert exp.classify_fundamental_trend({"yoy_pat_pct": 0}) == "FLAT"
    assert exp.classify_fundamental_trend({"yoy_pat_pct": -20}) == "DECLINE"
    assert exp.classify_fundamental_trend({}) == "UNKNOWN"


def test_expectation_proxy_score_and_notable():
    # buy-rumor + strong growth + low PCR -> strong positive (priced for rise)
    s = exp.expectation_proxy_score("BUY_RUMOR_IN_PLAY", "STRONG_GROWTH", 0.7)
    assert s > 0.5
    assert exp.is_notable({"run_up_class": "BUY_RUMOR_IN_PLAY"})
    assert exp.is_notable({"implied_move_pct": 8.0})
    assert not exp.is_notable({"run_up_class": "NORMAL", "implied_move_pct": 2.0,
                               "expectation_proxy_score": 0.1})


def test_build_flag_message_mentions_key_facts():
    setup = {
        "run_up_5d": 9.3, "run_up_class": "BUY_RUMOR_IN_PLAY",
        "implied_move_pct": 6.1, "iv_atm": 44.0, "pcr": 0.7,
        "oi_buildup_class": "LONG_BUILDUP", "growth_yoy_rev": 18.0,
        "growth_yoy_pat": 25.0, "fundamental_class": "STRONG_GROWTH",
        "sector_rank": 2, "sector_trend": "improving",
        "expectation_proxy_score": 0.72,
    }
    msg = exp.build_flag_message("TATAMOTORS", "2026-07-25", setup)
    assert "TATAMOTORS" in msg and "2026-07-25" in msg
    assert "BUY_RUMOR_IN_PLAY" in msg and "6.1%" in msg
    assert "priced for RISE" in msg


# --------------------------------------------------------------------------- #
# calendar.py
# --------------------------------------------------------------------------- #

def _add_board_meeting(conn, symbol, date_str, purpose, fp=None):
    conn.execute(
        "INSERT INTO raw_board_meetings (fingerprint, symbol, meeting_date, purpose, created_at) "
        "VALUES (?, ?, ?, ?, 1700000000)",
        (fp or f"{symbol}-{date_str}", symbol, date_str, purpose),
    )
    conn.commit()


def test_parse_nse_date():
    assert cal._parse_nse_date("25-Jul-2026") == dt.date(2026, 7, 25)
    assert cal._parse_nse_date("25-Jul-2026 17:00:00") == dt.date(2026, 7, 25)
    assert cal._parse_nse_date("2026-07-25") == dt.date(2026, 7, 25)
    assert cal._parse_nse_date(None) is None


def test_calendar_builds_and_reconciles(conn):
    today = dt.date(2026, 7, 1)
    now = dt.datetime(2026, 7, 1, 20, 0)
    _add_board_meeting(conn, "TCS", "25-Jul-2026", "To consider financial results")
    _add_board_meeting(conn, "OLDCO", "01-Jun-2026", "Financial Results")   # past
    _add_board_meeting(conn, "NOPE", "20-Jul-2026", "Buyback consideration") # not results

    rep = cal.run_calendar_pass(conn, now=now)
    rows = conn.execute(
        "SELECT symbol, expected_date, source, status FROM pending_events ORDER BY symbol"
    ).fetchall()
    syms = {r[0] for r in rows}
    assert "TCS" in syms          # future results meeting
    assert "NOPE" not in syms     # not a results meeting
    # OLDCO meeting was in the past -> not added as upcoming
    assert "OLDCO" not in syms


def test_calendar_marks_filed(conn):
    today = dt.date(2026, 7, 28)
    now = dt.datetime(2026, 7, 28, 20, 0)
    _add_board_meeting(conn, "TCS", "25-Jul-2026", "Financial Results")
    # a quarterly filing landed on the 25th
    conn.execute(
        "INSERT INTO raw_financial_results (fingerprint, symbol, period, filing_date, created_at) "
        "VALUES ('f1', 'TCS', 'Quarterly', '25-Jul-2026', 1700000000)"
    )
    conn.commit()
    # build (meeting in past now, so add manually as upcoming then reconcile)
    conn.execute(
        "INSERT INTO pending_events (symbol, event_type, expected_date, source, confidence, status, created_at) "
        "VALUES ('TCS','result','2026-07-25','board_meeting',0.9,'upcoming',1700000000)"
    )
    conn.commit()
    rec = cal.reconcile_status(conn, today, 1700000300)
    assert rec["filed"] == 1
    st = conn.execute("SELECT status FROM pending_events WHERE symbol='TCS'").fetchone()[0]
    assert st == "filed"


# --------------------------------------------------------------------------- #
# pre_screen.py — snapshot + flag (fake sender)
# --------------------------------------------------------------------------- #

def _seed_market_data(conn, symbol):
    # 12 daily closes rising ~10% over the last 5 sessions (buy-rumor run-up)
    base = dt.date(2026, 7, 10)
    closes = [100, 100, 100, 100, 100, 100, 100, 102, 104, 107, 109, 110]
    for i, c in enumerate(closes):
        d = (base + dt.timedelta(days=i)).isoformat()
        conn.execute(
            "INSERT INTO raw_bhavcopy_cm (date, symbol, series, close, volume) "
            "VALUES (?, ?, 'EQ', ?, 1000000)",
            (d, symbol, c),
        )
    # option chain: one expiry, strikes around spot 110, with IV + OI
    as_of = 1700000000
    for strike in (100, 110, 120):
        for ot, oi, iv in (("CE", 5000, 42.0), ("PE", 4000, 44.0)):
            conn.execute(
                "INSERT INTO raw_option_chain "
                "(symbol, expiry, strike, option_type, as_of, underlying_value, "
                " open_interest, implied_volatility) VALUES (?, '31-Jul-2026', ?, ?, ?, 110, ?, ?)",
                (symbol, strike, ot, as_of, oi, iv),
            )
    # sector state for the symbol's sector (ADANIPOWER -> NIFTY ENERGY in mapping)
    conn.execute(
        "INSERT INTO sector_state (sector_name, as_of, rs_rank, rs_trend) "
        "VALUES ('NIFTY ENERGY', '2026-07-20T15:30:00', 2, 'improving')"
    )
    conn.commit()


def test_pre_screen_builds_setup_and_flags_once(conn):
    symbol = "ADANIPOWER"   # present in config/sector_mapping.yaml
    _seed_market_data(conn, symbol)
    conn.execute(
        "INSERT INTO pending_events (symbol, event_type, expected_date, source, confidence, status, created_at) "
        "VALUES (?, 'result', '2026-07-25', 'board_meeting', 0.9, 'upcoming', 1700000000)",
        (symbol,),
    )
    conn.commit()

    sent = []
    def fake_sender(token, chat_id, text, thread_id, **_kw):
        sent.append(text)
        return True

    now = dt.datetime(2026, 7, 23, 20, 15)   # 2 days before the event
    rep = ps.run_pre_screen_pass(conn, horizon_days=3, now=now, sender=fake_sender)
    assert rep["screened"] == 1 and rep["flagged"] == 1
    assert len(sent) == 1 and symbol in sent[0]

    setup = conn.execute(
        "SELECT run_up_class, iv_atm, pcr, sector_rank, flagged_at "
        "FROM earnings_setups WHERE symbol=?", (symbol,)
    ).fetchone()
    assert setup[0] == "BUY_RUMOR_IN_PLAY"   # +10% run-up
    assert setup[1] == pytest.approx(43.0)   # avg ATM IV (42 CE, 44 PE)
    assert setup[2] == pytest.approx(0.8)    # PCR 4000/5000
    assert setup[3] == 2                      # sector rank
    assert setup[4] is not None              # flagged_at set

    # second pass: already flagged -> no new send
    rep2 = ps.run_pre_screen_pass(conn, horizon_days=3, now=now, sender=fake_sender)
    assert rep2["flagged"] == 0 and len(sent) == 1
