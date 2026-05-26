"""
Tests for the endpoints.yaml -> APScheduler job-registration layer.

Covers trigger construction per cadence, the runtime gate (including holiday
awareness the cron expression can't provide), multi-time run_at fan-out, and
end-to-end registration against the real config via a fake scheduler.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from nse_data.scheduler import jobs, market_hours
from nse_data.scheduler.jobs import (
    _load_collector,
    build_triggers,
    make_gate,
    register_jobs,
)


def _next(trigger, after: datetime) -> datetime:
    """Next fire time at/after `after` (APScheduler CronTrigger API)."""
    return trigger.get_next_fire_time(None, after)


def at(y, mo, d, h, mi) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=market_hours.IST)


# ============================================================================
# _load_collector
# ============================================================================

def test_load_collector_instantiates():
    c = _load_collector("nse_data.collectors.pre_open:PreOpen")
    assert c.table == "raw_pre_open"
    assert type(c).__name__ == "PreOpen"


def test_load_collector_rejects_bad_spec():
    with pytest.raises(ValueError):
        _load_collector("no_colon_here")


# ============================================================================
# build_triggers — cadence -> CronTrigger
# ============================================================================

def test_interval_5m_fires_every_five_minutes():
    (suffix, trig), = build_triggers({"cadence": "5m", "market_hours_only": True})
    assert suffix == ""
    t = _next(trig, at(2026, 5, 19, 10, 2))
    assert (t.hour, t.minute) == (10, 5)


def test_interval_hour_bound_to_market_hours():
    """A 5m market-hours job should not fire at 03:00."""
    (_, trig), = build_triggers({"cadence": "5m", "market_hours_only": True})
    t = _next(trig, at(2026, 5, 19, 3, 0))
    assert t.hour == 9   # first fire of the day is in the 9 o'clock hour


def test_1h_fires_top_of_hour():
    (_, trig), = build_triggers({"cadence": "1h", "active_hours": "08:00-19:00"})
    t = _next(trig, at(2026, 5, 19, 10, 30))
    assert (t.hour, t.minute) == (11, 0)


def test_daily_run_at_single():
    (suffix, trig), = build_triggers({"cadence": "daily", "run_at": "09:08"})
    assert suffix == ""
    t = _next(trig, at(2026, 5, 19, 6, 0))
    assert (t.hour, t.minute) == (9, 8)


def test_daily_run_at_multi_time_fans_out():
    """call_auction-style: two fire times -> two suffixed triggers."""
    triggers = build_triggers({"cadence": "daily", "run_at": ["09:05", "10:05"]})
    suffixes = sorted(s for s, _ in triggers)
    assert suffixes == ["@09:05", "@10:05"]
    by = dict(triggers)
    assert (_next(by["@09:05"], at(2026, 5, 19, 0, 0)).hour,
            _next(by["@09:05"], at(2026, 5, 19, 0, 0)).minute) == (9, 5)
    assert _next(by["@10:05"], at(2026, 5, 19, 0, 0)).minute == 5


def test_weekly_run_at_sunday():
    (suffix, trig), = build_triggers({"cadence": "weekly", "run_at": "Sun 06:00"})
    assert suffix == ""
    t = _next(trig, at(2026, 5, 19, 0, 0))   # Tue -> next Sunday
    assert t.weekday() == 6
    assert (t.hour, t.minute) == (6, 0)


def test_unknown_cadence_raises():
    with pytest.raises(ValueError):
        build_triggers({"cadence": "fortnightly"})


def test_daily_without_run_at_raises():
    with pytest.raises(ValueError):
        build_triggers({"cadence": "daily"})


# ============================================================================
# make_gate — runtime predicate
# ============================================================================

def test_market_hours_gate(monkeypatch):
    gate = make_gate({"market_hours_only": True})
    monkeypatch.setattr(market_hours, "now_ist", lambda: at(2026, 5, 19, 10, 0))
    assert gate() is True
    monkeypatch.setattr(market_hours, "now_ist", lambda: at(2026, 5, 19, 16, 0))
    assert gate() is False


def test_market_hours_gate_closed_on_holiday(monkeypatch):
    gate = make_gate({"market_hours_only": True})
    # 2026-01-26 Republic Day, during would-be market hours
    monkeypatch.setattr(market_hours, "now_ist", lambda: at(2026, 1, 26, 11, 0))
    assert gate() is False


def test_trading_day_gate_skips_holiday(monkeypatch):
    """pre_open's gate: fires at 09:08 daily, but must skip holidays."""
    gate = make_gate({"trading_day_only": True})
    monkeypatch.setattr(market_hours, "now_ist", lambda: at(2026, 5, 19, 9, 8))
    assert gate() is True   # Tuesday
    monkeypatch.setattr(market_hours, "now_ist", lambda: at(2026, 1, 26, 9, 8))
    assert gate() is False  # Republic Day
    monkeypatch.setattr(market_hours, "now_ist", lambda: at(2026, 5, 17, 9, 8))
    assert gate() is False  # Sunday


def test_active_hours_window(monkeypatch):
    gate = make_gate({"active_hours": "08:00-19:00"})
    monkeypatch.setattr(market_hours, "now_ist", lambda: at(2026, 5, 19, 12, 0))
    assert gate() is True
    monkeypatch.setattr(market_hours, "now_ist", lambda: at(2026, 5, 19, 7, 59))
    assert gate() is False
    monkeypatch.setattr(market_hours, "now_ist", lambda: at(2026, 5, 19, 19, 1))
    assert gate() is False


def test_no_conditions_always_runs(monkeypatch):
    """Weekly Sunday jobs must run on Sunday — i.e. no trading-day gate."""
    gate = make_gate({"cadence": "weekly", "run_at": "Sun 06:00"})
    monkeypatch.setattr(market_hours, "now_ist", lambda: at(2026, 5, 17, 6, 0))
    assert gate() is True   # Sunday


# ============================================================================
# register_jobs — end to end with a fake scheduler
# ============================================================================

class FakeScheduler:
    def __init__(self):
        self.jobs = []

    def add_job(self, func, trigger=None, id=None, **kw):
        self.jobs.append({"func": func, "trigger": trigger, "id": id, "kw": kw})


def test_register_skips_disabled():
    sched = FakeScheduler()
    endpoints = {
        "a": {"collector": "nse_data.collectors.pre_open:PreOpen",
              "cadence": "5m", "enabled": True, "market_hours_only": True},
        "b": {"collector": "nse_data.collectors.pre_open:PreOpen",
              "cadence": "5m", "enabled": False},
    }
    ids = register_jobs(sched, endpoints, runner=lambda c: None)
    assert ids == ["a"]
    assert len(sched.jobs) == 1


def test_register_multi_time_creates_two_jobs():
    sched = FakeScheduler()
    endpoints = {
        "call_auction": {
            "collector": "nse_data.collectors.pre_open:PreOpen",
            "cadence": "daily", "run_at": ["09:05", "10:05"], "enabled": True,
        },
    }
    ids = register_jobs(sched, endpoints, runner=lambda c: None)
    assert sorted(ids) == ["call_auction@09:05", "call_auction@10:05"]


def test_register_bad_entry_skipped_not_fatal():
    sched = FakeScheduler()
    endpoints = {
        "good": {"collector": "nse_data.collectors.pre_open:PreOpen",
                 "cadence": "5m", "enabled": True, "market_hours_only": True},
        "bad": {"collector": "nse_data.collectors.pre_open:PreOpen",
                "cadence": "fortnightly", "enabled": True},
    }
    ids = register_jobs(sched, endpoints, runner=lambda c: None)
    assert ids == ["good"]


def test_gated_job_does_not_call_runner_when_closed(monkeypatch):
    sched = FakeScheduler()
    called = []
    endpoints = {
        "x": {"collector": "nse_data.collectors.pre_open:PreOpen",
              "cadence": "5m", "enabled": True, "market_hours_only": True},
    }
    register_jobs(sched, endpoints, runner=lambda c: called.append(c.name))
    job = sched.jobs[0]["func"]

    monkeypatch.setattr(market_hours, "now_ist", lambda: at(2026, 5, 19, 3, 0))
    job()
    assert called == []   # market closed -> gated out

    monkeypatch.setattr(market_hours, "now_ist", lambda: at(2026, 5, 19, 10, 0))
    job()
    assert called == ["x"]   # market open -> runner invoked, name stamped


def test_register_against_real_endpoints_yaml():
    """Smoke: every enabled entry in the shipped config registers cleanly."""
    from nse_data.settings import load_endpoints

    sched = FakeScheduler()
    endpoints = load_endpoints("config/endpoints.yaml")
    ids = register_jobs(sched, endpoints, runner=lambda c: None)

    enabled = [n for n, c in endpoints.items() if c.get("enabled")]
    # At least one job per enabled endpoint (multi-time entries yield more).
    assert len(ids) >= len(enabled)
    assert "pre_open" in ids
