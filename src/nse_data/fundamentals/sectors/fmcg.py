"""§2.4 FMCG — ✅ BUILT (operating line + commodity guard + UVG narrative rule).

FMCG's whole signal is **underlying volume growth (UVG)** — revenue alone lies
(inflation/price fakes value growth). Generic reads value growth as quality; for
FMCG it isn't unless volumes are growing. This rule layers the sector's three
edges on the shared operating-quality verdict:

  * **Operating line: EBITDA** (not revenue) — a price-led print with shrinking
    margins already shows up as an EBITDA miss even before volume data lands.
    Validated on the real ITC Q2 FY26 filing — revenue FELL −2.4% YoY while
    EBITDA grew +3.5% (the *inverse* inflation trap), read correctly as a long.
  * **commodity guard** (shared with auto) — FMCG *buys* commodities (palm oil,
    packaging, wheat); a margin pop on cheaper inputs is an input windfall, not
    durable demand → caps the long when the input drop explains the margin gain.
  * **UVG (narrative, P7)** via ``base.apply_narrative`` — revenue up but volumes
    ≤0 = price-led (caps a long); an outright volume *contraction* is a demand
    red flag → never a long, and a short when the operating line doesn't offset
    it. UVG phrasing is already lifted by ``parsers/narrative``.

KPIs still narrative-only: gross margin, A&P spend, rural-vs-urban mix.
Regression: tests/fundamentals/sectors/test_fmcg_itc.py.
"""
from __future__ import annotations

from ..earnings_quality import QualityVerdict
from .base import (
    SectorClass,
    SectorSpec,
    apply_commodity_guard,
    classify_operating_quality,
    generic_operating_growth,
)


def _classify(growth: dict | None, fields: dict | None = None) -> QualityVerdict:
    # operating-quality verdict, then the shared input-cost (commodity) guard;
    # the UVG/volume edge is folded in later by base.apply_narrative (it needs
    # the filing's narrative, which classify() doesn't receive).
    return apply_commodity_guard(classify_operating_quality(growth, fields), growth)


SPEC = SectorSpec(
    sector_class=SectorClass.FMCG,
    operating_line=generic_operating_growth,
    classify=_classify,
    built=True,   # validated against the real ITC Q2 FY26 filing
    kpis=("volume growth", "gross margin", "EBITDA margin", "A&P spend", "rural vs urban"),
)
