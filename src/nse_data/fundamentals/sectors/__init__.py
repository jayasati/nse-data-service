"""Sector result-reading registry & router (SECTOR_RESULT_PLAYBOOK.md §4).

Generalises the binary ``is_bfsi`` switch into a per-sector lookup: every result
is routed by its ``SectorClass`` to a ``SectorSpec`` (operating line + verdict
rule + built flag). BFSI is the one BUILT sector; the rest declare their schema
and return a low-confidence neutral until their rule lands (the P1 out-of-scope
guard — this is what stops the engine emitting a confident verdict for a sector
it cannot yet read, e.g. the ONGC false LONG).

Public API:

    from nse_data.fundamentals.sectors import classify_result, spec_for, sector_class_for

    verdict = classify_result("ONGC", growth, fields)   # routed, guarded
    spec    = spec_for("HDFCBANK")                       # the SectorSpec
    klass   = sector_class_for("INFY")                   # SectorClass.IT

Adding a sector = add its module + one line in ``REGISTRY`` + one real-PDF
regression. Nothing else changes.
"""
from __future__ import annotations

from ..earnings_quality import QualityVerdict
from . import (
    auto,
    bfsi,
    capital_goods,
    energy,
    fmcg,
    it_services,
    metals,
    pharma,
)
from .base import (
    INDEX_TO_CLASS,
    SYMBOL_TO_CLASS,
    SectorClass,
    SectorSpec,
    apply_narrative,
    out_of_scope_verdict,
    unbuilt_spec,
)

# sector_class → its spec. The single place a new sector is registered.
REGISTRY: dict[SectorClass, SectorSpec] = {
    SectorClass.BFSI: bfsi.SPEC,
    SectorClass.ENERGY: energy.SPEC,
    SectorClass.IT: it_services.SPEC,
    SectorClass.FMCG: fmcg.SPEC,
    SectorClass.AUTO: auto.SPEC,
    SectorClass.PHARMA: pharma.SPEC,
    SectorClass.METALS: metals.SPEC,
    SectorClass.CAPGOODS: capital_goods.SPEC,
}

# A spec for symbols we can't place — out-of-scope, never confident.
_UNKNOWN_SPEC = unbuilt_spec(SectorClass.UNKNOWN, kpis=())


def sector_class_for(symbol: str) -> SectorClass:
    """Resolve a symbol to its SectorClass via the sectoral-index mapping.

    Symbol-level overrides (``base.SYMBOL_TO_CLASS``) win over the index
    mapping — capital goods has no constituent-backed sectoral index, and the
    index data files some industrials (ABB/SIEMENS/BHEL) under NIFTY ENERGY.
    Otherwise reuses ``market.sector_map`` (config/sector_mapping.yaml). A
    symbol with no mapping, or one in an index we haven't classified, is
    ``UNKNOWN``."""
    from ...market.sector_map import sector_for  # lazy: avoid import cycle at module load

    override = SYMBOL_TO_CLASS.get(symbol.upper())
    if override is not None:
        return override
    index = sector_for(symbol)
    if not index:
        return SectorClass.UNKNOWN
    return INDEX_TO_CLASS.get(index.upper(), SectorClass.UNKNOWN)


def spec_for(symbol: str) -> SectorSpec:
    """The SectorSpec a symbol's result should be read with."""
    return REGISTRY.get(sector_class_for(symbol), _UNKNOWN_SPEC)


def classify_result(
    symbol: str,
    growth: dict | None,
    fields: dict | None = None,
    narrative: dict | None = None,
) -> QualityVerdict:
    """Route a result to its sector rule and apply the P1 built guard.

    For a BUILT sector this is the sector's own verdict. For any sector whose
    rule is not yet validated (``built=False``), the verdict is downgraded to an
    explicit out-of-scope neutral regardless of what the draft rule computes, so
    the engine stays trustworthy only where it has been proven (playbook §5).

    ``narrative`` is the filing's press-release signal dict
    (``NarrativeFields.as_dict()``, P7 — guidance / volumes / FDA / order book);
    when present it is folded into the P&L verdict via ``apply_narrative``,
    using the sector's own operating line for the guidance gate."""
    spec = spec_for(symbol)
    if not spec.built:
        return out_of_scope_verdict(spec.sector_class)
    verdict = spec.classify(growth, fields)
    if narrative:
        verdict = apply_narrative(
            verdict, growth, narrative, spec.sector_class, spec.operating_line,
        )
    return verdict


__all__ = [
    "SectorClass",
    "SectorSpec",
    "REGISTRY",
    "sector_class_for",
    "spec_for",
    "classify_result",
]
