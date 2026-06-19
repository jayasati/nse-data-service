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
    weights = bs.REGIME_WEIGHTS.get((regime or "neutral").lower(), bs.REGIME_WEIGHTS["neutral"])

    rows = snapshot.compute_snapshot(conn, universe, ep, sector_of)
    f = rows.get(sym)
    if not f:
        print(f"{sym}: no factor coverage as-of {today}"); return 1
    conf = f.get("confidence")
    buy, contrib = bs.buy_raw(f, weights)
    vel, accel, hist_n = bs.velocity_accel(conn, sym, weights, today)
    cls = bs.classify(f)
    action, pos, neg = bs.verdict(buy, f, vel, regime, conf)

    # heuristic reward/risk + sizing (volatility from tradeable_universe)
    vol = conn.execute("SELECT ann_vol_pct FROM tradeable_universe WHERE symbol=?", (sym,)).fetchone()
    annv = (vol[0] if vol and vol[0] else 30.0) / 100.0
    sig = annv * (args.horizon / 252.0) ** 0.5 * 100      # ~1σ move over horizon (%)
    edge = ((buy or 50) - 50) / 50.0                       # -1..+1
    exp_dd = -round(sig * (1.2 - 0.4 * max(0, edge)), 1)    # downside ~1σ, lighter if strong
    exp_up = round(sig * max(0.0, edge) * 1.8, 1)
    rr = round(exp_up / abs(exp_dd), 2) if exp_dd else None
    buyable = action.startswith(("BUY", "STRONG"))
    base_alloc = max(0.0, ((buy or 0) - 50) / 5.0)         # 60→2%, 70→4%, 80→6%, 90→8%
    alloc = round(base_alloc * (conf or 60) / 100.0, 1) if buyable else 0.0

    def line(k, v):
        print(f"  {k:<22} {v}")
    print(f"\n=== BUY DECISION CARD — {sym} ({sector_of(sym)}, grade {grade_of[sym]}) "
          f"as-of {today} ===")
    line("Regime", f"{regime or 'n/a'}  → weights={ {k: weights[k] for k in bs.COMPONENTS} }")
    print("  --- factor scores (0-100, cross-sectional, point-in-time) ---")
    for k in ("quality", "valuation", "momentum", "surprise", "catalyst", "turnaround",
              "liquidity", "risk", "confidence"):
        v = f.get(k)
        line(k.capitalize(), "—" if v is None else f"{v:.1f}")
    line("Sector rank", f"{f.get('sector_rank')}/{f.get('sector_n')}")
    print("  --- decision ---")
    line("Opportunity (Q+V)", "—" if bs._opportunity(f) is None else f"{bs._opportunity(f):.1f}")
    line("BUY SCORE", f"{buy}   (contrib={contrib})")
    line("Score velocity (~20d)", "—" if vel is None else f"{vel:+.1f}  accel={accel:+.1f}  (hist {hist_n}d)")
    line("Confidence", "—" if conf is None else f"{conf:.0f}%")
    line("Classification", cls)
    line("VERDICT", action)
    line("Top positive", ", ".join(pos) or "—")
    line("Top negative", ", ".join(neg) or "—")
    line(f"Reward/Risk ({args.horizon}d)*", f"up {exp_up:+.1f}% / down {exp_dd:+.1f}%  RR={rr}")
    line("Suggested allocation*", f"{alloc}%")
    print("  * reward/risk + sizing are heuristics (volatility-scaled), NOT yet "
          "backtest-calibrated.\n")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
