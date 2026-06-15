"""Gentle backfill of raw_quote_metadata for symbols missing a sector route.

The sector router resolves a symbol via index membership + NSE's quote-metadata
taxonomy (raw_quote_metadata → sectors.base.class_for_metadata). Symbols absent
from raw_quote_metadata fall to UNKNOWN (no signal). This fetches the per-symbol
NSE /api/quote-equity payload for the UNKNOWN set so they can route — reusing the
QuoteMetadata collector's own normalize(), so the parsing matches the live job.

Paced + retried like fill_financials_xbrl (the NSE quote API throttles a long
per-symbol crawl): circuit disabled, gentle sleep, per-request backoff.

    PYTHONPATH=src .venv/bin/python -u scripts/fill_quote_metadata.py            # all UNKNOWN
    PYTHONPATH=src .venv/bin/python -u scripts/fill_quote_metadata.py --symbols BAJFINANCE,HDFCAMC
    PYTHONPATH=src .venv/bin/python -u scripts/fill_quote_metadata.py --sleep 2.0 --limit 50
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--symbols", help="comma list, overrides the UNKNOWN-set default")
    ap.add_argument("--limit", type=int, default=0, help="cap symbols (0 = all)")
    ap.add_argument("--sleep", type=float, default=1.5, help="seconds between symbols")
    ap.add_argument("--only-missing", action="store_true", default=True,
                    help="skip symbols already in raw_quote_metadata (default on)")
    ap.add_argument("--all", dest="only_missing", action="store_false",
                    help="re-fetch even symbols already present")
    args = ap.parse_args()

    from nse_data.storage.db import open_db
    from nse_data.session.manager import SessionManager
    from nse_data.session.circuit import CircuitOpenError
    from nse_data.collectors.base import Request
    from nse_data.collectors.quote_metadata import QuoteMetadata
    from nse_data.fundamentals.sectors import sector_class_for

    conn = open_db(args.db)
    collector = QuoteMetadata()
    session = SessionManager(circuit_failure_threshold=10**9)  # one-shot crawl: no breaker

    def get_json(path, ref, params):
        for attempt in range(4):
            try:
                return session.get_json("quote_metadata", path, referer=ref, params=params)
            except CircuitOpenError:
                time.sleep(20)
            except Exception:           # noqa: BLE001 — timeout / transient throttle
                if attempt == 3:
                    raise
                time.sleep(2 * (attempt + 1))
        return session.get_json("quote_metadata", path, referer=ref, params=params)

    # Target set: explicit list, else every extracted symbol still routing UNKNOWN.
    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        allsyms = [r[0] for r in conn.execute(
            "SELECT DISTINCT symbol FROM extracted_financials")]
        syms = [s for s in allsyms if sector_class_for(s).value == "unknown"]
        print(f"{len(syms)} symbols routing UNKNOWN", flush=True)

    if args.only_missing:
        have = {r[0].upper() for r in conn.execute(
            "SELECT symbol FROM raw_quote_metadata")}
        before = len(syms)
        syms = [s for s in syms if s.upper() not in have]
        print(f"--only-missing: {before - len(syms)} already have metadata, "
              f"{len(syms)} to fetch", flush=True)
    if args.limit:
        syms = syms[: args.limit]

    cols = ("symbol", "company_name", "isin", "industry", "sector", "listing_date",
            "face_value", "is_fno", "series", "trading_status", "last_price",
            "pe_ratio", "market_cap_cr", "fetched_at")
    placeholders = ",".join("?" * len(cols))
    upsert = (f"INSERT OR REPLACE INTO raw_quote_metadata ({','.join(cols)}) "
              f"VALUES ({placeholders})")

    n_ok = n_empty = n_err = 0
    started = time.time()
    for i, sym in enumerate(syms, 1):
        time.sleep(args.sleep)
        req = Request(
            path_or_url="/api/quote-equity",
            params={"symbol": sym},
            referer=f"https://www.nseindia.com/get-quotes/equity?symbol={sym}",
            response_type="json",
            meta={"symbol": sym},
        )
        try:
            data = get_json(req.path_or_url, req.referer, req.params)
        except Exception as e:  # noqa: BLE001
            n_err += 1
            print(f"[{i:>4}/{len(syms)}] {sym:<14} API-ERR {e!r}", flush=True)
            continue
        rows = collector.normalize(data, req)
        if not rows:
            n_empty += 1
            print(f"[{i:>4}/{len(syms)}] {sym:<14} empty", flush=True)
            continue
        r = rows[0]
        conn.execute(upsert, tuple(r.get(c) for c in cols))
        conn.commit()
        n_ok += 1
        print(f"[{i:>4}/{len(syms)}] OK  {sym:<14} sector={r.get('sector')!r} "
              f"industry={r.get('industry')!r}", flush=True)

    conn.close()
    print(f"\nDONE in {time.time()-started:.0f}s: {n_ok} fetched, "
          f"{n_empty} empty, {n_err} errors", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
