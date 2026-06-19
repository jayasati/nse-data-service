"""Load daily USDINR + Brent crude into raw_macro_market (for the Macro Shock engine's
currency + oil shock). Free data via yfinance (USDINR=INR=X, Brent=BZ=F). Idempotent
upsert; run daily (cron) or ad-hoc to backfill history.

    PYTHONPATH=src .venv/bin/python -u scripts/load_macro_market.py [--period 2y]
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
    ap.add_argument("--period", default="2y")
    args = ap.parse_args()

    import yfinance as yf
    from nse_data.storage.db import open_db, apply_migrations

    conn = open_db(args.db)
    conn.execute("PRAGMA busy_timeout=60000")
    apply_migrations(conn)

    series = {}
    for col, tk in (("usdinr", "INR=X"), ("brent", "BZ=F")):
        try:
            h = yf.Ticker(tk).history(period=args.period)
            for ts, row in h.iterrows():
                d = ts.date().isoformat()
                series.setdefault(d, {})[col] = float(row["Close"])
        except Exception as e:  # noqa: BLE001
            print(f"WARN {col} ({tk}) fetch failed: {e}")

    now = int(time.time())
    n = 0
    for d, v in sorted(series.items()):
        conn.execute(
            "INSERT INTO raw_macro_market (date, usdinr, brent, fetched_at) VALUES (?,?,?,?) "
            "ON CONFLICT(date) DO UPDATE SET usdinr=COALESCE(excluded.usdinr, usdinr), "
            "brent=COALESCE(excluded.brent, brent), fetched_at=excluded.fetched_at",
            (d, v.get("usdinr"), v.get("brent"), now))
        n += 1
    conn.commit()
    last = conn.execute("SELECT date, usdinr, brent FROM raw_macro_market ORDER BY date DESC LIMIT 1").fetchone()
    print(f"raw_macro_market: upserted {n} dates; latest {last}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
