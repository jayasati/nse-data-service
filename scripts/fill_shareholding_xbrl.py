"""Parse each symbol's shareholding XBRL (the master's xbrl_url) into the detailed
FII/DII/MF/promoter split → raw_shareholding_quarterly, keyed by (symbol, qe_date).
Populates the current quarter for the universe + seeds the quarterly history the
Ownership engine needs (run it every quarter to accumulate deltas). Idempotent.

    PYTHONPATH=src .venv/bin/python -u scripts/fill_shareholding_xbrl.py --sleep 0.5
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--symbols", help="comma list (default: all in the master)")
    ap.add_argument("--sleep", type=float, default=0.5)
    ap.add_argument("--only-missing", action="store_true", default=True,
                    help="skip (symbol,qe_date) already parsed (default on)")
    args = ap.parse_args()

    import httpx
    from nse_data.storage.db import open_db, apply_migrations
    from nse_data.parsers.shp_xbrl import parse_shp

    conn = open_db(args.db)
    conn.execute("PRAGMA busy_timeout=60000")
    apply_migrations(conn)

    q = "SELECT symbol, qe_date, xbrl_url FROM raw_shareholding_pattern WHERE xbrl_url IS NOT NULL"
    rows = conn.execute(q).fetchall()
    if args.symbols:
        want = {s.strip().upper() for s in args.symbols.split(",")}
        rows = [r for r in rows if r[0] in want]
    done = {(s, d) for s, d in conn.execute(
        "SELECT symbol, qe_date FROM raw_shareholding_quarterly")}

    cl = httpx.Client(headers={"User-Agent": _UA, "Referer": "https://www.nseindia.com/"},
                      timeout=30, follow_redirects=True)
    n_ok = n_skip = n_err = 0
    for i, (sym, qe, url) in enumerate(rows, 1):
        if args.only_missing and (sym, qe) in done:
            n_skip += 1
            continue
        try:
            f = parse_shp(cl.get(url).text)
        except Exception:  # noqa: BLE001
            f = None
        if not f:
            n_err += 1
        else:
            conn.execute(
                "INSERT OR REPLACE INTO raw_shareholding_quarterly (symbol, qe_date, "
                "promoter_pct, public_pct, fii_pct, dii_pct, mf_pct, xbrl_url, fetched_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (sym, qe, f.get("promoter_pct"), f.get("public_pct"), f.get("fii_pct"),
                 f.get("dii_pct"), f.get("mf_pct"), url, int(time.time())))
            n_ok += 1
        if i % 100 == 0:
            conn.commit()
            print(f"  [{i}/{len(rows)}] ok={n_ok} skip={n_skip} err={n_err}", flush=True)
        time.sleep(args.sleep)
    conn.commit()
    tot = conn.execute("SELECT COUNT(*) FROM raw_shareholding_quarterly").fetchone()[0]
    print(f"DONE: parsed={n_ok} skipped={n_skip} no-parse={n_err}  table_total={tot}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
