"""
Market regime classifier (FEATURE_CHECKLIST Phase 2, Week 7, tasks 7.2/7.3/7.6).

Every 5 minutes during market hours this reads the live market-context feeds and
writes one `market_state` row: where the index is, what volatility is doing,
breadth, and an `overall_regime` tag the confidence scorer (task 7.5) and the
morning brief (Week 9) consume.

Inputs (all best-effort — a missing feed contributes a neutral/None factor
rather than erroring, so the classifier still produces a row):

    raw_india_vix          VIX level + direction vs ~30 min ago        (7.2)
    raw_indices            NIFTY 50 level + session direction          (7.2)
    raw_gift_nifty         GIFT Nifty gap vs previous close            (7.2)
    raw_advances_declines  advance/decline ratio (breadth)            (7.2)
    indicator_live         % of symbols trading above their VWAP       (7.2)
    raw_fii_dii            today's partial FII net flow (when present) (7.2)

Pure classification helpers (top of file) are unit-tested directly; the DB
readers + `run_regime_pass` glue them to SQLite.

Registered from main.py via `register_regime_job` (IntervalTrigger 300s,
internally gated on is_market_open — same pattern as the live indicator job).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..scheduler import market_hours
from ..scheduler.market_hours import is_market_open
from ..storage.db import open_db

log = structlog.get_logger()

JOB_ID = "market_regime"
_INTERVAL_SECONDS = 300

# --- deadbands (keep tiny moves from flipping a direction every tick) --------
_NIFTY_FLAT_PCT = 0.15      # |session %| below this is 'flat'
_VIX_FLAT_POINTS = 0.25     # |Δvix vs 30m ago| below this is 'flat'
_GIFT_FLAT_PCT = 0.30       # |gap %| below this is 'neutral'
_VIX_LOOKBACK_SECS = 30 * 60

# --- task 7.3: VIX state thresholds ------------------------------------------
def classify_vix_state(vix: float | None) -> str | None:
    """VIX regime band (task 7.3)."""
    if vix is None:
        return None
    if vix < 12:
        return "low"          # complacent — option sellers win, mean-reversion
    if vix < 18:
        return "normal"
    if vix < 22:
        return "elevated"     # caution
    if vix < 28:
        return "high"         # trending moves, reduce size
    return "extreme"          # panic, full defensive


# --- direction helpers -------------------------------------------------------

def nifty_direction(pct_change: float | None) -> str | None:
    if pct_change is None:
        return None
    if pct_change > _NIFTY_FLAT_PCT:
        return "up"
    if pct_change < -_NIFTY_FLAT_PCT:
        return "down"
    return "flat"


def vix_direction(current: float | None, prior: float | None) -> str | None:
    """Falling VIX is risk-on; rising is risk-off. Compared vs ~30 min ago."""
    if current is None or prior is None:
        return None
    delta = current - prior
    if delta > _VIX_FLAT_POINTS:
        return "rising"
    if delta < -_VIX_FLAT_POINTS:
        return "falling"
    return "flat"


def gift_signal(gap_pct: float | None) -> str:
    """GIFT Nifty gap vs prev close → directional pre-open lean."""
    if gap_pct is None:
        return "neutral"
    if gap_pct > _GIFT_FLAT_PCT:
        return "aligned_bull"
    if gap_pct < -_GIFT_FLAT_PCT:
        return "aligned_bear"
    return "neutral"


# --- task 7.2: overall regime ------------------------------------------------

def classify_regime(
    *,
    nifty_dir: str | None,
    vix_dir: str | None,
    vix_level: float | None,
    ad_ratio: float | None,
    pct_above_vwap: float | None,
    gift: str | None = None,
) -> tuple[str, float]:
    """(overall_regime, regime_confidence) per the task-7.2 rule stack.

        VIX > 25                                               -> panic
        nifty up   & vix falling & AD > 1.5 & >60% above VWAP  -> risk_on
        nifty down & vix rising  & AD < 0.7                    -> risk_off
        otherwise                                              -> neutral

    `regime_confidence` ∈ [0,1] is how strongly the available factors agree:
    the net directional vote magnitude over the factors we actually have.
    """
    if vix_level is not None and vix_level > 25:
        return "panic", 0.9   # hard rule — overrides everything else

    if (nifty_dir == "up" and vix_dir == "falling"
            and ad_ratio is not None and ad_ratio > 1.5
            and pct_above_vwap is not None and pct_above_vwap > 60):
        return "risk_on", _agreement(nifty_dir, vix_dir, ad_ratio, pct_above_vwap, gift)

    if (nifty_dir == "down" and vix_dir == "rising"
            and ad_ratio is not None and ad_ratio < 0.7):
        return "risk_off", _agreement(nifty_dir, vix_dir, ad_ratio, pct_above_vwap, gift)

    return "neutral", _agreement(nifty_dir, vix_dir, ad_ratio, pct_above_vwap, gift)


def divergence_flags(
    nifty_dir: str | None, vix_dir: str | None, bank_pct: float | None,
) -> tuple[bool, bool, str | None]:
    """Intermarket divergence (task 9.6): (fragile_rally, internal_weakness, note).

    fragile_rally     — Nifty up while VIX is *also* rising (rally not trusted).
    internal_weakness — banks (NIFTY BANK) down while the index holds flat.
    """
    fragile = nifty_dir == "up" and vix_dir == "rising"
    bank_down = bank_pct is not None and bank_pct < -_NIFTY_FLAT_PCT
    internal_weak = bool(bank_down and nifty_dir == "flat")

    notes = []
    if fragile:
        notes.append("⚠ fragile rally (Nifty↑ with VIX↑)")
    if internal_weak:
        notes.append("⚠ internal weakness (banks↓, Nifty flat)")
    return fragile, internal_weak, (" | ".join(notes) or None)


def _agreement(nifty_dir, vix_dir, ad_ratio, pct_above_vwap, gift) -> float:
    """Net |vote| / number-of-available-factors. +1 bullish, −1 bearish each.

    For a strong regime all votes line up (→ ~1.0); for a mixed tape they cancel
    (→ ~0.0), which reads as a low-confidence / genuinely-neutral market.
    """
    votes: list[int] = []
    if nifty_dir in ("up", "down"):
        votes.append(1 if nifty_dir == "up" else -1)
    if vix_dir in ("falling", "rising"):
        votes.append(1 if vix_dir == "falling" else -1)
    if ad_ratio is not None:
        votes.append(1 if ad_ratio > 1.5 else -1 if ad_ratio < 0.7 else 0)
    if pct_above_vwap is not None:
        votes.append(1 if pct_above_vwap > 60 else -1 if pct_above_vwap < 40 else 0)
    if gift in ("aligned_bull", "aligned_bear"):
        votes.append(1 if gift == "aligned_bull" else -1)

    if not votes:
        return 0.0
    return round(abs(sum(votes)) / len(votes), 3)


# ============================================================================
# DB readers
# ============================================================================

def _latest_vix_and_prior(conn: sqlite3.Connection) -> tuple[float | None, float | None]:
    """(latest VIX, VIX ~30 min before the latest reading)."""
    row = conn.execute(
        "SELECT as_of, vix FROM raw_india_vix ORDER BY as_of DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None, None
    latest_as_of, latest_vix = row
    prior = conn.execute(
        "SELECT vix FROM raw_india_vix WHERE as_of <= ? ORDER BY as_of DESC LIMIT 1",
        (latest_as_of - _VIX_LOOKBACK_SECS,),
    ).fetchone()
    return latest_vix, (prior[0] if prior else None)


def _latest_index_pct(conn: sqlite3.Connection, index_symbol: str) -> float | None:
    row = conn.execute(
        "SELECT pct_change FROM raw_indices WHERE index_symbol = ? "
        "ORDER BY as_of DESC LIMIT 1",
        (index_symbol,),
    ).fetchone()
    return row[0] if row else None


def _latest_nifty_pct(conn: sqlite3.Connection) -> float | None:
    return _latest_index_pct(conn, "NIFTY 50")


def _gift_gap_pct(conn: sqlite3.Connection) -> float | None:
    """GIFT Nifty's latest gap vs its previous close, in %."""
    row = conn.execute(
        "SELECT curr_value, close_value, pct_change FROM raw_gift_nifty "
        "ORDER BY as_of DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    curr, close, pct = row
    if pct is not None:
        return pct
    if curr is not None and close:
        return (curr / close - 1.0) * 100.0
    return None


def _ad_ratio(conn: sqlite3.Connection) -> float | None:
    row = conn.execute(
        "SELECT advances, declines FROM raw_advances_declines "
        "ORDER BY as_of DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    advances, declines = row
    if not declines:        # 0 or None — avoid div-by-zero
        return None
    return round(advances / declines, 3)


def _pct_above_vwap(conn: sqlite3.Connection) -> float | None:
    """% of indicator_live symbols whose price is above VWAP."""
    row = conn.execute(
        "SELECT "
        "  SUM(CASE WHEN price_vs_vwap = 'above' THEN 1 ELSE 0 END), "
        "  SUM(CASE WHEN price_vs_vwap IN ('above','below') THEN 1 ELSE 0 END) "
        "FROM indicator_live"
    ).fetchone()
    above, total = row if row else (None, None)
    if not total:
        return None
    return round(100.0 * (above or 0) / total, 2)


def _fii_partial(conn: sqlite3.Connection, day: str) -> float | None:
    """Today's FII net flow if raw_fii_dii already carries it (usually EOD)."""
    row = conn.execute(
        "SELECT net_value FROM raw_fii_dii WHERE date = ? AND category = 'FII'",
        (day,),
    ).fetchone()
    return row[0] if row else None


# ============================================================================
# Pass orchestration
# ============================================================================

def build_market_state(conn: sqlite3.Connection, now: datetime) -> dict:
    """Read every feed, classify, and return the market_state row (not persisted)."""
    vix, vix_prior = _latest_vix_and_prior(conn)
    nifty_pct = _latest_nifty_pct(conn)
    ad = _ad_ratio(conn)
    above = _pct_above_vwap(conn)
    gift = gift_signal(_gift_gap_pct(conn))

    n_dir = nifty_direction(nifty_pct)
    v_dir = vix_direction(vix, vix_prior)
    regime, confidence = classify_regime(
        nifty_dir=n_dir, vix_dir=v_dir, vix_level=vix,
        ad_ratio=ad, pct_above_vwap=above, gift=gift,
    )
    bank_pct = _latest_index_pct(conn, "NIFTY BANK")
    fragile, internal_weak, warnings = divergence_flags(n_dir, v_dir, bank_pct)

    return {
        "as_of": now.isoformat(),
        "nifty_direction": n_dir,
        "nifty_return_pct": nifty_pct,
        "vix_level": vix,
        "vix_state": classify_vix_state(vix),
        "vix_direction": v_dir,
        "gift_nifty_signal": gift,
        "advance_decline_ratio": ad,
        "pct_above_vwap": above,
        "fii_partial_day": _fii_partial(conn, now.date().isoformat()),
        "overall_regime": regime,
        "regime_confidence": confidence,
        "updated_at": now.isoformat(),
        "fragile_rally": int(fragile),
        "internal_weakness": int(internal_weak),
        "regime_warnings": warnings,
    }


_COLUMNS = (
    "as_of", "nifty_direction", "nifty_return_pct", "vix_level", "vix_state",
    "vix_direction", "gift_nifty_signal", "advance_decline_ratio",
    "pct_above_vwap", "fii_partial_day", "overall_regime", "regime_confidence",
    "updated_at", "fragile_rally", "internal_weakness", "regime_warnings",
)


def _upsert(conn: sqlite3.Connection, state: dict) -> None:
    placeholders = ",".join("?" * len(_COLUMNS))
    conn.execute(
        f"INSERT OR REPLACE INTO market_state ({','.join(_COLUMNS)}) "
        f"VALUES ({placeholders})",
        tuple(state[c] for c in _COLUMNS),
    )


def run_regime_pass(conn: sqlite3.Connection, *, now: datetime | None = None) -> dict:
    """One classification + upsert. Returns the written market_state row."""
    now = now or market_hours.now_ist()
    state = build_market_state(conn, now)
    _upsert(conn, state)
    conn.commit()
    return state


def latest_market_state(conn: sqlite3.Connection) -> dict | None:
    """Most recent market_state row as a dict, or None (tolerant of no table)."""
    try:
        cur = conn.execute("SELECT * FROM market_state ORDER BY as_of DESC LIMIT 1")
    except sqlite3.OperationalError:
        return None
    row = cur.fetchone()
    if not row:
        return None
    cols = [c[0] for c in cur.description]
    return dict(zip(cols, row))


# ============================================================================
# Scheduling
# ============================================================================

def run_regime_job(db_path: str) -> dict:
    if not is_market_open():
        return {"skipped": "market_closed"}
    conn = open_db(db_path)
    try:
        return run_regime_pass(conn)
    finally:
        conn.close()


def register_regime_job(scheduler: BlockingScheduler, db_path: str) -> str:
    """Attach the 5-minute market-regime job (task 7.6). market-hours gated."""
    def _tick():
        try:
            report = run_regime_job(db_path)
            if "skipped" not in report:
                log.info("market_regime_tick",
                         regime=report["overall_regime"],
                         vix_state=report["vix_state"],
                         confidence=report["regime_confidence"])
        except Exception:
            log.exception("market_regime_failed")

    scheduler.add_job(
        _tick,
        trigger=IntervalTrigger(seconds=_INTERVAL_SECONDS),
        id=JOB_ID,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return JOB_ID
