"""Parse NSE result XBRL into canonical financials (authoritative ground truth).

NSE files an INDAS XBRL instance per result (namespace ``in-bse-fin:``), separate
for standalone vs consolidated. Facts are absolute rupees; we normalise to crore
(EPS stays per-share). This is the *model-independent* label source the eval
needs — the numbers are the company's own structured submission, not an LLM read.

    data = open("INDAS_*.xml", "rb").read()
    parse_xbrl(data)
    # -> {"scope": "standalone", "period_ending": "2026-03-31",
    #     "fields": {"revenue_cr": 254.97, "pat_cr": 27.36, "eps_basic": 4.2, ...}}
"""
from __future__ import annotations

import datetime as _dt
import xml.etree.ElementTree as ET

_RUPEES_TO_CRORE = 1e7

# in-bse-fin local tag name -> canonical *_cr field (amounts, scaled by 1e7).
_AMOUNT_TAGS = {
    "RevenueFromOperations": "revenue_cr",
    "OtherIncome": "other_income_cr",
    "Income": "total_income_cr",
    "Expenses": "total_expenses_cr",
    "ProfitBeforeTax": "pbt_cr",
    "TaxExpense": "tax_cr",
    "ProfitLossForPeriod": "pat_cr",
    "ComprehensiveIncomeForThePeriod": "total_comprehensive_income_cr",
}
# EPS (per-share rupees, NOT scaled). Headline = continuing+discontinued; fall
# back to continuing-only when the combined tag is absent.
_EPS_TAGS = {
    "eps_basic": [
        "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
        "BasicEarningsLossPerShareFromContinuingOperations",
    ],
    "eps_diluted": [
        "DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
        "DilutedEarningsLossPerShareFromContinuingOperations",
    ],
}

# A current-quarter context is a 3-month duration (allow month-length wobble).
_QUARTER_MIN_DAYS = 80
_QUARTER_MAX_DAYS = 100


def _local(tag: str) -> str:
    """Strip the XML namespace from an element tag."""
    return tag.rsplit("}", 1)[-1]


def _to_float(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        return float(text.strip())
    except (ValueError, AttributeError):
        return None


def _parse_contexts(root) -> dict[str, dict]:
    """{context_id: {start, end, instant, has_dim}} for every <context>."""
    out: dict[str, dict] = {}
    for el in root.iter():
        if _local(el.tag) != "context":
            continue
        ctx = {"start": None, "end": None, "instant": None, "has_dim": False}
        for sub in el.iter():
            lt = _local(sub.tag)
            if lt == "startDate":
                ctx["start"] = (sub.text or "").strip()
            elif lt == "endDate":
                ctx["end"] = (sub.text or "").strip()
            elif lt == "instant":
                ctx["instant"] = (sub.text or "").strip()
            elif lt == "explicitMember":
                ctx["has_dim"] = True       # dimensioned (segment/expense breakdown)
        out[el.get("id")] = ctx
    return out


def _quarter_days(ctx: dict) -> int | None:
    if not ctx["start"] or not ctx["end"]:
        return None
    try:
        s = _dt.date.fromisoformat(ctx["start"][:10])
        e = _dt.date.fromisoformat(ctx["end"][:10])
    except ValueError:
        return None
    return (e - s).days


def _current_quarter_context(contexts: dict[str, dict]) -> str | None:
    """The un-dimensioned ~3-month context with the latest end date.

    The headline P&L facts hang off this; dimensioned (segment/expense) and
    year-to-date contexts are excluded, and 'latest end' avoids the year-ago
    quarter."""
    cands = [
        (cid, ctx) for cid, ctx in contexts.items()
        if not ctx["has_dim"]
        and (d := _quarter_days(ctx)) is not None
        and _QUARTER_MIN_DAYS <= d <= _QUARTER_MAX_DAYS
    ]
    if not cands:
        return None
    return max(cands, key=lambda kc: kc[1]["end"])[0]


def parse_xbrl(data: bytes | str) -> dict | None:
    """Parse one XBRL instance → {scope, period_ending, fields} or None.

    Returns None when the document can't be parsed or has no current-quarter P&L
    context. ``scope`` is 'standalone'/'consolidated' (from the filing-level
    NatureOfReport tag); ``fields`` are canonical *_cr (+ EPS).
    """
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None

    contexts = _parse_contexts(root)
    cur_id = _current_quarter_context(contexts)
    if cur_id is None:
        return None

    scope = None
    raw: dict[str, float] = {}
    for el in root.iter():
        lt = _local(el.tag)
        if lt == "NatureOfReportStandaloneConsolidated" and scope is None:
            scope = (el.text or "").strip().lower()
        if el.get("contextRef") == cur_id:
            val = _to_float(el.text)
            if val is not None and lt not in raw:
                raw[lt] = val

    fields: dict[str, float] = {}
    for tag, canon in _AMOUNT_TAGS.items():
        if tag in raw:
            fields[canon] = round(raw[tag] / _RUPEES_TO_CRORE, 2)
    for canon, tags in _EPS_TAGS.items():
        for tag in tags:
            if tag in raw:
                fields[canon] = raw[tag]
                break

    period = contexts[cur_id]["end"]
    return {
        "scope": "consolidated" if scope and "consolid" in scope else "standalone",
        "period_ending": period[:10] if period else None,
        "fields": fields,
    }
