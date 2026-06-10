"""§2.6 Pharma — ✅ BUILT (operating-line verdict; FDA/US-sales KPIs pending P7).

Pharma's full tell is EBITDA margin + **US sales growth** + USFDA status (a
warning letter / import alert is binary and huge, very negative regardless of
the P&L) — US-geography sales and FDA status live in the press release and
news, not the P&L, so they wait on P7 text ingestion. What the P&L supports is
the engine's core: the operating line (EBITDA) vs the headline, plus the
other-income / tax props. The §2.6 prop — a one-off para-IV / launch upside
that won't repeat, or other income — is caught on the non-core side.

  * Operating line: EBITDA (generic non-bank line).
  * KPIs still pending: US sales, USFDA observations/letters, pipeline &
    launches, R&D spend.

Validated against the real Cipla Q2 FY26 consolidated filing (operating line
flat +0.5% with other income +41% YoY → conservative NEUTRAL, no false short —
the no-false-positive guarantee) in
tests/fundamentals/sectors/test_pharma_cipla.py.
"""
from __future__ import annotations

from .base import (
    SectorClass,
    SectorSpec,
    classify_operating_quality,
    generic_operating_growth,
)

SPEC = SectorSpec(
    sector_class=SectorClass.PHARMA,
    operating_line=generic_operating_growth,
    classify=classify_operating_quality,
    built=True,   # validated against the real Cipla Q2 FY26 filing
    kpis=("US sales", "EBITDA margin", "USFDA status", "pipeline/launches", "R&D"),
)
