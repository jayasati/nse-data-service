"""Participant-wise F&O open interest (FEATURE_CHECKLIST Week 22, task 22.1).

NSE publishes a daily participant-wise OI report (Client / DII / FII / Pro × future-index,
future-stock, option-index, option-stock legs) — the cleanest public read of how the big
participant buckets are positioned in derivatives. Used by the smart-money score (22.5):
FII net future-index and option call/put positioning is the "what are the big players doing"
signal that cash flows alone miss.

Date-stamped CSV at a fixed path → CsvCollector with url_for_date. Verified against the live
file on the server. The header/TOTAL rows are skipped; only the four participant rows are kept.
"""
from __future__ import annotations

import time
from datetime import date
from typing import Any

from .base import CsvCollector, Request, Row

_PARTICIPANTS = {"Client", "DII", "FII", "Pro"}
# the 14 numeric columns, in file order
_COLS = ("fut_idx_long", "fut_idx_short", "fut_stk_long", "fut_stk_short",
         "opt_idx_call_long", "opt_idx_put_long", "opt_idx_call_short", "opt_idx_put_short",
         "opt_stk_call_long", "opt_stk_put_long", "opt_stk_call_short", "opt_stk_put_short",
         "total_long", "total_short")


def _int(s: str):
    try:
        return int(s.strip().replace(",", ""))
    except (ValueError, AttributeError):
        return None


class ParticipantOi(CsvCollector):
    name = "participant_oi"
    table = "raw_participant_oi"
    pk_cols = ("report_date", "client_type")
    response_type = "text"

    def url_for_date(self, d: date) -> str:
        return (f"https://nsearchives.nseindia.com/content/nsccl/"
                f"fao_participant_oi_{d.strftime('%d%m%Y')}.csv")

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if isinstance(data, (bytes, bytearray)):
            data = data.decode("utf-8", errors="replace")
        if not isinstance(data, str):
            return []
        report_date = (request.meta or {}).get("date") or date.today().isoformat()
        now = int(time.time())
        rows: list[Row] = []
        for line in data.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if not parts or parts[0] not in _PARTICIPANTS or len(parts) < len(_COLS) + 1:
                continue
            nums = [_int(p) for p in parts[1:1 + len(_COLS)]]
            row: Row = {"report_date": report_date, "client_type": parts[0], "fetched_at": now}
            row.update(dict(zip(_COLS, nums)))
            rows.append(row)
        return rows
