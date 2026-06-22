"""SQLite connection management and migration runner."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def open_db(path: str = "data/nse.db") -> sqlite3.Connection:
    """
    Open a SQLite connection with sensible defaults.

    WAL mode lets concurrent readers proceed while a writer holds the lock —
    important once APScheduler is running multiple jobs against the same DB.
    busy_timeout makes us wait briefly on lock contention rather than failing.
    """
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    # 20s: under heavy concurrent writers (live ticks, option chain, intraday indicators) a
    # 5s wait was being exceeded → "database is locked". Wait longer before giving up.
    conn.execute("PRAGMA busy_timeout=20000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def apply_migrations(
    conn: sqlite3.Connection,
    migrations_dir: str | Path = "migrations",
) -> list[str]:
    """
    Apply migration files in lexical order. Idempotent — already-applied
    files are skipped. Returns the list of newly applied filenames.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename    TEXT PRIMARY KEY,
            applied_at  INTEGER NOT NULL
        )
    """)
    conn.commit()

    applied = {
        row[0] for row in conn.execute("SELECT filename FROM schema_migrations")
    }
    newly_applied: list[str] = []

    for path in sorted(Path(migrations_dir).glob("*.sql")):
        if path.name in applied:
            continue
        try:
            conn.executescript(path.read_text())
            conn.execute(
                "INSERT INTO schema_migrations (filename, applied_at) "
                "VALUES (?, strftime('%s','now'))",
                (path.name,),
            )
            conn.commit()
            newly_applied.append(path.name)
        except Exception:
            conn.rollback()
            raise

    return newly_applied