"""
SQLite persistence helpers — one per write pattern.

All helpers accept:
  conn  : sqlite3.Connection (or any DB-API 2.0 connection)
  table : destination table name
  rows  : list[dict[str, Any]]; the union of keys defines the column set

Each runs inside its own transaction (commit on success, rollback on error)
and returns a PersistResult. Only insert_ignore mutates input rows
(EventCollector relies on this to stamp the fingerprint).
"""

from __future__ import annotations

from typing import Any, Sequence

from ..collectors.base import PersistResult, Row


# ---- Helpers ----

def _columns(rows: list[Row]) -> list[str]:
    """Union of keys across rows, in first-seen order."""
    seen: dict[str, None] = {}
    for row in rows:
        for k in row:
            seen.setdefault(k, None)
    return list(seen.keys())


def _placeholders(n: int) -> str:
    return ",".join("?" * n)


def _row_values(row: Row, cols: list[str]) -> tuple:
    return tuple(row.get(c) for c in cols)


# ============================================================================
# upsert_many — INSERT ... ON CONFLICT(pk) DO UPDATE
# ============================================================================

def upsert_many(
    conn, table: str, rows: list[Row], pk_cols: Sequence[str]
) -> PersistResult:
    """
    Use when same-PK re-runs should overwrite (idempotent CSV re-runs) or
    when PK uniqueness is enforced by including a timestamp (snapshot
    inserts where collisions are rare-but-not-bugs).

    Returns:
        inserted  - rows whose PK was new
        updated   - rows whose PK existed and at least one field changed
        unchanged - rows whose PK existed and every field matches (cheap idempotency)
    """
    if not rows:
        return PersistResult()

    cols = _columns(rows)
    pk_set = set(pk_cols)
    update_cols = [c for c in cols if c not in pk_set]

    pk_where = " AND ".join(f"{c}=?" for c in pk_cols)
    select_sql = f"SELECT {','.join(cols)} FROM {table} WHERE {pk_where}"

    placeholders = _placeholders(len(cols))
    if update_cols:
        clause = ",".join(f"{c}=excluded.{c}" for c in update_cols)
        conflict = f"ON CONFLICT({','.join(pk_cols)}) DO UPDATE SET {clause}"
    else:
        conflict = f"ON CONFLICT({','.join(pk_cols)}) DO NOTHING"

    insert_sql = (
        f"INSERT INTO {table} ({','.join(cols)}) "
        f"VALUES ({placeholders}) {conflict}"
    )

    inserted = updated = unchanged = 0
    cur = conn.cursor()
    try:
        for row in rows:
            pk_vals = tuple(row.get(c) for c in pk_cols)
            existing = cur.execute(select_sql, pk_vals).fetchone()
            new_vals = _row_values(row, cols)
            if existing is None:
                cur.execute(insert_sql, new_vals)
                inserted += 1
            elif tuple(existing) == new_vals:
                unchanged += 1
            else:
                cur.execute(insert_sql, new_vals)
                updated += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return PersistResult(inserted=inserted, updated=updated, unchanged=unchanged)


# ============================================================================
# insert_ignore — INSERT ... ON CONFLICT(fp) DO NOTHING
# ============================================================================

def insert_ignore(
    conn, table: str, rows: list[Row], key_cols: Sequence[str]
) -> PersistResult:
    """
    EventCollector path. Each row has a fingerprint (or other deterministic
    key). Same fingerprint = same event = skip. Used for announcements,
    board_meetings, etc.

    Returns:
        inserted - rows whose key was new
        deduped  - rows whose key already existed (any field change is ignored
                   — NSE corrections to an existing announcement are rare and
                   would need a manual reconciliation path anyway)
    """
    if not rows:
        return PersistResult()

    cols = _columns(rows)
    placeholders = _placeholders(len(cols))
    insert_sql = (
        f"INSERT INTO {table} ({','.join(cols)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT({','.join(key_cols)}) DO NOTHING"
    )

    inserted = deduped = 0
    cur = conn.cursor()
    try:
        for row in rows:
            cur.execute(insert_sql, _row_values(row, cols))
            if cur.rowcount == 1:
                inserted += 1
            else:
                deduped += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return PersistResult(inserted=inserted, deduped=deduped)


# ============================================================================
# replace_all — DELETE FROM table; INSERT ...
# ============================================================================

def replace_all(conn, table: str, rows: list[Row]) -> PersistResult:
    """
    Wipe and reload. Use when the new payload IS the complete current state
    and you don't need a row-level diff. Fast, simple, no audit trail.

    Returns inserted = len(rows), removed = previous row count (for visibility).
    """
    cur = conn.cursor()
    try:
        prev = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        cur.execute(f"DELETE FROM {table}")

        if rows:
            cols = _columns(rows)
            placeholders = _placeholders(len(cols))
            sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
            cur.executemany(sql, [_row_values(r, cols) for r in rows])

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return PersistResult(inserted=len(rows), removed=prev)


# ============================================================================
# diff_upsert — full reconciliation with delete-missing
# ============================================================================

def diff_upsert(
    conn, table: str, rows: list[Row], key_cols: Sequence[str]
) -> PersistResult:
    """
    The honest version of replace_all. The new payload IS the current state,
    but we compute the diff so callers can see which rows were added,
    removed, updated, or unchanged. Required for /blacklist/changes?since=.

    Returns:
        inserted  - keys present in batch, absent from DB
        updated   - keys in both, at least one field differs
        unchanged - keys in both, all fields match
        removed   - keys absent from batch, deleted from DB
    """
    if not rows:
        # Empty batch means "the world is empty" — drop the table contents
        # to keep the contract honest. Callers that don't want this should
        # check upstream and skip the call.
        return replace_all(conn, table, [])

    cols = _columns(rows)
    cur = conn.cursor()

    existing = {
        tuple(r[c] for c in key_cols): r
        for r in (dict(zip(cols, vals)) for vals in cur.execute(
            f"SELECT {','.join(cols)} FROM {table}"
        ).fetchall())
    }

    inserted = updated = unchanged = removed = 0
    placeholders = _placeholders(len(cols))
    update_cols = [c for c in cols if c not in set(key_cols)]
    key_where = " AND ".join(f"{c}=?" for c in key_cols)

    insert_sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    update_sql = (
        f"UPDATE {table} SET {','.join(f'{c}=?' for c in update_cols)} "
        f"WHERE {key_where}"
    ) if update_cols else None
    delete_sql = f"DELETE FROM {table} WHERE {key_where}"

    try:
        seen_keys: set[tuple] = set()
        for row in rows:
            key = tuple(row.get(c) for c in key_cols)
            seen_keys.add(key)
            existing_row = existing.get(key)
            new_vals = _row_values(row, cols)
            if existing_row is None:
                cur.execute(insert_sql, new_vals)
                inserted += 1
            elif tuple(existing_row.get(c) for c in cols) == new_vals:
                unchanged += 1
            elif update_sql:
                cur.execute(
                    update_sql,
                    tuple(row.get(c) for c in update_cols) + key,
                )
                updated += 1
            else:
                # No non-key cols to update — treat as unchanged
                unchanged += 1

        # Delete keys that were in DB but not in this batch
        for key in existing.keys() - seen_keys:
            cur.execute(delete_sql, key)
            removed += 1

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return PersistResult(
        inserted=inserted, updated=updated,
        unchanged=unchanged, removed=removed,
    )