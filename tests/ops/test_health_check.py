"""Unit tests for ops.health_check — failing detection, thresholds, dedup."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from nse_data.ops import health, health_check
from nse_data.scheduler.market_hours import IST


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=IST)


def _collector(name, *, cadence="5m", enabled=True, status="ok", lag_seconds: int | None = 0):
    return {
        "name": name, "cadence": cadence, "enabled": enabled,
        "status": status, "lag_seconds": lag_seconds,
    }


# ---- thresholds ------------------------------------------------------------

def test_threshold_has_15min_floor():
    # fast feeds never alert before 15 minutes
    assert health_check.alert_threshold_seconds("5m") == 900
    assert health_check.alert_threshold_seconds("1m") == 900
    assert health_check.alert_threshold_seconds("3m") == 900


def test_threshold_scales_for_slower_intraday():
    assert health_check.alert_threshold_seconds("10m") == 1800   # 3 * 600
    assert health_check.alert_threshold_seconds("30m") == 5400   # 3 * 1800


# ---- find_failing ----------------------------------------------------------

def test_flags_stale_5m_collector():
    report = {"collectors": [_collector("oi_spurts", lag_seconds=1200)]}  # 20m > 15m
    failing = health_check.find_failing(report)
    assert [c["name"] for c in failing] == ["oi_spurts"]
    assert "stale" in failing[0]["reason"]


def test_ignores_fresh_collector():
    report = {"collectors": [_collector("indices", lag_seconds=300)]}  # 5m < 15m
    assert health_check.find_failing(report) == []


def test_ignores_disabled_and_non_intraday():
    report = {"collectors": [
        _collector("off", enabled=False, lag_seconds=99999),
        _collector("fii_dii", cadence="daily", lag_seconds=99999),
    ]}
    assert health_check.find_failing(report) == []


def test_flags_empty_and_missing_table():
    report = {"collectors": [
        _collector("empty_feed", status="empty", lag_seconds=None),
        _collector("nomig", status="no_table", lag_seconds=None),
    ]}
    names = {c["name"] for c in health_check.find_failing(report)}
    assert names == {"empty_feed", "nomig"}


def test_sorted_worst_first():
    report = {"collectors": [
        _collector("mild", lag_seconds=1000),
        _collector("severe", lag_seconds=9000),
    ]}
    assert [c["name"] for c in health_check.find_failing(report)] == ["severe", "mild"]


# ---- run_check dedup -------------------------------------------------------

class _Recorder:
    def __init__(self):
        self.messages: list[str] = []

    def __call__(self, token, chat_id, text):
        self.messages.append(text)
        return True


def _run(monkeypatch, canned, state_path, sender):
    monkeypatch.setattr(health, "build_report", lambda conn, endpoints, now=None: canned)
    return health_check.run_check(
        conn=None, endpoints={}, token="t", chat_id="c",  # type: ignore[arg-type]
        now=dt("2026-06-05T10:00:00"), state_path=state_path, sender=sender,
    )


def test_alert_then_silent_then_recover(monkeypatch, tmp_path: Path):
    state = tmp_path / "state.json"
    sender = _Recorder()
    failing_report = {"collectors": [_collector("oi_spurts", lag_seconds=1200)]}
    healthy_report = {"collectors": [_collector("oi_spurts", lag_seconds=60)]}

    # 1st run: a feed is failing -> alert + persist
    r1 = _run(monkeypatch, failing_report, state, sender)
    assert r1["action"] == "alerted" and len(sender.messages) == 1
    assert sender.messages[0].startswith("🔴")

    # 2nd run: same feed still failing -> stay quiet
    r2 = _run(monkeypatch, failing_report, state, sender)
    assert r2["action"] == "still_failing" and len(sender.messages) == 1

    # 3rd run: recovered -> all-clear message
    r3 = _run(monkeypatch, healthy_report, state, sender)
    assert r3["action"] == "recovered" and len(sender.messages) == 2
    assert sender.messages[1].startswith("✅")

    # 4th run: still healthy -> nothing
    r4 = _run(monkeypatch, healthy_report, state, sender)
    assert r4["action"] == "none" and len(sender.messages) == 2


def test_no_persist_when_send_fails(monkeypatch, tmp_path: Path):
    state = tmp_path / "state.json"
    failing_report = {"collectors": [_collector("oi_spurts", lag_seconds=1200)]}

    def failing_sender(token, chat_id, text):
        return False

    r = _run(monkeypatch, failing_report, state, failing_sender)
    assert r["action"] == "none"          # send failed, so no state advance
    assert not state.exists()             # nothing persisted -> retried next run
