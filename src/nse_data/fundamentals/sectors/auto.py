"""§2.5 Auto — ✅ BUILT (operating-line verdict; volume/realization KPIs pending).

Auto's full tell is EBITDA margin + **volume (units)** + realization/unit —
volumes are a separate monthly feed and realization/unit is derived from them,
so both wait on their own ingestion (P7 / monthly-volume feed). What the P&L
supports is the engine's core: the operating line (EBITDA) vs the headline,
plus the other-income / tax props. The §2.5 prop — margin up only on a
commodity tailwind, PAT on other income — is exactly the shape the shared
verdict guards against.

  * Operating line: EBITDA (generic non-bank line).
  * KPIs still pending: wholesale/retail volumes, realization/unit, input
    costs, EV mix, inventory commentary.

Validated against the real Maruti Suzuki Q2 FY26 filing in
tests/fundamentals/sectors/test_auto_maruti.py.
"""
from __future__ import annotations

from .base import (
    SectorClass,
    SectorSpec,
    classify_operating_quality,
    generic_operating_growth,
)

SPEC = SectorSpec(
    sector_class=SectorClass.AUTO,
    operating_line=generic_operating_growth,
    classify=classify_operating_quality,
    built=True,   # validated against the real Maruti Q2 FY26 filing
    kpis=("volume (units)", "EBITDA margin", "realization/unit", "input costs", "EV mix", "inventory"),
)
