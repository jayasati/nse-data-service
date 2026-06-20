"""Tests for the ntfy alert channel + the send_telegram mirror."""
from __future__ import annotations

from nse_data.bot import notify
from nse_data.bot.dispatcher import send_telegram


class _Resp:
    status_code = 200


def test_ntfy_noop_without_topic(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    assert notify.ntfy_send("hello") is False


def test_ntfy_posts_when_configured(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "secret-topic")
    monkeypatch.delenv("NTFY_SERVER", raising=False)
    cap = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        cap.update(url=url, data=data, headers=headers)
        return _Resp()

    monkeypatch.setattr("requests.post", fake_post)
    assert notify.ntfy_send("📊 Brief\nbody") is True
    assert cap["url"] == "https://ntfy.sh/secret-topic"
    assert cap["data"] == "📊 Brief\nbody".encode("utf-8")     # UTF-8 body preserved
    assert cap["headers"]["Title"] == "Brief"                  # emoji stripped from the header


def test_ascii_title_strips_non_latin():
    assert notify._ascii_title("🌅 Market Brief — 2026") == "Market Brief  2026"
    assert notify._ascii_title("") == "Stock-Bot"


def test_channel_routes_to_per_channel_topic(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "catchall")
    monkeypatch.setenv("NTFY_TOPIC_CREDIT", "credit-topic")
    monkeypatch.delenv("NTFY_TOPIC_SIGNALS", raising=False)
    cap = {}
    monkeypatch.setattr("requests.post",
                        lambda url, **k: cap.update(url=url) or _Resp())
    notify.ntfy_send("rating action", channel="credit")
    assert cap["url"].endswith("/credit-topic")            # routed to its own topic
    notify.ntfy_send("a signal", channel="signals")        # no signals topic set
    assert cap["url"].endswith("/catchall")                # falls back to catch-all


def test_send_telegram_mirrors_to_ntfy_when_telegram_unconfigured(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    monkeypatch.setattr("requests.post", lambda *a, **k: _Resp())
    # no Telegram token, but ntfy delivers → overall True
    assert send_telegram(None, None, "alert text") is True
