"""Per-sector earnings-quality contract — the keystone of the sector playbook.

See ``SECTOR_RESULT_PLAYBOOK.md``. The Week-17.5 engine is a binary ``is_bfsi``
switch; this package generalises it to **one spec per sector** so that adding a
sector is "one schema + one rule + one real-PDF regression" (the SBI/Axis/HDFC
method) and nothing else.

The contract (playbook §4, "Per-sector schema = the S1 pattern repeated"):

    sector_class  →  canonical operating fields  →  sector verdict rule

A ``SectorSpec`` bundles those three things. ``bfsi.py`` is the worked, BUILT
example; every other sector is a stub that declares its operating line + KPIs and
returns a low-confidence neutral until its rule lands (the P1 out-of-scope guard,
so the engine never emits a confident verdict for a sector it cannot yet read —
this is what fixes the ONGC false LONG).

This module owns the cross-sector machinery:
  * ``SectorClass``            — the enum every result is routed by.
  * ``SectorSpec``             — the (operating line, verdict rule, built?) bundle.
  * ``generic_operating_growth`` — P2: EBITDA → operating profit → revenue proxy.
  * ``other_income_propped`` / ``tax_propped`` — P1: the universal non-core guards.

Sector rules import only from here and from ``earnings_quality`` (the shared
``QualityVerdict`` + thresholds); they never import each other.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from ..earnings_quality import QualityVerdict, _g


class SectorClass(str, Enum):
    """Canonical sector buckets the result router maps every symbol into.

    Mirrors the playbook §2 blocks. ``UNKNOWN`` is the out-of-scope bucket: a
    symbol whose sector we cannot resolve, routed to a low-confidence neutral.
    """

    BFSI = "bfsi"              # §2.1  ✅ built
    ENERGY = "energy"         # §2.2  ✅ built
    IT = "it"                 # §2.3  ✅ built
    FMCG = "fmcg"             # §2.4  ✅ built
    AUTO = "auto"             # §2.5  ✅ built
    PHARMA = "pharma"         # §2.6  ✅ built
    METALS = "metals"         # §2.7  ✅ built
    CAPGOODS = "capgoods"     # §2.8  ✅ built
    REALTY = "realty"         # ✅ built (lumpy quarters — pre-sales KPI is P7)
    GENERIC = "generic"       # long-tail non-financials: the shared operating rule
    UNKNOWN = "unknown"       # out-of-scope → neutral, low confidence (P1)


# NSE sectoral-index name (as stored in config/sector_mapping.yaml) → SectorClass.
# sector_map.py owns BFSI today; this table extends the mapping for the rest
# without touching that module. Unlisted indices fall through to UNKNOWN.
INDEX_TO_CLASS: dict[str, SectorClass] = {
    "NIFTY BANK": SectorClass.BFSI,
    "NIFTY PSU BANK": SectorClass.BFSI,
    "NIFTY PRIVATE BANK": SectorClass.BFSI,
    "NIFTY FINANCIAL SERVICES": SectorClass.BFSI,
    "NIFTY ENERGY": SectorClass.ENERGY,
    "NIFTY OIL & GAS": SectorClass.ENERGY,
    "NIFTY IT": SectorClass.IT,
    "NIFTY FMCG": SectorClass.FMCG,
    "NIFTY AUTO": SectorClass.AUTO,
    "NIFTY PHARMA": SectorClass.PHARMA,
    "NIFTY HEALTHCARE INDEX": SectorClass.PHARMA,
    "NIFTY METAL": SectorClass.METALS,
    "NIFTY REALTY": SectorClass.REALTY,
}


# --- long-tail routing: NSE's own quote-metadata taxonomy --------------------
# raw_quote_metadata carries (sector, industry) for every symbol NSE quotes —
# the routing source for stocks outside the sectoral indices. The mapping is
# deliberately conservative:
#   * Financial Services → BFSI only for actual banks. NBFCs/brokers/AMCs/
#     insurers return None (→ UNKNOWN → neutral): for a lender, finance cost
#     is an OPERATING cost, so the generic EBITDA derivation (which adds it
#     back) would fabricate a healthy operating line — the one case where a
#     generic read is actively wrong rather than merely coarse.
#   * Recognised industries map to their specific sector rule.
#   * Everything else non-financial → GENERIC (the shared operating-quality
#     rule, flagged `sector_generic`).
_BANK_INDUSTRY = ("private sector bank", "public sector bank", "other bank")
_METAL_INDUSTRIES = ("aluminium", "zinc", "copper", "iron & steel",
                     "industrial minerals", "diversified metals")
_AUTO_INDUSTRIES = ("2/3 wheelers", "passenger cars & utility vehicles",
                    "commercial vehicles", "auto components & equipments",
                    "tractors", "tyres & rubber products")
_CAPGOODS_INDUSTRIES = ("heavy electrical equipment", "aerospace & defense",
                        "ship building & allied services", "civil construction",
                        "industrial products", "railway wagons")
_REALTY_INDUSTRIES = ("residential commercial projects", "real estate")

_MACRO_TO_CLASS = {
    "healthcare": SectorClass.PHARMA,
    "information technology": SectorClass.IT,
    "fast moving consumer goods": SectorClass.FMCG,
    "energy": SectorClass.ENERGY,
    "utilities": SectorClass.ENERGY,          # power = playbook §2.2's power block
}


def class_for_metadata(sector: str | None, industry: str | None) -> SectorClass | None:
    """NSE (sector, industry) → SectorClass; None = leave unrouted (UNKNOWN)."""
    macro = (sector or "").strip().lower()
    ind = (industry or "").strip().lower()
    if not macro:
        return None
    if macro == "financial services":
        return SectorClass.BFSI if any(b in ind for b in _BANK_INDUSTRY) else None
    if any(k in ind for k in _REALTY_INDUSTRIES):
        return SectorClass.REALTY
    if any(k in ind for k in _AUTO_INDUSTRIES):
        return SectorClass.AUTO
    if any(k in ind for k in _METAL_INDUSTRIES):
        return SectorClass.METALS
    if any(k in ind for k in _CAPGOODS_INDUSTRIES):
        return SectorClass.CAPGOODS
    if macro in _MACRO_TO_CLASS:
        return _MACRO_TO_CLASS[macro]
    # Industrials / Commodities / Services / Consumer / Telecom — non-financial
    # long tail: the generic operating-quality read.
    return SectorClass.GENERIC


# Symbol-level routing overrides, consulted BEFORE the index mapping. Two jobs:
# (1) capital goods has no NSE sectoral index with constituent data (NIFTY INFRA
#     is unmapped), so its leaders are pinned here — without this LT/BEL would
#     fall to UNKNOWN; (2) index membership sometimes files an industrial under
#     an energy index (ABB/SIEMENS/BHEL sit in NIFTY ENERGY in the constituent
#     data) — their results must be read with the capgoods schema, not energy's.
SYMBOL_TO_CLASS: dict[str, SectorClass] = {
    "LT": SectorClass.CAPGOODS,
    "BEL": SectorClass.CAPGOODS,
    "HAL": SectorClass.CAPGOODS,
    "SIEMENS": SectorClass.CAPGOODS,
    "ABB": SectorClass.CAPGOODS,
    "BHEL": SectorClass.CAPGOODS,
    "CUMMINSIND": SectorClass.CAPGOODS,
    "THERMAX": SectorClass.CAPGOODS,
    "RVNL": SectorClass.CAPGOODS,
    "IRCON": SectorClass.CAPGOODS,
}


@dataclass(frozen=True)
class SectorSpec:
    """The three things that define how a sector's result is read (playbook §4).

    * ``operating_line`` — given the extractor's growth dict, return the
      (growth %, label) of the *core operating line* for this sector (PPOP for
      banks, EBITDA for everyone else). This is law #1: read the operating line,
      not the headline.
    * ``classify`` — the sector verdict rule: (growth, fields) → QualityVerdict.
    * ``built`` — False until the rule is validated against a real-PDF
      regression; while False the router downgrades to neutral/low-confidence
      regardless of what ``classify`` returns (P1 out-of-scope guard).
    * ``kpis`` — the human-readable KPIs an analyst reads for this sector
      (playbook §2), surfaced in the alert card for context.
    """

    sector_class: SectorClass
    operating_line: Callable[[Optional[dict]], tuple[Optional[float], str]]
    classify: Callable[..., QualityVerdict]
    built: bool = False
    kpis: tuple[str, ...] = field(default_factory=tuple)


# --- P2: generic operating line for non-banks --------------------------------
# Read, in order of fidelity: EBITDA (not extracted yet) → core operating profit
# ex-other-income (PBT − other income, derived from extracted fields by
# from_results) → an "operating profit" subtotal if the filing prints one
# (stored as ppop) → revenue (last-resort proxy). Using the operating line
# instead of revenue is what removes the revenue-proxy weakness that mislabelled
# ONGC (whose revenue rose +2.7% while core profit fell −11.9%).
_GENERIC_OP_KEYS = (
    ("yoy_ebitda_pct", "EBITDA YoY"),
    ("qoq_ebitda_pct", "EBITDA QoQ"),
    ("yoy_operating_ex_oi_pct", "core operating YoY"),
    ("qoq_operating_ex_oi_pct", "core operating QoQ"),
    ("yoy_ppop_pct", "operating profit YoY"),
    ("qoq_ppop_pct", "operating profit QoQ"),
    ("yoy_revenue_pct", "revenue YoY"),     # weak proxy of last resort
    ("qoq_revenue_pct", "revenue QoQ"),
)


def generic_operating_growth(growth: dict | None) -> tuple[float | None, str]:
    """P2 — the non-bank operating line: EBITDA → core-ex-OI → operating profit → revenue."""
    for key, lbl in _GENERIC_OP_KEYS:
        v = _g(growth, key)
        if v is not None:
            return v, lbl
    return None, "operating line"


# --- P1: universal non-core prop guards (cheap, every sector) -----------------
# A PAT that holds up only because of a non-core line (other income, a tax
# write-back) while the core is flat/down is the universal low-quality shape
# (playbook law #2). These never trigger a SHORT alone — they corroborate an
# operating miss and otherwise cap the upside — mirroring the BFSI provision/
# treasury guards in earnings_quality.classify_quality.
_OTHER_INCOME_RISE = 15.0   # other income up more than this YoY while core flat = propped
_PAT_FLAT = 0.0


def other_income_propped(growth: dict | None) -> bool:
    """PAT up (or flat) while other income jumps and the core operating line
    does not — the ONGC signature (₹+553 cr other income > the ₹+201 cr PAT
    rise ⇒ core profit actually fell)."""
    pat = _g(growth, "yoy_pat_pct")
    oi = _g(growth, "yoy_other_income_pct")
    op, _ = generic_operating_growth(growth)
    if pat is None or oi is None or op is None:
        return False
    return pat >= _PAT_FLAT and oi > _OTHER_INCOME_RISE and op <= 0.0


def tax_propped(growth: dict | None) -> bool:
    """PAT held up by a falling / negative tax charge while pre-tax profit (PBT)
    is down — a tax write-back flattering the headline."""
    pat = _g(growth, "yoy_pat_pct")
    pbt = _g(growth, "yoy_pbt_pct")
    tax = _g(growth, "yoy_tax_pct")
    if pat is None or pbt is None or tax is None:
        return False
    return pbt < 0.0 and tax < 0.0 and pat >= pbt


# Thresholds for the shared operating verdict (percent). Same shape as the BFSI
# rule in earnings_quality: a confirmed operating-line decline is the only SHORT
# trigger; props corroborate but never fire alone.
_OP_DROP = -2.0          # operating line down more than this YoY = operating miss
_OP_BEAT = 2.0           # operating line up more than this = genuine operating beat
_PAT_BEAT = 0.0          # PAT at/above this is a "beat/flat" headline


def verdict_from_operating(
    pat_growth: float | None,
    op_growth: float | None,
    op_label: str,
    props: list[tuple[str, str]] | None = None,
) -> QualityVerdict:
    """Generic operating-line verdict, shared by non-bank sector rules.

    Encodes the catch-the-signal rule (playbook §0): SHORT only on a confirmed
    operating-line decline; ``props`` (the active non-core corroborations, each a
    ``(flag, reason)`` pair — other-income/tax props) reinforce a miss and cap an
    otherwise-clean beat, but never trigger a short on their own. This is the same
    logic ``earnings_quality.classify_quality`` applies for BFSI, generalised to
    any operating line."""
    props = props or []
    v = QualityVerdict()
    pat_beat = pat_growth is not None and pat_growth >= _PAT_BEAT
    operating_miss = op_growth is not None and op_growth < _OP_DROP
    operating_beat = op_growth is not None and op_growth > _OP_BEAT

    def _add_props() -> None:
        for flag, reason in props:
            v.flags.append(flag)
            v.reasons.append(reason)

    if operating_miss:
        if pat_beat:
            v.flags.append("low_quality_beat")
            v.reasons.append(
                f"headline beat, operating miss (PAT {pat_growth:+.1f}% vs {op_label} {op_growth:+.1f}%)"
            )
        else:
            v.flags.append("result_miss")
            pat_txt = f"{pat_growth:+.1f}%" if pat_growth is not None else "n/a"
            v.reasons.append(f"outright miss (PAT {pat_txt}, {op_label} {op_growth:+.1f}%)")
        _add_props()
        v.label, v.direction = "low", "short"
    elif operating_beat:
        if props:
            v.reasons.append(
                f"operating beat ({op_label} {op_growth:+.1f}%), but earnings-quality caveat"
            )
            _add_props()
            v.label, v.direction = "neutral", None
        else:
            v.label, v.direction = "high", "long"
            pat_txt = f"{pat_growth:+.1f}%" if pat_growth is not None else "n/a"
            v.reasons.append(f"clean beat (PAT {pat_txt}, {op_label} {op_growth:+.1f}%)")
    else:
        _add_props()
        v.label, v.direction = "neutral", None
    return v


def classify_operating_quality(growth: dict | None, fields: dict | None = None) -> QualityVerdict:
    """The generic non-bank earnings-quality verdict, shared by sector rules whose
    tell is the operating line + non-core props (energy, IT, and others as they
    are built).

    Reads the operating line via ``generic_operating_growth`` (true EBITDA when
    available, else core profit ex-other-income, else revenue) and corroborates
    with the universal other-income and tax-write-back props. SHORT only on a
    confirmed operating-line decline (playbook §0)."""
    _ = fields  # reserved for sector-specific level fields (inventory, forex, …)
    op_growth, op_label = generic_operating_growth(growth)
    pat = _g(growth, "yoy_pat_pct")

    props: list[tuple[str, str]] = []
    if other_income_propped(growth):
        oi = _g(growth, "yoy_other_income_pct")
        props.append((
            "other_income_propped",
            f"PAT propped by other income ({oi:+.1f}% YoY) while core operating fell",
        ))
    if tax_propped(growth):
        tax = _g(growth, "yoy_tax_pct")
        props.append((
            "tax_propped",
            f"headline held by a lower tax charge ({tax:+.1f}% YoY) on falling pre-tax profit",
        ))

    return verdict_from_operating(pat, op_growth, op_label, props)


# --- P7: fold the press-release narrative into the P&L verdict -----------------
# The narrative carries the KPIs the P&L can't: guidance (IT — the single
# biggest mover), volume growth (FMCG/auto), FDA status (pharma — binary and
# huge), order inflow (capgoods). The folding rules keep the engine's
# catch-the-signal discipline: a narrative signal must be unambiguous to move
# the verdict, and the only narrative that shorts *against* a healthy operating
# line is a pharma warning letter / import alert (playbook §2.6 — "very
# negative regardless of P&L").
_FDA_NEGATIVE = ("warning_letter", "import_alert")


def apply_narrative(
    verdict: QualityVerdict,
    growth: dict | None,
    narrative: dict | None,
    sector_class: SectorClass,
    operating_line: Callable[[Optional[dict]], tuple[Optional[float], str]] = generic_operating_growth,
) -> QualityVerdict:
    """Adjust a sector verdict with the filing's narrative signals (P7).

    * Pharma FDA warning letter / import alert → SHORT regardless of the P&L.
    * Guidance cut (non-BFSI) → SHORT unless the operating line genuinely beat
      (then a neutral-with-caveat: mixed print); guidance raised upgrades a
      clean flat-neutral to LONG only when the operating line did not fall and
      no prop is flagged.
    * FMCG/auto: revenue up but volumes flat/down (price-led, not demand-led)
      caps a long back to neutral — the §2.4/2.5 prop; never shorts alone.
    Order inflow, dividend and management tone are context for the alert card,
    not verdict inputs."""
    if not narrative:
        return verdict

    # The §2.6 binary: negative FDA action sinks the print whatever the P&L says.
    if sector_class == SectorClass.PHARMA and narrative.get("fda_status") in _FDA_NEGATIVE:
        verdict.flags.append("fda_negative")
        verdict.reasons.append(
            f"USFDA {narrative['fda_status'].replace('_', ' ')} — negative regardless of P&L"
        )
        verdict.label, verdict.direction = "low", "short"
        return verdict

    op_growth, _ = operating_line(growth)

    if sector_class != SectorClass.BFSI:   # banks don't guide in result filings
        guidance = narrative.get("guidance")
        if guidance == "cut":
            verdict.flags.append("guidance_cut")
            verdict.reasons.append("guidance cut in the result narrative")
            if verdict.direction == "long":
                # operating beat + guidance cut = mixed print, not a short
                verdict.label, verdict.direction = "neutral", None
            elif op_growth is not None and op_growth <= _OP_BEAT:
                # no operating beat to offset the cut — the market sells the cut
                verdict.label, verdict.direction = "low", "short"
        elif guidance == "raised":
            verdict.flags.append("guidance_raised")
            verdict.reasons.append("guidance raised in the result narrative")
            if (
                verdict.label == "neutral" and verdict.direction is None
                and op_growth is not None and op_growth >= 0.0
                and not any(f.endswith("_propped") for f in verdict.flags)
            ):
                # flat-but-clean operating quarter + a raise = the market buys it
                verdict.label, verdict.direction = "high", "long"

    if sector_class in (SectorClass.FMCG, SectorClass.AUTO):
        vol = narrative.get("volume_growth")
        rev = _g(growth, "yoy_revenue_pct")
        if vol is not None and vol <= 0.0 and rev is not None and rev > 0.0:
            verdict.flags.append("price_led_growth")
            verdict.reasons.append(
                f"revenue {rev:+.1f}% but volumes {vol:+.1f}% — price-led, not demand-led"
            )
            if verdict.direction == "long":
                verdict.label, verdict.direction = "neutral", None

    return verdict


def out_of_scope_verdict(sector_class: SectorClass) -> QualityVerdict:
    """The P1 guard's output for a sector with no built rule yet: explicitly
    neutral + low confidence, so the dashboard never implies more than BFSI
    coverage (playbook §5)."""
    v = QualityVerdict(label="neutral", direction=None)
    v.flags.append("sector_out_of_scope")
    v.reasons.append(f"{sector_class.value} result-reading not yet built — low confidence")
    return v


def unbuilt_spec(
    sector_class: SectorClass,
    kpis: tuple[str, ...],
    *,
    operating_line: Callable[[Optional[dict]], tuple[Optional[float], str]] = generic_operating_growth,
) -> SectorSpec:
    """Build a placeholder spec for a not-yet-implemented sector: declares its
    operating line + KPIs but routes the verdict to the out-of-scope guard.
    Replace with a real ``SectorSpec`` (own ``classify``, ``built=True``) once
    the sector's rule passes its real-PDF regression."""

    def _classify(*args, **kwargs) -> QualityVerdict:
        del args, kwargs  # signature kept for the router; inputs unused until built
        return out_of_scope_verdict(sector_class)

    return SectorSpec(
        sector_class=sector_class,
        operating_line=operating_line,
        classify=_classify,
        built=False,
        kpis=kpis,
    )
