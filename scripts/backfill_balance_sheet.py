"""Backfill the balance-sheet columns (migration 081) into existing
extracted_financials rows by re-parsing each result XBRL's period-end instant
context. Only filings that actually carry a balance sheet (SEBI: half-yearly)
update anything; the rest no-op. Idempotent.

XBRLs are on the nsearchives CDN (not throttled) → fetched in parallel.

    PYTHONPATH=src .venv/bin/python -u scripts/backfill_balance_sheet.py --workers 6
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_BS = ("equity_cr", "total_assets_cr", "current_assets_cr", "current_liabilities_cr",
       "total_liabilities_cr", "borrowings_cr", "cash_cr")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--only-missing", action="store_true", default=True,
                    help="skip filings whose row already has equity_cr (default on)")
    args = ap.parse_args()

    from nse_data.parsers.xbrl_financials import parse_xbrl
    from nse_data.session.manager import SessionManager
    from nse_data.storage.db import apply_migrations, open_db

    conn = open_db(args.db)
    conn.execute("PRAGMA busy_timeout=60000")
    apply_migrations(conn)

    # result XBRLs for symbols we actually have financials for
    rows = conn.execute(
        "SELECT DISTINCT i.symbol, i.xbrl_url FROM raw_integrated_filings i "
        "WHERE i.xbrl_url LIKE '%INDAS%' "
        "AND i.symbol IN (SELECT DISTINCT symbol FROM extracted_financials)").fetchall()
    print(f"candidate result XBRLs: {len(rows)}", flush=True)

    sm = SessionManager()

    def fetch_parse(url):
        try:
            return parse_xbrl(sm.get_bytes("xbrl_fin", url, referer="https://www.nseindia.com/"))
        except Exception:  # noqa: BLE001
            return None

    n_upd = n_nobs = n_nomatch = n_err = 0
    SET = ", ".join(f"{c}=?" for c in _BS)
    try:
        for i in range(0, len(rows), args.workers * 4):
            batch = rows[i:i + args.workers * 4]
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                parsed = list(ex.map(lambda r: (r[0], fetch_parse(r[1])), batch))
            for sym, r in parsed:
                if not r:
                    n_err += 1
                    continue
                f = r["fields"]
                bs = [f.get(c) for c in _BS]
                if all(v is None for v in bs):
                    n_nobs += 1
                    continue
                cur = conn.execute(
                    f"UPDATE extracted_financials SET {SET} "
                    "WHERE symbol=? AND period_ending=? AND scope=?",
                    (*bs, sym, r["period_ending"], r["scope"]))
                if cur.rowcount:
                    n_upd += 1
                else:
                    n_nomatch += 1
            conn.commit()
            done = i + len(batch)
            if (i // (args.workers * 4)) % 10 == 0:
                print(f"  [{done}/{len(rows)}] updated={n_upd} no_bs={n_nobs} "
                      f"no_match={n_nomatch} err={n_err}", flush=True)
    finally:
        sm.close()
    conn.commit()
    have = conn.execute(
        "SELECT COUNT(*) FROM extracted_financials WHERE equity_cr IS NOT NULL").fetchone()[0]
    print(f"DONE: updated={n_upd} no_bs={n_nobs} no_match={n_nomatch} err={n_err}  "
          f"rows_with_equity={have}", flush=True)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
