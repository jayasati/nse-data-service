"""GENERIC — the long-tail non-financial read (cement, chemicals, telecom,
retail, logistics, … anything with no sector-specific rule).

Playbook laws #1 and #2 are deliberately sector-universal: read the operating
line (EBITDA), not the headline; PAT propped by other income / a tax write-back
while the core falls is low quality everywhere. So the long tail runs the same
shared verdict the built sectors use — with one honesty marker: every
non-neutral verdict carries the ``sector_generic`` flag, so the alert card and
any reader know this was the generic read, not a sector-tuned rule with its
KPIs.

Routing guard (``base.class_for_metadata``): non-bank financials NEVER land
here — for a lender finance cost is operating, so the EBITDA derivation would
fabricate the operating line. They stay out-of-scope neutral instead.
"""
from __future__ import annotations

from ..earnings_quality import QualityVerdict
from .base import (
    SectorClass,
    SectorSpec,
    classify_operating_quality,
    generic_operating_growth,
)


def _classify(growth: dict | None, fields: dict | None = None) -> QualityVerdict:
    v = classify_operating_quality(growth, fields)
    if v.label != "neutral":
        v.flags.append("sector_generic")
        v.reasons.append("generic operating-quality read (no sector-specific rule)")
    return v


SPEC = SectorSpec(
    sector_class=SectorClass.GENERIC,
    operating_line=generic_operating_growth,
    classify=_classify,
    built=True,   # the rule itself is the one validated across 8 sectors
    kpis=("EBITDA", "other income", "tax line"),
)
