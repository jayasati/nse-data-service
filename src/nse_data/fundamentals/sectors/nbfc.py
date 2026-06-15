"""NBFC / non-bank lenders (NBFC, HFC, infra-finance) — ✅ BUILT.

Non-bank lenders file **interest income** as their top line and carry finance
cost as an OPERATING expense. So the generic EBITDA derivation (which adds
finance cost back) would fabricate a healthy operating line — the one case a
generic read is actively *wrong*, which is why the lender guard keeps these out
of GENERIC. Instead we read the interest/operating-income growth directly (the
``classify_quality`` revenue path, is_bfsi=False — NO add-back) plus PAT, with
the universal other-income / tax-prop guards.

Fields available today (from XBRL): interest income (``revenue_cr``),
total income, PAT, employee cost. NII / provisions / GNPA are NOT yet extracted
for NBFCs, so the asset-quality KPIs a full bank read uses are absent — the
verdict leans on the income-vs-PAT divergence until those land (P-future).

  * Operating line: interest / operating income growth (no EBITDA add-back).
  * KPIs (aspirational, surfaced for context): interest income, NII/NIM, AUM
    growth, credit cost, GNPA.
  * Prop to watch: PAT up while interest income flat (other-income / tax prop).
"""
from __future__ import annotations

from ..earnings_quality import QualityVerdict, _operating_growth, classify_quality
from .base import SectorClass, SectorSpec


def _operating_line(growth: dict | None) -> tuple[float | None, str]:
    # The lender's top line is interest/operating income — read its growth
    # directly (is_bfsi=False uses revenue growth, no finance-cost add-back).
    return _operating_growth(growth, is_bfsi=False)


def _classify(growth: dict | None, fields: dict | None = None) -> QualityVerdict:
    v = classify_quality(growth, fields, is_bfsi=False)
    if v.label != "neutral":
        v.flags.append("sector_nbfc")
        v.reasons.append(
            "NBFC read: interest-income operating line (no EBITDA add-back); "
            "NII/provisions/GNPA not yet extracted"
        )
    return v


SPEC = SectorSpec(
    sector_class=SectorClass.NBFC,
    operating_line=_operating_line,
    classify=_classify,
    built=True,   # validated against the real Bajaj Finance Q4 FY26 filing
    kpis=("interest income", "NII/NIM", "AUM growth", "credit cost", "GNPA"),
)
