"""
Insider trading (SEBI PIT) filings — promoter and insider buy/sell.

NSE endpoint: /api/corporates-pit?index=equities
Returns a list of recent insider transactions. Often empty during off-hours
(probed 20-May-2026 16:00 IST: 0 rows). Worth collecting anyway — the moment
a real filing happens, this catches it.

Fingerprint = sha256(symbol | acquirer | period_to | type | qty)[:16]

Field shape is speculative based on architecture §5.4 #35 + historical
schema. May need adjustment when real filings land. Both 'data' wrapper
and bare list payloads are accepted.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Mapping, Sequence

from .base import EventCollector, Request, Row


NSE_BASE = "https://www.nseindia.com"


class InsiderTrading(EventCollector):
    name = "insider_trading"
    table = "raw_insider_trading"

    universe_path: str = "config/universe.yaml"
    top1000_path: str = "config/universe_top1000.txt"

    def _load_symbols(self) -> list[str]:
        """Per-symbol fan-out. NSE's corporates-pit all-equities feed is DEAD (200 OK but
        data=[]); only ?symbol=<SYM> returns rows. Query the broad top-1000 focus universe —
        which includes mid/small-caps, where the promoter-buying edge is strongest — falling
        back to the F&O+watchlist set if that file is absent. Run DAILY (see endpoints.yaml):
        1000 per-symbol probes is one polite evening burst, well inside the 2-working-day
        insider-disclosure window."""
        import os
        if os.path.exists(self.top1000_path):
            try:
                with open(self.top1000_path) as f:
                    syms = {ln.strip().upper() for ln in f if ln.strip()}
                if syms:
                    return sorted(syms)
            except OSError:
                pass
        # fallback — F&O + watchlist from universe.yaml
        try:
            import yaml
            with open(self.universe_path) as f:
                cfg = yaml.safe_load(f) or {}
        except Exception:  # noqa: BLE001
            return []
        out: set[str] = set()
        oc = cfg.get("option_chain") or {}
        if isinstance(oc, dict):
            for v in oc.values():
                if isinstance(v, list):
                    out.update(s for s in v if isinstance(s, str))
        wl = cfg.get("watchlist")
        if isinstance(wl, list):
            out.update(s for s in wl if isinstance(s, str))
        return sorted(out)

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [Request(
            path_or_url="/api/corporates-pit",
            params={"index": "equities", "symbol": sym},
            referer=f"{NSE_BASE}/companies-listing/corporate-filings-insider-trading",
            response_type="json",
        ) for sym in self._load_symbols()]

    def normalize(self, data: Any, request: Request) -> list[Row]:
        # NSE wraps under 'data' key, but also returns a bare list sometimes
        if isinstance(data, dict):
            data = data.get("data") or []
        if not isinstance(data, list):
            return []

        now = int(time.time())
        req_symbol = (request.params or {}).get("symbol")
        rows: list[Row] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            symbol = (item.get("symbol") or req_symbol or "").strip()
            if not symbol:
                continue

            rows.append({
                "symbol":              symbol,
                "company_name":        item.get("company") or item.get("companyName"),
                "acquirer_name":       item.get("acqName"),
                "acquirer_category":   item.get("personCategory"),
                "securities_type":     item.get("secType"),
                "transaction_type":    item.get("tdpTransactionType"),
                "no_of_securities":    _i(item.get("secAcq")),
                "value_in_rupees":     _f(item.get("secVal")),
                # holding_before/after = % of capital (befAcqSharesPer/afterAcqSharesPer) for
                # reference; holding_change_pct is the CALIBRATED move (count-delta scaled by the
                # reliable before-%) — afterAcqSharesPer is often a data-entry 0, so don't difference
                # the Per fields directly. See _holding_change_pct.
                "holding_before":      _f(item.get("befAcqSharesPer")),
                "holding_after":       _f(item.get("afterAcqSharesPer")),
                "holding_change_pct":  _holding_change_pct(item),
                "period_from":         item.get("acquisitionFrom"),
                "period_to":           item.get("acquisitionTo"),
                "intimation_date":     _nse_date(item.get("tdpDate") or item.get("date")),
                "mode_of_acquisition": item.get("acquisitionMode"),
                "derivative_contract": item.get("derivativeType"),
                "attachment_url":      item.get("xbrl") or item.get("attchmntFile"),
                "created_at":          now,
            })
        return rows

    def fingerprint(self, row: Row) -> str:
        key = (
            f"{row['symbol']}|{row.get('acquirer_name') or ''}|"
            f"{row.get('period_to') or ''}|{row.get('transaction_type') or ''}|"
            f"{row.get('no_of_securities') or ''}"
        )
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _holding_change_pct(item):
    """True % move in promoter holding. NSE's afterAcqSharesPer is often a data-entry 0/blank, so
    we scale the reliable share-count delta by the before-% of capital:
        change% = (afterNo - befNo) / befNo * befPer
    Falls back to the after-side ratio, then to (aftPer - befPer), else None."""
    befNo, aftNo = _f(item.get("befAcqSharesNo")), _f(item.get("afterAcqSharesNo"))
    befPer, aftPer = _f(item.get("befAcqSharesPer")), _f(item.get("afterAcqSharesPer"))
    if befNo and befPer and aftNo is not None:        # befNo>0, befPer>0
        return round((aftNo - befNo) / befNo * befPer, 4)
    if aftNo and aftPer and befNo is not None:        # new/zero-before holder: use after-side
        return round((aftNo - befNo) / aftNo * aftPer, 4)
    if befPer is not None and aftPer is not None:
        return round(aftPer - befPer, 4)
    return None


def _nse_date(s):
    """NSE filing date ('18-Feb-2026 19:06' / '18-02-2026' / ISO) → 'YYYY-MM-DD' so the
    promoter-signal layer's date math works. Returns the raw string if unparseable."""
    if not s:
        return None
    import datetime as _dt
    head = str(s).split(" ")[0].strip()
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return _dt.datetime.strptime(head, fmt).date().isoformat()
        except ValueError:
            continue
    return head


def _f(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None