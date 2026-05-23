"""Run the parser pipeline against a single fingerprint. Debug aid.

Usage:
  # Show what would happen
  PYTHONPATH=src python scripts/parse_one.py <fingerprint>

  # Actually do it (writes to DB)
  PYTHONPATH=src python scripts/parse_one.py <fingerprint> --commit
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nse_data.parsers.job import run_job  # noqa: E402
from nse_data.session.manager import SessionManager  # noqa: E402

DB_PATH = Path("data/nse.db")
ARCHIVE_ROOT = Path("data/archive")


def main(args: argparse.Namespace) -> int:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    row = db.execute(
        "SELECT * FROM raw_announcements WHERE fingerprint = ?",
        (args.fingerprint,),
    ).fetchone()

    if row is None:
        print(f"No row with fingerprint {args.fingerprint!r}", file=sys.stderr)
        return 1

    print("Row info:")
    for k in ("fingerprint", "symbol", "subject", "broadcast_dt",
              "attachment_url", "priority", "pdf_status", "pdf_path"):
        print(f"  {k}: {row[k]}")
    print()

    if not args.commit:
        print("Dry run. Pass --commit to actually run the pipeline.")
        return 0

    session = SessionManager()
    try:
        report = run_job(
            db, session, ARCHIVE_ROOT,
            fingerprints=[args.fingerprint],
        )
    finally:
        session.close()

    print(f"\nDone in {report.duration_seconds:.1f}s")
    print(f"Outcomes: {dict(report.by_terminal_state)}")

    # Show the updated row
    row = db.execute(
        "SELECT * FROM raw_announcements WHERE fingerprint = ?",
        (args.fingerprint,),
    ).fetchone()
    print("\nUpdated row:")
    for k in ("pdf_status", "priority", "pdf_type", "pdf_path",
              "pdf_text_length", "pdf_error", "retention_policy"):
        print(f"  {k}: {row[k]}")

    db.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fingerprint")
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    sys.exit(main(args))