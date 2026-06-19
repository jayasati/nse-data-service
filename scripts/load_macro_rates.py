#!/usr/bin/env python
"""Load policy/benchmark rates into raw_macro_rates (Week 17.5, S6).

Two free, working routes — no live scraper required:

  # 1. Manual entry — the right call for the repo rate (changes ~6×/year at RBI
  #    MPC meetings) and fine for an occasional 10Y point:
  python scripts/load_macro_rates.py set 2026-03-31 --repo 6.00 --gsec10y 6.98

  # 2. CSV import — download the 10Y series from FBIL (fbil.org.in) or RBI DBIE
  #    (dbie.rbi.org.in) and import it (columns auto-detected):
  python scripts/load_macro_rates.py csv ~/Downloads/gsec_10y.csv
  python scripts/load_macro_rates.py csv f.csv --date-col Date --gsec-col "10Y Yield"

  # inspect the derived risk state:
  python scripts/load_macro_rates.py state
"""
from __future__ import annotations

import argparse

from nse_data.market import macro_rates as mr
from nse_data.storage.db import apply_migrations, open_db


def _fetch_rbi():
    """Scrape the RBI homepage (India-hosted, static HTML, reachable from ap-south-1)
    for the policy repo rate + the ~10y G-sec benchmark yield (the listed GS maturing
    closest to year+10). Returns (repo, gsec10y); either may be None."""
    import datetime
    import re
    import requests
    t = requests.get("https://www.rbi.org.in/", headers={"User-Agent": "Mozilla/5.0"},
                     timeout=30).text
    m = re.search(r"Policy Repo Rate\s*</th>\s*<td>\s*:?\s*([0-9.]+)", t)
    repo = float(m.group(1)) if m else None
    target = datetime.date.today().year + 10
    best = None                                          # (|year-target|, yield)
    for cm in re.finditer(r"GS (20[0-9]{2})\s*</th>\s*<td>\s*:?\s*([0-9.]+)", t):
        yr, yld = int(cm.group(1)), float(cm.group(2))
        if best is None or abs(yr - target) < best[0]:
            best = (abs(yr - target), yld)
    return repo, (best[1] if best else None)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/nse.db")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("set", help="manually record one day's rates")
    s.add_argument("date", help="as-of date, YYYY-MM-DD")
    s.add_argument("--repo", type=float, default=None, help="RBI repo rate, %%")
    s.add_argument("--gsec10y", type=float, default=None, help="10Y G-sec yield, %%")
    s.add_argument("--source", default="manual")

    c = sub.add_parser("csv", help="import a FBIL/DBIE CSV of the 10Y series")
    c.add_argument("path")
    c.add_argument("--date-col", default=None)
    c.add_argument("--gsec-col", default=None)
    c.add_argument("--repo-col", default=None)
    c.add_argument("--source", default="csv")

    sub.add_parser("state", help="print the derived macro risk state")
    sub.add_parser("rbi", help="auto-scrape repo + 10y from the RBI homepage")

    args = ap.parse_args()
    conn = open_db(args.db)
    conn.execute("PRAGMA busy_timeout=60000")
    apply_migrations(conn, "migrations")

    if args.cmd == "rbi":
        import datetime
        repo, gsec10y = _fetch_rbi()
        if repo is None and gsec10y is None:
            print("RBI scrape found nothing (page format changed?)")
        else:
            d = datetime.date.today().isoformat()
            mr.record_rates(conn, d, repo_rate=repo, gsec_10y_yield=gsec10y, source="rbi")
            print(f"recorded {d}: repo={repo} gsec10y={gsec10y} (rbi.org.in)")
    elif args.cmd == "set":
        if args.repo is None and args.gsec10y is None:
            ap.error("give at least one of --repo / --gsec10y")
        mr.record_rates(conn, args.date, repo_rate=args.repo,
                        gsec_10y_yield=args.gsec10y, source=args.source)
        print(f"recorded {args.date}: repo={args.repo} gsec10y={args.gsec10y}")
    elif args.cmd == "csv":
        rep = mr.import_rates_csv(
            conn, args.path, date_col=args.date_col,
            gsec_col=args.gsec_col, repo_col=args.repo_col, source=args.source,
        )
        print(f"imported {rep['imported']}/{rep['rows']} rows "
              f"(date={rep.get('date_col')}, gsec={rep.get('gsec_col')}, repo={rep.get('repo_col')})")
    elif args.cmd == "state":
        for k, v in mr.macro_state(conn).items():
            print(f"  {k}: {v}")
    conn.close()


if __name__ == "__main__":
    main()
