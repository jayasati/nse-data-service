"""
Retention sweeper for intraday indicator tables.

Intraday indicators (cadence="intraday") are stored at 5-minute resolution
and grow ~450K rows/symbol/year for the active universe — most of which is
useless after a few weeks because signals fire on the recent edge, not on
month-old intraday history.

Policy: keep the last 30 calendar days per intraday table; DELETE older.
Runs nightly after the EOD bhavcopy load — outside market hours so the
sweep can't interfere with the live compute job.
"""

from __future__ import annotations

import sqlite3
import time

from .registry import INDICATORS

RETENTION_DAYS = 30
_SECS_PER_DAY = 86_400


def sweep_intraday(conn: sqlite3.Connection, *, retention_days: int = RETENTION_DAYS) -> dict[str, int]:
    """Delete rows older than `retention_days` from every intraday indicator
    table. Returns {table_name: rows_deleted}.

    Assumes the time-key column is the second PK column (per the `Indicator`
    convention) and is an INTEGER epoch-seconds value. EOD/session indicators
    are skipped — their tables use date strings and don't churn at scale.
    """
    cutoff_ts = int(time.time()) - retention_days * _SECS_PER_DAY
    deleted: dict[str, int] = {}
    for ind in INDICATORS:
        if ind.cadence != "intraday":
            continue
        time_col = ind.pk_cols[1]
        cur = conn.execute(
            f"DELETE FROM {ind.table} WHERE {time_col} < ?", (cutoff_ts,),
        )
        deleted[ind.table] = cur.rowcount
    conn.commit()
    return deleted
