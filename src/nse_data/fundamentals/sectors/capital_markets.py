"""CAPMARKETS — fee-based financials — ✅ BUILT.

Asset managers, broking houses, exchanges, depositories, registrars (RTAs) and
wealth platforms. Unlike lenders they have NO meaningful lending book: fees /
commissions / transaction charges are the top line and finance cost is
immaterial — so the operating read is simply the revenue+PAT quality rule
(``classify_quality`` is_bfsi=False), with no bank NII/provision machinery and
no risk of the lender add-back fabricating an operating line.

Routed explicitly (``base.class_for_metadata``) so these names stop falling to
UNKNOWN under the (lender-only) financial-services guard.

  * Operating line: fee / operating revenue growth.
  * KPIs (surfaced for context): operating revenue, fee/commission income,
    PAT margin, employee-cost ratio.
"""
from __future__ import annotations

from ..earnings_quality import QualityVerdict, _operating_growth, classify_quality
from .base import SectorClass, SectorSpec


def _operating_line(growth: dict | None) -> tuple[float | None, str]:
    return _operating_growth(growth, is_bfsi=False)


def _classify(growth: dict | None, fields: dict | None = None) -> QualityVerdict:
    v = classify_quality(growth, fields, is_bfsi=False)
    if v.label != "neutral":
        v.flags.append("sector_capmarkets")
        v.reasons.append("capital-markets read: fee/operating revenue line")
    return v


SPEC = SectorSpec(
    sector_class=SectorClass.CAPMARKETS,
    operating_line=_operating_line,
    classify=_classify,
    built=True,   # validated against the real HDFC AMC Q4 FY26 filing
    kpis=("operating revenue", "fee/commission income", "PAT margin"),
)
