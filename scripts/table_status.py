"""
Table fill status — verify the Phase 3/4 tables are populating.

    PYTHONPATH=src python scripts/table_status.py

Prints row count + latest timestamp per table, plus when each is *expected* to
fill — so an empty table reads as "not due yet" rather than "broken". Read-only.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from nse_data.storage.db import open_db    # noqa: E402

_IST = timezone(timedelta(hours=5, minutes=30))

# (table, timestamp column, is_epoch, when it fills)
_TABLES = [
    ("indicator_live",            "updated_at",   False, "every 1 min, market hours"),
    ("indicator_eod",             "date",         False, "nightly EOD compute"),
    ("indicator_supertrend_5m",   "ts",           True,  "every 1 min, market hours"),
    ("indicator_volume_delta_5m", "ts",           True,  "every 1 min, market hours"),
    ("market_state",              "as_of",        False, "every 5 min, market hours"),
    ("sector_state",              "as_of",        False, "every 5 min, market hours"),
    ("live_watchlist",            "expires_at",   False, "every 15 min"),
    ("indicator_levels",          "session_date", False, "nightly 19:00"),
    ("delivery_conviction",       "session_date", False, "nightly 18:30"),
    ("stock_fundamentals",        "updated_date", False, "nightly 18:00"),
    ("backtest_registry",         "run_date",     False, "on-demand (phase3_eval)"),
]


def _fmt_latest(value, is_epoch: bool) -> str:
    if value is None:
        return "—"
    if is_epoch:
        try:
            return datetime.fromtimestamp(int(value), tz=_IST).strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            return str(value)
    return str(value)[:19]


def main() -> int:
    db = sys.argv[1] if len(sys.argv) > 1 else "data/nse.db"
    conn = open_db(db)
    print(f"\n{'table':<28}{'rows':>9}  {'latest':<20}{'fills'}")
    print("-" * 86)
    try:
        for table, ts_col, is_epoch, when in _TABLES:
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                latest = conn.execute(f"SELECT MAX({ts_col}) FROM {table}").fetchone()[0]
                mark = "🔴" if n == 0 else "  "
                print(f"{mark}{table:<26}{n:>9}  {_fmt_latest(latest, is_epoch):<20}{when}")
            except sqlite3.OperationalError:
                print(f"🔴{table:<26}{'NO TABLE':>9}  {'(deploy needed?)':<20}{when}")
    finally:
        conn.close()
    print("\n🔴 = empty / missing. For nightly tables, empty before the first "
          "post-deploy night is normal; market-hours tables fill only 09:15–15:30.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
