"""§2.1 Banking / NBFC (BFSI) — ✅ BUILT.

The worked example every other sector copies. The verdict rule already lives,
validated, in ``earnings_quality.classify_quality(is_bfsi=True)`` (SBI hidden
miss, Axis outright miss, HDFC clean beat). This module does not reimplement it —
it wraps it in a ``SectorSpec`` so BFSI plugs into the same router as the rest.

  * Operating line: pre-provision operating profit (PPOP) and NII.
  * KPIs: NII/NIM, GNPA/NNPA, loan growth, CASA/deposits, provisions, slippages, PCR.
  * Prop to watch: provision release, treasury gain, tax write-back.
"""
from __future__ import annotations

from ..earnings_quality import QualityVerdict, _operating_growth, classify_quality
from .base import SectorClass, SectorSpec


def _operating_line(growth: dict | None) -> tuple[float | None, str]:
    return _operating_growth(growth, is_bfsi=True)


def _classify(growth: dict | None, fields: dict | None = None) -> QualityVerdict:
    return classify_quality(growth, fields, is_bfsi=True)


SPEC = SectorSpec(
    sector_class=SectorClass.BFSI,
    operating_line=_operating_line,
    classify=_classify,
    built=True,
    kpis=("NII", "NIM", "PPOP", "GNPA", "NNPA", "loan growth", "provisions", "slippages", "PCR"),
)
