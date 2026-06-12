"""Result beat/miss alert card (FEATURE_CHECKLIST Week 18, task 18.6).

The checklist format, verbatim:

    🟢 {SYMBOL} — Result Beat
    Revenue: ₹{revenue}Cr (+{yoy}% YoY)
    PAT: ₹{pat}Cr | EPS: ₹{eps}
    Earnings Quality: {HIGH/LOW}

    RSI(5m): {rsi} | Regime: {regime}
    Confidence: {tier} ({score})

Stateless like the result-quality card: re-reads the latest extracted
financials and re-derives the quality flag, so nothing beyond
(symbol, signal_type) needs to be persisted on the signal row.
"""
from __future__ import annotations

import json
import sqlite3

from ..fundamentals.sectors import classify_result


def _fmt_num(v, *, signed: bool = False) -> str:
    if v is None:
        return "n/a"
    return f"{v:+,.1f}" if signed else f"{v:,.1f}"


def _tier(confidence: float) -> str:
    if confidence >= 0.80:
        return "High"
    if confidence >= 0.72:
        return "Medium"
    return "Low"


def _latest_financials(conn: sqlite3.Connection, symbol: str):
    try:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM extracted_financials "
            "WHERE symbol = ? AND scope = 'standalone' "
            "ORDER BY extracted_at DESC LIMIT 1",
            (symbol,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None


def format_result_alert(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    signal_type: str,
    context: dict,
    confidence: float,
) -> str:
    """Build the 18.6 card for a result_beat / result_miss signal.

    Returns "" when the financials row is gone (caller skips the send)."""
    row = _latest_financials(conn, symbol)
    if row is None:
        return ""
    keys = row.keys()

    growth = {}
    if "growth_json" in keys and row["growth_json"]:
        try:
            growth = json.loads(row["growth_json"])
        except (ValueError, TypeError):
            growth = {}
    if not growth and row["period_ending"]:
        from ..fundamentals.from_results import quarter_growth
        growth = quarter_growth(conn, symbol, row["period_ending"], "standalone")

    narrative = None
    if "narrative_json" in keys and row["narrative_json"]:
        try:
            narrative = json.loads(row["narrative_json"])
        except (ValueError, TypeError):
            narrative = None
    treasury = (row["profit_on_sale_of_investments_cr"]
                if "profit_on_sale_of_investments_cr" in keys else None)
    verdict = classify_result(
        symbol, growth, {"profit_on_sale_of_investments_cr": treasury},
        narrative=narrative,
    )
    quality_flag = "LOW" if verdict.label == "low" else "HIGH"

    is_beat = signal_type == "result_beat"
    head = (f"🟢 {symbol} — Result Beat" if is_beat
            else f"🔴 {symbol} — Result Miss")
    yoy = growth.get("yoy_revenue_pct")
    eps = row["eps_basic"] if "eps_basic" in keys else None
    rsi = context.get("rsi_5m")

    return (
        f"{head}\n"
        f"Revenue: ₹{_fmt_num(row['revenue_cr'])}Cr "
        f"({_fmt_num(yoy, signed=True)}% YoY)\n"
        f"PAT: ₹{_fmt_num(row['pat_cr'])}Cr | EPS: ₹{_fmt_num(eps)}\n"
        f"Earnings Quality: {quality_flag}\n"
        f"\n"
        f"RSI(5m): {_fmt_num(rsi)} | "
        f"Regime: {context.get('trend_regime') or 'n/a'}\n"
        f"Confidence: {_tier(confidence)} ({confidence:.2f})"
    )
