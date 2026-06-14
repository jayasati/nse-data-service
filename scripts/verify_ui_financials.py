"""Audit the financial terms the stock page renders, per stock.

Mirrors the dashboard contract (``webcore/services/stock_page.results`` →
``static/js/pages/stock_tabs.js``): non-bank rows show revenue / REV-YoY / other
income / PBT / PAT / PAT-YoY / EBITDA-YoY / EPS; bank rows show PAT / provisions /
PAT-YoY / PPOP-YoY plus NII & asset-quality. For the latest quarter of every
filled symbol it reports which of the EXPECTED terms (by sector) are present, so
a missing field-mapping (e.g. EBITDA-YoY when depreciation isn't extracted)
surfaces for all 1000 at once instead of by clicking through the UI.

    PYTHONPATH=src .venv/bin/python -u scripts/verify_ui_financials.py
    .venv/bin/python -u scripts/verify_ui_financials.py --symbols RELIANCE,SBIN
    .venv/bin/python -u scripts/verify_ui_financials.py --show-missing   # per-stock gaps
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Terms expected on the latest-quarter row, by row shape. (key, source) where
# source is 'f' (extracted_financials column) or 'g' (growth_json key).
_NONBANK = [
    ("revenue_cr", "f"), ("other_income_cr", "f"), ("pbt_cr", "f"),
    ("pat_cr", "f"), ("eps_basic", "f"),
    ("yoy_revenue_pct", "g"), ("yoy_pat_pct", "g"), ("yoy_ebitda_pct", "g"),
]
_BANK = [
    ("pat_cr", "f"), ("net_interest_income_cr", "f"), ("operating_profit_cr", "f"),
    ("provisions_cr", "f"), ("gross_npa_pct", "f"), ("net_npa_pct", "f"),
    ("eps_basic", "f"),
    ("yoy_pat_pct", "g"), ("yoy_ppop_pct", "g"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--scope", default="standalone")
    ap.add_argument("--symbols", help="comma list; default = all filled symbols")
    ap.add_argument("--show-missing", action="store_true",
                    help="print per-stock missing terms")
    args = ap.parse_args()

    from nse_data.storage.db import open_db
    from nse_data.fundamentals.sectors import sector_class_for, SectorClass

    conn = open_db(args.db)
    conn.row_factory = __import__("sqlite3").Row

    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        syms = [r[0] for r in conn.execute(
            "SELECT DISTINCT symbol FROM extracted_financials WHERE strategy='xbrl'")]

    # by sector: counts of stocks fully/partly/empty on their expected terms
    by_sector = defaultdict(lambda: {"n": 0, "full": 0, "partial": 0, "missing_term": defaultdict(int)})
    no_data = []
    checked = 0

    for sym in sorted(syms):
        row = conn.execute(
            "SELECT * FROM extracted_financials WHERE symbol=? AND scope=? "
            "ORDER BY period_ending DESC LIMIT 1", (sym, args.scope)).fetchone()
        if row is None:
            no_data.append(sym)
            continue
        checked += 1
        klass = sector_class_for(sym)
        is_bank = klass == SectorClass.BFSI
        expected = _BANK if is_bank else _NONBANK
        growth = json.loads(row["growth_json"]) if row["growth_json"] else {}

        missing = []
        for key, src in expected:
            val = row[key] if src == "f" else growth.get(key)
            if val is None:
                missing.append(key)

        s = by_sector[klass.value]
        s["n"] += 1
        if not missing:
            s["full"] += 1
        else:
            s["partial"] += 1
            for k in missing:
                s["missing_term"][k] += 1
        if args.show_missing and missing:
            print(f"  {sym:<14} [{klass.value}] missing: {', '.join(missing)}")

    print(f"\n=== UI financial-term coverage ({checked} stocks, scope={args.scope}) ===")
    print(f"{'sector':<10}{'n':>5}{'full':>6}{'partial':>9}   top missing terms")
    print("-" * 70)
    for sec in sorted(by_sector, key=lambda k: -by_sector[k]["n"]):
        s = by_sector[sec]
        top = sorted(s["missing_term"].items(), key=lambda x: -x[1])[:3]
        topstr = ", ".join(f"{k}({n})" for k, n in top)
        print(f"{sec:<10}{s['n']:>5}{s['full']:>6}{s['partial']:>9}   {topstr}")
    if no_data:
        print(f"\nno {args.scope} row for {len(no_data)} symbols: {', '.join(no_data[:20])}"
              + (" …" if len(no_data) > 20 else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
