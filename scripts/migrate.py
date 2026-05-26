"""
Apply pending SQLite migrations without restarting the scheduler.

    python scripts/migrate.py            # apply pending migrations
    python scripts/migrate.py --status   # list applied / pending, apply nothing
    python scripts/migrate.py --db data/nse.db --dir migrations

The scheduler applies migrations once at boot (main.py). During active
development we add migrations faster than we restart the long-running
scheduler, so this script lets us apply pending files against the live DB.

Safe to run while the scheduler is up: SQLite is in WAL mode, so this opens
its own connection and takes the write lock only briefly per migration.
apply_migrations is idempotent — already-applied files are skipped.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from nse_data.storage.db import apply_migrations, open_db


def _pending(db, migrations_dir: str) -> tuple[list[str], list[str]]:
    """Return (applied, pending) filenames without mutating anything."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(filename TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)"
    )
    applied = {row[0] for row in db.execute("SELECT filename FROM schema_migrations")}
    on_disk = sorted(p.name for p in Path(migrations_dir).glob("*.sql"))
    pending = [name for name in on_disk if name not in applied]
    return sorted(applied), pending


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/nse.db", help="SQLite path")
    parser.add_argument("--dir", default="migrations", help="migrations directory")
    parser.add_argument(
        "--status",
        action="store_true",
        help="show applied/pending migrations and exit without applying",
    )
    args = parser.parse_args()

    db = open_db(args.db)
    try:
        if args.status:
            applied, pending = _pending(db, args.dir)
            print(f"applied ({len(applied)}):")
            for name in applied:
                print(f"  ✓ {name}")
            print(f"pending ({len(pending)}):")
            for name in pending:
                print(f"  · {name}")
            return 0

        newly = apply_migrations(db, args.dir)
        if newly:
            print(f"applied {len(newly)} migration(s):")
            for name in newly:
                print(f"  ✓ {name}")
        else:
            print("no pending migrations; schema is up to date")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
