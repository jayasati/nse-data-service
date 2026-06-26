"""Financial-strength + value-trap screens (PROFITABILITY_PLAN Track C, R7 + R8).

Two research-backed conviction screens for the pre-buy card, computed from the
balance-sheet / cash-flow fields already in `extracted_financials`:

  R7  balance_sheet_score  — interest coverage, current ratio, D/E, debt trend, CFO sign
  R8  piotroski_f          — Piotroski F-score (0-9), the value-trap FILTER [R2]
      distress_flags       — clean red flags (negative net worth, IC<1.5, CR<1, high
                             leverage, −CFO, loss). A conservative stand-in for Altman Z,
                             which needs retained earnings we don't cleanly extract.

Caveats baked in: the F-score is computed YoY on the latest vs ~year-ago period (an
annual-Piotroski approximation over quarterly filings) and degrades gracefully — every
signal is scored only when its inputs exist, and `n_signals` reports how many of the 9
were computable. Per the research, the F-score is a value-trap *filter* (weak as a return
engine, esp. in large-caps) — use it to avoid losers, not to pick winners.

Pure scorers are unit-tested; the readers + nightly pass glue them to SQLite.
"""
from __future__ import annotations

import sqlite3
from datetime import date

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from ..scheduler import market_hours
from ..storage.db import open_db

log = structlog.get_logger()
JOB_ID = "stock_strength"

# fields pulled per period from extracted_financials
_FIELDS = (
    "pat_cr", "pbt_cr", "finance_cost_cr", "cfo_cr", "revenue_cr", "cost_of_materials_cr",
    "total_assets_cr", "current_assets_cr", "current_liabilities_cr", "total_liabilities_cr",
    "borrowings_cr", "equity_cr", "eps_basic",
)


# ---- R7 balance-sheet ratios -----------------------------------------------

def interest_coverage(m: dict) -> float | None:
    """EBIT ÷ interest = (PBT + finance cost) ÷ finance cost. None if no/zero finance cost."""
    fc, pbt = m.get("finance_cost_cr"), m.get("pbt_cr")
    if not fc or fc <= 0 or pbt is None:
        return None
    return round((pbt + fc) / fc, 2)


def current_ratio(m: dict) -> float | None:
    ca, cl = m.get("current_assets_cr"), m.get("current_liabilities_cr")
    if ca is None or not cl or cl <= 0:
        return None
    return round(ca / cl, 2)


def debt_to_equity(m: dict) -> float | None:
    b, e = m.get("borrowings_cr"), m.get("equity_cr")
    if b is None or not e or e <= 0:        # negative equity handled as a distress flag
        return None
    return round(b / e, 2)


def _g_ic(v):
    return None if v is None else 10.0 if v > 6 else 7.0 if v >= 3 else 4.0 if v >= 1.5 else 0.0


def _g_cr(v):
    return None if v is None else 10.0 if v > 2 else 7.0 if v >= 1.2 else 4.0 if v >= 1 else 0.0


def _g_de(v):
    return None if v is None else 10.0 if v < 0.3 else 7.0 if v <= 1 else 4.0 if v <= 2 else 0.0


def _debt_trend(now: dict, prior: dict | None) -> float | None:
    """10 falling / 6 flat / 2 rising borrowings YoY."""
    if not prior:
        return None
    b0, b1 = now.get("borrowings_cr"), prior.get("borrowings_cr")
    if b0 is None or b1 is None or b1 <= 0:
        return None
    chg = (b0 - b1) / b1
    return 2.0 if chg > 0.05 else 6.0 if chg >= -0.05 else 10.0


_BS_WEIGHTS = {"ic": 2.0, "cr": 1.0, "de": 2.0, "cfo": 1.0, "trend": 1.0}


def balance_sheet_score(now: dict, prior: dict | None = None) -> float | None:
    """0-100 balance-sheet strength, weighted over available components (graceful)."""
    cfo = now.get("cfo_cr")
    grades = {
        "ic": _g_ic(interest_coverage(now)),
        "cr": _g_cr(current_ratio(now)),
        "de": _g_de(debt_to_equity(now)),
        "cfo": (None if cfo is None else 10.0 if cfo > 0 else 0.0),
        "trend": _debt_trend(now, prior),
    }
    num = den = 0.0
    for k, g in grades.items():
        if g is None:
            continue
        num += _BS_WEIGHTS[k] * (g / 10.0)
        den += _BS_WEIGHTS[k]
    return round(num / den * 100, 1) if den > 0 else None


# ---- R8 Piotroski F-score (value-trap filter) ------------------------------

def _shares(m: dict):
    pat, eps = m.get("pat_cr"), m.get("eps_basic")
    if pat is None or not eps or eps == 0:
        return None
    return pat / eps                        # crore-shares proxy (PAT ₹cr ÷ EPS ₹)


def piotroski_f(now: dict, prior: dict | None) -> dict:
    """Piotroski F (0-9) over computable signals. Returns {f_score, n_signals, signals}."""
    ta, ta1 = now.get("total_assets_cr"), (prior or {}).get("total_assets_cr")
    roa = (now["pat_cr"] / ta) if (now.get("pat_cr") is not None and ta) else None
    roa1 = ((prior or {}).get("pat_cr") / ta1) if (prior and prior.get("pat_cr") is not None and ta1) else None
    cfo = now.get("cfo_cr")
    de0, de1 = debt_to_equity(now), debt_to_equity(prior) if prior else None
    cr0, cr1 = current_ratio(now), current_ratio(prior) if prior else None
    sh0, sh1 = _shares(now), _shares(prior) if prior else None
    gm = _gross_margin(now)
    gm1 = _gross_margin(prior) if prior else None
    at0 = (now["revenue_cr"] / ta) if (now.get("revenue_cr") is not None and ta) else None
    at1 = ((prior or {}).get("revenue_cr") / ta1) if (prior and prior.get("revenue_cr") is not None and ta1) else None

    sig: dict[str, int | None] = {
        "roa_pos": _b(roa, lambda x: x > 0),
        "cfo_pos": _b(cfo, lambda x: x > 0),
        "roa_up": _cmp(roa, roa1, lambda a, b: a > b),
        "accrual": (1 if (cfo is not None and now.get("pat_cr") is not None and cfo > now["pat_cr"]) else
                    0 if (cfo is not None and now.get("pat_cr") is not None) else None),
        "lev_down": _cmp(de0, de1, lambda a, b: a < b),
        "curr_up": _cmp(cr0, cr1, lambda a, b: a > b),
        "no_dilution": _cmp(sh0, sh1, lambda a, b: a <= b),
        "margin_up": _cmp(gm, gm1, lambda a, b: a > b),
        "turnover_up": _cmp(at0, at1, lambda a, b: a > b),
    }
    pts = [v for v in sig.values() if v is not None]
    return {"f_score": sum(pts), "n_signals": len(pts), "signals": sig}


def _gross_margin(m: dict | None):
    if not m:
        return None
    rev, com = m.get("revenue_cr"), m.get("cost_of_materials_cr")
    if not rev or rev <= 0 or com is None:
        return None
    return (rev - com) / rev


def _b(v, pred):
    return None if v is None else (1 if pred(v) else 0)


def _cmp(a, b, pred):
    return None if (a is None or b is None) else (1 if pred(a, b) else 0)


def distress_flags(now: dict) -> list[str]:
    """Conservative red flags from clean inputs (a stand-in for a full Altman Z)."""
    flags = []
    eq = now.get("equity_cr")
    if eq is not None and eq < 0:
        flags.append("negative_net_worth")
    ic = interest_coverage(now)
    if ic is not None and ic < 1.5:
        flags.append("interest_cover_below_1.5x")
    cr = current_ratio(now)
    if cr is not None and cr < 1:
        flags.append("current_ratio_below_1")
    de = debt_to_equity(now)
    if de is not None and de > 3:
        flags.append("high_leverage")
    cfo = now.get("cfo_cr")
    if cfo is not None and cfo < 0:
        flags.append("negative_cfo")
    pat = now.get("pat_cr")
    if pat is not None and pat < 0:
        flags.append("loss_making")
    return flags


# A Piotroski F needs the 9 signals to mean anything. With current data (quarterly P&L only — no
# cash flow for ~94% of names, balance sheet spotty, prior-year usually absent) most stocks compute
# only 2-3, so reporting "X/9" is misleading (paints quality names as weak). Require a real minimum;
# below it f_score is None (the bs_score below stays the reliable strength proxy). Lifts automatically
# once annual balance-sheet + cash-flow + prior-year coverage improves.
MIN_F_SIGNALS = 5


def compute_strength(now: dict | None, prior: dict | None) -> dict:
    """Bundle the R7+R8 screens for one symbol. All None/empty when no financials."""
    if not now:
        return {"f_score": None, "f_signals": None, "interest_coverage": None,
                "current_ratio": None, "debt_equity": None, "bs_score": None, "distress": []}
    f = piotroski_f(now, prior)
    return {
        "f_score": f["f_score"] if f["n_signals"] >= MIN_F_SIGNALS else None,
        "f_signals": f["n_signals"] or None,
        "interest_coverage": interest_coverage(now),
        "current_ratio": current_ratio(now),
        "debt_equity": debt_to_equity(now),
        "bs_score": balance_sheet_score(now, prior),
        "distress": distress_flags(now),
    }


# ---- DB reader + nightly pass ----------------------------------------------

def _has(conn, name) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def load_periods(conn: sqlite3.Connection, symbol: str) -> tuple[dict | None, dict | None]:
    """(now, prior≈1yr-ago) financial dicts for a symbol, consistent scope (consol>standalone)."""
    if not _has(conn, "extracted_financials"):
        return None, None
    rows = conn.execute(
        f"SELECT scope, period_ending, {','.join(_FIELDS)} FROM extracted_financials "
        "WHERE symbol=? ORDER BY period_ending DESC", (symbol,)).fetchall()
    by_scope: dict[str, list] = {}
    for r in rows:
        by_scope.setdefault(r[0], []).append(r)
    srows = by_scope.get("consolidated") or by_scope.get("standalone") or []
    if not srows:
        return None, None

    def _todict(r):
        return dict(zip(_FIELDS, r[2:]))

    now = _todict(srows[0])
    now_pe = date.fromisoformat(srows[0][1][:10])
    prior = None
    for r in srows[1:]:
        if (now_pe - date.fromisoformat(r[1][:10])).days >= 330:
            prior = _todict(r)
            break
    return now, prior


_COLUMNS = ("symbol", "f_score", "f_signals", "interest_coverage", "current_ratio",
            "debt_equity", "bs_score", "distress", "updated_date")


def run_strength_pass(conn: sqlite3.Connection, symbols, *, now=None) -> dict:
    today = (now or market_hours.now_ist()).date().isoformat()
    placeholders = ",".join("?" * len(_COLUMNS))
    written = scored = 0
    for sym in symbols:
        cur, prior = load_periods(conn, sym)
        s = compute_strength(cur, prior)
        conn.execute(
            f"INSERT OR REPLACE INTO stock_strength ({','.join(_COLUMNS)}) VALUES ({placeholders})",
            (sym, s["f_score"], s["f_signals"], s["interest_coverage"], s["current_ratio"],
             s["debt_equity"], s["bs_score"], ",".join(s["distress"]) or None, today))
        written += 1
        if s["bs_score"] is not None or s["f_score"] is not None:
            scored += 1
    conn.commit()
    return {"symbols": written, "scored": scored}


def run_strength_job(db_path: str) -> dict:
    from ..indicators.universe import fno_plus_nifty500
    conn = open_db(db_path)
    try:
        return run_strength_pass(conn, fno_plus_nifty500(conn))
    finally:
        conn.close()


def register_strength_job(scheduler: BlockingScheduler, db_path: str) -> str:
    """Nightly 18:05 IST financial-strength compute (after the 18:00 quality job)."""
    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            return
        try:
            log.info("stock_strength", **run_strength_job(db_path))
        except Exception:
            log.exception("stock_strength_failed")

    scheduler.add_job(
        _tick, trigger=CronTrigger(hour=18, minute=5, timezone=market_hours.IST),
        id=JOB_ID, max_instances=1, coalesce=True, replace_existing=True,
    )
    return JOB_ID
