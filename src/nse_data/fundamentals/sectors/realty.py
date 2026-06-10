"""Realty — ✅ BUILT (operating-line verdict; pre-sales KPI pending P7).

Realty's quarterly P&L is the lumpiest of any sector: revenue recognises on
project completion (Ind AS 115), so a quarter's revenue/EBITDA can swing
violently on handover timing without the business changing. The market
therefore prices **pre-sales / bookings** (and collections, net debt) — which
live in the press release / investor deck, not the P&L (P7 narrative; the
quarterly statement alone under-describes a realty print).

What the P&L still supports is the engine's core, conservatively: the
operating line (EBITDA) vs the headline, plus the other-income / tax props —
a completion-heavy quarter propped by other income reads exactly like the
universal low-quality shape.

Validated against a real filing in
tests/fundamentals/sectors/test_realty.py.
"""
from __future__ import annotations

from .base import (
    SectorClass,
    SectorSpec,
    classify_operating_quality,
    generic_operating_growth,
)

SPEC = SectorSpec(
    sector_class=SectorClass.REALTY,
    operating_line=generic_operating_growth,
    classify=classify_operating_quality,
    built=True,   # validated against a real realty filing
    kpis=("pre-sales/bookings", "collections", "EBITDA", "net debt", "launches"),
)
