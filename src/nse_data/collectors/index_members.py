"""
Index constituents — which stocks are in NIFTY 50, NIFTY 100, etc.

NSE endpoint: /api/equity-stock-indices?index=<NAME>
(renamed from /api/equity-stockIndices around 2026-05-22; old path 404s.)
Same endpoint as LiveEquity, but used here for reference data:
weightage rank rather than live LTPs.

FanoutCollector — config/universe.yaml lists which indices to track.
Default: NIFTY 50, NIFTY 100, NIFTY 500, NIFTY BANK, NIFTY IT,
NIFTY AUTO, NIFTY PHARMA, NIFTY FMCG, NIFTY FINANCIAL SERVICES.
Add or remove indices via universe.yaml — no code changes needed.

Used by Layer 5/6 for sector classification and sector RS computation.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from .base import FanoutCollector, Request, Row


log = logging.getLogger(__name__)

NSE_BASE = "https://www.nseindia.com"

DEFAULT_INDICES = (
    "NIFTY 50",
    "NIFTY 100",
    "NIFTY 500",
    "NIFTY BANK",
    "NIFTY IT",
    "NIFTY AUTO",
    "NIFTY PHARMA",
    "NIFTY FMCG",
    "NIFTY FINANCIAL SERVICES",
    "NIFTY ENERGY",
    "NIFTY METAL",
    "NIFTY REALTY",
)


class IndexMembers(FanoutCollector):
    name = "index_members"
    table = "raw_index_members"
    pk_cols = ("index_name", "symbol")

    universe_path: str = "config/universe.yaml"

    def targets(self, context: Mapping[str, Any] | None = None) -> Sequence[str]:
        try:
            with open(self.universe_path) as f:
                cfg = yaml.safe_load(f) or {}
            indices = (cfg.get("index_members") or {}).get("indices")
            if isinstance(indices, list) and indices:
                return [str(i).strip() for i in indices if i]
        except Exception as e:
            log.warning("universe.yaml load failed (%s); using DEFAULT_INDICES", e)
        return list(DEFAULT_INDICES)

    def plan_one(self, target: str) -> Request:
        return Request(
            path_or_url="/api/equity-stock-indices",
            params={"index": target},
            referer=f"{NSE_BASE}/market-data/live-equity-market",
            response_type="json",
            meta={"index_name": target},
        )

    def normalize(self, data: Any, request: Request) -> list[Row]:
            if not isinstance(data, dict):
                return []
            items = data.get("data") or []
            if not isinstance(items, list):
                return []

            now = int(time.time())
            index_name = (request.meta or {}).get("index_name", "UNKNOWN")
            index_marker = index_name.strip().upper()

            # Filter the header row out FIRST, then enumerate the survivors.
            # If we enumerate before filtering, the header consumes rank 1 and
            # the actual biggest constituent ends up at rank 2.
            constituents = [
                item for item in items
                if isinstance(item, dict)
                and (item.get("symbol") or "").strip()
                and (item.get("symbol") or "").strip().upper() != index_marker
            ]

            rows: list[Row] = []
            for rank, item in enumerate(constituents, start=1):
                symbol = item["symbol"].strip()
                rows.append({
                    "index_name":     index_name,
                    "symbol":         symbol,
                    "series":         item.get("series"),
                    "weightage_rank": rank,
                    "last_price":     _f(item.get("lastPrice")),
                    "fetched_at":     now,
                })
            return rows


def _f(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None