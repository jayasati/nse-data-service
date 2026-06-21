"""Weekly positional ranking + ATR sizing — the validated path (lean rank + slow-data edge).

Each week: rank the liquid universe on the OOS-validated Lean score, tilt it with the slow data
retail can't see in real time (promoter pledge, FII ownership, delivery conviction, block deals,
credit actions), hard-exclude the traps, size the basket by ATR risk, snapshot it (UI history),
and paper-trade the basket forward (P4 — measure real expectancy before any real money).

Reuses: research.lean_rank.rank (engine), research.paper_trade (_size_position + PaperTradeParams
caps), indicators.delivery_tracker, paper_book. Nothing here re-derives a factor that exists.
"""
from __future__ import annotations

import json
import time

import structlog

from ..indicators.delivery_tracker import compute_symbol_delivery
from ..scheduler import market_hours
from ..storage.db import open_db
from .lean_rank import rank as lean_rank
from .paper_trade import PaperTradeParams, _size_position

log = structlog.get_logger()
STRATEGY = "weekly_positional"
JOB_ID = "weekly_positional"


# ---- slow-data accessors (each degrades to None/{} if its table/column is absent) ----
def _shp(conn, sym: str) -> dict:
    try:
        rows = conn.execute(
            "SELECT promoter_pledge_pct, fii_pct FROM raw_shareholding_quarterly "
            "WHERE symbol=? ORDER BY qe_date DESC LIMIT 2", (sym,)).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    if not rows:
        return {}
    pl0, fii0 = rows[0]
    pl1, fii1 = rows[1] if len(rows) > 1 else (None, None)
    d = lambda a, b: round(a - b, 2) if (a is not None and b is not None) else None
    return {"pledge_pct": pl0, "pledge_delta": d(pl0, pl1),
            "fii_pct": fii0, "fii_delta": d(fii0, fii1)}


def _credit(conn, sym: str) -> dict | None:
    try:
        r = conn.execute("SELECT action, is_junk_downgrade FROM raw_rating_actions "
                         "WHERE symbol=? ORDER BY broadcast_dt DESC LIMIT 1", (sym,)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return {"action": r[0], "junk": bool(r[1])} if r else None


def _block_net_cr(conn, sym: str) -> float | None:
    try:
        r = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN buy_sell='BUY' THEN quantity*weighted_avg_price "
            "ELSE -quantity*weighted_avg_price END), 0) FROM raw_large_deals WHERE symbol=? "
            "AND deal_type IN ('block','bulk') AND deal_date >= date('now','-14 days')",
            (sym,)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return round(r[0] / 1e7, 2) if r and r[0] else None


def _atr_close(conn, sym: str) -> tuple:
    atr_pct = close = None
    try:
        r = conn.execute("SELECT atr_pct FROM tradeable_universe WHERE symbol=?", (sym,)).fetchone()
        atr_pct = r[0] if r else None
    except Exception:  # noqa: BLE001
        pass
    try:
        r = conn.execute("SELECT close FROM raw_intraday_candles WHERE symbol=? AND interval='day' "
                         "ORDER BY ts DESC LIMIT 1", (sym,)).fetchone()
        close = r[0] if r else None
    except Exception:  # noqa: BLE001
        pass
    return atr_pct, close


def _delivery(conn, sym: str, session_date: str | None) -> dict | None:
    if not session_date:
        return None
    try:
        return compute_symbol_delivery(conn, sym, session_date)
    except Exception:  # noqa: BLE001
        return None


# ---- overlay: tilt the validated score with the slow-data edge ----
def _overlay(row: dict, shp: dict, deliv: dict | None, credit: dict | None,
             block: float | None) -> tuple[float, list[str], bool]:
    # NB: the factor_snapshot `risk` column is ~100 for the whole liquid universe (not a usable
    # danger score), so risk control comes from the engine's liquidity grade + ATR sizing + heat
    # caps, not a risk gate here. Hard excludes are the real external slow-data red flags.
    adj, flags, excluded = row["lean_score"], [], False
    if credit and credit.get("junk"):                          # hard exclude — junk downgrade
        excluded = True; flags.append("junk-downgrade")
    elif credit and credit.get("action") == "downgrade":
        adj -= 5; flags.append("downgrade")
    pp, pd_ = shp.get("pledge_pct"), shp.get("pledge_delta")
    if pp and pp > 50 and (pd_ or 0) > 2:                      # hard exclude — high & rising pledge
        excluded = True; flags.append("pledge-high↑")
    elif (pd_ or 0) > 1:
        adj -= 6; flags.append("pledge↑")
    elif pd_ is not None and pd_ < -1:
        adj += 3; flags.append("pledge↓")
    fd = shp.get("fii_delta")
    if (fd or 0) > 0.5:
        adj += 4; flags.append("FII↑")
    elif (fd or 0) < -0.5:
        adj -= 4; flags.append("FII↓")
    conv = (deliv or {}).get("conviction")
    if conv is not None and conv >= 0.6:
        adj += 4; flags.append("deliv↑")
    elif conv is not None and conv < 0.3:
        adj -= 3; flags.append("deliv↓")
    if block and block > 5:
        adj += 3; flags.append("block-buy")
    elif block and block < -5:
        adj -= 3; flags.append("block-sell")
    return round(max(0.0, adj), 2), flags, excluded


def rank_week(conn, *, params: PaperTradeParams, top_n: int, session_date: str | None) -> list[dict]:
    """Rank → overlay → select+size the basket. Returns ALL candidates (selected flagged)."""
    base = lean_rank(conn, limit=100)
    ranked = []
    for i, row in enumerate(base):
        sym = row["symbol"]
        shp, credit, block = _shp(conn, sym), _credit(conn, sym), _block_net_cr(conn, sym)
        deliv = _delivery(conn, sym, session_date)
        adj, flags, excluded = _overlay(row, shp, deliv, credit, block)
        atr_pct, close = _atr_close(conn, sym)
        ranked.append({**row, "base_rank": i + 1, "adj_score": adj, "flags": flags,
                       "excluded": excluded, "pledge_pct": shp.get("pledge_pct"),
                       "pledge_delta": shp.get("pledge_delta"), "fii_pct": shp.get("fii_pct"),
                       "fii_delta": shp.get("fii_delta"),
                       "delivery_conviction": (deliv or {}).get("conviction"),
                       "block_net_cr": block, "credit_action": (credit or {}).get("action"),
                       "atr_pct": atr_pct, "entry_px": close, "selected": 0, "final_rank": None})
    ranked.sort(key=lambda r: -r["adj_score"])
    sector_count, picked = {}, 0
    for r in ranked:
        if r["excluded"] or picked >= top_n:
            continue
        sec = r.get("sector") or "?"
        if sector_count.get(sec, 0) >= params.sector_max:
            continue                                           # R5 sector cap
        stop, qty, risk_r = _size_position(r["entry_px"], r["atr_pct"], params)
        if qty is None:
            continue
        r.update({"stop_px": stop, "qty": qty, "risk_rupees": risk_r,
                  "selected": 1, "final_rank": picked + 1})
        sector_count[sec] = sector_count.get(sec, 0) + 1
        picked += 1
    return ranked


def _persist_snapshot(conn, week_date: str, ranked: list[dict]) -> None:
    now = int(time.time())
    conn.executemany(
        "INSERT OR REPLACE INTO weekly_ranking (week_date, symbol, sector, grade, base_rank, "
        "lean_score, valuation, surprise, risk, adj_score, pledge_pct, pledge_delta, fii_pct, "
        "fii_delta, delivery_conviction, block_net_cr, credit_action, flags, excluded, atr_pct, "
        "entry_px, stop_px, qty, risk_rupees, selected, final_rank, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(week_date, r["symbol"], r.get("sector"), r.get("grade"), r["base_rank"],
          r["lean_score"], r.get("valuation"), r.get("surprise"), r.get("risk"), r["adj_score"],
          r.get("pledge_pct"), r.get("pledge_delta"), r.get("fii_pct"), r.get("fii_delta"),
          r.get("delivery_conviction"), r.get("block_net_cr"), r.get("credit_action"),
          json.dumps(r["flags"]), 1 if r["excluded"] else 0, r.get("atr_pct"), r.get("entry_px"),
          r.get("stop_px"), r.get("qty"), r.get("risk_rupees"), r["selected"], r.get("final_rank"),
          now) for r in ranked])


def _rebalance_paper(conn, week_date: str, ranked: list[dict], params: PaperTradeParams) -> dict:
    """Open new basket names, close those that dropped out or hit their stop (weekly rotation)."""
    sel = {r["symbol"]: r for r in ranked if r["selected"]}
    opened = closed = 0
    for pid, sym, entry_px, qty, stop_px in conn.execute(
            "SELECT id, symbol, entry_px, qty, stop_px FROM paper_book WHERE strategy=? "
            "AND status='open'", (STRATEGY,)).fetchall():
        _, close = _atr_close(conn, sym)
        reason = "dropped" if sym not in sel else (
            "stop" if (close and stop_px and close <= stop_px) else None)
        if reason and close and entry_px:
            net_pct = (close - entry_px) / entry_px * 100.0
            conn.execute(
                "UPDATE paper_book SET status='closed', exit_date=?, exit_px=?, exit_reason=?, "
                "net_pct=?, net_pnl=?, updated_at=? WHERE id=?",
                (week_date, close, reason, round(net_pct, 3),
                 round((close - entry_px) * (qty or 0), 2), int(time.time()), pid))
            closed += 1
    cur_open = {r[0] for r in conn.execute(
        "SELECT symbol FROM paper_book WHERE strategy=? AND status='open'", (STRATEGY,))}
    for sym, r in sel.items():
        if sym in cur_open or len(cur_open) >= params.max_positions:
            continue
        conn.execute(
            "INSERT INTO paper_book (symbol, entry_date, entry_px, entry_score, peak_score, "
            "last_score, status, strategy, stop_px, qty, risk_rupees, updated_at) "
            "VALUES (?,?,?,?,?,?, 'open', ?,?,?,?,?)",
            (sym, week_date, r["entry_px"], r["adj_score"], r["adj_score"], r["adj_score"],
             STRATEGY, r["stop_px"], r["qty"], r["risk_rupees"], int(time.time())))
        cur_open.add(sym); opened += 1
    return {"opened": opened, "closed": closed, "open_now": len(cur_open)}


def run_weekly_positional(db_path: str, *, params: PaperTradeParams | None = None,
                          top_n: int = 15) -> dict:
    params = params or PaperTradeParams()
    conn = open_db(db_path)
    try:
        week_date = market_hours.now_ist().date().isoformat()
        session_date = conn.execute("SELECT MAX(date) FROM raw_bhavcopy_cm").fetchone()[0] \
            if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND "
                            "name='raw_bhavcopy_cm'").fetchone() else None
        ranked = rank_week(conn, params=params, top_n=top_n, session_date=session_date)
        _persist_snapshot(conn, week_date, ranked)
        reb = _rebalance_paper(conn, week_date, ranked, params)
        conn.commit()
        return {"week": week_date, "candidates": len(ranked),
                "selected": sum(r["selected"] for r in ranked), **reb}
    finally:
        conn.close()


def register_weekly_positional_job(scheduler, db_path: str, *,
                                   params: PaperTradeParams | None = None) -> str:
    """Monday 09:00 IST — rank, snapshot, paper-rebalance the positional book."""
    from apscheduler.triggers.cron import CronTrigger

    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            return
        try:
            log.info("weekly_positional", **run_weekly_positional(db_path, params=params))
        except Exception:
            log.exception("weekly_positional_failed")

    scheduler.add_job(_tick, trigger=CronTrigger(day_of_week="mon", hour=9, minute=0,
                      timezone=market_hours.IST), id=JOB_ID, max_instances=1,
                      coalesce=True, replace_existing=True)
    return JOB_ID
