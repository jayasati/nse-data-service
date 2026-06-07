"""
Pre-open readiness check — run before the market opens to confirm the system
will actually generate and send signals today.

    PYTHONPATH=src python scripts/preopen_check.py

Prints a ✅ / ⚠ / 🔴 line per check and an overall verdict. Read-only: it does
NOT send a Telegram message or write anything. (To prove the Telegram path
end-to-end, run scripts/send_test_alert.py separately.)

Critical checks (must be green to expect alerts):
  - today is a trading day
  - nse-collector / nse-bot services active
  - Telegram token + chat configured
  - indicator_live seeded (the table the signal engine + confidence scorer read)
  - live universe resolves to a non-empty symbol set
Informational checks (context, not blockers):
  - market-data feed freshness, market_state / sector_state, signals so far today
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nse_data.bot.dispatcher import load_telegram_config        # noqa: E402
from nse_data.ops import health                                 # noqa: E402
from nse_data.indicators.universe import live_universe          # noqa: E402
from nse_data.scheduler import market_hours                     # noqa: E402
from nse_data.settings import load_endpoints                    # noqa: E402
from nse_data.storage.db import open_db                         # noqa: E402

OK, WARN, BAD = "✅", "⚠️ ", "🔴"
_DB = "data/nse.db"
_SERVICES = ("nse-collector@ubuntu", "nse-bot@ubuntu", "nse-dashboard@ubuntu")

_critical_ok = True


def line(mark: str, label: str, detail: str = "") -> None:
    print(f"  {mark} {label}" + (f" — {detail}" if detail else ""))


def crit(ok: bool, label: str, detail: str = "") -> None:
    global _critical_ok
    if not ok:
        _critical_ok = False
    line(OK if ok else BAD, label, detail)


# ---------------------------------------------------------------- checks

def check_trading_day(now) -> None:
    is_td = market_hours.is_trading_day(now.date())
    crit(is_td, "Trading day",
         now.date().isoformat() + ("" if is_td else " — NOT a trading day"))


def check_services() -> None:
    if shutil.which("systemctl") is None:
        line(WARN, "Services", "systemctl not found (not on the server?) — skipped")
        return
    for unit in _SERVICES:
        try:
            out = subprocess.run(["systemctl", "is-active", unit],
                                 capture_output=True, text=True, timeout=5)
            state = out.stdout.strip() or out.stderr.strip()
        except Exception as e:
            state = f"error: {e}"
        ok = state == "active"
        if unit.startswith(("nse-collector", "nse-bot")):   # bot+collector are critical
            crit(ok, unit, state)
        else:                                               # dashboard is informational
            line(OK if ok else WARN, unit, state)


def check_telegram() -> None:
    token, chat = load_telegram_config()
    crit(bool(token and chat), "Telegram configured",
         "TELEGRAM_TOKEN + CHAT_ID set" if token and chat
         else "missing TELEGRAM_TOKEN / TELEGRAM_CHAT_ID in .env")


def check_indicator_live(conn) -> None:
    try:
        n = conn.execute("SELECT COUNT(*) FROM indicator_live").fetchone()[0]
        newest = conn.execute("SELECT MAX(updated_at) FROM indicator_live").fetchone()[0]
    except Exception as e:
        crit(False, "indicator_live", f"read error: {e}")
        return
    crit(n > 0, "indicator_live seeded",
         f"{n} symbols, newest {newest}" if n else "EMPTY — signal engine has no context")


def check_universe(conn) -> None:
    try:
        u = live_universe(conn)
    except Exception as e:
        crit(False, "live universe", f"error: {e}")
        return
    crit(len(u) > 0, "live universe", f"{len(u)} symbols")


def check_feeds(conn) -> None:
    try:
        endpoints = load_endpoints("config/endpoints.yaml")
        report = health.build_report(conn, endpoints)
    except Exception as e:
        line(WARN, "feed freshness", f"could not build report: {e}")
        return
    summary = report.get("summary", {})
    stale = summary.get("stale", 0) + summary.get("down", 0)
    line(OK if stale == 0 else WARN, "feed freshness",
         f"{summary.get('ok', 0)} ok / {stale} stale-or-down "
         "(pre-open staleness is normal)")


def check_context(conn) -> None:
    for tbl in ("market_state", "sector_state"):
        try:
            row = conn.execute(f"SELECT MAX(as_of) FROM {tbl}").fetchone()
            line(OK if row and row[0] else WARN, tbl,
                 f"latest {row[0]}" if row and row[0] else "no rows yet")
        except Exception:
            line(WARN, tbl, "table missing")


def check_signals_today(conn) -> None:
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE substr(detected_at,1,10)=?",
            (market_hours.now_ist().date().isoformat(),),
        ).fetchone()[0]
        sent = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE dispatched=1 AND substr(detected_at,1,10)=?",
            (market_hours.now_ist().date().isoformat(),),
        ).fetchone()[0]
        line(OK, "signals today", f"{n} fired, {sent} dispatched")
    except Exception:
        line(WARN, "signals today", "signals table not ready")


def main() -> int:
    now = market_hours.now_ist()
    print(f"\n=== Pre-open readiness — {now.isoformat(timespec='seconds')} IST ===\n")
    print(" CRITICAL")
    check_trading_day(now)
    check_services()
    check_telegram()
    conn = open_db(_DB)
    try:
        check_indicator_live(conn)
        check_universe(conn)
        print("\n INFORMATIONAL")
        check_feeds(conn)
        check_context(conn)
        check_signals_today(conn)
    finally:
        conn.close()

    print()
    if _critical_ok:
        print(f"{OK} READY — critical checks pass; expect alerts when a setup clears the gate.")
        return 0
    print(f"{BAD} NOT READY — fix the 🔴 critical items above before expecting alerts.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
