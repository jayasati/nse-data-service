"""
SEBI/NSE unsolicited-messages watchlist — pump-dump flagged securities.

Source (XLSX): https://nsearchives.nseindia.com/web/sites/default/files/inline-files/Current_list_of_symbols_1.xlsx
Page: /static/regulations/unsolicited-messages-report

Securities here are being promoted via unsolicited SMS / tips — a hard
exclude-from-signals source, so the table feeds the `blacklist` view.

The sheet has a title row, a blank, a header row (Sr. No., Date of
Dissemination, Symbol, Scrip Code, Name of the Company, Remarks, Company
Response), the data rows, then a footer note. We locate the header by its
'Symbol' cell and read subsequent rows whose Symbol cell is populated — that
naturally skips the title/blank/note lines. The list is frequently empty (no
active watchlist), which ReferenceCollector handles by clearing the table.
"""

from __future__ import annotations

import io
from datetime import date, datetime
from typing import Any, Mapping, Sequence

import openpyxl

from .base import ReferenceCollector, Request, Row


NSE_BASE = "https://www.nseindia.com"
UNSOLICITED_REFERER = f"{NSE_BASE}/static/regulations/unsolicited-messages-report"
CURRENT_LIST_URL = (
    "https://nsearchives.nseindia.com/web/sites/default/files/"
    "inline-files/Current_list_of_symbols_1.xlsx"
)

# Header label -> our row key.
_FIELDS = {
    "Symbol": "symbol",
    "Scrip Code": "scrip_code",
    "Name of the Company": "company_name",
    "Date of Dissemination": "date_disseminated",
    "Remarks": "remarks",
    "Company Response": "company_response",
}


class UnsolicitedWatchlist(ReferenceCollector):
    name = "unsolicited_watchlist"
    table = "raw_unsolicited_watchlist"
    key_cols = ("symbol",)
    replace_strategy = "diff"
    # The watchlist is frequently empty — an empty fetch must clear the table
    # (and thus the blacklist), not be skipped. Safe here: a fetch failure is
    # still guarded against in run().
    persist_empty = True

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url=CURRENT_LIST_URL,
            referer=UNSOLICITED_REFERER,
            response_type="bytes",
        )]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if not isinstance(data, (bytes, bytearray)) or not data:
            return []
        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception:
            return []
        ws = wb.active
        grid = list(ws.iter_rows(values_only=True))

        # Locate the header row (the one containing a 'Symbol' cell).
        header_idx = None
        for i, r in enumerate(grid):
            if r and any(_clean(c) == "Symbol" for c in r):
                header_idx = i
                break
        if header_idx is None:
            return []

        header = [_clean(c) for c in grid[header_idx]]
        col = {label: header.index(label) for label in _FIELDS if label in header}
        sym_i = col.get("Symbol")
        if sym_i is None:
            return []

        rows: list[Row] = []
        for r in grid[header_idx + 1:]:
            if not r:
                continue
            symbol = _clean(r[sym_i]) if sym_i < len(r) else ""
            if not symbol:
                continue  # skips blank lines and the trailing footnote
            rows.append({
                key: (_clean(r[idx]) if idx < len(r) else None) or None
                for label, key in _FIELDS.items()
                if (idx := col.get(label)) is not None
            })
        return rows


def _clean(v) -> str:
    """Cell -> trimmed string; dates -> ISO; None/blank -> ''."""
    if v is None:
        return ""
    if isinstance(v, (datetime, date)):
        return v.date().isoformat() if isinstance(v, datetime) else v.isoformat()
    return str(v).strip()
