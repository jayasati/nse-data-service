"""E3 tests: reaction-window rule, surprise/confidence, direction-aware tracking."""
from __future__ import annotations

import datetime as dt

import pytest

from nse_data.events import matcher
from nse_data.fundamentals import from_results as fr
from nse_data.scheduler.market_hours import IST
from nse_data.signals import confidence as cf
from nse_data.signals import detect
from nse_data.signals import paper_tracker as pt
from nse_data.storage import db as dbmod


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.open_db(str(tmp_path / "t.db"))
    dbmod.apply_migrations(c, migrations_dir="migrations")
    yield c
    c.close()


# --------------------------------------------------------------------------- #
# confidence: earnings evidence + direction inversion
# --------------------------------------------------------------------------- #

def test_confidence_earnings_confirm_beats_contradict():
    base_ctx = {}
    confirm = cf.score_confidence(
        base_ctx, earnings={"surprise_sign": 1, "confirms_direction": True}, direction="long")
    contra = cf.score_confidence(
        base_ctx, earnings={"surprise_sign": 1, "confirms_direction": False}, direction="long")
    assert confirm > contra


def test_confidence_buy_rumor_fades_long():
    plain = cf.score_confidence({}, earnings={"surprise_sign": 1, "confirms_direction": True})
    fade = cf.score_confidence({}, earnings={
        "surprise_sign": 1, "confirms_direction": True, "run_up_class": "BUY_RUMOR_IN_PLAY"})
    assert fade < plain


def test_confidence_short_inverts_directional_terms():
    # below-VWAP + downtrend is BAD for a long, GOOD for a short
    ctx = {"price_vs_vwap": "below", "trend_regime": "downtrend"}
    long_score = cf.score_confidence(ctx, direction="long")
    short_score = cf.score_confidence(ctx, direction="short")
    assert short_score > long_score


# --------------------------------------------------------------------------- #
# matcher: surprise + evidence
# --------------------------------------------------------------------------- #

def test_classify_surprise():
    assert matcher.classify_surprise({"yoy_pat_pct": 30}) == (1, 30.0)
    assert matcher.classify_surprise({"yoy_pat_pct": -25}) == (-1, 25.0)
    assert matcher.classify_surprise({"yoy_pat_pct": 3}) == (0, 3.0)
    assert matcher.classify_surprise({}) == (0, 0.0)


def _store(conn, symbol, period, rev, pat):
    fr.persist_extraction(
        conn, symbol=symbol, period_ending=period, scope="standalone",
        fields={"revenue_cr": rev, "pat_cr": pat, "total_income_cr": rev}, units_phrase="INR crore",
        confidence=1.0, strategy="vision", source_fingerprint=None, broadcast_dt=None, now=1)


def test_build_earnings_evidence_confirms(conn):
    # PAT up 50% YoY -> beat; a long reaction confirms it
    _store(conn, "ACME", "2025-03-31", rev=100, pat=10)
    _store(conn, "ACME", "2026-03-31", rev=140, pat=15)
    ev = matcher.build_earnings_evidence(conn, "ACME", reaction_direction="long")
    assert ev["surprise_sign"] == 1
    assert ev["confirms_direction"] is True
    # a short reaction would contradict the beat
    ev2 = matcher.build_earnings_evidence(conn, "ACME", reaction_direction="short")
    assert ev2["confirms_direction"] is False


def test_build_earnings_evidence_none_without_actuals(conn):
    assert matcher.build_earnings_evidence(conn, "NOPE", reaction_direction="long") is None


# --------------------------------------------------------------------------- #
# detect: reaction-window rule
# --------------------------------------------------------------------------- #

def _seed_quote(conn, symbol, last_price):
    conn.execute(
        "INSERT INTO raw_equity_quotes (symbol, as_of, index_name, last_price, pct_change) "
        "VALUES (?, 1700000000, 'NIFTY 50', ?, 0)",
        (symbol, last_price),
    )
    conn.commit()


def test_rule_fires_long_on_upmove_in_window(conn):
    _seed_quote(conn, "ACME", 105.0)        # +5% vs baseline 100
    now = dt.datetime(2026, 4, 30, 15, 40, tzinfo=IST)
    result_ts = now.timestamp() - 7 * 60     # result 7 min ago (inside 5-15 window)
    m = detect._rule_earnings_direction(
        conn, "ACME", now, {"result_ts": result_ts, "baseline": 100.0})
    assert m is not None
    assert m["direction"] == "long"
    assert m["realized_move_pct"] == pytest.approx(5.0)


def test_rule_fires_short_on_downmove(conn):
    _seed_quote(conn, "ACME", 96.0)         # -4% vs baseline 100
    now = dt.datetime(2026, 4, 30, 15, 40, tzinfo=IST)
    result_ts = now.timestamp() - 8 * 60
    m = detect._rule_earnings_direction(
        conn, "ACME", now, {"result_ts": result_ts, "baseline": 100.0})
    assert m is not None and m["direction"] == "short"


def test_rule_silent_too_early_and_too_small(conn):
    _seed_quote(conn, "ACME", 105.0)
    now = dt.datetime(2026, 4, 30, 15, 40, tzinfo=IST)
    # 2 min after result -> before the 5-min window
    early = {"result_ts": now.timestamp() - 2 * 60, "baseline": 100.0}
    assert detect._rule_earnings_direction(conn, "ACME", now, early) is None
    # in window but move below threshold
    _seed_quote(conn, "FLAT", 100.5)        # +0.5%
    inwin = {"result_ts": now.timestamp() - 7 * 60, "baseline": 100.0}
    assert detect._rule_earnings_direction(conn, "FLAT", now, inwin) is None


def test_broadcast_epoch_parses_nse_format():
    now = dt.datetime(2026, 4, 30, 15, 40, tzinfo=IST)
    ts = detect._broadcast_epoch("30-Apr-2026 15:33:00", now)
    assert ts is not None
    # same wall-clock in IST
    assert dt.datetime.fromtimestamp(ts, IST).hour == 15


# --------------------------------------------------------------------------- #
# paper_tracker: short bracket + exit
# --------------------------------------------------------------------------- #

def test_compute_sl_t1_short_flips_bracket():
    sl_l, t1_l = pt.compute_sl_t1(100, 2, "long")
    assert (sl_l, t1_l) == (97.0, 103.0)
    sl_s, t1_s = pt.compute_sl_t1(100, 2, "short")
    assert (sl_s, t1_s) == (103.0, 97.0)     # stop above, target below


def test_exit_decision_short():
    # short: target 97 (hit on last<=97), stop 103 (hit on last>=103)
    assert pt._exit_decision(96.0, 103.0, 97.0, False, "short") == ("hit_t1", 97.0)
    assert pt._exit_decision(104.0, 103.0, 97.0, False, "short") == ("hit_sl", 103.0)
    assert pt._exit_decision(100.0, 103.0, 97.0, False, "short") == (None, 0.0)


# --------------------------------------------------------------------------- #
# dispatcher routing
# --------------------------------------------------------------------------- #

def test_detect_earnings_reactions_emits_directional_signal(conn):
    """Full live path: a just-filed result + an up-move -> a long signal row."""
    from nse_data.signals.dedup import SignalDedup

    now = dt.datetime(2026, 4, 30, 15, 40, tzinfo=IST)
    result_ts = int(now.timestamp() - 7 * 60)
    # result announcement 7 min ago
    conn.execute(
        "INSERT INTO raw_announcements (fingerprint, segment, symbol, subject, broadcast_dt, created_at) "
        "VALUES ('fp', 'equities', 'ACME', 'Financial Results', ?, 1)",
        (dt.datetime.fromtimestamp(result_ts, IST).strftime("%d-%b-%Y %H:%M:%S"),),
    )
    # a pre-result 1-min candle (baseline 100) + listing history for the gate
    conn.execute(
        "INSERT INTO raw_intraday_candles (symbol, interval, ts, open, high, low, close, volume) "
        "VALUES ('ACME', 'minute', ?, 100, 100, 100, 100, 1000)",
        (result_ts - 60,),
    )
    base = dt.date(2026, 1, 1)
    for i in range(40):
        conn.execute(
            "INSERT INTO raw_bhavcopy_cm (date, symbol, series, close, volume) "
            "VALUES (?, 'ACME', 'EQ', 100, 1000)", ((base + dt.timedelta(days=i)).isoformat(),))
    _seed_quote(conn, "ACME", 105.0)   # +5% reaction
    conn.commit()

    fired = detect._detect_earnings_reactions(
        conn, None, SignalDedup(None), now.isoformat(), None, now)
    assert fired == 1
    row = conn.execute(
        "SELECT signal_type, direction FROM signals WHERE symbol='ACME'").fetchone()
    assert row == ("earnings_direction", "long")


def test_topic_routing_earnings(monkeypatch):
    from nse_data.bot import dispatcher
    monkeypatch.setenv("TELEGRAM_TOPIC_EARNINGS", "555")
    monkeypatch.setenv("TELEGRAM_TOPIC_INTRADAY", "111")
    assert dispatcher._topic_for({"signal_type": "earnings_direction", "horizon": "intraday"}) == 555
    assert dispatcher._topic_for({"signal_type": "orb_breakout", "horizon": "intraday"}) == 111
    assert dispatcher._SIGNAL_LABELS["earnings_direction"] == "Earnings Reaction"
