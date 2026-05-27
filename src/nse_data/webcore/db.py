"""Read-only SQLite access for the dashboard/API.

The collector process writes under WAL; everything here opens the DB read-only
(mode=ro URI) so it can never create, lock, or disturb the live writer.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import DB_PATH


class DatabaseUnavailable(RuntimeError):
    """Raised when the database file doesn't exist yet."""


def open_ro(path: str = DB_PATH) -> sqlite3.Connection:
    """Open a read-only connection with Row access. Raises DatabaseUnavailable
    if the file is missing (routes translate this to HTTP 503)."""
    abs_path = Path(path).resolve()
    if not abs_path.exists():
        raise DatabaseUnavailable(f"database not found at {path}")
    conn = sqlite3.connect(f"file:{abs_path}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn
