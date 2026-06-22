#!/usr/bin/env python
"""Opening-drive digest → ntfy. Pushes the gap-adjusted opening-drive reads for GAP names during the
opening window (09:25–10:00 IST) so you don't have to watch the board live.

For each name whose 5M trigger is the OPENING-DRIVE (gap > 0.5 ATR, the first ~45 min), it reports
the drive read (held / failed-gap / reclaim), the conviction direction, and whether the drive AGREES
with that direction (a confirmed opening = high-quality entry timing; a conflict = caution / failed
gap). Run by cron at ~09:35 + 10:00 IST on weekdays.

Usage: PYTHONPATH=src .venv/bin/python scripts/opening_drive_report.py
"""
from __future__ import annotations

import json
import sqlite3

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from nse_data.bot.notify import ntfy_send  # noqa: E402
from nse_data.scheduler.market_hours import IST  # noqa: E402

import datetime as dt  # noqa: E402


def main():
    conn = sqlite3.connect("data/nse.db", timeout=20)
    conn.execute("PRAGMA busy_timeout=20000")
    d = conn.execute("SELECT MAX(as_of_date) FROM conviction_daily").fetchone()[0]
    if not d:
        return
    rows = conn.execute(
        "SELECT symbol, direction, conviction_adj, conf_label, gap_pct, stages_json "
        "FROM conviction_daily WHERE as_of_date=? ORDER BY conviction_adj DESC", (d,)).fetchall()

    drives = []
    for sym, direction, conv, conf, gap, sj in rows:
        st = json.loads(sj).get("structure", {}) if sj else {}
        note = st.get("m5_note") or ""
        if "gap-" not in note:                       # only names whose 5M trigger is the opening-drive
            continue
        m5 = st.get("m5_trend")
        dsign = 1 if direction == "LONG" else -1 if direction == "SHORT" else 0
        agree = (m5 and m5 == dsign)
        icon = "▲" if m5 == 1 else "▼" if m5 == -1 else "·"
        tag = "✓agrees" if agree else "✗vs-thesis" if (m5 and m5 == -dsign) else "·neutral"
        drives.append((conv or 0, f"{icon} {sym} {direction or '-'} (conv {conv}) "
                                  f"{gap:+}% · {note} {tag}"))
    drives.sort(reverse=True)

    now = dt.datetime.now(IST).strftime("%H:%M")
    if not drives:
        body = (f"No opening-drive reads yet ({now} IST). Either no >0.5-ATR gaps today, or it's "
                f"outside the 09:25–10:00 window / pre-open.")
    else:
        body = "\n".join(x[1] for x in drives)
    text = (f"Opening-Drive @ {now} IST · {d}\n\n{body}\n\n"
            "(opening-drive = scored 5M structure trigger, 09:25–10:00; ✓agrees = drive direction "
            "matches the conviction call → confirmed opening timing.)")
    ok = ntfy_send(text, channel="signals", title=f"Opening-Drive {len(drives)} gap names")
    print(f"[{now}] {len(drives)} drives · ntfy_sent={ok}\n{text}")


if __name__ == "__main__":
    main()
