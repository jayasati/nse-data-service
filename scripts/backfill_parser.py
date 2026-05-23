"""Run the parser job in batch mode against pending raw_announcements rows.

Stratified sampling by priority so all code paths get exercised. The 500-PDF
default is meant for Phase 2 validation; bump it once you trust the pipeline.

Usage:
  # Default: 500 rows, stratified, with confirmation
  PYTHONPATH=src python scripts/backfill_parser.py

  # Custom counts:
  PYTHONPATH=src python scripts/backfill_parser.py --count 500
  PYTHONPATH=src python scripts/backfill_parser.py --count 100 --priority high
  PYTHONPATH=src python scripts/backfill_parser.py --count 50 --symbol RELIANCE

  # Dry-run (just show what would happen):
  PYTHONPATH=src python scripts/backfill_parser.py --dry-run

  # Filter by pdf_type (requires rows that have been classified):
  PYTHONPATH=src python scripts/backfill_parser.py --pdf-type scanned --count 20
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Configure logging early so module-level loggers emit to stdout
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
from nse_data.parsers.job import run_job  # noqa: E402
from nse_data.parsers.state import State  # noqa: E402
from nse_data.parsers.subject_classifier import classify_subject  # noqa: E402
from nse_data.session.manager import SessionManager  # noqa: E402

DB_PATH = Path("data/nse.db")
ARCHIVE_ROOT = Path("data/archive")

# Phase 2 default: 500 PDFs, stratified to exercise all paths
DEFAULT_STRATIFICATION = {
    "high": 200,
    "medium": 150,
    "low": 100,
    "skip": 50,
}


def select_candidates(
    db: sqlite3.Connection,
    total_count: int,
    priority_filter: str | None,
    symbol_filter: str | None,
    pdf_type_filter: str | None,
) -> list[dict]:
    """Pick rows to process. Stratification only applies for default mode."""
    db.row_factory = sqlite3.Row

    if priority_filter or symbol_filter or pdf_type_filter:
        # Filter mode — no stratification
        clauses = ["pdf_status = ?"]
        params = [State.PENDING]
        if symbol_filter:
            clauses.append("symbol = ?")
            params.append(symbol_filter)
        if pdf_type_filter:
            clauses.append("pdf_type = ?")
            params.append(pdf_type_filter)

        where = " AND ".join(clauses)
        params.append(total_count)
        rows = db.execute(
            f"SELECT * FROM raw_announcements WHERE {where} "
            f"ORDER BY broadcast_dt DESC LIMIT ?",
            params,
        ).fetchall()

        # Priority filter is post-hoc since priority isn't set yet on
        # pending rows; we classify subjects to filter
        if priority_filter:
            rows = [
                r for r in rows
                if classify_subject(r["subject"]) == priority_filter
            ][:total_count]
        return [dict(r) for r in rows]

    # Stratified sampling
    scale = total_count / sum(DEFAULT_STRATIFICATION.values())
    strat = {p: int(n * scale) for p, n in DEFAULT_STRATIFICATION.items()}

    selected: list[dict] = []
    # Pull a generous candidate pool, then classify and bucket
    candidates = db.execute(
        "SELECT * FROM raw_announcements WHERE pdf_status = ? "
        "ORDER BY broadcast_dt DESC LIMIT ?",
        (State.PENDING, total_count * 5),  # 5x oversampling for stratification
    ).fetchall()

    buckets: dict[str, list[dict]] = {p: [] for p in strat}
    for row in candidates:
        priority = classify_subject(row["subject"])
        if priority in buckets and len(buckets[priority]) < strat[priority]:
            buckets[priority].append(dict(row))

    for priority, target in strat.items():
        got = len(buckets[priority])
        if got < target:
            print(f"  warning: wanted {target} {priority} rows, only got {got}")
        selected.extend(buckets[priority])

    return selected[:total_count]


def main(args: argparse.Namespace) -> int:
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}", file=sys.stderr)
        return 1

    db = sqlite3.connect(DB_PATH)
    candidates = select_candidates(
        db, args.count, args.priority, args.symbol, args.pdf_type,
    )

    # Summarize composition
    composition: dict[str, int] = {}
    for row in candidates:
        p = classify_subject(row["subject"])
        composition[p] = composition.get(p, 0) + 1

    print(f"Selected {len(candidates)} rows to process:")
    for priority, n in sorted(composition.items()):
        print(f"  {priority:8s}: {n}")
    print()

    if args.dry_run:
        print("DRY RUN — no changes made.")
        print("\nSample fingerprints:")
        for row in candidates[:10]:
            print(f"  {row['fingerprint']}  {row['symbol']:15s}  {row['subject'][:50]}")
        db.close()
        return 0

    if not args.yes:
        confirm = input(f"Process {len(candidates)} rows? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            db.close()
            return 0

    fingerprints = [r["fingerprint"] for r in candidates]

    session = SessionManager()
    try:
        report = run_job(
            db, session, ARCHIVE_ROOT, fingerprints=fingerprints,
        )
    finally:
        session.close()
        db.close()

    print()
    print(f"Done in {report.duration_seconds:.1f}s")
    print(f"Rows processed: {report.rows_processed}")
    print(f"Outcomes:")
    for state, count in sorted(report.by_terminal_state.items()):
        print(f"  {state:30s}: {count}")
    if report.errors:
        print(f"Unhandled errors: {len(report.errors)}")
        for err in report.errors[:5]:
            print(f"  {err}")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=500,
                        help="Total rows to process (default 500)")
    parser.add_argument("--priority", choices=["high", "medium", "low", "skip"],
                        help="Filter to one priority bucket")
    parser.add_argument("--symbol", help="Filter to one symbol")
    parser.add_argument("--pdf-type",
                        choices=["native_text", "presentation", "scanned", "hybrid"],
                        help="Filter to one pdf_type (requires prior classification)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would happen without doing it")
    parser.add_argument("--yes", action="store_true",
                        help="Skip confirmation prompt")
    args = parser.parse_args()
    sys.exit(main(args))