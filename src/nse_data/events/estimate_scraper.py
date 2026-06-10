"""Consensus-estimate ingestion scaffold (Phase 5, E4).

Deliberately source-agnostic and NOT auto-run. Pulling consensus estimates from
a third-party site (Moneycontrol/ET) is fragile and has ToS implications, and
the page structure can't be pinned down here — so this module provides the
ingestion *plumbing* (normalise arbitrary records → the canonical estimate shape
→ ``consensus.ingest_estimates``) with the actual data source injected as a
``fetcher`` callable. Concrete safe paths:

  * Manual / CSV:  ingest_records(conn, my_list_of_dicts, source="manual")
  * API / scraper: fetch_and_ingest(conn, symbols, fetcher=my_fetcher, source="...")

No scheduler job is registered for this — wiring a specific live source is a
deliberate, separate decision (it needs a verified, permitted source).
"""
from __future__ import annotations

import sqlite3

import structlog

from . import consensus

log = structlog.get_logger()

# Accepted input aliases → canonical keys, so records from any source normalise.
_FIELD_ALIASES = {
    "symbol": ("symbol", "nse_symbol", "ticker"),
    "period_ending": ("period_ending", "period", "quarter_end", "qtr_end"),
    "rev_est_cr": ("rev_est_cr", "revenue_estimate", "rev_est", "sales_est_cr"),
    "pat_est_cr": ("pat_est_cr", "pat_estimate", "pat_est", "net_profit_est_cr"),
    "eps_est": ("eps_est", "eps_estimate", "eps"),
    # BFSI lines (manual/CSV only — no live source carries them):
    "nii_est_cr": ("nii_est_cr", "nii_estimate", "nii_est"),
    "nim_est_pct": ("nim_est_pct", "nim_estimate", "nim_est", "nim"),
}


def normalise_record(record: dict) -> dict | None:
    """Map an arbitrary source record to the canonical estimate dict.

    Returns None when neither symbol nor period can be resolved. Numeric fields
    are coerced to float; unparseable numbers become None.
    """
    out: dict = {}
    for canon, aliases in _FIELD_ALIASES.items():
        val = next((record[a] for a in aliases if a in record and record[a] not in (None, "")), None)
        if canon in ("symbol", "period_ending"):
            out[canon] = str(val).strip() if val is not None else None
        else:
            out[canon] = _to_float(val)
    if not out.get("symbol") or not out.get("period_ending"):
        return None
    return out


def _to_float(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("₹", "")
    try:
        return float(s)
    except ValueError:
        return None


def ingest_records(conn: sqlite3.Connection, records: list[dict], *, source: str) -> int:
    """Normalise then store a batch of source records. Returns count ingested."""
    rows = [n for r in records if (n := normalise_record(r)) is not None]
    return consensus.ingest_estimates(conn, rows, source=source)


def fetch_and_ingest(
    conn: sqlite3.Connection, symbols: list[str], *, fetcher, source: str,
) -> int:
    """Ingest estimates using an injected ``fetcher(symbol) -> list[dict] | dict``.

    The fetcher owns all I/O (HTTP, file, API). This function only normalises and
    stores what it returns, so it's fully testable with a fake fetcher and never
    performs a network call on its own.
    """
    records: list[dict] = []
    for symbol in symbols:
        try:
            got = fetcher(symbol)
        except Exception as e:  # noqa: BLE001 — one bad symbol shouldn't abort the batch
            log.warning("estimate_fetch_failed", symbol=symbol, error=str(e))
            continue
        if got is None:
            continue
        records.extend(got if isinstance(got, list) else [got])
    return ingest_records(conn, records, source=source)
