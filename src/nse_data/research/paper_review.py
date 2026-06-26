"""Scheduled paper-track review — judge each strategy's edge only when the sample is real.

Reviewing a 5-trade, one-week paper record is reading noise. This computes per-strategy realized
stats from paper_book (excluding same-day 'dropped' non-trades) and ALERTS (ntfy) only when a
strategy has accumulated >= MATURE_MIN closed real trades — so the review fires when the data can
actually support a verdict, not before. Snapshots are recorded in paper_review for trend.

Weekly Sunday 09:00 IST. Strategies still accruing are logged silently (no push spam).
"""
from __future__ import annotations

import sqlite3
import statistics as st

import structlog

log = structlog.get_logger(__name__)

MATURE_MIN = 20          # closed real trades before a strategy is judgeable


def _strategy_stats(conn, strategy: str) -> dict:
    # real closed trades: exclude same-day 'dropped' churn (entry==exit, no hold)
    rets = [r[0] for r in conn.execute(
        "SELECT net_pct FROM paper_book WHERE strategy=? AND status='closed' AND net_pct IS NOT NULL "
        "AND NOT (exit_reason='dropped' AND julianday(exit_date)-julianday(entry_date) < 1)",
        (strategy,))]
    n_open = conn.execute("SELECT COUNT(*) FROM paper_book WHERE strategy=? AND status='open'",
                          (strategy,)).fetchone()[0]
    if not rets:
        return {"strategy": strategy, "n_closed": 0, "n_open": n_open, "mature": 0}
    wins = [r for r in rets if r > 0]
    gl = -sum(r for r in rets if r < 0)
    return {
        "strategy": strategy, "n_closed": len(rets), "n_open": n_open,
        "win_pct": round(100 * len(wins) / len(rets), 0),
        "avg_net_pct": round(st.mean(rets), 2),
        "median_net_pct": round(st.median(rets), 2),
        "profit_factor": round(sum(wins) / gl, 2) if gl else None,
        "total_pnl": None, "mature": 1 if len(rets) >= MATURE_MIN else 0,
    }


def compute_review(conn) -> list[dict]:
    strategies = [r[0] for r in conn.execute(
        "SELECT DISTINCT strategy FROM paper_book WHERE strategy IS NOT NULL")]
    return sorted((_strategy_stats(conn, s) for s in strategies),
                  key=lambda d: (-d.get("mature", 0), -d["n_closed"]))


def _format(review: list[dict]) -> str:
    mature = [r for r in review if r.get("mature")]
    lines = ["📊 Paper-track review"]
    if mature:
        lines.append("\nMATURE (≥%d closed — judgeable):" % MATURE_MIN)
        for r in mature:
            verdict = "✅ +EV" if (r["avg_net_pct"] or 0) > 0 else "❌ -EV"
            lines.append(f"• {r['strategy']}: n={r['n_closed']} win={r['win_pct']:.0f}% "
                         f"avg={r['avg_net_pct']:+.2f}% PF={r['profit_factor']} {verdict}")
    accruing = [r for r in review if not r.get("mature")]
    if accruing:
        lines.append("\naccruing (n<%d, not yet judgeable): " % MATURE_MIN
                     + ", ".join(f"{r['strategy']}({r['n_closed']})" for r in accruing))
    return "\n".join(lines)


def run_review(conn: sqlite3.Connection, *, push: bool = True) -> dict:
    from ..scheduler import market_hours
    today = market_hours.now_ist().date().isoformat()
    review = compute_review(conn)
    for r in review:
        conn.execute(
            "INSERT OR REPLACE INTO paper_review (review_date, strategy, n_closed, n_open, win_pct, "
            "avg_net_pct, median_net_pct, profit_factor, total_pnl, mature, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))",
            (today, r["strategy"], r["n_closed"], r["n_open"], r.get("win_pct"),
             r.get("avg_net_pct"), r.get("median_net_pct"), r.get("profit_factor"),
             r.get("total_pnl"), r.get("mature", 0)))
    conn.commit()
    n_mature = sum(r.get("mature", 0) for r in review)
    pushed = False
    if push and n_mature > 0:                 # only alert when there's something judgeable
        try:
            from ..bot.notify import ntfy_send
            ntfy_send(_format(review), channel="digest", title=f"Paper review — {today}")
            pushed = True
        except Exception:  # noqa: BLE001
            log.exception("paper_review_push_failed")
    report = {"date": today, "strategies": len(review), "mature": n_mature, "pushed": pushed}
    log.info("paper_review", **report)
    return report


def register_paper_review_job(scheduler, db_path: str) -> str:
    """Weekly Sunday 09:00 IST. Pushes only when >=1 strategy is mature (>=20 closed)."""
    from apscheduler.triggers.cron import CronTrigger

    from ..events.calendar import _feature_enabled
    from ..scheduler import market_hours
    from ..storage.db import open_db
    job_id = "paper_review"

    def _tick():
        if not _feature_enabled("paper_review", True):
            return
        conn = open_db(db_path)
        try:
            run_review(conn)
        except Exception:
            log.exception("paper_review_failed")
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=CronTrigger(day_of_week="sun", hour=9, minute=0, timezone=market_hours.IST),
        id=job_id, max_instances=1, coalesce=True, replace_existing=True)
    return job_id
