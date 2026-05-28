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
    # check_same_thread=False: FastAPI's sync routes run in a threadpool, and
    # a single request can hop threads between the `get_conn` dependency and
    # the route handler. SQLite's default thread-safety check otherwise raises
    # ProgrammingError on perfectly safe read-only access. Safe here because:
    # (a) the connection is opened read-only (mode=ro), so no write contention,
    # (b) each request gets its own short-lived connection via `get_conn`.
    conn = sqlite3.connect(
        f"file:{abs_path}?mode=ro", uri=True, timeout=5.0, check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    return conn
