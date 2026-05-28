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


def last_computed_key(
    conn: sqlite3.Connection,
    indicator: Indicator,
    symbol: str,
):
    """Most recent time-key we have for this (symbol, indicator), or None.

    Reads MAX of the indicator's time column — `pk_cols[1]` by convention.
    Returns a date string for EOD indicators (`date TEXT`) or an integer epoch
    for intraday/session indicators (`ts INTEGER`). Callers stay polymorphic
    — they treat the result as an opaque "watermark" suitable for `>` filters
    and for passing as `since` to the matching DataSource.
    """
    time_col = indicator.pk_cols[1]
    row = conn.execute(
        f"SELECT MAX({time_col}) FROM {indicator.table} WHERE symbol = ?",
        (symbol,),
    ).fetchone()
    return row[0] if row and row[0] is not None else None


# Back-compat alias for any caller still importing the EOD-only name.
last_computed_date = last_computed_key


def _rows_to_write(
    symbol: str,
    indicator: Indicator,
    values: pd.DataFrame,
) -> Iterable[tuple]:
    """Yield (pk..., output...) tuples, skipping rows that are entirely NaN.

    The DataFrame's index value is the time key — a `date` string for EOD or
    an integer `ts` for intraday/session. Stays opaque here; the indicator's
    own table column dictates the storage type.
    """
    for key, row in values.iterrows():
        outputs = [row.get(c) for c in indicator.output_columns]
        if all(_is_null(v) for v in outputs):
            continue
        yield (symbol, key, *[_clean(v) for v in outputs])


def _is_null(v) -> bool:
    # pd.isna handles every pandas missing-value sentinel: None, float NaN,
    # pd.NA (nullable dtype), pd.NaT. Returning a bool also for scalar input.
    return bool(pd.isna(v))


def _clean(v):
    # SQLite stores NaN as a quirky float; coerce to NULL so query filters
    # like `WHERE col IS NULL` behave normally downstream.
    return None if _is_null(v) else float(v)
