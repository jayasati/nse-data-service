"""
Daily stock-profile builder (FEATURE_CHECKLIST Phase 4, Week 15, task 15.4).

Nightly at 19:30, joins every Layer-4 output into one wide row per symbol in
`stock_profile_daily` — the ML training archive. Each source is read as its
latest row (settled for the session); pattern flags are "did this pattern occur
today". Missing sources leave their columns NULL (graceful — never crashes the
roll-up). Run after the 18:00/18:30/19:00 jobs so it captures their output.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from ..scheduler import market_hours
from ..storage.db import open_db

log = structlog.get_logger()
JOB_ID = "stock_profile_daily"

# (table, order-by column, [columns to pull]). Pulled columns map 1:1 to profile
# columns except where renamed in _RENAME.
_SOURCES = [
    ("stock_fundamentals", "updated_date", [
        "quality_score", "revenue_growth_yoy", "roe", "roce", "debt_equity",
        "pe_ratio", "market_cap", "promoter_holding", "loss_making", "high_debt"]),
    ("delivery_conviction", "session_date", [
        "delivery_ratio", "delivery_ratio_5d_avg", "delivery_ratio_z_score",
        "delivery_trend", "delivery_conviction_score"]),
    ("indicator_eod", "date", [
        "ema9", "ema21", "bb_upper", "bb_lower", "bb_width", "bb_squeeze", "adx",
        "di_plus", "di_minus", "supertrend", "supertrend_dir", "obv",
        "vol_sma20", "volume_ratio"]),
    ("indicator_sma", "date", ["sma_20", "sma_50", "sma_200"]),
    ("indicator_rsi", "date", ["rsi_14"]),
    ("indicator_macd", "date", ["macd", "macd_signal", "macd_hist"]),
    ("indicator_levels", "session_date", [
        "pdh", "pdl", "high_52w", "low_52w", "days_since_52w_high",
        "range_5d_high", "range_5d_low", "range_20d_high", "range_20d_low",
        "nearest_round_number", "round_number_prior_failures",
        "r1", "r2", "s1", "s2"]),
    ("indicator_live", "updated_at", [
        "trend_regime", "momentum_state", "price_vs_vwap", "atr_14_daily"]),
]

_PATTERN_FLAGS = {
    "had_inside_bar": "inside_bar",
    "had_volume_dryup": "volume_dryup",
    "had_bullish_divergence": "bullish_divergence",
    "had_bearish_divergence": "bearish_divergence",
    "near_support": "near_support",
    "near_resistance": "near_resistance",
}

_ALL_COLUMNS = (
    ["symbol", "session_date"]
    + [c for _, _, cols in _SOURCES for c in cols]
    + list(_PATTERN_FLAGS)
    + ["updated_at"]
)


def _has(conn, name) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _latest(conn, table, order_col, cols, symbol) -> dict:
    if not _has(conn, table):
        return {}
    try:
        row = conn.execute(
            f"SELECT {','.join(cols)} FROM {table} WHERE symbol = ? "
            f"ORDER BY {order_col} DESC LIMIT 1", (symbol,),
        ).fetchone()
    except sqlite3.OperationalError:
        return {}        # source table missing a column (schema drift) -> NULLs
    return dict(zip(cols, row)) if row else {}


def _pattern_flags(conn, symbol, session_date) -> dict:
    flags = {k: 0 for k in _PATTERN_FLAGS}
    if not _has(conn, "patterns"):
        return flags
    types = {r[0] for r in conn.execute(
        "SELECT DISTINCT pattern_type FROM patterns WHERE symbol = ? AND session_date = ?",
        (symbol, session_date),
    )}
    for col, ptype in _PATTERN_FLAGS.items():
        flags[col] = 1 if ptype in types else 0
    return flags


def build_profile_row(conn, symbol, session_date, now_iso) -> dict:
    row: dict = {c: None for c in _ALL_COLUMNS}
    row["symbol"], row["session_date"], row["updated_at"] = symbol, session_date, now_iso
    for table, order_col, cols in _SOURCES:
        row.update(_latest(conn, table, order_col, cols, symbol))
    row.update(_pattern_flags(conn, symbol, session_date))
    return row


def run_profile_pass(conn: sqlite3.Connection, symbols, *, now: datetime | None = None) -> dict:
    now = now or market_hours.now_ist()
    session_date = now.date().isoformat()
    now_iso = now.isoformat()
    placeholders = ",".join("?" * len(_ALL_COLUMNS))
    written = 0
    for sym in symbols:
        row = build_profile_row(conn, sym, session_date, now_iso)
        conn.execute(
            f"INSERT OR REPLACE INTO stock_profile_daily ({','.join(_ALL_COLUMNS)}) "
            f"VALUES ({placeholders})",
            tuple(row[c] for c in _ALL_COLUMNS),
        )
        written += 1
    conn.commit()
    return {"symbols": written, "session_date": session_date}


def run_profile_job(db_path: str) -> dict:
    from ..indicators.universe import fno_plus_nifty500
    conn = open_db(db_path)
    try:
        return run_profile_pass(conn, fno_plus_nifty500(conn))
    finally:
        conn.close()


def register_profile_builder(scheduler: BlockingScheduler, db_path: str) -> str:
    """Nightly 19:30 IST profile roll-up (task 15.4). Trading-day gated."""
    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            return
        try:
            log.info("stock_profile_daily", **run_profile_job(db_path))
        except Exception:
            log.exception("stock_profile_daily_failed")

    scheduler.add_job(
        _tick, trigger=CronTrigger(hour=19, minute=30, timezone=market_hours.IST),
        id=JOB_ID, max_instances=1, coalesce=True, replace_existing=True,
    )
    return JOB_ID
