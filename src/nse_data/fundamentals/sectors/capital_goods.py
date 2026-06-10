"""§2.8 Capital Goods / Infra / Defence / Railways — ✅ BUILT (operating-line
verdict; order-book KPIs pending P7).

CapGoods' full tell is **order inflow / order book (book-to-bill)** + EBITDA
margin + execution — the order book lives in the press release / deck, not the
P&L, so it waits on P7 narrative ingestion (the §2.8 prop, revenue from order
drawdown without fresh inflow, is invisible to the P&L alone). What the P&L
supports is the engine's core: the operating line (EBITDA) vs the headline,
plus the other-income / tax props.

Routing note: there is no constituent-backed NSE capital-goods index, so the
sector's leaders are pinned in ``base.SYMBOL_TO_CLASS`` (LT, BEL, SIEMENS, ABB,
BHEL, …) rather than resolved via the index mapping.

  * Operating line: EBITDA (generic non-bank line).
  * KPIs still pending: order inflow, order book / book-to-bill, working
    capital & receivables commentary.

Validated against the real Larsen & Toubro Q2 FY26 consolidated filing in
tests/fundamentals/sectors/test_capgoods_lt.py.
"""
from __future__ import annotations

from .base import (
    SectorClass,
    SectorSpec,
    classify_operating_quality,
    generic_operating_growth,
)

SPEC = SectorSpec(
    sector_class=SectorClass.CAPGOODS,
    operating_line=generic_operating_growth,
    classify=classify_operating_quality,
    built=True,   # validated against the real L&T Q2 FY26 filing
    kpis=("order inflow", "order book", "book-to-bill", "EBITDA margin", "working capital"),
)
