"""One-time backfill of raw_announcements.broadcast_epoch (migration 076) by
parsing the existing broadcast_dt strings. Idempotent — only fills NULL rows, so
it's safe to re-run. After this, the 6 'ORDER BY broadcast_epoch' query sites
order announcements chronologically instead of lexically.

    PYTHONPATH=src .venv/bin/python -u scripts/backfill_broadcast_epoch.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> int:
    from nse_data.storage.db import open_db
    from nse_data.collectors.announcements import broadcast_epoch

    conn = open_db("data/nse.db")
    conn.execute("PRAGMA busy_timeout=60000")

    todo = conn.execute(
        "SELECT COUNT(*) FROM raw_announcements WHERE broadcast_epoch IS NULL"
    ).fetchone()[0]
    print(f"rows needing broadcast_epoch: {todo:,}")

    rows = conn.execute(
        "SELECT fingerprint, broadcast_dt FROM raw_announcements WHERE broadcast_epoch IS NULL"
    ).fetchall()

    filled = unparseable = 0
    batch = []
    for fp, bdt in rows:
        ep = broadcast_epoch(bdt)
        if ep is None:
            unparseable += 1
            continue
        batch.append((ep, fp))
        if len(batch) >= 5000:
            conn.executemany(
                "UPDATE raw_announcements SET broadcast_epoch=? WHERE fingerprint=?", batch)
            conn.commit()
            filled += len(batch)
            batch = []
            print(f"  …{filled:,} filled", flush=True)
    if batch:
        conn.executemany(
            "UPDATE raw_announcements SET broadcast_epoch=? WHERE fingerprint=?", batch)
        conn.commit()
        filled += len(batch)

    still_null = conn.execute(
        "SELECT COUNT(*) FROM raw_announcements WHERE broadcast_epoch IS NULL"
    ).fetchone()[0]
    print(f"done: filled={filled:,}  unparseable(left NULL)={unparseable:,}  still_null={still_null:,}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
