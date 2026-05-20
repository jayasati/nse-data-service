"""
NSE surveillance collectors — three feeds writing to raw_surveillance_*.

GSM:    /api/reportGSM?type=GSM&json=true                  → bare list
ASM-LT: /api/reportASM?json=true → data["longterm"]["data"]
ASM-ST: /api/reportASM?json=true → data["shortterm"]["data"]

All three use ReferenceCollector's diff_upsert semantics:
  - rows in NSE's response but not in DB        → inserted
  - rows in DB but not in NSE's response        → removed (escaped surveillance)
  - rows in both with different fields          → updated (stage bumped or downgraded)
  - rows in both with identical fields          → unchanged

The blacklist view derives the union. Layer 6 reads from blacklist.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from .base import ReferenceCollector, Request, Row


NSE_BASE = "https://www.nseindia.com"
SURV_REFERER = f"{NSE_BASE}/market-data/price-bands-surveillance-actions"


# ============================================================================
# GSM — Graded Surveillance Measure
# ============================================================================

class GsmSurveillance(ReferenceCollector):
    name = "surveillance_gsm"
    table = "raw_surveillance_gsm"
    key_cols = ("symbol",)
    replace_strategy = "diff"

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/reportGSM",
            params={"type": "GSM", "json": "true"},
            referer=SURV_REFERER,
            response_type="json",
        )]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if not isinstance(data, list):
            return []
        now = int(time.time())
        rows: list[Row] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            symbol = (item.get("symbol") or "").strip()
            if not symbol:
                continue
            rows.append({
                "symbol":       symbol,
                "company_name": item.get("companyName"),
                "isin":         item.get("isin"),
                "stage":        item.get("gsmStage"),
                "surv_code":    item.get("survCode"),
                "surv_desc":    item.get("survDesc"),
                "as_on":        item.get("gsmTime"),
                "fetched_at":   now,
            })
        return rows


# ============================================================================
# ASM — Additional Surveillance Measure, long-term + short-term
# ============================================================================

class _AsmBase(ReferenceCollector):
    """
    Shared parsing for ASM-LT and ASM-ST.

    Both subclasses fetch /api/reportASM (which returns both blocks in one
    response) and extract their respective block from the nested structure:
        data["longterm"]["data"]    -> list of LT rows
        data["shortterm"]["data"]   -> list of ST rows

    The duplicate fetch (two HTTP calls when one would suffice) is intentional:
    each subclass is independently scheduled, independently diff-tracked, and
    independently fails. Sharing a fetch would require coordination between
    Collector instances, which the base contract doesn't support.
    """
    asm_block: str = ""  # 'longterm' or 'shortterm', set by subclass
    key_cols = ("symbol",)
    replace_strategy = "diff"

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/reportASM",
            params={"json": "true"},
            referer=SURV_REFERER,
            response_type="json",
        )]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if not isinstance(data, dict):
            return []
        block = data.get(self.asm_block) or {}
        items = block.get("data") if isinstance(block, dict) else None
        if not isinstance(items, list):
            return []
        now = int(time.time())
        rows: list[Row] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            symbol = (item.get("symbol") or "").strip()
            if not symbol:
                continue
            rows.append({
                "symbol":       symbol,
                "series":       item.get("series"),
                "company_name": item.get("companyName"),
                "isin":         item.get("isin"),
                "stage":        item.get("asmSurvIndicator"),
                "surv_code":    item.get("survCode"),
                "surv_desc":    item.get("survDesc"),
                "as_on":        item.get("asmTime"),
                "fetched_at":   now,
            })
        return rows


class AsmLongTermSurveillance(_AsmBase):
    name = "surveillance_asm_lt"
    table = "raw_surveillance_asm_lt"
    asm_block = "longterm"


class AsmShortTermSurveillance(_AsmBase):
    name = "surveillance_asm_st"
    table = "raw_surveillance_asm_st"
    asm_block = "shortterm"