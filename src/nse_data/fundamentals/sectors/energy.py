"""§2.2 Energy — Oil & Gas / Power — ✅ BUILT (P3).

ONGC Q4 FY26 was the motivating bug: PAT +3.1% (₹+202 cr) but other income rose
₹+553 cr and a deferred-tax write-back held the headline up, while **core
operating profit ex-other-income fell −11.9% YoY** (PBT − other income: ₹5,896 cr
vs ₹6,693 cr). The market gapped down ~1.5%. The old engine called LONG because it
read revenue (+2.7%) as the proxy and ignored the other-income / tax props.

The rule now:
  * Operating line = the generic non-bank line (``base.generic_operating_growth``):
    core operating profit ex-other-income (PBT − other income), derived from the
    extracted fields by ``from_results``. EBITDA proper needs depreciation/finance
    lines that aren't extracted yet — core-ex-OI is the derivable operating line
    and it isolates exactly the other-income prop.
  * SHORT only on a confirmed operating-line decline (playbook §0). The
    other-income prop (P1 ``other_income_propped``) and tax write-back
    (``tax_propped``) corroborate the miss but never trigger a short alone.

  * KPIs (E&P): crude realization $/bbl, gas price, production volume, cess, dividend.
  * KPIs (refiners): GRM $/bbl, throughput, inventory gain/loss, petchem margin.

Validated against the real ONGC filing in
``tests/fundamentals/sectors/test_energy_ongc.py``.
"""
from __future__ import annotations

from .base import (
    SectorClass,
    SectorSpec,
    classify_operating_quality,
    generic_operating_growth,
)

SPEC = SectorSpec(
    sector_class=SectorClass.ENERGY,
    operating_line=generic_operating_growth,
    classify=classify_operating_quality,
    built=True,   # P3 — validated against the real ONGC Q4 FY26 filing
    kpis=("EBITDA", "crude realization $/bbl", "GRM $/bbl",
          "production", "other income", "dividend"),
)
