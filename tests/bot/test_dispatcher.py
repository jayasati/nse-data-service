"""Telegram dispatcher (bot/dispatcher.py)."""

from __future__ import annotations

from datetime import timedelta

from nse_data.bot import dispatcher as d

from .conftest import NOW, FakeRedis, seed_signal, set_high_confidence


def _collecting_sender():
    sent = []
    def sender(token, chat_id, text):
        sent.append((token, chat_id, text))
        return True
    return sent, sender


def _dispatched(conn, sid):
    return conn.execute(
        "SELECT dispatched, dispatched_at FROM signals WHERE id = ?", (sid,)
    ).fetchone()


def test_high_confidence_sends_and_marks(bot_db):
    sid = seed_signal(bot_db, volume_ratio=4.0)
    r = FakeRedis()
    set_high_confidence(r)
    sent, sender = _collecting_sender()

    report = d.dispatch_pass(bot_db, token="tok", chat_id="chat",
                             redis_client=r, now=NOW, sender=sender)
    assert report["sent"] == 1
    assert len(sent) == 1
    assert sent[0][:2] == ("tok", "chat")
    assert "ACME" in sent[0][2] and "Long Buildup" in sent[0][2]
    assert _dispatched(bot_db, sid)[0] == 1


def test_low_confidence_recent_held_undispatched(bot_db):
    sid = seed_signal(bot_db, volume_ratio=0.5)   # below-1 vol penalty, weak ctx
    r = FakeRedis()                                # no ind hash -> ~base 0.5 - 0.1
    sent, sender = _collecting_sender()

    report = d.dispatch_pass(bot_db, token="tok", chat_id="chat",
                             redis_client=r, now=NOW, sender=sender)
    assert report["low_confidence"] == 1
    assert sent == []
    assert _dispatched(bot_db, sid)[0] == 0       # left for re-eval


def test_low_confidence_aged_out_marked(bot_db):
    old = (NOW - timedelta(minutes=20)).isoformat()
    sid = seed_signal(bot_db, detected_at=old, volume_ratio=0.5)
    r = FakeRedis()
    sent, sender = _collecting_sender()

    report = d.dispatch_pass(bot_db, token="tok", chat_id="chat",
                             redis_client=r, now=NOW, sender=sender)
    assert report["aged_out"] == 1
    assert sent == []
    assert _dispatched(bot_db, sid)[0] == 1       # given up on


def test_blacklisted_gated_and_marked(bot_db):
    sid = seed_signal(bot_db, volume_ratio=4.0)
    r = FakeRedis()
    set_high_confidence(r)
    r.sets["blacklist:symbols"] = {"ACME"}
    sent, sender = _collecting_sender()

    report = d.dispatch_pass(bot_db, token="tok", chat_id="chat",
                             redis_client=r, now=NOW, sender=sender)
    assert report["gated"] == 1
    assert sent == []                              # never sent
    assert _dispatched(bot_db, sid)[0] == 1


def test_send_failure_not_marked(bot_db):
    sid = seed_signal(bot_db, volume_ratio=4.0)
    r = FakeRedis()
    set_high_confidence(r)

    def failing_sender(token, chat_id, text):
        return False

    report = d.dispatch_pass(bot_db, token=None, chat_id=None,
                             redis_client=r, now=NOW, sender=failing_sender)
    assert report["held"] == 1
    assert _dispatched(bot_db, sid)[0] == 0       # retried next pass


def test_send_telegram_unconfigured_returns_false():
    assert d.send_telegram(None, None, "hi") is False
    assert d.send_telegram("tok", None, "hi") is False
