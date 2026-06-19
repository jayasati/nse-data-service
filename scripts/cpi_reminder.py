"""Monthly Telegram nudge to refresh India CPI (the Macro engine's inflation input,
which is manual — see load_macro_cpi.py for why no free auto-source works). Run by a
cron a couple of days after the monthly CPI release (~12th).

    PYTHONPATH=src .venv/bin/python scripts/cpi_reminder.py
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> int:
    from nse_data.storage.db import open_db
    from nse_data.bot.dispatcher import load_telegram_config, send_telegram

    conn = open_db("data/nse.db")
    cur = None
    try:
        cur = conn.execute("SELECT date, cpi_yoy FROM raw_macro_market WHERE cpi_yoy IS NOT NULL "
                           "ORDER BY date DESC LIMIT 1").fetchone()
    except Exception:  # noqa: BLE001
        pass
    conn.close()
    have = (f"stored: {cur[1]}% (as of {cur[0]})" if cur else "none stored yet")
    stale = ""
    if cur:
        try:
            age = (_dt.date.today() - _dt.date.fromisoformat(cur[0])).days
            if age > 45:
                stale = f"  ⚠️ {age}d old"
        except ValueError:
            pass
    msg = ("🔔 Monthly CPI update\n"
           f"India CPI YoY inflation is released ~12th. Current {have}{stale}.\n"
           "Update it:  PYTHONPATH=src .venv/bin/python scripts/load_macro_cpi.py --cpi <value>")

    token, chat_id = load_telegram_config()
    ok = send_telegram(token, chat_id, msg)
    print(msg)
    print("telegram sent:" , ok)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
