"""Generic SQLite table introspection — schema-aware helpers shared by the
read-core and the collector-health domain.

No web dependency and no freshness reasoning: just "what columns does this table
have", "does it exist", "which column best records when a row landed", and an
identifier guard for the few places we interpolate a config/schema-derived name
into SQL. ops/health.py builds its freshness verdicts on top of these; the
repository uses them to pick a sane ORDER BY for raw-table previews.
"""

from __future__ import annotations

import sqlite3

# Columns that record *when we collected* a row, newest-intent first. Preferred
# over content-date columns because they reflect collector liveness, not the
# business date the data is about.
COLLECTION_TS_COLUMNS = [
    "as_of", "fetched_at", "created_at", "as_on",
    "ingested_at", "collected_at", "snapshot_ts",
]

# Date-only fallbacks (bhavcopy, volatility report, …). Anchored at end-of-day
# when parsed, since the row represents a full session's data.
DATE_TS_COLUMNS = ["trade_date", "business_date", "date"]


def _safe_ident(name: str) -> str:
    """Guard against injection — identifiers are config/schema-derived, but
    we still refuse anything that isn't a plain identifier."""
    if not name.replace("_", "").isalnum():
        raise ValueError(f"unsafe identifier: {name!r}")
    return name


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({_safe_ident(table)})").fetchall()
    return [r[1] for r in rows]


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def detect_ts_column(conn: sqlite3.Connection, table: str) -> tuple[str | None, bool]:
    """Return (column, is_date_only) for the best freshness column, or (None, _)."""
    cols = set(_table_columns(conn, table))
    for c in COLLECTION_TS_COLUMNS:
        if c in cols:
            return c, False
    for c in DATE_TS_COLUMNS:
        if c in cols:
            return c, True
    return None, False
