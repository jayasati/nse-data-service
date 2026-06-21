#!/usr/bin/env python3
"""Run the intraday high-conviction synthesis engine and print the 13-section report.

    PYTHONPATH=src python scripts/conviction.py

Synthesis over collected data only — DATA_GAP markers where inputs are missing (never invented).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nse_data.research.conviction import run_conviction  # noqa: E402


def _bias(m):
    if m.get("status") != "ok":
        return "Macro regime: DATA_GAP — global macro collector not active. Regime withheld."
    return (f"{m['regime']} · gap-bias {m['gap_bias']} (GIFT {m['gift_gap_pct']:+}%) · "
            f"US S&P {m['us_spx_pct']:+}% · US VIX {m['us_vix']} ({m['us_vix_pct']:+}%) · "
            f"India VIX {m['india_vix']} · Nifty {m['nifty_last']} ({m['nifty_pct']:+}%)")


def main() -> int:
    conn = sqlite3.connect(str(ROOT / "data/nse.db"))
    r = run_conviction(conn)
    ranked = r["ranked"]
    dirn = lambda x: "LONG" if x["stages"]["options"].get("max_pain_drift_pct", 0) >= 0 \
        and x["stages"]["structure"].get("range_pos_pct", 50) < 80 else "SHORT"

    print("=" * 70)
    print("1. MARKET BIAS")
    print("   " + _bias(r["macro"]))
    print(f"   Smart-money score: {r['smart_money_score']} (>0.7 accumulation, <0.4 distribution)")
    print(f"\n2. UNIVERSE: {r['n']} F&O names scored (options-covered set)")

    print("\n3. TOP-12 WATCHLIST (composite · tier · gaps)")
    print(f"   {'sym':11} {'comp':>4} {'tier':>5} {'cat':>4} {'pos':>4} {'opt':>4} {'str':>4} "
          f"{'vol':>4} {'rs':>4} {'vx':>4}  data_gaps")
    g = lambda x, s: ("—" if x["stages"][s]["score"] == "DATA_GAP" else x["stages"][s]["score"])
    for x in ranked[:12]:
        print(f"   {x['symbol']:11} {x['composite']:>4} {x['tier'][:5]:>5} "
              f"{g(x,'catalyst'):>4} {g(x,'positioning'):>4} {g(x,'options'):>4} {g(x,'structure'):>4} "
              f"{g(x,'volume'):>4} {g(x,'rel_strength'):>4} {g(x,'vol_expansion'):>4}  "
              f"{','.join(x['data_gaps']) or 'none'}")

    longs = [x for x in ranked if dirn(x) == "LONG"][:6]
    shorts = sorted([x for x in ranked if dirn(x) == "SHORT"], key=lambda x: x["composite"])[:6]
    print("\n4. TOP LONGS:", ", ".join(f"{x['symbol']}({x['composite']})" for x in longs))
    print("5. TOP SHORTS:", ", ".join(f"{x['symbol']}({x['composite']})" for x in shorts))

    top = ranked[0]
    print(f"\n6. HIGHEST-CONVICTION: {top['symbol']} (composite {top['composite']}, tier {top['tier']})")
    for s, d in top["stages"].items():
        print(f"     {s:13} {d['score']!s:9} src={d.get('src')}")

    # 11. trade to avoid = most DATA_GAPs
    worst = max(ranked, key=lambda x: len(x["data_gaps"]))
    print(f"\n11. TRADE TO AVOID: {worst['symbol']} — {len(worst['data_gaps'])} DATA_GAPs "
          f"({','.join(worst['data_gaps'])}); price action not trustworthy without these.")

    print("\n13. COLLECTOR GAP REPORT (degraded stages today)")
    from collections import Counter
    gapc = Counter(g for x in ranked for g in x["data_gaps"])
    aplus = sum(1 for x in ranked if x["tier"] == "A+")
    print(f"   A+ candidates: {aplus} (A+ requires zero gaps in catalyst/positioning/options + comp≥7.5)")
    for stage, n in gapc.most_common():
        print(f"   {stage:13} DATA_GAP on {n}/{r['n']} names")
    print("   Known structural gaps: per-stock FII futures (participant_oi is index-only); "
          "IV-percentile (no IV history); options only for the top-50 F&O set.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
