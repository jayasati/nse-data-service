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

from ..earnings_quality import QualityVerdict
from .base import (
    SectorClass,
    SectorSpec,
    classify_operating_quality,
    generic_operating_growth,
)


def _classify(growth: dict | None, fields: dict | None = None) -> QualityVerdict:
    """Operating-quality read, but flagged POCS-lumpy. The P&L direction is kept
    for context, yet ``base.enrich_signal`` pins realty to LOW confidence so the
    read is never *tradable* on the P&L alone (Ind AS 115 makes a quarter's
    revenue/EBITDA swing on handover timing). It becomes tradable only when
    pre-sales / bookings (the number the market prices) land from the deck (P7)."""
    v = classify_operating_quality(growth, fields)
    if v.direction is not None:
        v.flags.append("realty_pocs_lumpy")
        v.reasons.append(
            "realty revenue is POCS-lumpy (Ind AS 115) — P&L read not tradable "
            "without pre-sales / bookings")
    return v


SPEC = SectorSpec(
    sector_class=SectorClass.REALTY,
    operating_line=generic_operating_growth,
    classify=_classify,
    built=True,   # validated against a real realty filing
    kpis=("pre-sales/bookings", "collections", "EBITDA", "net debt", "launches"),
)
