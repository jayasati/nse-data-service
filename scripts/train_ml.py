"""P7 — train + walk-forward-evaluate the ML layer, then emit a live ranked
leaderboard. DEFAULT target = RANKING the continuous forward sector-excess (the
institutional "learn the ranking, not BUY/SELL" target); --target binary keeps the
P(outperform) classifier.

    PYTHONPATH=src .venv/bin/python -u scripts/train_ml.py                 # ridge ranker
    PYTHONPATH=src .venv/bin/python -u scripts/train_ml.py --model hgb     # GBM ranker
    PYTHONPATH=src .venv/bin/python -u scripts/train_ml.py --target binary --model logistic

Source of the matrix: --source history self-bootstraps by replaying engines over a
date grid (works today); --source store reads the factor_snapshot feature store
(use once it has accrued enough dates). Either way the split is temporal + embargoed.

Honest by construction: for ranking the headline is OOS **Rank IC** (Spearman of the
prediction vs realised excess). Rank IC ≲ 0.03 or a non-monotone decile lift = NO
certified edge — don't act on it (the same bar the engines face).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--target", default="rank", choices=["rank", "binary"])
    ap.add_argument("--model", default=None, help="rank: ridge|hgb|xgb|lgbm  binary: logistic|hgb|xgb|lgbm")
    ap.add_argument("--source", default="history", choices=["history", "store"])
    ap.add_argument("--horizon", type=int, default=60)
    ap.add_argument("--step", type=int, default=20)
    ap.add_argument("--lookback", type=int, default=600)
    ap.add_argument("--cost", type=float, default=0.50)
    ap.add_argument("--out", default="data/ml_model.json")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()
    is_rank = args.target == "rank"
    model_kind = args.model or ("ridge" if is_rank else "logistic")

    import numpy as np
    from nse_data.storage.db import open_db
    from nse_data.ml import trainer, eval as mleval
    from nse_data.ml.scorer import make_ranker, make_model
    from nse_data.research.score import as_of_now_epoch
    from nse_data.fundamentals.sectors import sector_class_for

    conn = open_db(args.db)
    print(f"building dataset (target={args.target}, source={args.source}, horizon={args.horizon}d, "
          f"step={args.step}, lookback={args.lookback}, cost={args.cost}%)...")
    if args.source == "store":
        X, y, fwd, dates, _syms, fnames = trainer.load_store_dataset(conn, horizon=args.horizon)
    else:
        X, y, fwd, dates, _syms, fnames = trainer.build_dataset(
            conn, step=args.step, lookback=args.lookback, horizon=args.horizon, cost=args.cost)
    print(f"dataset: n={len(fwd)} rows  over {len(set(dates.tolist()))} as-of dates  "
          f"mean_excess={fwd.mean():+.2f}%  features={fnames}\n")
    if len(fwd) < 200:
        print("too few rows to train/validate honestly — gather more history first.")
        return 1

    tr, te = trainer.temporal_split(dates)
    if te.sum() < 50 or tr.sum() < 50:
        print(f"split too thin (train={int(tr.sum())}, test={int(te.sum())}).")
        return 1

    if is_rank:
        model = make_ranker(model_kind, feature_names=fnames)
        model.fit(X[tr], fwd[tr])                      # learn the continuous excess
        pred = model.predict(X[te])
        s = mleval.rank_summary(pred, fwd[te])
        print(f"=== OUT-OF-SAMPLE rank ({model_kind}) — train={int(tr.sum())} test={int(te.sum())} ===")
        print(f"  Rank IC = {s['rank_ic']:+.3f}   (Spearman of prediction vs realised excess)")
        print("  decile mean realised excess (low→high pred):  "
              + "  ".join(f"{x:+.1f}" for x in s["decile_lift"]))
        print(f"  top-minus-bottom decile = {s['top_minus_bottom']:+.2f}%   monotone={s['monotone']}")
        ok = s["rank_ic"] >= 0.03 and s["monotone"] and s["top_minus_bottom"] > 0
    else:
        model = make_model(model_kind, feature_names=fnames)
        model.fit(X[tr], y[tr])
        pred = model.predict_proba(X[te])
        s = mleval.summary(y[te], pred, fwd[te])
        print(f"=== OUT-OF-SAMPLE binary ({model_kind}) — train={int(tr.sum())} test={int(te.sum())} ===")
        print(f"  base_rate={s['base_rate']:.3f}  AUC={s['auc']:.3f}  Rank IC={s['ic']:+.3f}")
        print("  decile mean fwd-excess (low P→high P):  "
              + "  ".join(f"{x:+.1f}" for x in s["decile_lift"]))
        print(f"  top-minus-bottom decile = {s['top_minus_bottom']:+.2f}%   monotone={s['monotone']}")
        ok = s["auc"] >= 0.53 and s["monotone"] and s["top_minus_bottom"] > 0
    print(f"  VERDICT: {'SIGNAL (OOS)' if ok else 'NO certified edge'}\n")

    imp = model.importances()
    if imp:
        print("  factor weights (sign = direction, |size| = pull):")
        for k, v in imp.items():
            print(f"    {k:<11} {v:+.3f}")
    model.save(args.out)
    print(f"\n  saved -> {args.out}")

    # --- live leaderboard as-of now ---
    sec = {}
    def sector_of(x):
        if x not in sec:
            sec[x] = sector_class_for(x).value
        return sec[x]
    engines = trainer._load_engines()
    universe = [r[0] for r in conn.execute(
        "SELECT symbol FROM tradeable_universe WHERE grade IN ('A core','B tradeable')")]
    ep = as_of_now_epoch()
    scored = {name: mod.score_universe(conn, universe, ep, sector_of) for name, mod in engines}
    live = []
    for sym in universe:
        feats = [scored[n].get(sym, {}).get("score", trainer.NEUTRAL) for n in fnames]
        if all(v == trainer.NEUTRAL for v in feats):
            continue
        x = np.asarray([feats], float)
        val = float(model.predict(x)[0]) if is_rank else float(model.predict_proba(x)[0])
        live.append((sym, val))
    live.sort(key=lambda kv: -kv[1])
    label = f"predicted excess {args.horizon}d" if is_rank else f"P(outperform {args.horizon}d)"
    print(f"\n=== live ranking — {label} — top {args.top} of {len(live)} ===")
    for sym, v in live[:args.top]:
        shown = f"{v:+5.1f}%" if is_rank else f"{v*100:5.1f}%"
        print(f"  {sym:<12} {shown}   {sector_of(sym)}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
