"""§2.4 FMCG — ✅ BUILT (operating-line verdict; volume/margin KPIs pending P7).

FMCG's full tell is **volume growth** + gross/EBITDA margin — revenue alone lies
(inflation fakes revenue growth; the §2.4 prop is "revenue up but volumes flat",
price-led not demand-led). Volume lives in the press release / investor deck,
not the P&L, so it waits on P7 narrative ingestion. What the P&L *does* support
is the engine's core: the operating line (EBITDA) vs the headline, plus the
other-income / tax props — and because the operating line is EBITDA (not
revenue), a price-led print with shrinking margins shows up as an EBITDA miss
even before volume data lands.

  * Operating line: EBITDA (generic non-bank line). Volume growth & gross
    margin proper need P7.
  * KPIs still pending: underlying volume growth, gross margin, A&P spend,
    rural vs urban commentary.

Validated against the real ITC Q2 FY26 standalone filing (clean operating beat
→ long; revenue actually FELL −2.4% YoY while EBITDA grew — the inverse of the
inflation trap, which the EBITDA line reads correctly) in
tests/fundamentals/sectors/test_fmcg_itc.py.
"""
from __future__ import annotations

from .base import (
    SectorClass,
    SectorSpec,
    classify_operating_quality,
    generic_operating_growth,
)

SPEC = SectorSpec(
    sector_class=SectorClass.FMCG,
    operating_line=generic_operating_growth,
    classify=classify_operating_quality,
    built=True,   # validated against the real ITC Q2 FY26 filing
    kpis=("volume growth", "gross margin", "EBITDA margin", "A&P spend", "rural vs urban"),
)
