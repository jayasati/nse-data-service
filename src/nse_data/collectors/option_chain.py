"""
NSE option chain collector — the FanoutCollector archetype's first concrete.

NSE's new option-chain-v3 endpoint (migrated from option-chain-{indices,equities}
in 2026, see LEARNINGS.md 2026-05-20) requires both `type` and `expiry` as query
params. So each symbol is a two-step fetch:

  1. /api/option-chain-contract-info?symbol=X       -> list of expiryDates
  2. /api/option-chain-v3?type=Indices|Equities&symbol=X&expiry=Y  -> the chain

The base FanoutCollector contract is plan_one(target) -> Request. We extend it
slightly: targets() does the meta-fetch synchronously and returns a list of
(symbol, expiry, type) tuples — each becomes one Request via plan_one().

This means the meta-fetch is NOT under the run() loop's per-call error isolation.
If contract-info fails for a symbol, that symbol drops out of the run, but
no row appears in run_report.errors for it. Trade-off: we could put meta-fetch
under run() too (2N requests per cycle, half of which are tiny), but the
asymmetry is intentional — a failed meta-fetch means we don't even know what
expiry to ask for, so there's no chain Request to issue and no error to attach.
Logged at info-level instead so it's visible in operations.

Layer 1 must be warm for the option-chain-v3 endpoint specifically; warm-up
hop to /option-chain is already in place from Phase 6 prep (see manager.py).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from .base import FanoutCollector, Request, Row, Session


log = logging.getLogger(__name__)

NSE_BASE = "https://www.nseindia.com"
OC_REFERER = f"{NSE_BASE}/option-chain"


class OptionChain(FanoutCollector):
    name = "option_chain"
    table = "raw_option_chain"
    pk_cols = ("symbol", "expiry", "strike", "option_type", "as_of")

    # Loaded once per run from config/universe.yaml
    universe_path: str = "config/universe.yaml"

    # The session is needed by targets() for the meta-fetch.
    # Set by run() before targets()/plan() is called — see _resolve_targets.
    _session: Session | None = None

    def run(self, session, db, context=None):
        """Override run to stash the session for targets() to use."""
        self._session = session
        try:
            return super().run(session, db, context)
        finally:
            self._session = None

    # ----- FanoutCollector contract -----

    def targets(self, context: Mapping[str, Any] | None = None
                ) -> Sequence[tuple[str, str, str]]:
        """
        Return (symbol, expiry, type) tuples after meta-fetching contract-info
        for each symbol. Symbols whose meta-fetch fails are skipped with a
        warning — see module docstring for rationale.
        """
        cfg = self._load_universe()
        n_expiries = max(1, int(cfg.get("n_expiries", 1)))
        plan: list[tuple[str, str, str]] = []

        for ty in ("indices", "equities"):
            for sym in cfg.get(ty, []):
                api_type = "Indices" if ty == "indices" else "Equities"
                try:
                    info = self._session.get_json(
                        self.name,
                        "/api/option-chain-contract-info",
                        referer=OC_REFERER,
                        params={"symbol": sym},
                    )
                except Exception as e:
                    log.warning("meta_fetch_failed symbol=%s err=%s", sym, e)
                    continue

                expiries = (info or {}).get("expiryDates") or []
                if not expiries:
                    log.warning("no_expiries_returned symbol=%s", sym)
                    continue

                for expiry in expiries[:n_expiries]:
                    plan.append((sym, expiry, api_type))

        return plan

    def plan_one(self, target: tuple[str, str, str]) -> Request:
        symbol, expiry, api_type = target
        return Request(
            path_or_url="/api/option-chain-v3",
            params={"type": api_type, "symbol": symbol, "expiry": expiry},
            referer=OC_REFERER,
            response_type="json",
            meta={"symbol": symbol, "expiry": expiry, "type": api_type},
        )

    def normalize(self, data: Any, request: Request) -> list[Row]:
        symbol = request.meta["symbol"]
        expiry = request.meta["expiry"]
        as_of = int(time.time())

        if not isinstance(data, dict):
            return []
        rec = data.get("records") or {}
        items = rec.get("data") or []
        if not isinstance(items, list):
            return []

        rows: list[Row] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            strike = item.get("strikePrice")
            if strike is None:
                continue
            try:
                strike = float(strike)
            except (TypeError, ValueError):
                continue

            # Each item has CE and/or PE sub-objects. Emit one row per leg
            # present. Skip strikes where neither is present.
            for opt_type, leg in (("CE", item.get("CE")), ("PE", item.get("PE"))):
                if not isinstance(leg, dict):
                    continue
                rows.append(_row_from_leg(
                    symbol=symbol,
                    expiry=expiry,
                    strike=strike,
                    option_type=opt_type,
                    as_of=as_of,
                    leg=leg,
                ))

        return rows

    # ----- Helpers -----

    def _load_universe(self) -> dict:
        with open(self.universe_path) as f:
            cfg = yaml.safe_load(f) or {}
        oc = cfg.get("option_chain") or {}
        if not isinstance(oc, dict):
            return {}
        return oc


def _row_from_leg(
    *, symbol: str, expiry: str, strike: float,
    option_type: str, as_of: int, leg: dict,
) -> Row:
    return {
        "symbol":              symbol,
        "expiry":              expiry,
        "strike":              strike,
        "option_type":         option_type,
        "as_of":               as_of,
        "underlying_value":    _f(leg.get("underlyingValue")),
        "open_interest":       _i(leg.get("openInterest")),
        "change_in_oi":        _i(leg.get("changeinOpenInterest")),
        "pct_change_in_oi":    _f(leg.get("pchangeinOpenInterest")),
        "total_traded_volume": _i(leg.get("totalTradedVolume")),
        "last_price":          _f(leg.get("lastPrice")),
        "price_change":        _f(leg.get("change")),
        "pct_price_change":    _f(leg.get("pchange")),
        "implied_volatility":  _f(leg.get("impliedVolatility")),
        "total_buy_quantity":  _i(leg.get("totalBuyQuantity")),
        "total_sell_quantity": _i(leg.get("totalSellQuantity")),
        "bid_price":           _f(leg.get("buyPrice1")),
        "bid_quantity":        _i(leg.get("buyQuantity1")),
        "ask_price":           _f(leg.get("sellPrice1")),
        "ask_quantity":        _i(leg.get("sellQuantity1")),
        "identifier":          leg.get("identifier"),
    }


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
        return int(float(v))   # NSE writes some ints as floats
    except (TypeError, ValueError):
        return None