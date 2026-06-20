"""Weekly paper-book digest to Telegram (P4 monitoring).

A pushed summary of the forward track record so you don't have to SSH in and run the
monitor: per strategy, open/closed counts, progress to the ~100-trade significance
threshold, the closed-trade expectancy + R9 verdict (once trades close), and a few
notable movers. Telegram-friendly plain text (the ASCII dashboard in `paper_monitor`
won't column-align in Telegram's proportional font).

Registered from main.py via `register_paper_digest_job` (CronTrigger Mon 08:30 IST).
Mirrors `bot/morning_brief` for the Telegram send + scheduling.
"""
from __future__ import annotations

import os
import sqlite3

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from ..research.paper_monitor import monitor_snapshot
from ..scheduler.market_hours import IST
from ..storage.db import open_db
from .dispatcher import load_telegram_config, send_telegram

log = structlog.get_logger()
JOB_ID = "bot_paper_digest"


def _movers(opens: list[dict], k: int = 2) -> str:
    """Top-k gainers + worst loser among open positions, by unrealized %."""
    have = [o for o in opens if o.get("unrealized_pct") is not None]
    if not have:
        return ""
    have.sort(key=lambda o: o["unrealized_pct"], reverse=True)
    picks = have[:k] + ([have[-1]] if len(have) > k else [])
    seen, out = set(), []
    for o in picks:
        if o["symbol"] in seen:
            continue
        seen.add(o["symbol"])
        out.append(f"{o['symbol']} {o['unrealized_pct']:+.1f}%")
    return ", ".join(out)


def build_paper_digest(conn: sqlite3.Connection) -> str:
    snap = monitor_snapshot(conn)
    if not snap["strategies"]:
        return ("📋 Paper-Book Weekly Digest\n"
                "No positions yet — the 19:15 loop will open the book on the next session.")
    t = snap["totals"]
    lines = [f"📋 Paper-Book Weekly Digest — {snap['as_of']}",
             "━━━━━━━━━━━━━━━━━━━",
             f"Book: {t['open']} open · {t['closed']} closed"]
    for name, s in snap["strategies"].items():
        prog = s["progress"]
        lines.append(f"\n▶ {name} — {s['n_open']} open · {prog['closed']} closed "
                     f"({prog['pct']}% to {prog['target']})")
        c = s["closed"]
        if c.get("n"):
            val = c.get("validation", {})
            verdict = (val.get("verdict") or "?").upper()
            dsr = f"DSR {val['dsr']:.2f}" if val.get("dsr") is not None else "DSR n/a"
            rr = f" · {c['avg_r']:+.2f}R" if c.get("avg_r") is not None else ""
            pf = "∞" if c["profit_factor"] is None else f"{c['profit_factor']:.2f}"
            lines.append(f"   Exp {c['expectancy']:+.2f}%{rr} · PF {pf} · "
                         f"win {(c['win_rate'] or 0) * 100:.0f}% → {verdict} ({dsr})")
        else:
            lines.append("   no closed trades yet — expectancy pending")
        mv = _movers(s["open"])
        if mv:
            lines.append(f"   movers: {mv}")
    lines.append("\n━━━━━━━━━━━━━━━━━━━")
    lines.append("Full holdings: scripts/paper_monitor.py")
    return "\n".join(lines)


def send_paper_digest(db_path: str, *, sender=send_telegram) -> dict:
    token, chat_id = load_telegram_config()
    if not token or not chat_id:
        return {"skipped": "no_telegram_config"}
    conn = open_db(db_path)
    try:
        text = build_paper_digest(conn)
    finally:
        conn.close()
    thread = os.environ.get("TELEGRAM_TOPIC_SWING")        # reuse the swing topic if set
    sent = sender(token, chat_id, text, int(thread) if (thread and thread.isdigit()) else None)
    return {"sent": sent, "chars": len(text)}


def register_paper_digest_job(scheduler: BlockingScheduler, db_path: str) -> str:
    """Attach the weekly (Mon 08:30 IST) paper-book digest to Telegram."""
    def _tick():
        try:
            log.info("paper_digest", **send_paper_digest(db_path))
        except Exception:
            log.exception("paper_digest_failed")

    scheduler.add_job(
        _tick,
        trigger=CronTrigger(day_of_week="mon", hour=8, minute=30, timezone=IST),
        id=JOB_ID,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return JOB_ID
