"""Daily paper-trade / live-signal engine. Runs one or more strategies in parallel,
each accumulating an INDEPENDENT forward, out-of-sample track record in paper_book
(tagged by `strategy`) — the gate no backtest can replace.

Strategies:
  qvm                — the validated Q+V+Momentum dynamic (survivorship-corrected
                       +30% CAGR / Sharpe ~1.5); the live default.
  buyscore_adaptive  — the regime-adaptive integrated Buy Score (grand-prompt v2);
                       backtest leaned positive in the practical range but NOT certified
                       (worse drawdown, lost 1/3 sub-periods) → tracked forward to settle it.

Each run (after the EOD candle update): rebuild the point-in-time eligible universe,
score each strategy over it, then drive ITS positions through the same state machine —
  BUY  : eligible name not held whose score ≥ T_in
  SELL : held name whose score < T_out, OR trails `trail` off its held-peak,
         OR hit max-hold / stop / dropped from the eligible+scored set.

    PYTHONPATH=src .venv/bin/python -u scripts/paper_trade.py                 # all strategies
    PYTHONPATH=src .venv/bin/python -u scripts/paper_trade.py --strategies qvm
"""
from __future__ import annotations

import argparse
import datetime as _dt
import statistics as st
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

WIN, MIN_DAYS, MIN_TURN_CR, MAX_VOL = 252, 200, 5.0, 50.0
QVM_ENGINES = ("quality", "valuation", "momentum")


def _d(s):
    return _dt.date.fromisoformat(s)


def _score_qvm(conn, eligible, ep, sector_of) -> dict:
    """Q+V+Mom composite (mean of percentile-within-sector engine scores), point-in
    -time. Requires a fundamental (Q or V) so funds/ETFs (momentum-only) are excluded."""
    import importlib
    engs = [importlib.import_module(f"nse_data.research.{e}_engine") for e in QVM_ENGINES]
    per = [m.score_universe(conn, eligible, ep, sector_of) for m in engs]
    fund_idx = [i for i, n in enumerate(QVM_ENGINES) if n in ("quality", "valuation")]
    out = {}
    for sym in eligible:
        vals = [p[sym]["score"] for p in per if sym in p]
        if vals and any(sym in per[i] for i in fund_idx):
            out[sym] = round(sum(vals) / len(vals), 1)
    return out


def _score_buyscore_adaptive(conn, eligible, ep, sector_of) -> dict:
    """Regime-adaptive integrated Buy Score (already fund/ETF-guarded)."""
    from nse_data.research import buy_score_engine_adaptive as bsa
    return {s: round(r["score"], 1) for s, r in bsa.score_universe(conn, eligible, ep, sector_of).items()}


STRATEGIES = {
    "qvm": (_score_qvm, "Q+V+Mom"),
    "buyscore_adaptive": (_score_buyscore_adaptive, "BuyScore-Adaptive"),
}


def _run_strategy(conn, key, label, score, today, args, price_now, risk_tag):
    """Drive one strategy's paper_book positions through the state machine + report."""
    open_rows = conn.execute(
        "SELECT id, symbol, entry_date, entry_px, peak_score FROM paper_book "
        "WHERE status='open' AND strategy=?", (key,)).fetchall()
    held = {r[1] for r in open_rows}
    now = int(time.time())
    sells, buys = [], []

    for pid, sym, entry_date, entry_px, peak in open_rows:
        px = price_now(sym)
        sc = score.get(sym)
        peak = max(peak or 0.0, sc if sc is not None else (peak or 0.0))
        hd = (_d(today) - _d(entry_date)).days
        gross = ((px / entry_px - 1) * 100) if (px and entry_px) else 0.0
        reason = ("dropped" if sc is None else "t_out" if sc < args.t_out
                  else "trail" if peak - sc >= args.trail else "stop" if gross <= args.stop
                  else "max_hold" if hd >= args.max_hold else None)
        if reason:
            net = gross - args.cost
            sells.append((sym, hd, net, reason, sc))
            if not args.dry_run:
                conn.execute(
                    "UPDATE paper_book SET status='closed', exit_date=?, exit_px=?, exit_reason=?, "
                    "net_pct=?, last_score=?, peak_score=?, updated_at=? WHERE id=?",
                    (today, px, reason, round(net, 2), sc, round(peak, 1), now, pid))
        elif not args.dry_run:
            conn.execute("UPDATE paper_book SET peak_score=?, last_score=?, updated_at=? WHERE id=?",
                         (round(peak, 1), sc, now, pid))

    for sym, sc in sorted(score.items(), key=lambda kv: -kv[1]):
        if sc >= args.t_in and sym not in held:
            buys.append((sym, sc, price_now(sym)))
            if not args.dry_run:
                conn.execute(
                    "INSERT INTO paper_book (symbol, entry_date, entry_px, entry_score, "
                    "peak_score, last_score, status, strategy, updated_at) "
                    "VALUES (?,?,?,?,?,?, 'open', ?, ?)",
                    (sym, today, price_now(sym), sc, sc, sc, key, now))
    if not args.dry_run:
        conn.commit()

    print(f"\n=== {label} [{key}]  as-of {today}  scored={len(score)} "
          f"{'[DRY-RUN]' if args.dry_run else ''} ===")
    print(f"BUY ({len(buys)}):  " + (", ".join(f"{s}({sc:.0f}){risk_tag(s)}" for s, sc, _ in buys[:20]) or "—"))
    print(f"SELL ({len(sells)}): " + (", ".join(f"{s} {net:+.1f}% [{r}]" for s, _h, net, r, _sc in sells) or "—"))
    cur = conn.execute(
        "SELECT symbol, entry_date, entry_px, last_score FROM paper_book "
        "WHERE status='open' AND strategy=? ORDER BY entry_date", (key,)).fetchall()
    print(f"HOLDINGS ({len(cur)}):")
    for sym, ed, epx, ls in cur[:40]:
        px = price_now(sym)
        unr = ((px / epx - 1) * 100) if (px and epx) else 0.0
        print(f"  {sym:12s} in {ed} ({(_d(today)-_d(ed)).days:>3}d)  {unr:+6.1f}%  score={ls}{risk_tag(sym)}")
    closed = [r[0] for r in conn.execute(
        "SELECT net_pct FROM paper_book WHERE status='closed' AND net_pct IS NOT NULL "
        "AND strategy=?", (key,)).fetchall()]
    if closed:
        wins = [x for x in closed if x > 0]
        print(f"CLOSED: {len(closed)} trades  win={100*len(wins)/len(closed):.0f}%  "
              f"avg={sum(closed)/len(closed):+.2f}%  total={sum(closed):+.1f}%")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--strategies", default=",".join(STRATEGIES),
                    help="comma list: " + ",".join(STRATEGIES))
    ap.add_argument("--t-in", type=float, default=80.0)
    ap.add_argument("--t-out", type=float, default=60.0)
    ap.add_argument("--trail", type=float, default=15.0)
    ap.add_argument("--max-hold", type=int, default=120)
    ap.add_argument("--stop", type=float, default=-15.0)
    ap.add_argument("--cost", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true", help="report signals, don't write the book")
    args = ap.parse_args()

    from nse_data.storage.db import open_db, apply_migrations
    from nse_data.fundamentals.sectors import sector_class_for
    from nse_data.research.risk_engine import risk_raw

    conn = open_db(args.db)
    conn.execute("PRAGMA busy_timeout=60000")
    apply_migrations(conn)

    today, ep = conn.execute(
        "SELECT date(ts,'unixepoch','+05:30') d, MAX(ts) FROM raw_intraday_candles "
        "WHERE symbol='NIFTYBEES' AND interval='day' GROUP BY d ORDER BY d DESC LIMIT 1").fetchone()

    sec_cache: dict = {}
    def sector_of(s):
        if s not in sec_cache:
            sec_cache[s] = sector_class_for(s).value
        return sec_cache[s]

    def price_now(sym):
        r = conn.execute(
            "SELECT close FROM raw_intraday_candles WHERE symbol=? AND interval='day' "
            "AND close IS NOT NULL ORDER BY ts DESC LIMIT 1", (sym,)).fetchone()
        return r[0] if r else None

    # point-in-time eligible universe AS OF today (trailing liquidity + vol), ETFs out.
    eligible = []
    for (sym,) in conn.execute("SELECT symbol FROM tradeable_universe WHERE grade != 'etf'"):
        bars = conn.execute(
            "SELECT close, volume FROM raw_intraday_candles WHERE symbol=? AND interval='day' "
            "ORDER BY ts DESC LIMIT ?", (sym, WIN)).fetchall()
        if len(bars) < MIN_DAYS:
            continue
        tov = [(c * v / 1e7) for c, v in bars if c and v]
        if not tov or st.median(tov) < MIN_TURN_CR:
            continue
        cl = [c for c, _ in bars if c]
        rets = [cl[i] / cl[i + 1] - 1 for i in range(len(cl) - 1) if cl[i + 1]]
        if len(rets) > 5 and st.pstdev(rets) * (252 ** 0.5) * 100 >= MAX_VOL:
            continue
        eligible.append(sym)

    _rk: dict = {}
    def risk_tag(sym):
        if sym not in _rk:
            _rk[sym] = risk_raw(conn, sym, ep)
        r = _rk[sym]
        if r["score"] >= 100 or not r["components"]:
            return ""
        top = max(r["components"], key=r["components"].get)
        return f" ⚠risk{r['score']:.0f}[{top}]"

    print(f"paper-trade as-of {today}  eligible={len(eligible)}")
    for key in [s.strip() for s in args.strategies.split(",") if s.strip() in STRATEGIES]:
        scorer, label = STRATEGIES[key]
        score = scorer(conn, eligible, ep, sector_of)
        _run_strategy(conn, key, label, score, today, args, price_now, risk_tag)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
