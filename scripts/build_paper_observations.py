"""P4 capture: log every aggressive move on a tracked A/B name as a paper
observation, stamped with its PRIMARY internal catalyst bucket and (if present)
the LLM "why" from move_causes. The labeler (label_paper_observations.py) fills
net-of-cost forward returns later.

Multi-factor by design — NOT earnings-only. The catalyst bucket is the same one
backtest_move_causes.py uses (announcements split into result vs other, block
deals, rating actions, corp actions, OI spurts, 52w breaks…), so paper buckets
line up with the historical census. See plans/P4_PAPER_TRADE_PLAN.md.

OOS discipline (--batch):
  forward   the honest out-of-sample stream → the CERTIFYING set (default).
  backfill  seed from history for colour/debug ONLY; never enters the promote gate.

INSERT OR IGNORE keeps the FIRST capture of a (symbol, move_date) — a forward row
is never later re-stamped backfill.

    # daily forward capture (cron, after move-events refresh):
    PYTHONPATH=src .venv/bin/python -u scripts/build_paper_observations.py \
        --since $(date -d -7days +%F) --batch forward

    # one-time historical seed (kept separate from the gate):
    PYTHONPATH=src .venv/bin/python -u scripts/build_paper_observations.py \
        --since 2023-06-13 --until 2025-12-31 --batch backfill
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

TRACKED = ("A core", "B tradeable")   # C volatile excluded (historical edge artifact)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--since", default="2023-06-13", help="capture moves on/after this date")
    ap.add_argument("--until", default="9999-12-31", help="capture moves on/before this date")
    ap.add_argument("--min-move", type=float, default=0.0,
                    help="abs %% move-from-open floor (0 = every detected move event)")
    ap.add_argument("--batch", choices=("forward", "backfill"), default="forward",
                    help="forward = certifying OOS set (default); backfill = colour only")
    ap.add_argument("--grades", default=",".join(TRACKED),
                    help="comma list of universe grades to capture (default A core,B tradeable)")
    ap.add_argument("--window-days", type=int, default=4, help="cause lookback window")
    args = ap.parse_args()
    grades = tuple(g.strip() for g in args.grades.split(",") if g.strip())

    from nse_data.storage.db import open_db, apply_migrations
    from nse_data.research.cause_engine import primary_catalyst
    from nse_data.research.move_causes import ensure_table as ensure_causes
    from nse_data.fundamentals.sectors import sector_class_for

    conn = open_db(args.db)
    conn.execute("PRAGMA busy_timeout=60000")   # coexist with live-collector writes
    apply_migrations(conn)
    ensure_causes(conn)   # LEFT JOIN target; created lazily by the cause pipeline

    # Moves on tracked names in window. LEFT JOIN universe for the grade filter;
    # LEFT JOIN move_causes for the LLM "why" (may be absent — capture anyway).
    placeholders = ",".join("?" * len(grades))
    rows = conn.execute(
        f"""SELECT e.symbol, e.date, e.direction, e.move_pct, e.day_close,
                   t.grade,
                   c.category, c.cause_summary, c.idiosyncratic, c.source_url, c.source_date
            FROM intraday_move_events e
            JOIN tradeable_universe t ON t.symbol = e.symbol
            LEFT JOIN move_causes c ON c.symbol = e.symbol AND c.date = e.date
            WHERE ABS(e.move_pct) >= ? AND e.date >= ? AND e.date <= ?
              AND t.grade IN ({placeholders})
            ORDER BY e.date""",
        (args.min_move, args.since, args.until, *grades),
    ).fetchall()

    ins = """INSERT OR IGNORE INTO paper_observations
             (symbol, move_date, batch, grade, sector, direction, move_pct, entry_price,
              catalyst, cause_category, cause_summary, idiosyncratic, source_url, source_date)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""

    n_seen = n_new = n_skip = 0
    for (symbol, date, direction, move_pct, day_close, grade,
         category, cause_summary, idiosyncratic, source_url, source_date) in rows:
        n_seen += 1
        if not day_close:          # no entry mark → cannot label later
            n_skip += 1
            continue
        catalyst = primary_catalyst(conn, symbol, date, window_days=args.window_days)
        sector = sector_class_for(symbol).value
        cur = conn.execute(
            ins,
            (symbol, date, args.batch, grade, sector, direction, move_pct, day_close,
             catalyst, category, cause_summary, idiosyncratic, source_url, source_date),
        )
        if cur.rowcount:
            n_new += 1
        if n_seen % 200 == 0:
            conn.commit()
    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM paper_observations").fetchone()[0]
    print(f"capture batch={args.batch} grades={list(grades)} window {args.since}..{args.until}")
    print(f"  move events seen={n_seen}  new rows={n_new}  skipped(no close)={n_skip}")
    print(f"  paper_observations total={total}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
