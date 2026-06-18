"""Daily paper-trade / live-signal engine for the validated Q+V+Momentum dynamic
strategy (the most robust result: survivorship-corrected +30% CAGR, Sharpe 1.49).

Each run (after the EOD candle update): rebuild the point-in-time eligible universe
as of the latest trading day, score the Q+V+Momentum composite over it, then drive
the paper_book through the SAME state machine the backtest validated —
  BUY  : eligible name not held whose composite ≥ T_in
  SELL : held name whose composite < T_out, OR trails `trail` off its held-peak,
         OR hit max-hold / stop / fell out of the eligible+scored set
and report today's BUY/SELL signals + current holdings + running realised P&L.

This accumulates a FORWARD, out-of-sample track record — the gate no backtest can
replace. Run it daily (cron) after candles update. Idempotent within a day.

    PYTHONPATH=src .venv/bin/python -u scripts/paper_trade.py
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
ENGINES = ("quality", "valuation", "momentum")


def _d(s):
    return _dt.date.fromisoformat(s)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--t-in", type=float, default=80.0)
    ap.add_argument("--t-out", type=float, default=60.0)
    ap.add_argument("--trail", type=float, default=15.0)
    ap.add_argument("--max-hold", type=int, default=120)
    ap.add_argument("--stop", type=float, default=-15.0)
    ap.add_argument("--cost", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true", help="report signals, don't write the book")
    args = ap.parse_args()

    import importlib
    from nse_data.storage.db import open_db, apply_migrations
    from nse_data.research import edge_stats
    from nse_data.fundamentals.sectors import sector_class_for
    engs = [importlib.import_module(f"nse_data.research.{e}_engine") for e in ENGINES]

    conn = open_db(args.db)
    conn.execute("PRAGMA busy_timeout=60000")
    apply_migrations(conn)

    # latest trading day + its epoch (point-in-time as-of)
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

    # point-in-time eligible universe AS OF today (trailing liquidity + vol)
    universe = [s for (s,) in conn.execute("SELECT symbol FROM tradeable_universe")]
    eligible = []
    for sym in universe:
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

    # composite = mean of the engine scores (percentile within sector), point-in-time
    per = [m.score_universe(conn, eligible, ep, sector_of) for m in engs]
    score = {}
    for sym in eligible:
        vals = [p[sym]["score"] for p in per if sym in p]
        if vals:
            score[sym] = round(sum(vals) / len(vals), 1)

    open_rows = conn.execute(
        "SELECT id, symbol, entry_date, entry_px, peak_score FROM paper_book WHERE status='open'"
    ).fetchall()
    held = {r[1] for r in open_rows}
    now = int(time.time())
    sells, buys = [], []

    # ---- evaluate exits on current holdings -------------------------------
    for pid, sym, entry_date, entry_px, peak in open_rows:
        px = price_now(sym)
        sc = score.get(sym)
        peak = max(peak or 0.0, sc if sc is not None else (peak or 0.0))
        hd = (_d(today) - _d(entry_date)).days
        gross = ((px / entry_px - 1) * 100) if (px and entry_px) else 0.0
        reason = None
        if sc is None:
            reason = "dropped"                         # left eligible/scored set
        elif sc < args.t_out:
            reason = "t_out"
        elif peak - sc >= args.trail:
            reason = "trail"
        elif gross <= args.stop:
            reason = "stop"
        elif hd >= args.max_hold:
            reason = "max_hold"
        if reason:
            net = gross - args.cost
            sells.append((sym, hd, net, reason, sc))
            if not args.dry_run:
                conn.execute(
                    "UPDATE paper_book SET status='closed', exit_date=?, exit_px=?, "
                    "exit_reason=?, net_pct=?, last_score=?, peak_score=?, updated_at=? WHERE id=?",
                    (today, px, reason, round(net, 2), sc, round(peak, 1), now, pid))
        elif not args.dry_run:
            conn.execute("UPDATE paper_book SET peak_score=?, last_score=?, updated_at=? WHERE id=?",
                         (round(peak, 1), sc, now, pid))

    # ---- new entries -------------------------------------------------------
    for sym, sc in sorted(score.items(), key=lambda kv: -kv[1]):
        if sc >= args.t_in and sym not in held:
            px = price_now(sym)
            buys.append((sym, sc, px))
            if not args.dry_run:
                conn.execute(
                    "INSERT INTO paper_book (symbol, entry_date, entry_px, entry_score, "
                    "peak_score, last_score, status, updated_at) VALUES (?,?,?,?,?,?, 'open', ?)",
                    (sym, today, px, sc, sc, sc, now))
    if not args.dry_run:
        conn.commit()

    # ---- report ------------------------------------------------------------
    print(f"=== PAPER TRADE (Q+V+Mom)  as-of {today}  eligible={len(eligible)} "
          f"scored={len(score)} {'[DRY-RUN]' if args.dry_run else ''} ===")
    print(f"\nBUY ({len(buys)}):  " + (", ".join(f"{s}({sc:.0f})" for s, sc, _ in buys[:25]) or "—"))
    print(f"SELL ({len(sells)}): " + (", ".join(f"{s} {net:+.1f}% [{r}]" for s, _h, net, r, _sc in sells) or "—"))

    cur = conn.execute(
        "SELECT symbol, entry_date, entry_px, last_score FROM paper_book WHERE status='open' "
        "ORDER BY entry_date").fetchall()
    print(f"\nHOLDINGS ({len(cur)}):")
    for sym, ed, epx, ls in cur[:40]:
        px = price_now(sym)
        unr = ((px / epx - 1) * 100) if (px and epx) else 0.0
        print(f"  {sym:12s} in {ed} ({(_d(today)-_d(ed)).days:>3}d)  {unr:+6.1f}%  score={ls}")

    closed = conn.execute("SELECT net_pct FROM paper_book WHERE status='closed' AND net_pct IS NOT NULL").fetchall()
    if closed:
        nets = [c[0] for c in closed]
        wins = [x for x in nets if x > 0]
        print(f"\nCLOSED: {len(nets)} trades  win={100*len(wins)/len(nets):.0f}%  "
              f"avg={sum(nets)/len(nets):+.2f}%  total={sum(nets):+.1f}%")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
