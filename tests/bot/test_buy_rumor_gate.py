"""Week 18.3: BUY_RUMOR_IN_PLAY suppressing longs into an imminent result."""
from __future__ import annotations

from nse_data.bot import dispatcher as d

from .conftest import NOW, FakeRedis, seed_signal, set_high_confidence


def _collecting_sender():
    sent = []

    def sender(token, chat_id, text, thread_id=None):
        sent.append(text)
        return True

    return sent, sender


def _set_pre_event(conn, symbol, state="BUY_RUMOR_IN_PLAY", days=2, run10=11.0):
    conn.execute(
        "INSERT INTO indicator_live (symbol, updated_at, pre_event_state, "
        "days_to_event, pre_event_run_10d) VALUES (?, 'x', ?, ?, ?) "
        "ON CONFLICT(symbol) DO UPDATE SET pre_event_state=excluded.pre_event_state, "
        "days_to_event=excluded.days_to_event, pre_event_run_10d=excluded.pre_event_run_10d",
        (symbol, state, days, run10),
    )
    conn.commit()


def test_long_suppressed_with_warning(bot_db):
    sid = seed_signal(bot_db, volume_ratio=4.0)
    _set_pre_event(bot_db, "ACME", days=2)
    r = FakeRedis()
    set_high_confidence(r)
    sent, sender = _collecting_sender()

    report = d.dispatch_pass(bot_db, token="t", chat_id="c",
                             redis_client=r, now=NOW, sender=sender)
    assert report["buy_rumor_suppressed"] == 1
    assert report["sent"] == 0
    assert len(sent) == 1
    assert "BUY RUMOR WARNING" in sent[0]
    assert "ACME" in sent[0]
    # the suppressed signal is finished, not retried
    assert bot_db.execute(
        "SELECT dispatched FROM signals WHERE id=?", (sid,)).fetchone()[0] == 1
    # the warning left its audit row
    assert bot_db.execute(
        "SELECT COUNT(*) FROM signals WHERE signal_type='buy_rumor_warning'",
    ).fetchone()[0] == 1


def test_warning_sent_once_per_day(bot_db):
    seed_signal(bot_db, volume_ratio=4.0)
    bot_db.execute(
        "INSERT INTO signals (symbol, signal_type, detected_at, volume_ratio) "
        "VALUES ('ACME', 'long_buildup', ?, 4.0)", (NOW.isoformat(),),
    )
    bot_db.commit()
    _set_pre_event(bot_db, "ACME", days=1)
    r = FakeRedis()
    set_high_confidence(r)
    sent, sender = _collecting_sender()

    report = d.dispatch_pass(bot_db, token="t", chat_id="c",
                             redis_client=r, now=NOW, sender=sender)
    assert report["buy_rumor_suppressed"] == 2
    assert len(sent) == 1          # both suppressed, one warning


def test_event_too_far_not_suppressed(bot_db):
    seed_signal(bot_db, volume_ratio=4.0)
    _set_pre_event(bot_db, "ACME", days=5)   # > 3 days out
    r = FakeRedis()
    set_high_confidence(r)
    sent, sender = _collecting_sender()

    report = d.dispatch_pass(bot_db, token="t", chat_id="c",
                             redis_client=r, now=NOW, sender=sender)
    assert report["buy_rumor_suppressed"] == 0
    assert report["sent"] == 1
    assert "BUY RUMOR WARNING" not in sent[0]


def test_short_signal_not_suppressed(bot_db):
    seed_signal(bot_db, volume_ratio=4.0)
    bot_db.execute("UPDATE signals SET direction='short'")
    bot_db.commit()
    _set_pre_event(bot_db, "ACME", days=1)
    r = FakeRedis()
    # short-favourable context: below VWAP, downtrend, low RSI
    r.hashes["ind:ACME"] = {
        "price_vs_vwap": "below", "vwap_slope": "-0.5",
        "rsi_5m": "40.0", "trend_regime": "strong_downtrend",
    }
    sent, sender = _collecting_sender()

    report = d.dispatch_pass(bot_db, token="t", chat_id="c",
                             redis_client=r, now=NOW, sender=sender)
    assert report["buy_rumor_suppressed"] == 0
    assert report["sent"] == 1


def test_mild_anticipation_not_suppressed(bot_db):
    seed_signal(bot_db, volume_ratio=4.0)
    _set_pre_event(bot_db, "ACME", state="MILD_ANTICIPATION", days=1, run10=5.0)
    r = FakeRedis()
    set_high_confidence(r)
    sent, sender = _collecting_sender()

    report = d.dispatch_pass(bot_db, token="t", chat_id="c",
                             redis_client=r, now=NOW, sender=sender)
    assert report["buy_rumor_suppressed"] == 0
    assert report["sent"] == 1
