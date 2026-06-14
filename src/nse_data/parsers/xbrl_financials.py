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

# canonical *_cr field -> XBRL local tag names in PRIORITY order (first present
# wins). Amounts are absolute rupees, scaled by 1e7. Covers both the commercial
# INDAS taxonomy and the BANKING taxonomy (banks tag the same concepts
# differently — e.g. ProfitLossForThePeriod, not ProfitLossForPeriod).
_AMOUNT_TAGS = {
    "revenue_cr": ["RevenueFromOperations"],
    "other_income_cr": ["OtherIncome"],
    "total_income_cr": ["Income", "TotalIncome", "OperatingIncome"],   # OperatingIncome = general insurance
    "total_expenses_cr": ["Expenses", "TotalExpenses"],
    "pbt_cr": ["ProfitBeforeTax",
               "ProfitBeforeExtraordinaryItemsAndTax",            # bank
               "ProfitLossFromOrdinaryActivitiesBeforeTax",       # bank alt
               "ProfitLossBeforeTax",                             # life insurance (IRDAI)
               "ProfitOrLossBeforeTax"],                          # general insurance
    "tax_cr": ["TaxExpense"],
    "pat_cr": ["ProfitLossForPeriod",
               "ProfitLossForThePeriod",                           # bank
               "ProfitLossAfterTaxesMinorityInterestAndShareOfProfitLossOfAssociates",  # consolidated
               "ProfitLossAfterTaxAndExtraordinaryItems",          # life insurance
               "ProfitLossAfterTaxBeforeExtraordinaryItems",       # life insurance (no EO items)
               "ProfitLossAfterTax"],                             # general insurance
    "total_comprehensive_income_cr": ["ComprehensiveIncomeForThePeriod"],
    # --- non-bank operating-EBITDA inputs (EBITDA = PBT + finance_cost +
    #     depreciation − other_income; from_results.derive_ebitda). Without these
    #     the EBITDA YoY column is blank and the operating line falls back to the
    #     weaker core-ex-OI / revenue proxy. ---
    "depreciation_cr": ["DepreciationDepletionAndAmortisationExpense",
                        "DepreciationAndAmortisationExpense"],
    "finance_cost_cr": ["FinanceCosts", "FinanceCost"],
    # --- cost structure (non-bank): gross margin & bottom-up EBITDA inputs ---
    "cost_of_materials_cr": ["CostOfMaterialsConsumed"],
    "purchases_of_stock_cr": ["PurchasesOfStockInTrade"],
    "change_in_inventory_cr": ["ChangesInInventoriesOfFinishedGoodsWorkInProgressAndStockInTrade"],
    "employee_cost_cr": ["EmployeeBenefitExpense", "EmployeesCost"],   # non-bank | bank
    "other_expenses_cr": ["OtherExpenses"],
    # --- earnings quality: the prop / one-off detectors ---
    "exceptional_items_cr": ["ExceptionalItemsBeforeTax", "ExceptionalItems",
                             "ExtraordinaryItems"],
    "pbt_before_exceptional_cr": ["ProfitBeforeExceptionalItemsAndTax"],
    "current_tax_cr": ["CurrentTax"],
    "deferred_tax_cr": ["DeferredTax"],
    # --- below-the-line / comprehensive ---
    "share_of_associates_cr": ["ShareOfProfitLossOfAssociatesAndJointVenturesAccountedForUsingEquityMethod"],
    "other_comprehensive_income_cr": ["OtherComprehensiveIncomeNetOfTaxes",
                                      "OtherComprehensiveIncome"],
    # --- BFSI / banking lines ---
    "operating_profit_cr": ["OperatingProfitBeforeProvisionAndContingencies"],  # PPOP
    "provisions_cr": ["ProvisionsOtherThanTaxAndContingencies"],
    "interest_earned_cr": ["InterestEarned"],
    "interest_expended_cr": ["InterestExpended"],
    "operating_expenses_cr": ["OperatingExpenses",
                              "ExpenditureExcludingProvisionsAndContingencies"],
    "gross_npa_cr": ["GrossNonPerformingAssets"],
    "net_npa_cr": ["NonPerformingAssets"],
}
# EPS (per-share rupees, NOT scaled). Headline = continuing+discontinued; bank
# taxonomy uses the (Before|After)ExtraordinaryItems variants.
_EPS_TAGS = {
    # NSE's headline EPS is the CONTINUING-operations figure, so it comes first;
    # the combined continuing+discontinued tag is only a fallback for filers that
    # don't split it out (for them the two are equal anyway).
    "eps_basic": [
        "BasicEarningsLossPerShareFromContinuingOperations",
        "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
        "BasicEarningsPerShareBeforeExtraordinaryItems",           # bank (NSE headline = before EO)
        "BasicEarningsPerShareAfterExtraordinaryItems",            # bank alt
        "BasicAndDilutedEPSAfterExtraordinaryItemsNetOfTaxExpenseForThePeriodNotToBeAnnualized",   # insurance
        "BasicAndDilutedEPSBeforeExtraordinaryItemsNetOfTaxExpenseForThePeriodNotToBeAnnualized",  # insurance
    ],
    "eps_diluted": [
        "DilutedEarningsLossPerShareFromContinuingOperations",
        "DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
        "DilutedEarningsPerShareBeforeExtraordinaryItems",         # bank (NSE headline = before EO)
        "DilutedEarningsPerShareAfterExtraordinaryItems",          # bank alt
        "BasicAndDilutedEPSAfterExtraordinaryItemsNetOfTaxExpenseForThePeriodNotToBeAnnualized",   # insurance (combined)
        "BasicAndDilutedEPSBeforeExtraordinaryItemsNetOfTaxExpenseForThePeriodNotToBeAnnualized",  # insurance (combined)
    ],
}

# Ratio/percentage tags (banks): stored as FRACTIONS in the XBRL
# (PercentageOfGrossNpa = 0.0149 → 1.49%), so scale ×100, never to crore. These
# feed the asset-quality columns the bank result row renders (GNPA%/NNPA%).
_RATIO_TAGS = {
    "gross_npa_pct": ["PercentageOfGrossNpa"],
    "net_npa_pct": ["PercentageOfNpa"],
    "cet1_ratio": ["CET1Ratio"],              # bank capital adequacy
    "return_on_assets": ["ReturnOnAssets"],   # bank ROA
}

# A reporting period is a duration; the full-year YTD is ~365 days. Anything
# shorter is an interim (quarter, or a half-year for banks that file H2 at Q4).
_ANNUAL_MIN_DAYS = 350


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
            elif lt in ("explicitMember", "typedMember"):
                ctx["has_dim"] = True       # dimensioned (segment/related-party/etc.)
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
    """The un-dimensioned interim-reporting context the result leads with.

    Headline P&L facts hang off this. Dimensioned (segment/expense) contexts are
    excluded. We take the latest end date (avoids the year-ago period) and, among
    those, prefer the shortest *sub-annual* duration — the quarter for normal
    filers, or the half-year for banks that report H2 at the Q4 filing — falling
    back to the full-year only if no interim period exists."""
    cands = [
        (cid, ctx, d) for cid, ctx in contexts.items()
        if not ctx["has_dim"]
        and (d := _quarter_days(ctx)) is not None
        and d > 0
    ]
    if not cands:
        return None
    latest_end = max(c[1]["end"] for c in cands)
    at_latest = [c for c in cands if c[1]["end"] == latest_end]
    sub_annual = [c for c in at_latest if c[2] < _ANNUAL_MIN_DAYS]
    pool = sub_annual or at_latest        # interim if present, else the YTD/year
    return min(pool, key=lambda c: c[2])[0]   # shortest = the lead reporting period


def _current_instant_context(contexts: dict[str, dict], period_end: str | None) -> str | None:
    """The undimensioned balance-sheet (instant) context at the reporting date.

    Balance-sheet facts (receivables / inventory / payables — the working-capital
    inputs) hang off an *instant* context, not the quarter's duration context.
    Prefer the instant that matches the period end; else the latest instant."""
    cands = [(cid, ct["instant"]) for cid, ct in contexts.items()
             if not ct["has_dim"] and ct["instant"]]
    if not cands:
        return None
    if period_end:
        for cid, inst in cands:
            if inst[:10] == period_end[:10]:
                return cid
    return max(cands, key=lambda c: c[1])[0]


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
    for canon, tags in _AMOUNT_TAGS.items():
        for tag in tags:                       # first present tag wins
            if tag in raw:
                fields[canon] = round(raw[tag] / _RUPEES_TO_CRORE, 2)
                break
    for canon, tags in _EPS_TAGS.items():
        for tag in tags:
            if tag in raw:
                fields[canon] = raw[tag]
                break
    for canon, tags in _RATIO_TAGS.items():       # fraction → percent
        for tag in tags:
            if tag in raw:
                fields[canon] = round(raw[tag] * 100.0, 2)
                break
    # Banks report NII implicitly; derive it from the interest lines.
    if "interest_earned_cr" in fields and "interest_expended_cr" in fields:
        fields["net_interest_income_cr"] = round(
            fields["interest_earned_cr"] - fields["interest_expended_cr"], 2)

    period = contexts[cur_id]["end"]

    # Balance-sheet working-capital inputs (capgoods working_capital_balloon
    # guard): read from the period-end INSTANT context. Receivables & payables
    # sum current + non-current (capgoods carry large non-current retention).
    inst_id = _current_instant_context(contexts, period)
    if inst_id is not None:
        raw_i: dict[str, float] = {}
        for el in root.iter():
            if el.get("contextRef") == inst_id:
                lt = _local(el.tag)
                v = _to_float(el.text)
                if v is not None and lt not in raw_i:
                    raw_i[lt] = v

        def _sum_bs(*tags: str) -> float | None:
            present = [raw_i[t] for t in tags if t in raw_i]
            return round(sum(present) / _RUPEES_TO_CRORE, 2) if present else None

        for canon, tags in (
            ("trade_receivables_cr", ("TradeReceivablesCurrent", "TradeReceivablesNoncurrent")),
            ("inventories_cr", ("Inventories",)),
            ("trade_payables_cr", ("TradePayablesCurrent", "TradePayablesNoncurrent")),
        ):
            v = _sum_bs(*tags)
            if v is not None:
                fields[canon] = v
    return {
        "scope": "consolidated" if scope and "consolid" in scope else "standalone",
        "period_ending": period[:10] if period else None,
        "fields": fields,
    }
