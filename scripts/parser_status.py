"""Operational status dashboard for the parser pipeline.

Prints status distributions, disk usage, and recent error counts. Run
periodically during backfill, and after the scheduler has been running
for a while.

Usage:
  PYTHONPATH=src python scripts/parser_status.py
"""

from __future__ import annotations

import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

DB_PATH = Path("data/nse.db")
ARCHIVE_ROOT = Path("data/archive")


def disk_usage(path: Path) -> tuple[int, int]:
    """Return (file_count, total_size_bytes) for files under path."""
    if not path.exists():
        return (0, 0)
    count = 0
    size = 0
    for f in path.rglob("*"):
        if f.is_file():
            count += 1
            try:
                size += f.stat().st_size
            except OSError:
                pass
    return count, size


def fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def main() -> int:
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}", file=sys.stderr)
        return 1

    db = sqlite3.connect(DB_PATH)

    print("PDF Pipeline Status")
    print("=" * 60)

    # ---- Status distribution ----
    print("\nStatus distribution:")
    rows = db.execute("""
        SELECT pdf_status, priority, COUNT(*)
          FROM raw_announcements
         GROUP BY pdf_status, priority
         ORDER BY pdf_status, priority
    """).fetchall()
    by_status: dict[str, dict] = {}
    for status, priority, count in rows:
        by_status.setdefault(status or "(null)", {})[priority or "(null)"] = count

    print(f"  {'status':<28} {'high':>7} {'medium':>7} {'low':>7} {'skip':>7} {'total':>8}")
    for status, by_p in sorted(by_status.items()):
        h = by_p.get("high", 0)
        m = by_p.get("medium", 0)
        l = by_p.get("low", 0)
        s = by_p.get("skip", 0)
        total = sum(by_p.values())
        print(f"  {status:<28} {h:>7} {m:>7} {l:>7} {s:>7} {total:>8}")

    # ---- PDF type distribution (post-classification) ----
    print("\npdf_type distribution (classified rows):")
    type_rows = db.execute("""
        SELECT pdf_type, COUNT(*)
          FROM raw_announcements
         WHERE pdf_type IS NOT NULL
         GROUP BY pdf_type
         ORDER BY 2 DESC
    """).fetchall()
    total_classified = sum(c for _, c in type_rows)
    for pdf_type, count in type_rows:
        pct = 100 * count / total_classified if total_classified else 0
        print(f"  {pdf_type:<20} {count:>6}  ({pct:.1f}%)")

    # ---- Disk usage ----
    print("\nDisk usage:")
    for subdir in ("pdfs", "pdfs_temp"):
        path = ARCHIVE_ROOT / subdir
        n, sz = disk_usage(path)
        print(f"  {str(path):<40} {n:>6} files  {fmt_bytes(sz):>10}")

    # ---- Text length distribution by pdf_type ----
    print("\nText length stats by pdf_type:")
    print(f"  {'pdf_type':<20} {'count':>7} {'avg':>10} {'min':>10} {'max':>10}")
    text_rows = db.execute("""
        SELECT pdf_type,
               COUNT(*),
               AVG(pdf_text_length),
               MIN(pdf_text_length),
               MAX(pdf_text_length)
          FROM raw_announcements
         WHERE pdf_text_length IS NOT NULL
         GROUP BY pdf_type
    """).fetchall()
    for pdf_type, count, avg, mn, mx in text_rows:
        print(f"  {pdf_type or '(null)':<20} {count:>7} "
              f"{int(avg or 0):>10} {mn or 0:>10} {mx or 0:>10}")

    # ---- Recent errors ----
    print("\nTop error reasons (last 24h):")
    cutoff = int(time.time()) - 86400
    err_rows = db.execute("""
        SELECT pdf_error, COUNT(*)
          FROM raw_announcements
         WHERE pdf_error IS NOT NULL
           AND pdf_status_updated_at > ?
         GROUP BY pdf_error
         ORDER BY 2 DESC
         LIMIT 10
    """, (cutoff,)).fetchall()
    for err, count in err_rows:
        print(f"  {count:>5}  {err[:70]}")
    if not err_rows:
        print("  (none)")

    # ---- Unknown subjects (priority defaulted to medium) ----
    print("\nUnknown subjects that defaulted to medium (top 10):")
    from nse_data.parsers.subject_classifier import _load_priority_map
    known = set(_load_priority_map().keys())
    unknown_rows = db.execute("""
        SELECT subject, COUNT(*)
          FROM raw_announcements
         WHERE priority = 'medium'
           AND pdf_status != ?
         GROUP BY subject
         ORDER BY 2 DESC
         LIMIT 20
    """, (None,)).fetchall()
    shown = 0
    for subject, count in unknown_rows:
        if subject and subject not in known:
            print(f"  {count:>5}  {subject[:70]}")
            shown += 1
            if shown >= 10:
                break
    if shown == 0:
        print("  (all medium subjects are in config/priority.yaml — good)")

    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())