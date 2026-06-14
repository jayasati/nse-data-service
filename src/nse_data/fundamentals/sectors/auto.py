"""§2.5 Auto — ✅ BUILT (operating line + commodity-tailwind guard).

Auto's tell is **EBITDA margin + volume (units) + realization/unit**. The single
biggest false-signal trap (the roadmap's headline edge for this sector): an OEM's
margin can pop simply because **steel / aluminium got cheaper**, with no real
improvement in demand, mix or pricing — a *commodity tailwind*, not durable
quality. The generic engine reads that margin pop as a clean beat; it isn't.

This rule adds that edge on top of the shared operating-quality verdict:

  * **Operating line:** EBITDA (generic non-bank line; now exceptional-adjusted).
  * **commodity_tailwind guard:** when the operating line beats but the
    material-cost intensity (cost of materials ÷ revenue) *fell* ≥1.5pp, the
    margin gain is input-cost-driven, not demand-driven — cap the long to a
    neutral (we can't confirm durability from the P&L; a confirming volume read
    in the narrative is what would re-validate it). Computed from
    ``material_ratio_chg_pp`` in ``quarter_growth``.
  * **commodity_headwind flag:** the inverse — an operating miss while input
    costs *rose* ≥1.5pp is contextualised (still a miss; the flag explains it).
  * **price-led volume** (revenue up, volumes ≤0) is handled by
    ``base.apply_narrative`` when the filing carried a volume read.
  * Plus the universal other-income / tax-write-back props (shared verdict).

KPIs still narrative-only (P7 / monthly-volume feed): wholesale/retail volumes,
realization/unit, EV mix, inventory commentary.

Validated against the real Maruti Suzuki Q2 FY26 filing (tax-propped headline →
neutral, no false long) and a synthetic steel-tailwind beat, in
tests/fundamentals/sectors/test_auto_maruti.py.
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
    # operating-quality verdict, then the shared input-cost (commodity) guard:
    # a steel/aluminium-driven margin pop is not a durable demand-led beat.
    return apply_commodity_guard(classify_operating_quality(growth, fields), growth)


SPEC = SectorSpec(
    sector_class=SectorClass.AUTO,
    operating_line=generic_operating_growth,
    classify=_classify,
    built=True,   # validated against the real Maruti Q2 FY26 filing + tailwind case
    kpis=("volume (units)", "EBITDA margin", "realization/unit", "input costs", "EV mix", "inventory"),
)
