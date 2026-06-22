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

    # GAMMA-REGIME calls on the top names — amplifying (neg γ, targets ×1.25, pin doesn't bind) vs
    # pinning (pos γ, ×0.85, max-pain magnet binds). The flip is the intraday vol-regime switch; a
    # name within ~1.5% of its flip is at risk of FLIPPING regime (watch it).
    gam, n_amp, n_pin = [], 0, 0
    for sym, direction, conv, conf, gap, sj in rows[:8]:
        o = json.loads(sj).get("options", {}) if sj else {}
        reg, flip, fd = o.get("gamma_regime"), o.get("gex_flip_level"), o.get("flip_dist_pct")
        if not reg:
            continue
        n_amp += reg == "amplifying"; n_pin += reg == "pinning"
        mult = "×1.25" if reg == "amplifying" else "×0.85"
        ic = "🔺" if reg == "amplifying" else "🔵"
        near = " ⚡NEAR-FLIP (regime may switch)" if (fd is not None and abs(fd) < 1.5) else ""
        gam.append(f"{ic} {sym} {reg} {mult} · flip {flip} ({fd:+}% away){near}")

    now = dt.datetime.now(IST).strftime("%H:%M")
    drive_body = ("\n".join(x[1] for x in drives) if drives else
                  f"No >0.5-ATR gap drives ({now} IST) — flat opens or outside 09:25–10:00.")
    gam_body = ("\n".join(gam) if gam else "no gamma data") + \
        (f"\n→ book regime: {n_amp} amplifying / {n_pin} pinning" if (n_amp or n_pin) else "")
    text = (f"Conviction AM @ {now} IST · {d}\n\n"
            f"— OPENING-DRIVE (gap names) —\n{drive_body}\n\n"
            f"— GAMMA REGIME (top names) —\n{gam_body}\n\n"
            "(drive ✓agrees = opening confirms the call; γ amplifying→targets extended, pinning→"
            "tightened+pin binds; ⚡near-flip = price may cross the vol-regime switch.)")
    ok = ntfy_send(text, channel="signals", title=f"Conviction AM · {len(drives)} drives · {n_amp}amp/{n_pin}pin")
    print(f"[{now}] {len(drives)} drives · {n_amp}amp/{n_pin}pin · ntfy_sent={ok}\n{text}")


if __name__ == "__main__":
    main()
