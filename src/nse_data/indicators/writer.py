"""
Persist a computed-indicator DataFrame into its target table.

One generic upsert helper works for every indicator because the Indicator
ABC pins down the shape: PK columns + output columns. We INSERT OR REPLACE
on the PK, which gives idempotent re-runs over the same date range — useful
when bhavcopy corrections trigger a recompute.

Rows whose output values are all NaN are skipped. Indicators emit NaN for
the early history they can't yet score (e.g. SMA-200 needs 200 bars), and
we don't want to litter the table with empty rows.
"""

from __future__ import annotations

import math
import sqlite3
from typing import Iterable

import pandas as pd

from .base import Indicator


def write_indicator(
    conn: sqlite3.Connection,
    indicator: Indicator,
    symbol: str,
    values: pd.DataFrame,
) -> int:
    """
    Upsert `values` into `indicator.table` for `symbol`. Returns the number
    of rows written.

    `values` is what Indicator.compute() returned: indexed by date,
    columns == indicator.output_columns. We assume `symbol` is not in the
    DataFrame columns (the orchestrator owns one symbol at a time) and
    inject it into each row.
    """
    if values.empty:
        return 0

    rows = list(_rows_to_write(symbol, indicator, values))
    if not rows:
        return 0

    cols = list(indicator.pk_cols) + list(indicator.output_columns)
    placeholders = ",".join("?" * len(cols))
    column_list = ",".join(cols)
    sql = (
        f"INSERT OR REPLACE INTO {indicator.table} ({column_list}) "
        f"VALUES ({placeholders})"
    )
    conn.executemany(sql, rows)
    conn.commit()
    return len(rows)


def last_computed_date(
    conn: sqlite3.Connection,
    indicator: Indicator,
    symbol: str,
) -> str | None:
    """Most recent `date` we have for this (symbol, indicator), or None."""
    row = conn.execute(
        f"SELECT MAX(date) FROM {indicator.table} WHERE symbol = ?",
        (symbol,),
    ).fetchone()
    return row[0] if row and row[0] else None


def _rows_to_write(
    symbol: str,
    indicator: Indicator,
    values: pd.DataFrame,
) -> Iterable[tuple]:
    """Yield (pk..., output...) tuples, skipping rows that are entirely NaN."""
    for date, row in values.iterrows():
        outputs = [row.get(c) for c in indicator.output_columns]
        if all(_is_null(v) for v in outputs):
            continue
        yield (symbol, date, *[_clean(v) for v in outputs])


def _is_null(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _clean(v):
    # SQLite stores NaN as a quirky float; coerce to NULL so query filters
    # like `WHERE col IS NULL` behave normally downstream.
    return None if _is_null(v) else float(v)
