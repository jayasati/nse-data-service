"""§2.3 IT Services — ✅ BUILT (operating-line verdict; guidance/TCV pending P7).

IT's full tell is EBIT margin + constant-currency revenue + **guidance** (the
single biggest mover) + TCV — but guidance/TCV/cc-revenue live in the press
release, not the P&L, so they wait on P7 text ingestion. What the P&L *does*
support is exactly the engine's core: the operating line vs the headline, and the
non-core props. IT is a prime case for the other-income prop — large cash piles
mean a soft operating quarter can be papered over by treasury other income (and a
buyback/lower tax flattering EPS). So IT runs the shared operating-quality
verdict (``base.classify_operating_quality``): SHORT only on a confirmed
operating-line (EBITDA/EBIT) decline, corroborated — never triggered — by the
other-income / tax props.

  * Operating line: EBITDA / EBIT (generic operating line). cc-revenue & EBIT
    margin proper need P7.
  * KPIs still pending: cc-revenue, TCV, guidance (text), attrition, USDINR.

Hardened against real filings (INFY Q2 FY26 clean beat → long; TCS Q1 FY26 QoQ
flat → neutral) in tests/fundamentals/sectors/test_it_services.py — the
no-false-short guarantee on a healthy IT print with a big other-income jump.
"""
from __future__ import annotations

from .base import (
    SectorClass,
    SectorSpec,
    classify_operating_quality,
    generic_operating_growth,
)

SPEC = SectorSpec(
    sector_class=SectorClass.IT,
    operating_line=generic_operating_growth,
    classify=classify_operating_quality,
    built=True,
    kpis=("EBIT/EBITDA margin", "cc-revenue", "TCV", "guidance", "attrition", "USDINR"),
)
