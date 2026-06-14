"""§2.8 Capital Goods / Infra / Defence / Railways — ✅ BUILT
(operating line + working-capital-balloon guard; order-book read = next layer).

CapGoods' tell is **order inflow / book-to-bill** (forward demand) + EBITDA margin
+ **execution that converts to cash**. The classic low-quality print: revenue and
profit grow, but the growth is funded by a **ballooning working capital** —
receivables and work-in-progress inventory pile up faster than revenue while the
cash never arrives. The roadmap's sequencing: build the income-statement / balance-
sheet guard first (works on the standard extract), layer order-book second.

  * **Operating line: EBITDA** (generic non-bank line). Validated on the real
    L&T Q2 FY26 filing (EBITDA +7%, PAT +14% → clean long).
  * **working_capital_balloon guard:** the quarterly XBRL has no cash-flow
    statement, but it does carry the period-end balance sheet — so we read
    working capital = trade receivables + inventories − trade payables and watch
    its intensity (WC ÷ revenue). When WC intensity rises materially YoY (cash
    stuck in execution) the "beat" isn't converting to cash → cap the long to a
    flagged neutral. Uses ``wc_ratio_chg_pp`` from ``quarter_growth`` (YoY, so
    seasonality is controlled). Plus the universal other-income / tax props.
  * **order inflow / order book / book-to-bill** — the forward read — are lifted
    by ``parsers/narrative`` (P7) and surfaced as KPI context today; promotion to
    a verdict input is the next layer (needs the backlog vs revenue from the deck).

Routing: no constituent-backed NSE capital-goods index, so leaders are pinned in
``base.SYMBOL_TO_CLASS`` (LT, BEL, SIEMENS, ABB, BHEL, …).
Regression: tests/fundamentals/sectors/test_capgoods_lt.py.
"""
from __future__ import annotations

from ..earnings_quality import QualityVerdict, _g
from .base import (
    SectorClass,
    SectorSpec,
    classify_operating_quality,
    generic_operating_growth,
)

# Working-capital intensity (WC ÷ revenue) rise, in percentage points of revenue
# YoY, that flags execution funded by stuck cash rather than converted to profit.
_WC_BALLOON_PP = 20.0


def _classify(growth: dict | None, fields: dict | None = None) -> QualityVerdict:
    v = classify_operating_quality(growth, fields)
    wc = _g(growth, "yoy_wc_ratio_chg_pp")        # +ve = WC grew faster than revenue
    if wc is not None and wc >= _WC_BALLOON_PP:
        v.flags.append("working_capital_balloon")
        v.reasons.append(
            f"working capital ballooned (+{wc:.0f}pp of revenue YoY) — execution "
            f"not converting to cash")
        if v.direction == "long":
            v.label, v.direction = "neutral", None
    return v


SPEC = SectorSpec(
    sector_class=SectorClass.CAPGOODS,
    operating_line=generic_operating_growth,
    classify=_classify,
    built=True,   # validated against the real L&T Q2 FY26 filing
    kpis=("order inflow", "order book", "book-to-bill", "EBITDA margin", "working capital"),
)
