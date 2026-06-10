"""BFSI-aware result-quality alert card (Phase 5 — Week 17.5, S5).

The headline-PAT-only card is exactly what hid the SBI 8-May print. For a
result_quality signal we render the operating story the market actually prices:
NII, pre-provision operating profit (PPOP), provisions, the treasury line, and
asset quality (GNPA/NNPA) — with the YoY/QoQ moves that drove the verdict.

    text = format_result_quality(conn, symbol="SBIN", direction="short")

Stateless: re-reads extracted_financials + re-classifies, so it needs nothing
persisted on the signal row beyond (symbol, signal_type, direction).
"""
from __future__ import annotations

import json
import sqlite3

from ..fundamentals.sectors import classify_result
from ..market.sector_map import is_bfsi

# Confidence assigned to a result-quality signal — these signals carry no
# price/volume, so the live scorer doesn't apply. A clean two-sided beat gets a
# solid base; a low-quality verdict scales with the number of corroborating
# flags (operating miss + provision-propped + treasury).
_HIGH_CONF = 0.72
_CONF_BY_FLAGS = {1: 0.66, 2: 0.74, 3: 0.82}


def verdict_confidence(label: str, n_flags: int) -> float:
    if label == "high":
        return _HIGH_CONF
    return _CONF_BY_FLAGS.get(min(max(n_flags, 1), 3), 0.82)


def _fmt_cr(v) -> str:
    if v is None:
        return "n/a"
    return f"₹{v:,.0f} cr"


def _fmt_pct(growth: dict, key: str) -> str:
    v = growth.get(key)
    return f"{v:+.1f}%" if isinstance(v, (int, float)) else "n/a"


def _load_row(conn: sqlite3.Connection, symbol: str) -> sqlite3.Row | None:
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


def format_result_quality(
    conn: sqlite3.Connection, *, symbol: str, direction: str,
) -> tuple[str, float]:
    """Build the alert text + a confidence for a result_quality signal.

    Returns ("", 0.0) when the underlying financials row can't be found, so the
    caller can skip the send gracefully."""
    row = _load_row(conn, symbol)
    if row is None:
        return "", 0.0
    keys = row.keys()
    # Growth from the PDF's own comparative columns (growth_json, text-anchored at
    # extraction), mirroring the detector; stored history only if the filing had
    # no comparatives — so the card's verdict matches the fired signal.
    period_ending = row["period_ending"] if "period_ending" in keys else None
    growth = {}
    if "growth_json" in keys and row["growth_json"]:
        try:
            growth = json.loads(row["growth_json"])
        except (ValueError, TypeError):
            growth = {}
    if not growth and period_ending:
        from ..fundamentals.from_results import quarter_growth
        growth = quarter_growth(conn, symbol, period_ending, "standalone")
    bfsi = is_bfsi(symbol)   # drives the BFSI-specific card section below
    treasury = row["profit_on_sale_of_investments_cr"] if "profit_on_sale_of_investments_cr" in keys else None
    # The filing's press-release narrative (guidance/volumes/FDA/order book —
    # P7, lifted at extraction time), folded into the verdict like the detector.
    narrative = None
    if "narrative_json" in keys and row["narrative_json"]:
        try:
            narrative = json.loads(row["narrative_json"])
        except (ValueError, TypeError):
            narrative = None
    # Same sector-routed verdict the detector fired on (BFSI rule, or out-of-scope
    # neutral for sectors not yet built) — keeps the card consistent with the signal.
    verdict = classify_result(
        symbol, growth, {"profit_on_sale_of_investments_cr": treasury},
        narrative=narrative,
    )

    if direction == "short" or verdict.label == "low":
        kind = "Weak Result" if "result_miss" in verdict.flags else "Low-Quality Result"
        head = f"🔻 {symbol} — {kind} (SHORT bias)"
    else:
        head = f"🟢 {symbol} — Clean Result (LONG bias)"

    lines = [head, verdict.summary]
    pat = row["pat_cr"] if "pat_cr" in keys else None
    lines.append(f"PAT {_fmt_cr(pat)} ({_fmt_pct(growth, 'yoy_pat_pct')} YoY)")

    if bfsi:
        nii = row["net_interest_income_cr"] if "net_interest_income_cr" in keys else None
        ppop = row["operating_profit_cr"] if "operating_profit_cr" in keys else None
        prov = row["provisions_cr"] if "provisions_cr" in keys else None
        gnpa = row["gross_npa_pct"] if "gross_npa_pct" in keys else None
        nnpa = row["net_npa_pct"] if "net_npa_pct" in keys else None
        lines.append(
            f"• PPOP {_fmt_cr(ppop)} ({_fmt_pct(growth, 'yoy_ppop_pct')} YoY, "
            f"{_fmt_pct(growth, 'qoq_ppop_pct')} QoQ)"
        )
        # NII sanity: a bank's NII is ~30–40% of total income. If the extracted
        # value is well below that, the interest-earned line was misread — show
        # it as uncertain rather than print a misleading YoY off a bad level.
        ti = row["total_income_cr"] if "total_income_cr" in keys else None
        nii_reliable = (
            nii is not None and isinstance(ti, (int, float)) and ti and (nii / ti) >= 0.28
        )
        if nii is None:
            pass
        elif nii_reliable:
            lines.append(f"• NII {_fmt_cr(nii)} ({_fmt_pct(growth, 'yoy_nii_pct')} YoY)")
        else:
            lines.append(f"• NII {_fmt_cr(nii)} (read uncertain — excluded from verdict)")
        lines.append(f"• Provisions {_fmt_cr(prov)} ({_fmt_pct(growth, 'yoy_provisions_pct')} YoY)")
        if isinstance(treasury, (int, float)):
            tag = "loss" if treasury < 0 else "gain"
            lines.append(f"• Treasury: {tag} {_fmt_cr(abs(treasury))} on investments")
        if gnpa is not None or nnpa is not None:
            g = f"{gnpa:.2f}%" if gnpa is not None else "n/a"
            n = f"{nnpa:.2f}%" if nnpa is not None else "n/a"
            lines.append(f"• GNPA {g} / NNPA {n}")
        # NIM is not extracted from the P&L (lives in the press release); NII YoY
        # is shown as the margin proxy. See Week 17.5 S8 follow-up.
        lines.append("ℹ️ Market prices the operating line, not headline PAT.")

    narrative_line = _format_narrative(narrative)
    if narrative_line:
        lines.append(narrative_line)

    return "\n".join(lines), verdict_confidence(verdict.label, len(verdict.flags))


# (key, format) for the numeric narrative KPIs shown on the 📰 line, in card
# order. Verdict-movers (guidance/FDA/volume) come first, sector KPIs after.
_NARRATIVE_NUM_FMT: tuple[tuple[str, str], ...] = (
    ("volume_growth", "volumes {:+.1f}%"),
    ("order_inflow", "order inflow ₹{:,.0f} cr"),
    ("order_book", "order book ₹{:,.0f} cr"),
    ("dividend", "dividend ₹{:g}/share"),
    ("cc_revenue_growth_pct", "cc-rev {:+.1f}%"),
    ("tcv_usd_mn", "TCV ${:,.0f} mn"),
    ("attrition_pct", "attrition {:.1f}%"),
    ("grm_usd_bbl", "GRM ${:.1f}/bbl"),
    ("ebitda_per_tonne", "EBITDA/t ₹{:,.0f}"),
    ("us_sales_growth_pct", "US sales {:+.1f}%"),
)


def _format_narrative(narrative: dict | None) -> str | None:
    """One compact card line for the press-release signals (P7), e.g.
    ``📰 Guidance raised · volumes +4.5% · TCV $3,100 mn (press release)``."""
    if not narrative:
        return None
    parts: list[str] = []
    if narrative.get("guidance"):
        parts.append(f"Guidance {narrative['guidance']}")
    if narrative.get("fda_status"):
        parts.append(f"USFDA: {narrative['fda_status'].replace('_', ' ')}")
    for key, fmt in _NARRATIVE_NUM_FMT:
        v = narrative.get(key)
        if isinstance(v, (int, float)):
            parts.append(fmt.format(v))
    if narrative.get("mgmt_tone"):
        parts.append(f"mgmt tone {narrative['mgmt_tone']}")
    if not parts:
        return None
    line = "📰 " + " · ".join(parts)
    sources = narrative.get("_sources")
    if isinstance(sources, list) and sources:
        line += f" ({', '.join(s.lower() for s in sources)})"
    return line
