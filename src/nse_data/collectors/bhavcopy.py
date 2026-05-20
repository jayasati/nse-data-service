"""
NSE end-of-day bhavcopy collector (cash market).

URL template: 
    https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv

Header (confirmed 2026-05-19):
    SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE,
    LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS,
    NO_OF_TRADES, DELIV_QTY, DELIV_PER

NSE's CSV has leading spaces after each comma — we strip column names in the
DictReader. Missing values (especially DELIV_QTY/DELIV_PER for non-equity series)
arrive as the literal string '-'; _parse_value() coerces them to NULL.

Idempotency: re-running for the same date with the same data produces 0 inserts,
N unchanged. This is what makes scripts/backfill.py safe to restart mid-range.
"""

from __future__ import annotations

import csv
import io
import shutil
import time
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from .base import CsvCollector, Request, Row


# NSE publishes bhavcopy ~17:30 IST, but late by 30+ min is common.
PUBLISH_RETRY_MAX = 6        # 6 attempts
PUBLISH_RETRY_DELAY = 300    # 5 min apart -> ~30min window total


class Bhavcopy(CsvCollector):
    name = "bhavcopy_cm"
    table = "raw_bhavcopy_cm"
    pk_cols = ("date", "symbol", "series")
    response_type = "bytes"

    archive_root = Path("data/archive/bhavcopy")

    def url_for_date(self, d: date) -> str:
        return (
            "https://nsearchives.nseindia.com/products/content/"
            f"sec_bhavdata_full_{d.strftime('%d%m%Y')}.csv"
        )

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if not isinstance(data, (bytes, bytearray)):
            return []

        d_str = (request.meta or {}).get("date") or date.today().isoformat()
        target_date = date.fromisoformat(d_str)
        iso_date = target_date.isoformat()

        text = data.decode("utf-8", errors="replace")
        rows: list[Row] = []

        reader = csv.DictReader(io.StringIO(text))
        # NSE's header has leading spaces; reader.fieldnames will reflect that
        # so we re-key the reader to use stripped names.
        if reader.fieldnames is None:
            return []

        stripped_to_raw = {k.strip(): k for k in reader.fieldnames}

        for raw_row in reader:
            # Strip-and-rekey for ergonomic access
            row = {k: raw_row[v] for k, v in stripped_to_raw.items()}

            symbol = (row.get("SYMBOL") or "").strip()
            series = (row.get("SERIES") or "").strip()
            if not symbol or not series:
                continue

            rows.append({
                "date":          iso_date,
                "symbol":        symbol,
                "series":        series,
                "prev_close":    _parse_value(row.get("PREV_CLOSE"),    float),
                "open":          _parse_value(row.get("OPEN_PRICE"),    float),
                "high":          _parse_value(row.get("HIGH_PRICE"),    float),
                "low":           _parse_value(row.get("LOW_PRICE"),     float),
                "last_price":    _parse_value(row.get("LAST_PRICE"),    float),
                "close":         _parse_value(row.get("CLOSE_PRICE"),   float),
                "avg_price":     _parse_value(row.get("AVG_PRICE"),     float),
                "volume":        _parse_value(row.get("TTL_TRD_QNTY"),  int),
                "turnover_lacs": _parse_value(row.get("TURNOVER_LACS"), float),
                "trades":        _parse_value(row.get("NO_OF_TRADES"),  int),
                "delivery_qty":  _parse_value(row.get("DELIV_QTY"),     int),
                "delivery_pct":  _parse_value(row.get("DELIV_PER"),     float),
            })

        # Archive the raw CSV per architecture §15. Forever-keep, since bhavcopy
        # is the historical truth source. Do this AFTER successful parse so a
        # broken CSV stays where we can poke at it.
        if rows:
            self._archive_csv(data, target_date)

        return rows

    def _archive_csv(self, raw: bytes, d: date) -> None:
        year_dir = self.archive_root / str(d.year)
        year_dir.mkdir(parents=True, exist_ok=True)
        out = year_dir / f"sec_bhavdata_full_{d.strftime('%d%m%Y')}.csv"
        # Avoid re-writing if we already have it (backfill idempotency).
        if not out.exists():
            out.write_bytes(raw)


def _parse_value(raw: Any, cast: type):
    """
    Coerce a CSV cell to the right Python type, returning None on:
      - empty string
      - whitespace-only
      - NSE's '-' placeholder (missing delivery data on non-EQ series)
      - cast failure
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s == "-":
        return None
    try:
        if cast is int:
            # NSE sometimes writes integers as "20.0" — int("20.0") fails.
            return int(float(s))
        return cast(s)
    except (TypeError, ValueError):
        return None