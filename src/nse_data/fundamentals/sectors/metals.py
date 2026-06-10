"""§2.7 Metals — ✅ BUILT (operating-line verdict; per-tonne KPIs pending).

Metals' full tell is **EBITDA/tonne** + realization — both need volumes
(tonnes), which live in the press release / investor deck, so per-tonne reads
wait on P7. What the P&L supports is the engine's core: aggregate EBITDA vs
the headline, plus the other-income / tax props. The §2.7 prop — PAT on
inventory / forex / other income while EBITDA falls — is the same non-core
shape the shared verdict guards against (inventory & forex swings land in the
expense/other-income lines and show up as EBITDA-vs-PAT divergence).

  * Operating line: EBITDA (generic non-bank line).
  * KPIs still pending: EBITDA/tonne, realization, volumes (tonnes), net debt,
    China-demand commentary.

Validated against the real JSW Steel Q2 FY26 consolidated filing (EBITDA
+39.6% YoY clean beat → long; year-ago PBT carried a ₹342 cr exceptional) in
tests/fundamentals/sectors/test_metals_jswsteel.py.
"""
from __future__ import annotations

from .base import (
    SectorClass,
    SectorSpec,
    classify_operating_quality,
    generic_operating_growth,
)

SPEC = SectorSpec(
    sector_class=SectorClass.METALS,
    operating_line=generic_operating_growth,
    classify=classify_operating_quality,
    built=True,   # validated against the real JSW Steel Q2 FY26 filing
    kpis=("EBITDA/tonne", "realization", "volumes (tonnes)", "China demand", "net debt"),
)
