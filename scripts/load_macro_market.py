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

    # Geopolitical Risk (Caldara-Iacoviello) — daily .xls, the one reliable free feed.
    try:
        import io
        import requests
        import pandas as pd
        c = requests.get("https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=90).content
        g = pd.read_excel(io.BytesIO(c))
        g = g.dropna(subset=["date"]).tail(800)             # recent history is enough
        for _, row in g.iterrows():
            d = row["date"].date().isoformat() if hasattr(row["date"], "date") else str(row["date"])[:10]
            v = row.get("GPRD_MA7", row.get("GPRD"))         # 7-day smoothed
            if v == v:                                       # not NaN
                series.setdefault(d, {})["gpr"] = float(v)
    except Exception as e:  # noqa: BLE001
        print(f"WARN gpr fetch failed: {e}")

    # CPI YoY (best-effort; FRED/data.gov.in are unreliable from ap-south-1 — non-fatal)
    try:
        import io
        import requests
        import pandas as pd
        t = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=INDCPALTT02GYM",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=20).text
        df = pd.read_csv(io.StringIO(t)).dropna()
        last = df.iloc[-1]
        series.setdefault(str(last.iloc[0])[:10], {})["cpi_yoy"] = float(last.iloc[1])
    except Exception as e:  # noqa: BLE001
        print(f"WARN cpi fetch failed (best-effort): {e}")

    now = int(time.time())
    cols = ("usdinr", "brent", "gpr", "cpi_yoy")
    sets = ", ".join(f"{c}=COALESCE(excluded.{c}, {c})" for c in cols)
    n = 0
    for d, v in sorted(series.items()):
        conn.execute(
            f"INSERT INTO raw_macro_market (date, {','.join(cols)}, fetched_at) "
            f"VALUES (?,?,?,?,?,?) ON CONFLICT(date) DO UPDATE SET {sets}, "
            "fetched_at=excluded.fetched_at",
            (d, v.get("usdinr"), v.get("brent"), v.get("gpr"), v.get("cpi_yoy"), now))
        n += 1
    conn.commit()
    last = conn.execute("SELECT date, usdinr, brent, gpr, cpi_yoy FROM raw_macro_market "
                        "WHERE gpr IS NOT NULL ORDER BY date DESC LIMIT 1").fetchone()
    print(f"raw_macro_market: upserted {n} dates; latest-with-gpr {last}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
