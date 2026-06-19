"""Weekly ranking-model retrain + feature-store health report (EC2 cron).

Run by the nse-retrain@.timer on the always-on host. Retrains the ridge ranker on
the freshest data — from the `factor_snapshot` store once it has enough labelled
dates, else self-bootstrapped from history — measures OOS Rank IC, saves the model,
and Telegrams a one-glance summary so the store's growth and the model's edge are
tracked without anyone having to remember to look.

    PYTHONPATH=src .venv/bin/python -u scripts/retrain_report.py [--horizon 60]

Read-only on everything except data/ml_model.json. Never raises to the cron: any
failure is caught and reported (a silent dead cron is worse than a failure ping).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

STORE_MIN_DATES = 8        # use the feature store only once it has this many labelled dates


def _build(conn, horizon, lookback, step, cost):
    """Prefer the matured feature store; fall back to history self-bootstrap.
    Returns (X, y, fwd, dates, syms, fnames, source)."""
    from nse_data.ml import trainer
    X, y, fwd, dates, syms, fnames = trainer.load_store_dataset(conn, horizon=horizon)
    if len(fwd) and len(set(dates.tolist())) >= STORE_MIN_DATES:
        return X, y, fwd, dates, syms, fnames, "store"
    X, y, fwd, dates, syms, fnames = trainer.build_dataset(
        conn, step=step, lookback=lookback, horizon=horizon, cost=cost)
    return X, y, fwd, dates, syms, fnames, "history"


def _report(db, horizon, lookback, step, cost, out):
    from nse_data.storage.db import open_db
    from nse_data.ml import trainer, eval as mleval
    from nse_data.ml.scorer import make_ranker

    conn = open_db(db)
    rows = conn.execute("SELECT COUNT(*) FROM factor_snapshot").fetchone()[0]
    sdates = conn.execute("SELECT COUNT(DISTINCT snapshot_date) FROM factor_snapshot").fetchone()[0]
    ldates = conn.execute(f"SELECT COUNT(DISTINCT snapshot_date) FROM factor_snapshot "
                          f"WHERE fwd_excess_{horizon} IS NOT NULL").fetchone()[0]

    X, y, fwd, dates, _s, fnames, source = _build(conn, horizon, lookback, step, cost)
    head = (f"📊 Ranking retrain ({horizon}d) — {sdates} store dates "
            f"({ldates} labelled), {rows} rows")
    if len(fwd) < 200:
        conn.close()
        return head + f"\nsource={source}: only {len(fwd)} rows — too thin to validate yet."

    tr, te = trainer.temporal_split(dates)
    if te.sum() < 50 or tr.sum() < 50:
        conn.close()
        return head + f"\nsource={source}: split too thin (train={int(tr.sum())}, test={int(te.sum())})."

    model = make_ranker("ridge", feature_names=fnames).fit(X[tr], fwd[tr])
    s = mleval.rank_summary(model.predict(X[te]), fwd[te])
    model.save(out)
    ok = s["rank_ic"] >= 0.03 and s["monotone"] and s["top_minus_bottom"] > 0
    top = "  ".join(f"{k}{v:+.2f}" for k, v in list(model.importances().items())[:4])
    return (head
            + f"\nsource={source}  n={len(fwd)} (tr {int(tr.sum())}/te {int(te.sum())})"
            + f"\nOOS Rank IC = {s['rank_ic']:+.3f}   decile spread {s['top_minus_bottom']:+.1f}%"
              f"   mono={s['monotone']}"
            + f"\nverdict: {'SIGNAL (OOS)' if ok else 'no certified edge yet'}"
            + f"\ntop: {top}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--horizon", type=int, default=60)
    ap.add_argument("--lookback", type=int, default=500)
    ap.add_argument("--step", type=int, default=20)
    ap.add_argument("--cost", type=float, default=0.50)
    ap.add_argument("--out", default="data/ml_model.json")
    ap.add_argument("--no-telegram", action="store_true")
    args = ap.parse_args()

    try:
        msg = _report(args.db, args.horizon, args.lookback, args.step, args.cost, args.out)
    except Exception as e:  # noqa: BLE001 — never kill the cron silently
        import traceback
        traceback.print_exc()
        msg = f"⚠️ Ranking retrain FAILED: {type(e).__name__}: {e}"

    print(msg)
    if not args.no_telegram:
        try:
            from nse_data.bot.dispatcher import load_telegram_config, send_telegram
            token, chat_id = load_telegram_config()
            send_telegram(token, chat_id, msg)
        except Exception:  # noqa: BLE001
            import traceback
            traceback.print_exc()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
