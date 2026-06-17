"""Load/refresh the BSE scrip master (ISIN → BSE numeric code) from BSE's active
equity list JSON API. Lights up cause_engine.bse_announcements (the BSE API call
is already written; it only lacked the numeric strScrip).

Source: api.bseindia.com ListofScripData (segment=Equity, status=Active) — ~4900
rows with SCRIP_CD / ISIN_NUMBER / Scrip_Name. (BSE's List_of_companies.csv is
the GSM surveillance subset only — not used.) ISIN-keyed INSERT OR REPLACE; rows
without an ISIN are skipped. Run monthly (BSE codes are permanent).

    PYTHONPATH=src .venv/bin/python -u scripts/load_bse_scrip_master.py
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_URL = ("https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w"
        "?Group=&Scripcode=&industry=&segment=Equity&status=Active")
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://www.bseindia.com/",
    "Accept": "application/json",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--url", default=_URL)
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args()

    import httpx
    from nse_data.storage.db import open_db, apply_migrations

    r = httpx.get(args.url, headers=_HEADERS, timeout=args.timeout, follow_redirects=True)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        print(f"unexpected payload (not a list): {str(data)[:200]}")
        return 1

    now = int(time.time())
    rows, skipped = [], 0
    for rec in data:
        isin = (rec.get("ISIN_NUMBER") or "").strip()
        code = (rec.get("SCRIP_CD") or "").strip()
        name = (rec.get("Scrip_Name") or rec.get("Issuer_Name") or "").strip() or None
        if not isin or not code:
            skipped += 1
            continue
        rows.append((isin, code, name, now))

    conn = open_db(args.db)
    conn.execute("PRAGMA busy_timeout=60000")   # coexist with live-collector writes
    apply_migrations(conn)
    conn.executemany(
        "INSERT OR REPLACE INTO raw_bse_scrip_master (isin, bse_code, security_name, fetched_at) "
        "VALUES (?,?,?,?)", rows)
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM raw_bse_scrip_master").fetchone()[0]
    matched = conn.execute(
        "SELECT COUNT(*) FROM raw_quote_metadata q "
        "JOIN raw_bse_scrip_master m ON q.isin = m.isin").fetchone()[0]
    conn.close()
    print(f"BSE scrip master: parsed={len(rows)} skipped(no isin/code)={skipped} "
          f"table_total={total}")
    print(f"  our symbols resolving to a BSE code: {matched}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
