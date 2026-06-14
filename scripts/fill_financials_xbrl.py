"""Bulk-fill extracted_financials for the whole focus universe from XBRL only.

Deterministic and free: for each top-1000 symbol we ask NSE's NextApi for the
latest result quarter, run the (audited) XBRL extractor, and persist the
standalone + consolidated P&L. No PDF and no LLM are involved, so this never
touches the Azure spend cap. Symbols without a parseable XBRL for their latest
quarter are skipped (the announcement-driven LLM path / reconcile catches those).

Idempotent: persist_extraction upserts on (symbol, period_ending, scope), so
re-running only refreshes. Strategy is stamped ``xbrl`` (confidence 1.0) — the
same authoritative tier the after-close reconcile produces.

    PYTHONPATH=src .venv/bin/python -u scripts/fill_financials_xbrl.py
    # subset / smoke:
    .venv/bin/python -u scripts/fill_financials_xbrl.py --symbols SBIN,INFY,RELIANCE
    .venv/bin/python -u scripts/fill_financials_xbrl.py --limit 25 --sleep 0.5

`-u` (unbuffered) keeps the per-symbol progress streaming live over SSH.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--universe", default="config/universe_top1000.txt")
    ap.add_argument("--symbols", help="comma list, overrides the universe file")
    ap.add_argument("--limit", type=int, default=0, help="cap symbols (0 = all)")
    ap.add_argument("--sleep", type=float, default=0.4,
                    help="seconds between symbols (be gentle on NSE)")
    ap.add_argument("--only-missing", action="store_true",
                    help="skip symbols that already have an xbrl-strategy row")
    args = ap.parse_args()

    from nse_data.storage.db import open_db
    from nse_data.session.manager import SessionManager
    from nse_data.parsers.xbrl_extract import extract_via_xbrl, _api_date
    from nse_data.fundamentals.from_results import persist_extraction

    conn = open_db(args.db)
    session = SessionManager()

    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        lines = Path(args.universe).read_text().splitlines()
        syms = [l.strip().upper() for l in lines if l.strip() and not l.startswith("#")]
    if args.only_missing:
        have = {r[0].upper() for r in conn.execute(
            "SELECT DISTINCT symbol FROM extracted_financials WHERE strategy='xbrl'")}
        syms = [s for s in syms if s not in have]
    if args.limit:
        syms = syms[: args.limit]

    print(f"filling {len(syms)} symbols from XBRL (sleep={args.sleep}s)\n", flush=True)

    n_ok = n_noxbrl = n_nofiling = n_err = 0
    rows_stored = 0
    started = time.time()
    for i, sym in enumerate(syms, 1):
        time.sleep(args.sleep)   # gentle on NSE to avoid blocked responses
        t0 = time.time()
        # Find the latest result quarter for this symbol so extract_via_xbrl
        # resolves the right filing (passing that quarter-end as broadcast_dt).
        try:
            data = session.get_json(
                "nextapi",
                f"/api/NextApi/apiClient/GetQuoteApi?functionName=getIntegratedFilingData&symbol={quote(sym, safe='')}",
                referer=f"https://www.nseindia.com/get-quote/equity/{sym}")
        except Exception as e:  # noqa: BLE001
            n_err += 1
            print(f"[{i:>4}/{len(syms)}] {sym:<14} API-ERR {e!r}", flush=True)
            continue
        latest = max((_api_date(r.get("gfrQuaterEnded")) for r in (data or [])
                      if _api_date(r.get("gfrQuaterEnded"))), default=None)
        if latest is None:
            n_nofiling += 1
            print(f"[{i:>4}/{len(syms)}] {sym:<14} no-filing", flush=True)
            continue
        bdt = latest.strftime("%d-%b-%Y")

        try:
            res = extract_via_xbrl(conn, sym, bdt, session=session)
        except Exception as e:  # noqa: BLE001 — one bad XBRL must not stop the batch
            n_err += 1
            print(f"[{i:>4}/{len(syms)}] {sym:<14} XBRL-ERR {e!r}", flush=True)
            continue
        if res is None or not res.period_ending or not (res.fields or res.consolidated):
            n_noxbrl += 1
            print(f"[{i:>4}/{len(syms)}] {sym:<14} no-xbrl (qe {bdt})", flush=True)
            continue

        stored = 0
        for scope, block in (("standalone", res.fields), ("consolidated", res.consolidated)):
            if block:
                persist_extraction(
                    conn, symbol=sym, period_ending=res.period_ending, scope=scope,
                    fields=block, units_phrase="xbrl", confidence=res.confidence,
                    strategy="xbrl", source_fingerprint=None, broadcast_dt=bdt,
                )
                stored += 1
        n_ok += 1
        rows_stored += stored
        print(f"[{i:>4}/{len(syms)}] OK  {sym:<14} stored={stored} "
              f"period={res.period_ending} std={bool(res.fields)} "
              f"con={bool(res.consolidated)} ({time.time()-t0:.1f}s)", flush=True)

    conn.close()
    print(f"\nDONE in {time.time()-started:.0f}s: "
          f"{n_ok} filled ({rows_stored} rows), {n_noxbrl} no-xbrl, "
          f"{n_nofiling} no-filing, {n_err} errors", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
