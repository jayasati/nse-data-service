"""Backfill 3y of BALANCE-SHEET history into extracted_financials. The integrated
-filing archive only reaches mid-2025, so for deeper history we use NSE's
per-symbol results-history endpoint (same shape as the shareholding backfill):

    /api/corporates-financial-results?index=equities&symbol=<SYM>&period=Quarterly

It returns the symbol's quarterly results back years, each with an INDAS XBRL url.
Per symbol: pull the last --quarters filings → parse each XBRL's period-end
balance sheet (parsers/xbrl_financials) → UPDATE the matching extracted_financials
row's BS columns (migration 081). The balance sheet is filed half-yearly, so ~half
the quarters update; the rest no-op. Idempotent.

The per-symbol endpoint is throttle-prone (like the SHP master) → paced + retried
+ adaptive cooldown; XBRL downloads are on the nsearchives CDN → fetched parallel.

    PYTHONPATH=src .venv/bin/python -u scripts/backfill_financials_history.py --quarters 12
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_BS = ("equity_cr", "total_assets_cr", "current_assets_cr", "current_liabilities_cr",
       "total_liabilities_cr", "borrowings_cr", "cash_cr")
_REFERER = "https://www.nseindia.com/companies-listing/corporate-filings-financial-results"


def _date(s):
    for fmt in ("%d-%b-%Y", "%d-%b-%Y %H:%M", "%Y-%m-%d"):
        try:
            return _dt.datetime.strptime((s or "").strip(), fmt)
        except (ValueError, TypeError):
            continue
    return _dt.datetime.min


def _history(sm, sym, attempts=4):
    """Per-symbol quarterly results with an XBRL, newest first. Retries with
    backoff on fetch failure (throttle); returns [] empty, None on persistent error."""
    errored = False
    for a in range(attempts):
        try:
            d = sm.get_json("finres_hist", "/api/corporates-financial-results",
                            referer=_REFERER,
                            params={"index": "equities", "symbol": sym, "period": "Quarterly"})
        except Exception:  # noqa: BLE001
            errored = True
            time.sleep(1.5 * (a + 1))
            continue
        items = d.get("data") if isinstance(d, dict) else d
        if not items:
            return []
        xs = [it for it in items
              if it.get("xbrl") and not str(it.get("xbrl")).rstrip("/").endswith("-")]
        xs.sort(key=lambda it: _date(it.get("toDate") or it.get("fromDate")), reverse=True)
        return xs
    return None if errored else []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--symbols", help="comma list (default: tracked universe)")
    ap.add_argument("--quarters", type=int, default=12)
    ap.add_argument("--workers", type=int, default=12, help="parallel XBRL downloads/symbol")
    ap.add_argument("--concurrency", type=int, default=12,
                    help="SessionManager global in-flight cap (CDN tolerates >4)")
    ap.add_argument("--pace", type=float, default=0.05, help="pause per symbol (master call)")
    ap.add_argument("--max-cooldown", type=float, default=30.0)
    ap.add_argument("--skip-covered", type=int, default=0,
                    help="skip symbols already having >=N quarters with equity_cr")
    args = ap.parse_args()

    from nse_data.parsers.xbrl_financials import parse_xbrl
    from nse_data.session.manager import SessionManager
    from nse_data.storage.db import apply_migrations, open_db

    conn = open_db(args.db)
    conn.execute("PRAGMA busy_timeout=60000")
    apply_migrations(conn)

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        try:
            symbols = [s for (s,) in conn.execute(
                "SELECT symbol FROM tradeable_universe WHERE grade != 'etf'")]
        except Exception:  # noqa: BLE001
            symbols = [s for (s,) in conn.execute(
                "SELECT DISTINCT symbol FROM extracted_financials")]
    symbols = sorted(set(symbols))

    have = dict(conn.execute(
        "SELECT symbol, COUNT(*) FROM extracted_financials WHERE equity_cr IS NOT NULL "
        "GROUP BY symbol"))

    sm = SessionManager(global_concurrency=args.concurrency)
    SET = ", ".join(f"{c}=?" for c in _BS)
    stats = {"upd": 0, "nobs": 0, "nomatch": 0, "fail": 0, "covered": 0}
    errored = []
    consec = 0

    def fetch_parse(url):
        try:
            return parse_xbrl(sm.get_bytes("xbrl_fin", url, referer="https://www.nseindia.com/"))
        except Exception:  # noqa: BLE001
            return None

    def process(sym) -> bool:
        recs = _history(sm, sym)
        if recs is None:
            return False
        if not recs:
            return True
        urls = [it["xbrl"] for it in recs[:args.quarters]]
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            parsed = list(ex.map(fetch_parse, urls))
        for r in parsed:
            if not r:
                stats["fail"] += 1
                continue
            bs = [r["fields"].get(c) for c in _BS]
            if all(v is None for v in bs):
                stats["nobs"] += 1
                continue
            cur = conn.execute(
                f"UPDATE extracted_financials SET {SET} "
                "WHERE symbol=? AND period_ending=? AND scope=?",
                (*bs, sym, r["period_ending"], r["scope"]))
            stats["upd" if cur.rowcount else "nomatch"] += 1
        return True

    try:
        for i, sym in enumerate(symbols, 1):
            if args.skip_covered and have.get(sym, 0) >= args.skip_covered:
                stats["covered"] += 1
                continue
            ok = process(sym)
            if not ok:
                errored.append(sym)
                consec += 1
                time.sleep(min(args.pace * (2 ** consec), args.max_cooldown))
            else:
                consec = 0
                time.sleep(args.pace)
            if i % 25 == 0:
                conn.commit()
                print(f"  [{i}/{len(symbols)}] {sym} upd={stats['upd']} nobs={stats['nobs']} "
                      f"nomatch={stats['nomatch']} fail={stats['fail']} err={len(errored)}", flush=True)
        conn.commit()
        if errored:
            print(f"retrying {len(errored)} errored...", flush=True)
            still = [s for s in errored if not (process(s) or time.sleep(args.pace))]
            conn.commit()
            errored = still
    finally:
        sm.close()
    tot = conn.execute(
        "SELECT COUNT(*) FROM extracted_financials WHERE equity_cr IS NOT NULL").fetchone()[0]
    syms = conn.execute(
        "SELECT COUNT(DISTINCT symbol) FROM extracted_financials WHERE equity_cr IS NOT NULL").fetchone()[0]
    print(f"DONE: updated={stats['upd']} no_bs={stats['nobs']} no_match={stats['nomatch']} "
          f"parse_fail={stats['fail']} covered={stats['covered']} unrecovered={len(errored)}  "
          f"rows_with_equity={tot} symbols_with_equity={syms}", flush=True)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
