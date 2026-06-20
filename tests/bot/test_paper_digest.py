"""Tests for the weekly paper-book Telegram digest."""
from __future__ import annotations

import sqlite3

from nse_data.bot import paper_digest as pd

_BOOK = """
CREATE TABLE paper_book (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, strategy TEXT,
  status TEXT, entry_date TEXT, entry_px REAL, last_score REAL, stop_px REAL, trail_stop REAL,
  qty INTEGER, risk_rupees REAL, net_pct REAL, r_multiple REAL, exit_reason TEXT, exit_date TEXT);
CREATE TABLE raw_intraday_candles (symbol TEXT, interval TEXT, ts INTEGER, close REAL);
"""
_TS = 1_790_000_000


def _conn(pb=(), prices=()):
    c = sqlite3.connect(":memory:")
    c.executescript(_BOOK)
    c.executemany("INSERT INTO paper_book (symbol,strategy,status,entry_date,entry_px,last_score,"
                  "stop_px,trail_stop,qty,risk_rupees,net_pct,r_multiple,exit_reason,exit_date) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", pb)
    c.executemany("INSERT INTO raw_intraday_candles VALUES (?,?,?,?)",
                  [(s, "day", _TS, px) for s, px in prices] + [("NIFTYBEES", "day", _TS, 280.0)])
    c.commit()
    return c


def test_digest_empty_book():
    out = pd.build_paper_digest(sqlite3.connect(":memory:"))
    assert "No positions yet" in out


def test_digest_open_only():
    c = _conn(
        pb=[("AAA", "lean", "open", "2026-01-05", 100.0, 86, 95.0, None, 100, 1000, None, None, None, None),
            ("BBB", "lean", "open", "2026-01-06", 200.0, 81, 190.0, None, 50, 1000, None, None, None, None)],
        prices=[("AAA", 112.0), ("BBB", 196.0)])
    out = pd.build_paper_digest(c)
    assert "▶ lean — 2 open" in out
    assert "no closed trades yet" in out
    assert "movers:" in out and "AAA +12.0%" in out


def test_digest_with_closed_shows_verdict():
    pb = [("C%d" % i, "lean", "closed", "2026-01-01", 100.0, 70, 95.0, None, 100, 1000,
           (6.0 if i % 2 == 0 else -3.0), (1.2 if i % 2 == 0 else -1.0), "t_out", "2026-01-10")
          for i in range(40)]
    out = pd.build_paper_digest(_conn(pb=pb, prices=[("C0", 100.0)]))
    assert "40 closed (40% to 100)" in out
    assert "Exp +1.50%" in out and "WATCH" in out          # 40 < 100 → can't promote yet


def test_send_skips_without_telegram_config(monkeypatch):
    monkeypatch.setattr(pd, "load_telegram_config", lambda: (None, None))
    assert pd.send_paper_digest("ignored.db")["skipped"] == "no_telegram_config"


def test_send_calls_sender(monkeypatch, tmp_path):
    db = tmp_path / "t.db"
    sqlite3.connect(str(db)).close()                       # empty file → build returns "no positions"
    monkeypatch.setattr(pd, "load_telegram_config", lambda: ("TOKEN", "CHAT"))
    captured = {}

    def fake_send(token, chat_id, text, thread_id=None):
        captured.update(token=token, chat_id=chat_id, text=text)
        return True

    rep = pd.send_paper_digest(str(db), sender=fake_send)
    assert rep["sent"] is True and captured["token"] == "TOKEN"
    assert "Paper-Book Weekly Digest" in captured["text"]


def test_register_weekly_job():
    from apscheduler.schedulers.background import BackgroundScheduler
    sch = BackgroundScheduler()
    jid = pd.register_paper_digest_job(sch, "x.db")
    job = sch.get_job(jid)
    assert job is not None and "mon" in str(job.trigger)
