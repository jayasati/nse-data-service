"""Per-stock Buy Decision card (grand-prompt v2) — integrates every engine into one
explainable verdict. Tested on a single stock:

    PYTHONPATH=src .venv/bin/python -u scripts/buy_card.py --symbol HDFCBANK

Point-in-time: scores the universe as-of the date (cross-sectional percentiles) and
reads the factor_snapshot history for score velocity/acceleration. Combines via
regime-adaptive weights, gates on Trend (the value-trap fix), classifies, and prints
Buy/Hold/Reduce/Exit with drivers + a heuristic reward/risk + suggested size.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--symbol", default="HDFCBANK")
    ap.add_argument("--horizon", type=int, default=60)
    args = ap.parse_args()
    sym = args.symbol.upper()

    from nse_data.storage.db import open_db
    from nse_data.research import snapshot, buy_score as bs
    from nse_data.research.score import as_of_now_epoch
    from nse_data.fundamentals.sectors import sector_class_for

    conn = open_db(args.db)
    ep = as_of_now_epoch()
    today = _dt.datetime.fromtimestamp(ep, _IST).date().isoformat()
    grade_of = {r[0]: r[1] for r in conn.execute("SELECT symbol, grade FROM tradeable_universe")}
    universe = [s for s, g in grade_of.items() if g in ("A core", "B tradeable", "C volatile")]
    if sym not in grade_of:
        print(f"{sym}: not in tradeable_universe"); return 1
    sec_cache: dict = {}
    def sector_of(s):
        if s not in sec_cache:
            sec_cache[s] = sector_class_for(s).value
        return sec_cache[s]

    # regime → adaptive weights
    try:
        from nse_data.market.regime_job import latest_market_state
        regime = (latest_market_state(conn) or {}).get("overall_regime")
    except Exception:  # noqa: BLE001
        regime = None

    rows = snapshot.compute_snapshot(conn, universe, ep, sector_of)
    f = rows.get(sym)
    if not f:
        print(f"{sym}: no factor coverage as-of {today}"); return 1
    vol = conn.execute("SELECT ann_vol_pct FROM tradeable_universe WHERE symbol=?", (sym,)).fetchone()
    card = bs.assemble_card(conn, sym, f, regime, vol[0] if vol else None, today, args.horizon)

    def line(k, v):
        print(f"  {k:<22} {v}")
    print(f"\n=== BUY DECISION CARD — {sym} ({card['sector']}, grade {grade_of[sym]}) "
          f"as-of {today} ===")
    m = card["macro"]
    line("Macro (Engine 1)", f"{m['state']}  score={m['score']}  VIX={m['vix']}  "
         f"comps={m['components']}  (missing: {', '.join(m['missing'])})")
    line("Regime", f"{regime or 'n/a'}  → weights={card['weights']}")
    print("  --- factor scores (0-100, cross-sectional, point-in-time) ---")
    for k, v in card["factors"].items():
        line(k.capitalize(), "—" if v is None else f"{v:.1f}")
    line("Sector rank", f"{card['sector_rank']}/{card['sector_n']}")
    print("  --- decision ---")
    line("Opportunity (Q+V)", "—" if card["opportunity"] is None else f"{card['opportunity']:.1f}")
    line("BUY SCORE", f"{card['buy_score']}   (contrib={card['contributions']})")
    line("Score velocity (~20d)", "—" if card["velocity"] is None else
         f"{card['velocity']:+.1f}  accel={card['acceleration']:+.1f}  (hist {card['history_days']}d)")
    line("Confidence", "—" if card["confidence"] is None else f"{card['confidence']:.0f}%")
    line("Classification", card["classification"])
    line("VERDICT", card["verdict"])
    line("Top positive", ", ".join(card["drivers_positive"]) or "—")
    line("Top negative", ", ".join(card["drivers_negative"]) or "—")
    line(f"Reward/Risk ({args.horizon}d)*",
         f"up {card['expected_upside_pct']:+.1f}% / down {card['expected_drawdown_pct']:+.1f}%  RR={card['reward_risk']}")
    line("Suggested allocation*", f"{card['suggested_allocation_pct']}%")
    print("  * reward/risk + sizing are heuristics (volatility-scaled), NOT yet "
          "backtest-calibrated.\n")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
