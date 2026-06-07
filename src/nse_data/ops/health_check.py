"""
Ops health check + Telegram alerting (FEATURE_CHECKLIST Week 6, task 6.3).

Run every 15 minutes during market hours (system cron — see the crontab line in
`docs/DEPLOY.md` / below). For each intraday collector it asks the same question
the dashboard does (`ops/health.build_report`): is the newest row as fresh as
the schedule allows? If a 5-minute (or other intraday) collector hasn't produced
data within its alert window, it sends a 🔴 Telegram alert to the ops chat.

Why a separate cron process and not a job inside the collector scheduler: a
monitor that lives inside the thing it monitors dies with it. Running this from
system cron means it still fires — and still alerts — if the whole
`nse-collector` process is down, which is exactly the failure we most want to
hear about.

Alert routing (task 6.3 — "separate ops chat, or same chat with a 🔴 prefix"):
    TELEGRAM_OPS_CHAT_ID   if set, alerts go here
    TELEGRAM_CHAT_ID       fallback (same chat as signals), always 🔴-prefixed
Token reuses TELEGRAM_TOKEN (bot/dispatcher.load_telegram_config).

De-duplication: re-running every 15 minutes would re-send an identical alert for
an ongoing outage. We persist the last-alerted set of failing collectors to a
small state file and only message when the set *changes* — a new failure appears
(🔴) or every feed recovers (✅). Same set as last time → stay quiet.

CLI:
    python -m nse_data.ops.health_check            # gated on market hours
    python -m nse_data.ops.health_check --force    # ignore the market-hours gate
    python -m nse_data.ops.health_check --dry-run  # print, don't send or persist
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import structlog

from ..bot.dispatcher import send_telegram
from ..scheduler import market_hours
from ..settings import load_endpoints
from ..storage.db import open_db
from . import health

log = structlog.get_logger()

# Cadence tokens we treat as "should be ticking during the session". Daily and
# weekly feeds are out of scope here — their staleness is a different alarm.
_INTRADAY = health._INTRADAY

# Where the last-alerted state lives, relative to the project root (same
# convention as data/nse.db).
DEFAULT_STATE_PATH = Path("data/ops/health_state.json")

DEFAULT_DB_PATH = "data/nse.db"
DEFAULT_ENDPOINTS = "config/endpoints.yaml"


# ============================================================================
# Failing-collector detection
# ============================================================================

def alert_threshold_seconds(cadence: str) -> int:
    """How stale an intraday feed may get before we alert.

    The checklist target is "a 5-minute collector that hasn't run in 15 min".
    We generalise: allow ~3 missed ticks but never alert before 15 minutes, so a
    fast feed still gets the 15-minute floor and a slower intraday feed (10m/30m)
    isn't flagged the instant it's one tick late.
    """
    base = health.CADENCE_SECONDS.get(cadence, 300)
    return max(900, 3 * base)


def find_failing(report: dict[str, Any], endpoints: dict) -> list[dict[str, Any]]:
    """Market-data heartbeat collectors that are unhealthy right now, worst-first.

    Scope is deliberately narrow: only `market_hours_only` intraday feeds — the
    market-data heartbeat (indices, gainers, oi_spurts, option_chain,
    live_equity, price_band, india_vix, 52w high/low). Those genuinely produce a
    row every interval the session is open, so staleness == the collector is
    broken.

    Event-driven feeds (announcements, corporate actions, board meetings,
    insider trading, large deals, financial results — `market_hours_only:
    False`) are EXCLUDED: they legitimately go quiet for hours when there's
    nothing to report, so freshness is not a health signal and alerting on them
    is a false alarm.

    A collector is failing if it's enabled, a market-hours heartbeat feed, and
    either has no data this session (`empty`/`no_table`) or its newest row lags
    further than `alert_threshold_seconds`.
    """
    failing = []
    for c in report.get("collectors", []):
        cfg = endpoints.get(c["name"], {})
        if not c.get("enabled") or c.get("cadence") not in _INTRADAY:
            continue
        if not cfg.get("market_hours_only"):
            continue  # event-driven / non-heartbeat feed — freshness != health

        status = c.get("status")
        lag = c.get("lag_seconds")

        if status in ("empty", "no_table"):
            reason = "no data this session" if status == "empty" else "table missing"
        elif lag is not None and lag > alert_threshold_seconds(c.get("cadence", "")):
            reason = f"{lag // 60}m stale"
        else:
            continue

        failing.append({
            "name": c["name"],
            "cadence": c.get("cadence"),
            "status": status,
            "lag_seconds": lag,
            "reason": reason,
        })

    failing.sort(key=lambda c: (-(c["lag_seconds"] or 1 << 30), c["name"]))
    return failing


# ============================================================================
# Message formatting
# ============================================================================

def format_alert(failing: list[dict[str, Any]], now: datetime) -> str:
    """🔴 alert body listing each failing collector and why."""
    lines = [f"🔴 NSE collector health — {len(failing)} feed(s) failing",
             f"({now.strftime('%Y-%m-%d %H:%M')} IST)", ""]
    for c in failing:
        lines.append(f"• {c['name']} [{c['cadence']}] — {c['reason']}")
    return "\n".join(lines)


def format_recovery(now: datetime) -> str:
    """✅ all-clear once previously-failing feeds are healthy again."""
    return (f"✅ NSE collectors recovered — all intraday feeds healthy "
            f"({now.strftime('%Y-%m-%d %H:%M')} IST)")


# ============================================================================
# Alert-state persistence (de-dup across runs)
# ============================================================================

def _load_state(path: Path) -> set[str]:
    try:
        data = json.loads(path.read_text())
        return set(data.get("failing", []))
    except (FileNotFoundError, ValueError):
        return set()


def _save_state(path: Path, names: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"failing": sorted(names)}))


# ============================================================================
# Config
# ============================================================================

def load_ops_telegram_config() -> tuple[str | None, str | None, bool]:
    """(token, chat_id, is_dedicated_ops_chat).

    Prefers a dedicated TELEGRAM_OPS_CHAT_ID; falls back to the signals chat.
    The third element tells the caller whether to prefix (we always prefix with
    an emoji anyway, so it's informational/logging only).
    """
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    token = os.environ.get("TELEGRAM_TOKEN")
    ops_chat = os.environ.get("TELEGRAM_OPS_CHAT_ID")
    if ops_chat:
        return token, ops_chat, True
    return token, os.environ.get("TELEGRAM_CHAT_ID"), False


# ============================================================================
# Orchestration
# ============================================================================

def run_check(
    conn: sqlite3.Connection,
    endpoints: dict,
    *,
    token: str | None,
    chat_id: str | None,
    now: datetime | None = None,
    state_path: Path = DEFAULT_STATE_PATH,
    sender: Callable[[str | None, str | None, str], bool] = send_telegram,
    persist: bool = True,
) -> dict[str, Any]:
    """One health sweep. Sends an alert/recovery only when the failing set
    changes. Returns a report dict (also useful for tests/dry-run)."""
    now = now or market_hours.now_ist()
    report = health.build_report(conn, endpoints, now=now)
    failing = find_failing(report, endpoints)
    failing_names = {c["name"] for c in failing}
    previous = _load_state(state_path)

    action = "none"
    if failing_names and failing_names != previous:
        if sender(token, chat_id, format_alert(failing, now)):
            action = "alerted"
            if persist:
                _save_state(state_path, failing_names)
    elif not failing_names and previous:
        if sender(token, chat_id, format_recovery(now)):
            action = "recovered"
            if persist:
                _save_state(state_path, set())
    elif failing_names and failing_names == previous:
        action = "still_failing"  # ongoing outage, already alerted — stay quiet

    return {
        "action": action,
        "failing": failing,
        "failing_count": len(failing),
        "previously_failing": sorted(previous),
    }


# ============================================================================
# CLI
# ============================================================================

def main(argv: list[str] | None = None) -> int:
    import logging
    import sys

    parser = argparse.ArgumentParser(description="Ops collector health check (task 6.3)")
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    parser.add_argument("--endpoints", default=DEFAULT_ENDPOINTS)
    parser.add_argument("--state", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--force", action="store_true",
                        help="run even outside market hours")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the verdict; don't send Telegram or persist state")
    args = parser.parse_args(argv)

    logging.basicConfig(stream=sys.stdout, level=logging.INFO)
    structlog.configure(processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ])

    now = market_hours.now_ist()
    if not args.force and not market_hours.is_market_open(now):
        log.info("health_check_skipped_market_closed", now=now.isoformat())
        return 0

    token, chat_id, dedicated = load_ops_telegram_config()
    if not args.dry_run and (not token or not chat_id):
        log.warning("ops_telegram_not_configured",
                    hint="set TELEGRAM_TOKEN and TELEGRAM_OPS_CHAT_ID (or TELEGRAM_CHAT_ID)")

    endpoints = load_endpoints(args.endpoints)
    conn = open_db(args.db)
    try:
        result = run_check(
            conn, endpoints,
            token=token, chat_id=chat_id, now=now,
            state_path=Path(args.state),
            sender=(lambda *_: True) if args.dry_run else send_telegram,
            persist=not args.dry_run,
        )
    finally:
        conn.close()

    log.info("health_check", action=result["action"],
             failing=result["failing_count"], ops_chat_dedicated=dedicated)
    if args.dry_run and result["failing"]:
        print(format_alert(result["failing"], now))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
