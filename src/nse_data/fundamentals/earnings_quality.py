"""Earnings-quality divergence (Phase 5 — Week 17.5, S3).

The SBI 8-May-2026 lesson: a headline PAT *beat* can hide an operating *miss*.
SBI's PAT grew +5.6% YoY while pre-provision operating profit (PPOP) fell
−11.45% YoY, provisions fell −36.6% YoY (propping PAT), and the treasury line
swung to a loss. A system that reads only ``pat_cr`` sees a healthy print; the
market sold the operating line.

This module turns the extractor's PDF-derived growth dict (+ a few level fields)
into a quality verdict — pure arithmetic, **no external data and no stored
history** (the growth comes from the filing's own comparative columns). It is the
input the ``result_quality_low`` / ``result_quality_high`` signal fires on.

    verdict = classify_quality(growth, fields, is_bfsi=True)
    verdict.label        # 'low' | 'high' | 'neutral'
    verdict.direction    # 'short' | 'long' | None  (market-reaction lean)
    verdict.flags        # ['low_quality_beat', 'provision_propped', 'treasury_hit']
    verdict.summary      # one-line human string for the alert

Generalises beyond BFSI: the same "headline up, operating down" shape applies to
any sector once its operating line is extracted (IT margin, FMCG volume, …). For
now the operating line is PPOP (BFSI) or, as a weak generic proxy, revenue.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Materiality thresholds (percent). A move smaller than this is treated as noise
# rather than a divergence — keeps the signal off near-flat prints.
_PPOP_DROP = -2.0          # operating profit down more than this YoY = operating miss
_PAT_FLAT = 0.0            # PAT growth at/above this counts as a "beat/flat" headline
_PROVISION_DROP = -10.0    # provisions down more than this YoY = materially propping PAT
_OTHER_INCOME_DROP = -15.0  # other income down more than this YoY = non-core hit
_CLEAN_BEAT_MIN = 2.0      # PAT and operating both up by more than this = high quality


@dataclass
class QualityVerdict:
    label: str = "neutral"            # 'low' | 'high' | 'neutral'
    direction: str | None = None      # 'short' | 'long' | None — market-reaction lean
    flags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "no quality divergence"

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "direction": self.direction,
            "flags": list(self.flags),
            "summary": self.summary,
        }


def _g(growth: dict | None, key: str) -> float | None:
    if not growth:
        return None
    v = growth.get(key)
    return float(v) if isinstance(v, (int, float)) else None


def _operating_growth(growth: dict | None, *, is_bfsi: bool) -> tuple[float | None, str]:
    """The operating-line growth used for the divergence, YoY preferred over QoQ.

    BFSI → pre-provision operating profit (PPOP). Otherwise fall back to revenue
    as a weak proxy (until per-sector operating lines are extracted)."""
    if is_bfsi:
        for key, lbl in (("yoy_ppop_pct", "PPOP YoY"), ("qoq_ppop_pct", "PPOP QoQ")):
            v = _g(growth, key)
            if v is not None:
                return v, lbl
        return None, "PPOP"
    for key, lbl in (("yoy_revenue_pct", "revenue YoY"), ("qoq_revenue_pct", "revenue QoQ")):
        v = _g(growth, key)
        if v is not None:
            return v, lbl
    return None, "revenue"


def classify_quality(
    growth: dict | None,
    fields: dict | None = None,
    *,
    is_bfsi: bool = False,
) -> QualityVerdict:
    """Classify a result's earnings quality from its growth dict (+ levels).

    ``growth`` keys are the extractor's ``*_pct`` outputs (yoy/qoq for revenue,
    pat, and — for BFSI — nii/ppop/provisions/other_income). ``fields`` carries
    level values like ``profit_on_sale_of_investments_cr`` for the treasury read.
    """
    fields = fields or {}
    v = QualityVerdict()

    pat_yoy = _g(growth, "yoy_pat_pct")
    op_growth, op_label = _operating_growth(growth, is_bfsi=is_bfsi)
    pat_beat = pat_yoy is not None and pat_yoy >= _PAT_FLAT

    # The bearish trigger is an OPERATING MISS — the pre-provision operating line
    # (PPOP for banks) falling materially. That is bearish regardless of the
    # headline: if PAT is up/flat it's a hidden miss (low_quality_beat); if PAT is
    # also down it's an outright miss (result_miss). Either flips to SHORT. Using
    # the operating line (not PAT) removes the knife-edge at PAT = 0.
    operating_miss = op_growth is not None and op_growth < _PPOP_DROP
    operating_beat = op_growth is not None and op_growth > _CLEAN_BEAT_MIN

    # Corroborating earnings-quality flags. These NEVER trigger a SHORT on their
    # own — a falling provision charge (esp. with improving asset quality) or a
    # treasury loss inside a genuine operating beat is a footnote, not a sell.
    # They reinforce an operating miss, and otherwise only cap the upside.
    prov_yoy = _g(growth, "yoy_provisions_pct")
    treasury = fields.get("profit_on_sale_of_investments_cr")
    oi_yoy = _g(growth, "yoy_other_income_pct")
    prov_propped = pat_beat and prov_yoy is not None and prov_yoy < _PROVISION_DROP
    treasury_hit = is_bfsi and (
        (isinstance(treasury, (int, float)) and treasury < 0)
        or (oi_yoy is not None and oi_yoy < _OTHER_INCOME_DROP)
    )

    def _add_corroboration() -> None:
        if prov_propped:
            v.flags.append("provision_propped")
            v.reasons.append(f"profit supported by provisions {prov_yoy:+.1f}% YoY, not core")
        if treasury_hit:
            v.flags.append("treasury_hit")
            if isinstance(treasury, (int, float)) and treasury < 0:
                v.reasons.append(f"treasury loss on sale of investments (₹{treasury:,.0f} cr)")
            else:
                v.reasons.append(f"non-core/other income {oi_yoy:+.1f}% YoY")

    if operating_miss:
        # Bearish: pre-provision operating profit is shrinking.
        if pat_beat:
            v.flags.append("low_quality_beat")            # headline holds, operating down
            v.reasons.append(
                f"headline beat, operating miss (PAT {pat_yoy:+.1f}% vs {op_label} {op_growth:+.1f}%)"
            )
        else:
            v.flags.append("result_miss")                 # headline ALSO down — outright miss
            pat_txt = f"{pat_yoy:+.1f}%" if pat_yoy is not None else "n/a"
            v.reasons.append(
                f"outright miss (PAT {pat_txt}, {op_label} {op_growth:+.1f}%)"
            )
        _add_corroboration()
        v.label, v.direction = "low", "short"
    elif operating_beat:
        # Operating line genuinely grew → NOT a short. A clean two-sided beat is
        # high; if the headline was flattered by a provision cut or carries a
        # treasury loss, stay neutral (positive op, but quality caveat) — never short.
        if prov_propped or treasury_hit:
            v.reasons.append(
                f"operating beat ({op_label} {op_growth:+.1f}%), but earnings-quality caveat"
            )
            _add_corroboration()
            v.label, v.direction = "neutral", None
        else:
            v.label, v.direction = "high", "long"
            v.reasons.append(f"clean beat (PAT {pat_yoy:+.1f}%, {op_label} {op_growth:+.1f}%)")
    else:
        # Operating line flat / weak-but-not-a-miss / unreadable: surface any
        # quality caveat but don't short without a confirmed operating miss.
        _add_corroboration()
        v.label, v.direction = "neutral", None
    return v
