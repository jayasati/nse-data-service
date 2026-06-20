"""Swing vs intraday split: message templates, topic routing, EOD window."""

from __future__ import annotations

from datetime import datetime

from nse_data.bot import dispatcher
from nse_data.scheduler.market_hours import IST

from .conftest import NOW, FakeRedis, seed_signal, set_high_confidence

_BASE = {"symbol": "ZED", "price": 100.0, "atr_14_daily": 2.0,
         "oi_change_pct": 1.0, "price_change_pct": 2.0, "volume_ratio": 1.5}
_CTX = {"price_vs_vwap": "above", "vwap_slope": 0.1, "rsi_5m": 55,
        "trend_regime": "uptrend"}


def test_message_template_branches_by_horizon():
    swing = dispatcher.format_message(
        {**_BASE, "signal_type": "breakout_52wh", "horizon": "swing"}, _CTX, 0.80,
        credit={"min_lt_grade": "AA", "quality_score": 88})
    assert "[SWING]" in swing and "Hold days" in swing
    assert "Credit: AA q88" in swing and "Flat by 15:15" not in swing

    intraday = dispatcher.format_message(
        {**_BASE, "signal_type": "oi_spurt", "horizon": "intraday"}, _CTX, 0.80)
    assert "[INTRADAY]" in intraday and "Flat by 15:15" in intraday
    assert "Hold days" not in intraday


def test_topic_routing_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOPIC_INTRADAY", "5")
    monkeypatch.setenv("TELEGRAM_TOPIC_SWING", "3")
    assert dispatcher._topic_for({"horizon": "intraday"}) == 5
    assert dispatcher._topic_for({"horizon": "swing"}) == 3
    monkeypatch.delenv("TELEGRAM_TOPIC_INTRADAY")
    assert dispatcher._topic_for({"horizon": "intraday"}) is None   # no topic → main channel


def test_eod_window_for_swing_batch():
    # Monday 2026-06-08, a trading day
    assert dispatcher._in_eod_window(datetime(2026, 6, 8, 16, 0, tzinfo=IST)) is True
    assert dispatcher._in_eod_window(datetime(2026, 6, 8, 12, 0, tzinfo=IST)) is False  # market hrs
    assert dispatcher._in_eod_window(datetime(2026, 6, 8, 19, 0, tzinfo=IST)) is False  # too late
    assert dispatcher._in_eod_window(datetime(2026, 6, 7, 16, 0, tzinfo=IST)) is False  # Sunday


def test_swing_fires_while_intraday_suppressed_after_1520(bot_db):
    """The key timing-split: at 15:25 (NO_NEW_TRADES), a swing setup still
    dispatches (EOD batch) but an intraday setup is time-suppressed."""
    seed_signal(bot_db, symbol="ACME", volume_ratio=4.0)            # swing (default)
    intr = seed_signal(bot_db, symbol="ZEDX", volume_ratio=4.0)
    bot_db.execute("UPDATE signals SET signal_type='oi_spurt', horizon='intraday' "
                   "WHERE id=?", (intr,))
    bot_db.commit()

    r = FakeRedis()
    set_high_confidence(r, "ACME")
    set_high_confidence(r, "ZEDX")
    sent = []
    sender = lambda tok, chat, text, thread=None, **_kw: sent.append(text) or True

    after_close = NOW.replace(hour=15, minute=25)
    report = dispatcher.dispatch_pass(bot_db, token="tok", chat_id="chat",
                                      redis_client=r, now=after_close, sender=sender)

    assert any("ACME" in t for t in sent)              # swing went out
    assert not any("ZEDX" in t for t in sent)          # intraday held
    assert report["time_suppressed"] >= 1
