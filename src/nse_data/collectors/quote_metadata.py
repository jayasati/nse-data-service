"""
Per-symbol fundamental metadata — sector, industry, ISIN, P/E, market cap.

NSE endpoint: /api/quote-equity?symbol=X
FanoutCollector — one HTTP call per symbol.

Target list: union of (config/universe.yaml watchlist) + (raw_fno_list).
Weekly cadence — fundamental data changes slowly. Auto-picks up new
F&O additions because we read raw_fno_list from the DB at plan() time.

Used by Layer 4's fundamentals.from_nse_quote to populate the canonical
fundamentals table.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from .base import FanoutCollector, Request, Row, Session


log = logging.getLogger(__name__)

NSE_BASE = "https://www.nseindia.com"


class QuoteMetadata(FanoutCollector):
    name = "quote_metadata"
    table = "raw_quote_metadata"
    pk_cols = ("symbol",)

    universe_path: str = "config/universe.yaml"
    _db_path: str | None = None

    def run(self, session, db, context=None):
        # Stash the DB path so targets() can re-open it on-thread.
        # Same pattern as OptionChain's session stashing.
        try:
            self._db_path = db.execute("PRAGMA database_list").fetchone()[2]
        except Exception:
            self._db_path = None
        try:
            return super().run(session, db, context)
        finally:
            self._db_path = None

    def targets(self, context: Mapping[str, Any] | None = None) -> Sequence[str]:
        symbols: set[str] = set()

        # 1. Watchlist from universe.yaml
        try:
            with open(self.universe_path) as f:
                cfg = yaml.safe_load(f) or {}
            wl = cfg.get("watchlist") or []
            if isinstance(wl, list):
                symbols.update(s.strip() for s in wl if isinstance(s, str) and s.strip())
        except Exception as e:
            log.warning("watchlist load failed: %s", e)

        # 2. F&O list from raw_fno_list (if available)
        if self._db_path:
            try:
                conn = sqlite3.connect(self._db_path)
                try:
                    fno = conn.execute(
                        "SELECT symbol FROM raw_fno_list"
                    ).fetchall()
                    symbols.update(row[0] for row in fno)
                finally:
                    conn.close()
            except sqlite3.OperationalError:
                # Table doesn't exist yet (first run, before fno_list ran)
                log.info("raw_fno_list not found yet — using watchlist only")

        log.info("quote_metadata: %d target symbols", len(symbols))
        return sorted(symbols)

    def plan_one(self, target: str) -> Request:
        return Request(
            path_or_url="/api/quote-equity",
            params={"symbol": target},
            referer=f"{NSE_BASE}/get-quotes/equity?symbol={target}",
            response_type="json",
            meta={"symbol": target},
        )

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if not isinstance(data, dict):
            return []
        symbol = (request.meta or {}).get("symbol")
        if not symbol:
            return []

        info = data.get("info") or {}
        meta = data.get("metadata") or {}
        sec = data.get("securityInfo") or {}
        price = data.get("priceInfo") or {}
        industry_info = data.get("industryInfo") or {}

        now = int(time.time())
        return [{
            "symbol":          symbol,
            "company_name":    info.get("companyName") or meta.get("companyName"),
            "isin":            meta.get("isin"),
            "industry":        info.get("industry") or meta.get("industry"),
            "sector":          industry_info.get("macro") or industry_info.get("sector"),
            "listing_date":    meta.get("listingDate"),
            "face_value":      _f(meta.get("faceValue") or sec.get("faceValue")),
            "is_fno":          1 if info.get("isFNOSec") in (True, "Yes", "yes") else 0,
            "series":          meta.get("series"),
            "trading_status":  sec.get("tradingStatus"),
            "last_price":      _f(price.get("lastPrice")),
            "pe_ratio":        _f(meta.get("pdSectorPe")),
            "market_cap_cr":   None,   # NSE doesn't include in this payload
            "fetched_at":      now,
        }]


def _f(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None