"""
Week-6 spot-check tooling (FEATURE_CHECKLIST tasks 6.4, 6.5, 6.7).

This does NOT call the production labeler/tracker — it re-derives the numbers
independently from the raw intraday/bhavcopy data and the cost model, then diffs
against what's stored. A clean diff is evidence the pipeline is correct; a
mismatch (or the printed raw bars) is what you eyeball by hand.

    python scripts/spot_check.py all                # run every check
    python scripts/spot_check.py outcomes -n 5      # 6.4: signal_outcomes vs intraday
    python scripts/spot_check.py trades   -n 5      # 6.5: paper_trades P&L vs cost model
    python scripts/spot_check.py premarket          # 6.7: indicator_live seeding

Run it on the server (or after `scripts/transfer_db.sh`) — a fresh dev DB has no
signals/trades to check.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime

from nse_data.costs.model import compute_costs
from nse_data.indicators.intraday_ohlcv import read_intraday_5m
from nse_data.signals.paper_tracker import _position_size
from nse_data.storage.db import open_db

# A recomputed percentage/price within this of the stored value is "OK".
_PCT_TOL = 0.05      # percentage points
_MONEY_TOL = 0.50    # rupees

_OK = "✓"
_BAD = "✗"


def _epoch(detected_at: str) -> int:
    return int(datetime.fromisoformat(detected_at).timestamp())


def _pct(value: float | None, entry: float) -> float | None:
    if value is None or entry <= 0:
        return None
    return (value / entry - 1.0) * 100.0


def _cmp(label: str, recomputed, stored, tol: float) -> str:
    """One comparison line: recomputed vs stored, with a pass/fail mark."""
    if recomputed is None and stored is None:
        return f"    {label:10s} both None"
    if recomputed is None or stored is None:
        return f"    {_BAD} {label:8s} recomputed={recomputed} stored={stored}"
    mark = _OK if abs(recomputed - stored) <= tol else _BAD
    return f"    {mark} {label:8s} recomputed={recomputed:+.3f}  stored={stored:+.3f}"


# ============================================================================
# 6.4 — signal_outcomes vs raw intraday
# ============================================================================

def check_outcomes(conn: sqlite3.Connection, n: int) -> int:
    rows = conn.execute(
        "SELECT s.id, s.symbol, s.detected_at, s.price, "
        "       o.ret_30m, o.ret_eod, o.mae, o.mfe "
        "FROM signal_outcomes o JOIN signals s ON s.id = o.signal_id "
        "ORDER BY s.detected_at DESC LIMIT ?",
        (n,),
    ).fetchall()

    print(f"\n=== 6.4 signal_outcomes spot-check ({len(rows)} signals) ===")
    if not rows:
        print("  no labeled signals found (empty/dev DB?)")
        return 0

    mismatches = 0
    for sid, symbol, detected_at, entry, ret_30m, ret_eod, mae, mfe in rows:
        print(f"\n  signal #{sid} {symbol} @ {detected_at}  entry={entry}")
        if not entry:
            print("    (no entry price — skipped)")
            continue

        detected_ts = _epoch(detected_at)
        intraday = read_intraday_5m(conn, symbol, since_ts=detected_ts)
        after = intraday.loc[intraday.index >= detected_ts] if not intraday.empty else intraday
        if after.empty:
            print("    (no post-entry intraday bars available)")
            continue

        # Independent recomputation.
        ahead_30 = after.loc[after.index >= detected_ts + 30 * 60]
        rc_30m = _pct(float(ahead_30["close"].iloc[0]), entry) if not ahead_30.empty else None
        rc_eod = _pct(float(after["close"].iloc[-1]), entry)
        rc_mae = _pct(float(after["low"].min()), entry)
        rc_mfe = _pct(float(after["high"].max()), entry)

        bar30 = float(ahead_30["close"].iloc[0]) if not ahead_30.empty else None
        print(f"    bars: T+30m close={bar30}  EOD close={float(after['close'].iloc[-1]):.2f}  "
              f"low={float(after['low'].min()):.2f}  high={float(after['high'].max()):.2f}")
        for line in (
            _cmp("ret_30m", rc_30m, ret_30m, _PCT_TOL),
            _cmp("ret_eod", rc_eod, ret_eod, _PCT_TOL),
            _cmp("mae", rc_mae, mae, _PCT_TOL),
            _cmp("mfe", rc_mfe, mfe, _PCT_TOL),
        ):
            print(line)
            if line.lstrip().startswith(_BAD):
                mismatches += 1

    print(f"\n  -> {mismatches} mismatch(es)")
    return mismatches


# ============================================================================
# 6.5 — paper_trades P&L vs cost model
# ============================================================================

def check_trades(conn: sqlite3.Connection, n: int) -> int:
    rows = conn.execute(
        "SELECT id, symbol, entry_price, exit_price, exit_reason, sl_price, "
        "       t1_price, gross_pnl, net_pnl "
        "FROM paper_trades WHERE status = 'closed' AND exit_price IS NOT NULL "
        "ORDER BY exit_time DESC LIMIT ?",
        (n,),
    ).fetchall()

    print(f"\n=== 6.5 paper_trades P&L spot-check ({len(rows)} closed trades) ===")
    if not rows:
        print("  no closed paper trades found (empty/dev DB?)")
        return 0

    mismatches = 0
    for tid, symbol, entry, exit_p, reason, sl, t1, gross, net in rows:
        qty = _position_size(entry)
        costs = compute_costs(entry, exit_p, qty, trade_type="long")
        print(f"\n  trade #{tid} {symbol}  entry={entry} exit={exit_p} ({reason}) qty={qty}")
        print(f"    SL={sl} T1={t1}  costs={costs.total_costs:.2f}")
        for line in (
            _cmp("gross", costs.gross_pnl, gross, _MONEY_TOL),
            _cmp("net", costs.net_pnl, net, _MONEY_TOL),
        ):
            print(line)
            if line.lstrip().startswith(_BAD):
                mismatches += 1

        # Sanity: was the exit fill consistent with the reason/bracket?
        if reason == "hit_t1" and abs(exit_p - t1) > _MONEY_TOL:
            print(f"    {_BAD} exit fill {exit_p} != T1 {t1} for hit_t1")
            mismatches += 1
        elif reason == "hit_sl" and abs(exit_p - sl) > _MONEY_TOL:
            print(f"    {_BAD} exit fill {exit_p} != SL {sl} for hit_sl")
            mismatches += 1

    print(f"\n  -> {mismatches} mismatch(es)")
    return mismatches


# ============================================================================
# 6.7 — indicator_live pre-market seeding
# ============================================================================

def check_premarket(conn: sqlite3.Connection) -> int:
    print("\n=== 6.7 indicator_live seeding spot-check ===")
    total, syms, oldest, newest = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(updated_at), MAX(updated_at) "
        "FROM indicator_live"
    ).fetchone()
    print(f"  rows={total}  symbols={syms}  updated_at range: {oldest} .. {newest}")
    if not total:
        print(f"  {_BAD} indicator_live is empty — pre-market loader has not seeded it")
        return 1

    latest_day = (newest or "")[:10]
    seeded_before_open = conn.execute(
        "SELECT COUNT(*) FROM indicator_live "
        "WHERE substr(updated_at,1,10) = ? AND substr(updated_at,12,5) <= '09:15'",
        (latest_day,),
    ).fetchone()[0]
    print(f"  on {latest_day}: {seeded_before_open} symbol(s) seeded at/before 09:15")

    problems = 0
    if seeded_before_open == 0:
        print(f"  {_BAD} nothing seeded before 09:15 on {latest_day} "
              "(pre_market_loader at 08:45 may not have run)")
        problems += 1
    else:
        print(f"  {_OK} pre-market seeding present before the open")
    return problems


# ============================================================================
# CLI
# ============================================================================

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Week-6 spot checks (6.4/6.5/6.7)")
    p.add_argument("check", choices=["all", "outcomes", "trades", "premarket"])
    p.add_argument("-n", type=int, default=5, help="how many recent rows to check")
    p.add_argument("--db", default="data/nse.db")
    args = p.parse_args(argv)

    conn = open_db(args.db)
    try:
        problems = 0
        if args.check in ("all", "outcomes"):
            problems += check_outcomes(conn, args.n)
        if args.check in ("all", "trades"):
            problems += check_trades(conn, args.n)
        if args.check in ("all", "premarket"):
            problems += check_premarket(conn)
    finally:
        conn.close()

    print(f"\nTOTAL PROBLEMS: {problems}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
