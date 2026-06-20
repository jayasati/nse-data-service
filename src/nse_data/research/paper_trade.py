"""Daily paper-trade / live-signal engine (package core + scheduled job).

Runs one or more ranking strategies in parallel, each accumulating an INDEPENDENT
forward, out-of-sample track record in `paper_book` (tagged by `strategy`) — the
gate no backtest can replace (P4 in plans/MASTER_CHECKLIST.md).

Strategies:
  qvm                — the validated Q+V+Momentum dynamic (survivorship-corrected
                       +30% CAGR / Sharpe ~1.5); the live default.
  buyscore_adaptive  — the regime-adaptive integrated Buy Score (grand-prompt v2);
                       backtest leaned positive but NOT certified → tracked forward.
  lean               — the OOS-validated Valuation+Surprise signal (composite IC
                       +0.095 unseen / +0.058 in-sample); the forward test the whole
                       investigation pointed to.

Each run (after the EOD candle update): rebuild the point-in-time eligible universe,
score each strategy over it, then drive ITS positions through the same state machine —
  BUY  : eligible name not held whose score >= t_in
  SELL : held name whose score < t_out, OR trails `trail` off its held-peak,
         OR hit max-hold / stop / dropped from the eligible+scored set.

The CLI wrapper lives at scripts/paper_trade.py (calls `run_paper_trade`); the
service schedules `register_paper_trade_job` (CronTrigger 19:15 IST, trading-day
gated — after EOD candles land and after the 18:45 factor snapshot).
"""
from __future__ import annotations

import datetime as _dt
import statistics as st
import time
from dataclasses import dataclass

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from ..costs.model import compute_costs
from ..scheduler import market_hours
from ..scheduler.market_hours import IST
from ..storage.db import open_db

log = structlog.get_logger(__name__)
JOB_ID = "paper_trade"

WIN, MIN_DAYS, MIN_TURN_CR, MAX_VOL = 252, 200, 5.0, 50.0
QVM_ENGINES = ("quality", "valuation", "momentum")

# Sizing fallbacks (R4): when a name has no ATR%, risk to a flat 10% stop; never let
# the ATR stop sit more than 30% away (clamps absurd sizing on very volatile names).
DEFAULT_STOP_FRAC = 0.10
MAX_STOP_FRAC = 0.30


@dataclass(frozen=True)
class PaperTradeParams:
    """State-machine thresholds + risk sizing; defaults mirror the validated paper_trade CLI."""

    t_in: float = 80.0
    t_out: float = 60.0
    trail: float = 15.0
    max_hold: int = 120
    stop: float = -15.0           # catastrophe % stop (backstop to the per-trade ATR stop)
    cost: float = 1.0             # flat % fallback for legacy rows without a sized qty
    dry_run: bool = False
    # R4 — risk-based ATR sizing + R2 delivery cost model
    capital: float = 1_000_000.0  # notional book per strategy (sizing only; heat caps are R5)
    risk_pct: float = 1.0         # % of capital risked to the ATR stop per trade
    atr_k: float = 2.5            # stop distance = k · ATR
    segment: str = "delivery"     # swing/positional → delivery cost schedule
    # R5 — portfolio risk caps (per strategy book). With 1% risk/trade, heat 10% ≈ 10
    # concurrent names; the research's 6% assumes trailing stops that bleed off open risk
    # over the hold (R10, not yet built), so we run a flat 10% until trailing exists.
    max_positions: int = 10       # max concurrent open positions
    heat_pct: float = 10.0        # max total open risk (Σ risk_rupees) as % of capital
    sector_max: int = 3           # max concurrent positions in one sector


def _d(s):
    return _dt.date.fromisoformat(s)


# --- strategy scorers (point-in-time, ETF/fund-guarded) ----------------------

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


def _score_lean(conn, eligible, ep, sector_of) -> dict:
    """OOS-VALIDATED Lean score = equal-weight mean of Valuation + Surprise — the only two
    factors that survived cross-regime OOS (2023-24 + 2025-26; composite IC +0.095/+0.058).
    This is the forward, out-of-sample test the whole investigation pointed to. Requires
    Valuation present (so funds/ETFs are excluded)."""
    from nse_data.research import valuation_engine as ve, surprise_engine as se
    from nse_data.research.buy_score import lean_raw

    val = ve.score_universe(conn, eligible, ep, sector_of)
    sur = se.score_universe(conn, eligible, ep, sector_of)
    out = {}
    for sym in eligible:
        s, parts = lean_raw({"valuation": val.get(sym, {}).get("score"),
                             "surprise": sur.get(sym, {}).get("score")})
        if s is not None and "valuation" in parts:
            out[sym] = round(s, 1)
    return out


STRATEGIES = {
    "qvm": (_score_qvm, "Q+V+Mom"),
    "buyscore_adaptive": (_score_buyscore_adaptive, "BuyScore-Adaptive"),
    "lean": (_score_lean, "Lean (Val+Surprise · OOS-validated)"),
}


def _size_position(entry_px, atr_pct, params: PaperTradeParams):
    """(stop_px, qty, risk_rupees) for a long entry sized to risk params.risk_pct of
    capital to a k·ATR stop. Falls back to a flat stop when ATR% is missing; never sizes
    a single name above the full book notional."""
    if not entry_px or entry_px <= 0:
        return None, None, None
    frac = (params.atr_k * atr_pct / 100.0) if (atr_pct and atr_pct > 0) else DEFAULT_STOP_FRAC
    frac = min(frac, MAX_STOP_FRAC)
    risk_per_share = entry_px * frac
    stop_px = round(entry_px - risk_per_share, 2)
    qty = int((params.capital * params.risk_pct / 100.0) // risk_per_share)
    qty = min(qty, int(params.capital // entry_px))      # cap at full-book notional
    qty = max(1, qty)
    return stop_px, qty, round(qty * risk_per_share, 2)


def _run_strategy(conn, key, label, score, today, params: PaperTradeParams,
                  price_now, risk_tag, atr_of, sector_of) -> dict:
    """Drive one strategy's paper_book positions through the state machine.

    Writes BUY/SELL/peak updates to paper_book (unless params.dry_run) and returns a
    structured summary (no printing) for the CLI to render or the job to log. New buys
    are risk-sized (R4); exits book real net P&L via the delivery cost model (R2). Rows
    opened before sizing existed (NULL qty) fall back to the flat-% cost path.
    """
    open_rows = conn.execute(
        "SELECT id, symbol, entry_date, entry_px, peak_score, stop_px, qty, risk_rupees "
        "FROM paper_book WHERE status='open' AND strategy=?", (key,)).fetchall()
    held = {r[1] for r in open_rows}
    now = int(time.time())
    sells, buys = [], []

    for pid, sym, entry_date, entry_px, peak, stop_px, qty, risk_rupees in open_rows:
        px = price_now(sym)
        sc = score.get(sym)
        peak = max(peak or 0.0, sc if sc is not None else (peak or 0.0))
        hd = (_d(today) - _d(entry_date)).days
        gross = ((px / entry_px - 1) * 100) if (px and entry_px) else 0.0
        hit_stop = (stop_px is not None and px is not None and px <= stop_px) or gross <= params.stop
        reason = ("dropped" if sc is None else "t_out" if sc < params.t_out
                  else "trail" if peak - sc >= params.trail else "stop" if hit_stop
                  else "max_hold" if hd >= params.max_hold else None)
        if reason:
            if qty and entry_px and px:
                tc = compute_costs(entry_px, px, int(qty), "long", segment=params.segment)
                net_pnl = round(tc.net_pnl, 2)
                net_pct = round(net_pnl / (entry_px * qty) * 100, 2)
                r_multiple = round(net_pnl / risk_rupees, 3) if risk_rupees else None
            else:                                          # legacy row: flat-% fallback
                net_pnl, r_multiple = None, None
                net_pct = round(gross - params.cost, 2)
            sells.append((sym, hd, net_pct, reason, sc))
            if not params.dry_run:
                conn.execute(
                    "UPDATE paper_book SET status='closed', exit_date=?, exit_px=?, exit_reason=?, "
                    "net_pct=?, net_pnl=?, r_multiple=?, last_score=?, peak_score=?, updated_at=? "
                    "WHERE id=?",
                    (today, px, reason, net_pct, net_pnl, r_multiple, sc, round(peak, 1), now, pid))
        elif not params.dry_run:
            conn.execute("UPDATE paper_book SET peak_score=?, last_score=?, updated_at=? WHERE id=?",
                         (round(peak, 1), sc, now, pid))

    # R5 — live-book state from positions that did NOT close this pass, then gate new
    # buys on max-positions / portfolio-heat / per-sector caps (best scores first).
    closed_syms = {s[0] for s in sells}
    survivors = [r for r in open_rows if r[1] not in closed_syms]
    n_open = len(survivors)
    heat = sum((r[7] or 0.0) for r in survivors)          # Σ risk_rupees of open book
    sector_counts: dict[str, int] = {}
    for r in survivors:
        sector_counts[sector_of(r[1])] = sector_counts.get(sector_of(r[1]), 0) + 1
    heat_cap = params.capital * params.heat_pct / 100.0
    eligible = capped = 0

    for sym, sc in sorted(score.items(), key=lambda kv: -kv[1]):
        if sc < params.t_in or sym in held:
            continue
        eligible += 1
        if n_open >= params.max_positions or heat >= heat_cap:
            capped += 1
            continue
        sec = sector_of(sym)
        if sector_counts.get(sec, 0) >= params.sector_max:
            capped += 1
            continue
        entry_px = price_now(sym)
        stop_px, qty, risk_rupees = _size_position(entry_px, atr_of(sym), params)
        if risk_rupees and heat + risk_rupees > heat_cap:
            capped += 1
            continue
        buys.append((sym, sc, entry_px))
        n_open += 1
        heat += risk_rupees or 0.0
        sector_counts[sec] = sector_counts.get(sec, 0) + 1
        if not params.dry_run:
            conn.execute(
                "INSERT INTO paper_book (symbol, entry_date, entry_px, entry_score, "
                "peak_score, last_score, status, strategy, stop_px, qty, risk_rupees, updated_at) "
                "VALUES (?,?,?,?,?,?, 'open', ?,?,?,?,?)",
                (sym, today, entry_px, sc, sc, sc, key, stop_px, qty, risk_rupees, now))
    if not params.dry_run:
        conn.commit()

    cur = conn.execute(
        "SELECT symbol, entry_date, entry_px, last_score FROM paper_book "
        "WHERE status='open' AND strategy=? ORDER BY entry_date", (key,)).fetchall()
    holdings = []
    for sym, ed, epx, ls in cur:
        px = price_now(sym)
        unr = ((px / epx - 1) * 100) if (px and epx) else 0.0
        holdings.append((sym, ed, (_d(today) - _d(ed)).days, round(unr, 1), ls, risk_tag(sym)))
    closed = [r[0] for r in conn.execute(
        "SELECT net_pct FROM paper_book WHERE status='closed' AND net_pct IS NOT NULL "
        "AND strategy=?", (key,)).fetchall()]
    wins = [x for x in closed if x > 0]
    return {
        "key": key, "label": label, "scored": len(score),
        "buys": [(s, sc) for s, sc, _ in buys],
        "sells": [(s, round(net, 2), r) for s, _h, net, r, _sc in sells],
        "n_open": len(cur), "holdings": holdings,
        "eligible_buys": eligible, "capped": capped,        # R5 — no silent caps
        "heat_used_pct": round(heat / params.capital * 100, 1) if params.capital else None,
        "closed_n": len(closed),
        "win_pct": round(100 * len(wins) / len(closed), 0) if closed else None,
        "avg_pct": round(sum(closed) / len(closed), 2) if closed else None,
        "total_pct": round(sum(closed), 1) if closed else None,
    }


def _as_of(conn) -> tuple[str, int]:
    """Last EOD candle date (IST) and its epoch — the point-in-time anchor."""
    return conn.execute(
        "SELECT date(ts,'unixepoch','+05:30') d, MAX(ts) FROM raw_intraday_candles "
        "WHERE symbol='NIFTYBEES' AND interval='day' GROUP BY d ORDER BY d DESC LIMIT 1").fetchone()


def _eligible_universe(conn) -> list[str]:
    """Point-in-time tradeable set AS OF today (trailing liquidity + vol), ETFs out."""
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
    return eligible


def run_paper_trade(conn, *, strategies=None, params: PaperTradeParams | None = None) -> dict:
    """Run the requested strategies over the point-in-time universe on `conn`.

    Caller owns `conn` (open + close); writes are committed per strategy unless
    params.dry_run. Returns {today, eligible, strategies: [summary, ...]}.
    """
    from ..fundamentals.sectors import sector_class_for
    from .risk_engine import risk_raw

    params = params or PaperTradeParams()
    keys = [k for k in (strategies or list(STRATEGIES)) if k in STRATEGIES]

    today, ep = _as_of(conn)

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

    atr_cache = {
        s: a for s, a in conn.execute(
            "SELECT symbol, atr_pct FROM tradeable_universe WHERE atr_pct IS NOT NULL")
    }

    def atr_of(sym):
        return atr_cache.get(sym)        # ATR% for R4 sizing; None → flat-stop fallback

    eligible = _eligible_universe(conn)

    _rk: dict = {}

    def risk_tag(sym):
        if sym not in _rk:
            _rk[sym] = risk_raw(conn, sym, ep)
        r = _rk[sym]
        if r["score"] >= 100 or not r["components"]:
            return ""
        top = max(r["components"], key=r["components"].get)
        return f" ⚠risk{r['score']:.0f}[{top}]"

    summaries = []
    for key in keys:
        scorer, label = STRATEGIES[key]
        score = scorer(conn, eligible, ep, sector_of)
        summaries.append(
            _run_strategy(conn, key, label, score, today, params, price_now, risk_tag,
                          atr_of, sector_of))
    return {"today": today, "eligible": len(eligible), "strategies": summaries}


def run_paper_trade_db(db_path: str, *, strategies=None,
                       params: PaperTradeParams | None = None) -> dict:
    """Open `db_path`, run the strategies, close. Returns a log-friendly compact dict."""
    conn = open_db(db_path)
    try:
        conn.execute("PRAGMA busy_timeout=60000")
        report = run_paper_trade(conn, strategies=strategies, params=params)
    finally:
        conn.close()
    compact = {
        "date": report["today"], "eligible": report["eligible"],
        "strategies": {
            s["key"]: {"buys": len(s["buys"]), "sells": len(s["sells"]), "open": s["n_open"]}
            for s in report["strategies"]
        },
    }
    return compact


def register_paper_trade_job(scheduler: BlockingScheduler, db_path: str, *,
                             strategies=None, params: PaperTradeParams | None = None) -> str:
    """Attach the 19:15-IST daily paper-trade job. Trading-day gated.

    Runs after the 16:00 EOD candle cron and the 18:45 factor snapshot so each
    strategy's forward paper_book track record advances once per session with no
    human in the loop (P4 — accumulate a quarter, then promote).
    """
    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            log.info("paper_trade_skipped_non_trading_day")
            return
        try:
            report = run_paper_trade_db(db_path, strategies=strategies, params=params)
            log.info("paper_trade", **report)
        except Exception:
            log.exception("paper_trade_failed")

    scheduler.add_job(
        _tick,
        trigger=CronTrigger(hour=19, minute=15, timezone=IST),
        id=JOB_ID,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return JOB_ID
