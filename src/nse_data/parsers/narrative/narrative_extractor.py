"""Extract non-P&L signals from the result narrative (playbook §4 P7) — first cut.

The sector verdicts read the P&L; the *deciding* sector KPIs often live in the
press release / outcome letter instead: IT guidance (the dominant IT signal),
FMCG/auto volume growth, capital-goods order inflow, pharma FDA status,
dividend, and the §3.5 management-words tone. This module lifts those with
deliberate, conservative regex heuristics — a field is ``None`` unless the text
states it plainly; no value is ever guessed. (LLM/vision narrative reading can
replace individual heuristics later behind the same ``NarrativeFields``.)

    fields = extract_narrative(pdf_text)
    fields.guidance        # 'raised' | 'cut' | 'maintained' | None
    fields.volume_growth   # underlying volume growth %, signed
    fields.order_inflow    # fresh order inflow, ₹ crore
    fields.fda_status      # 'import_alert' | 'warning_letter' | 'observation' | 'clean' | None
    fields.dividend        # ₹ per share
    fields.mgmt_tone       # 'positive' | 'negative' | None
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class NarrativeFields:
    """Structured signals lifted from the result narrative (all optional).

    The first block are the verdict-moving signals (guidance / FDA / volume,
    folded in by ``sectors.base.apply_narrative``); the second block are the
    per-sector KPIs (playbook §2) — card context for now, promoted to verdict
    inputs once their live extraction accuracy is proven."""

    guidance: str | None = None
    volume_growth: float | None = None
    order_inflow: float | None = None      # ₹ crore
    fda_status: str | None = None
    dividend: float | None = None          # ₹ per share
    mgmt_tone: str | None = None
    # --- sector KPIs (card context) ---
    cc_revenue_growth_pct: float | None = None   # IT: constant-currency revenue YoY %
    tcv_usd_mn: float | None = None              # IT: deal TCV, USD million
    attrition_pct: float | None = None           # IT
    grm_usd_bbl: float | None = None             # refiners: gross refining margin $/bbl
    ebitda_per_tonne: float | None = None        # metals: ₹/tonne
    us_sales_growth_pct: float | None = None     # pharma: US business YoY %
    order_book: float | None = None              # capgoods: ₹ crore

    def as_dict(self) -> dict:
        return {
            "guidance": self.guidance,
            "volume_growth": self.volume_growth,
            "order_inflow": self.order_inflow,
            "fda_status": self.fda_status,
            "dividend": self.dividend,
            "mgmt_tone": self.mgmt_tone,
            "cc_revenue_growth_pct": self.cc_revenue_growth_pct,
            "tcv_usd_mn": self.tcv_usd_mn,
            "attrition_pct": self.attrition_pct,
            "grm_usd_bbl": self.grm_usd_bbl,
            "ebitda_per_tonne": self.ebitda_per_tonne,
            "us_sales_growth_pct": self.us_sales_growth_pct,
            "order_book": self.order_book,
        }


def _num(s: str) -> float:
    """'1,15,784.5' (Indian digit grouping) → 115784.5."""
    return float(s.replace(",", ""))


# --- guidance (IT — the single biggest mover; §2.3) ---------------------------
# Verb and the word "guidance" within one clause, either order:
# "raised its revenue guidance", "guidance ... revised upwards", "FY26 guidance
# maintained". Window-bounded so a verb in one sentence can't pair with a
# "guidance" several paragraphs later.
_G_RAISE = r"(?:rais\w+|increas\w+|upgrad\w+|revis\w+\s+(?:\w+\s+){0,3}upward\w*)"
_G_CUT = r"(?:cut[s]?|lower\w+|reduc\w+|downgrad\w+|revis\w+\s+(?:\w+\s+){0,3}downward\w*|trimm?\w*)"
_G_KEEP = r"(?:maintain\w+|retain\w+|reiterat\w+|unchanged|kept|reaffirm\w+)"
_GUIDANCE_RES: tuple[tuple[str, re.Pattern], ...] = tuple(
    (label, re.compile(rf"(?:{verb}[^.\n]{{0,80}}?guidance|guidance[^.\n]{{0,80}}?{verb})", re.I))
    for label, verb in (("raised", _G_RAISE), ("cut", _G_CUT), ("maintained", _G_KEEP))
)


def _guidance(text: str) -> str | None:
    hits = [(m.start(), label)
            for label, rx in _GUIDANCE_RES
            for m in [rx.search(text)] if m]
    if not hits:
        return None
    # A raise/cut statement beats a boilerplate "maintained" elsewhere; between
    # raise and cut (rare), trust whichever the text states first.
    directional = [h for h in hits if h[1] != "maintained"]
    return min(directional or hits)[1]


# --- volume growth (FMCG / auto; §2.4–2.5) ------------------------------------
# "underlying volume growth of 5%", "volumes grew 4.2%", "volume declined by 3%",
# "UVG of 6%". The sign comes from the verb.
_VOL_OF = re.compile(
    r"(?:underlying\s+)?volume\s+(?:growth|grew|increase\w*|rose|up)\s*(?:of|by|at)?\s*"
    r"(-?\d+(?:\.\d+)?)\s*%", re.I)
_VOL_DOWN = re.compile(
    r"volumes?\s+(?:declin\w+|fell|de-?grew|down|contract\w+|dropp?\w*)\s*(?:of|by)?\s*"
    r"(-?\d+(?:\.\d+)?)\s*%", re.I)
_UVG = re.compile(r"\bUVG\b[^.\n]{0,30}?(-?\d+(?:\.\d+)?)\s*%", re.I)


def _volume_growth(text: str) -> float | None:
    m = _VOL_OF.search(text) or _UVG.search(text)
    if m:
        return _num(m.group(1))
    m = _VOL_DOWN.search(text)
    if m:
        return -abs(_num(m.group(1)))
    return None


# --- order inflow (capital goods; §2.8) ---------------------------------------
# "order inflow of ₹1,15,784 crore", "orders worth Rs 8,000 crore", "received
# orders valued at ₹ 12,000 crore". Returned in ₹ crore.
_RS = r"(?:₹|Rs\.?|INR)\s*"
_ORDER_INFLOW = re.compile(
    rf"order\s+(?:inflow|inflows|wins?|intake)[^.\n]{{0,60}}?{_RS}([\d,]+(?:\.\d+)?)\s*crore", re.I)
_ORDERS_WORTH = re.compile(
    rf"orders?\s+(?:worth|valued\s+at|aggregating(?:\s+to)?)\s*{_RS}([\d,]+(?:\.\d+)?)\s*crore", re.I)


def _order_inflow(text: str) -> float | None:
    m = _ORDER_INFLOW.search(text) or _ORDERS_WORTH.search(text)
    return _num(m.group(1)) if m else None


# --- FDA status (pharma — binary and huge; §2.6) -------------------------------
# Severity-ordered: an import alert outranks a warning letter outranks Form-483
# observations; a clean EIR / "no observations" only counts when nothing worse
# is mentioned.
_FDA_LEVELS: tuple[tuple[str, re.Pattern], ...] = (
    ("import_alert", re.compile(r"import\s+alert", re.I)),
    ("warning_letter", re.compile(r"warning\s+letter", re.I)),
    ("observation", re.compile(r"form\s*-?\s*483|(?:US\s*FDA|USFDA|FDA)[^.\n]{0,60}?observations?", re.I)),
    ("clean", re.compile(
        r"\bEIR\b|establishment\s+inspection\s+report|"
        r"(?:no|zero|nil)\s+observations?|successfully\s+(?:complet\w+|clear\w+)[^.\n]{0,40}?inspection", re.I)),
)


def _fda_status(text: str) -> str | None:
    for label, rx in _FDA_LEVELS:
        if rx.search(text):
            return label
    return None


# --- dividend (₹ per share) ----------------------------------------------------
# "interim dividend of ₹16 per equity share", "dividend of Rs. 5/- per share".
_DIVIDEND = re.compile(
    rf"dividend\s+of\s+{_RS}([\d,]+(?:\.\d+)?)\s*(?:/-)?\s*(?:\(.*?\)\s*)?per\s+(?:equity\s+)?share", re.I)


def _dividend(text: str) -> float | None:
    m = _DIVIDEND.search(text)
    return _num(m.group(1)) if m else None


# --- sector KPIs (playbook §2 — card context, regex fallback layer) ------------
# Same discipline as the core fields: anchored on the literal phrasings real
# filings use, None otherwise. The LLM layer (llm_narrative) reads these with
# more context; these regexes both fill its gaps offline and act as the unit
# sanity-check it is reconciled against.
_CC_REV = (
    re.compile(r"(-?\d+(?:\.\d+)?)\s*%[^.\n]{0,40}?\bin\s+constant\s+currency", re.I),
    re.compile(r"constant\s+currency[^.\n]{0,40}?(?:growth|grew|of)\s*(?:of|by|at)?\s*(-?\d+(?:\.\d+)?)\s*%", re.I),
)
_TCV = re.compile(
    r"(?:large\s+deal\s+|total\s+contract\s+value\s*|deal\s+)?TCV[^.\n]{0,40}?"
    r"(?:US\s*)?\$\s*([\d,]+(?:\.\d+)?)\s*(billion|bn|million|mn)", re.I)
_ATTRITION = re.compile(
    r"attrition(?:\s*\(?LTM\)?)?[^.\n]{0,30}?(\d+(?:\.\d+)?)\s*%", re.I)
_GRM = re.compile(
    r"(?:GRM|gross\s+refining\s+margin)[^.\n]{0,40}?(?:US\s*)?\$\s*(\d+(?:\.\d+)?)\s*(?:/|per\s+)(?:bbl|barrel)", re.I)
_EBITDA_TONNE = re.compile(
    rf"EBITDA\s*(?:/|per\s+)(?:t\b|tonne|ton\b)[^.\n]{{0,30}}?{_RS}([\d,]+(?:\.\d+)?)", re.I)
_US_SALES_UP = re.compile(
    r"US\s+(?:sales|business|revenues?|formulations?)[^.\n]{0,40}?(?:grew|increased|rose|up)\s*(?:by|at)?\s*(\d+(?:\.\d+)?)\s*%", re.I)
_US_SALES_DOWN = re.compile(
    r"US\s+(?:sales|business|revenues?|formulations?)[^.\n]{0,40}?(?:declin\w+|fell|de-?grew|down|contract\w+)\s*(?:by|at)?\s*(\d+(?:\.\d+)?)\s*%", re.I)
_ORDER_BOOK = re.compile(
    rf"order\s+book[^.\n]{{0,60}}?{_RS}([\d,]+(?:\.\d+)?)\s*crore", re.I)


def _cc_revenue(text: str) -> float | None:
    for rx in _CC_REV:
        m = rx.search(text)
        if m:
            return _num(m.group(1))
    return None


def _tcv_usd_mn(text: str) -> float | None:
    m = _TCV.search(text)
    if not m:
        return None
    v = _num(m.group(1))
    return v * 1000.0 if m.group(2).lower() in ("billion", "bn") else v


def _attrition(text: str) -> float | None:
    m = _ATTRITION.search(text)
    return _num(m.group(1)) if m else None


def _grm(text: str) -> float | None:
    m = _GRM.search(text)
    return _num(m.group(1)) if m else None


def _ebitda_per_tonne(text: str) -> float | None:
    m = _EBITDA_TONNE.search(text)
    return _num(m.group(1)) if m else None


def _us_sales(text: str) -> float | None:
    m = _US_SALES_UP.search(text)
    if m:
        return _num(m.group(1))
    m = _US_SALES_DOWN.search(text)
    if m:
        return -abs(_num(m.group(1)))
    return None


def _order_book(text: str) -> float | None:
    m = _ORDER_BOOK.search(text)
    return _num(m.group(1)) if m else None


# --- management tone (§3 step 5 word lists) ------------------------------------
_POSITIVE_WORDS = (
    "strong demand", "robust pipeline", "robust demand", "margin expansion",
    "guidance raised", "strong order book", "record revenue", "record profit",
    "healthy growth", "strong momentum", "broad-based growth", "all-time high",
)
_NEGATIVE_WORDS = (
    "headwinds", "weak demand", "margin pressure", "slowdown", "uncertainty",
    "challenging environment", "demand softness", "subdued", "muted demand",
    "pricing pressure", "cost pressure",
)


def _mgmt_tone(text: str) -> str | None:
    low = text.lower()
    pos = sum(low.count(w) for w in _POSITIVE_WORDS)
    neg = sum(low.count(w) for w in _NEGATIVE_WORDS)
    if pos == neg:           # includes the no-mentions case
        return None
    return "positive" if pos > neg else "negative"


def extract_narrative(text: str) -> NarrativeFields:
    """Parse narrative signals from result press-release / outcome-letter text.

    Conservative by design: every field is ``None`` unless the text states it
    in a recognised phrasing — a missing narrative read must never block or
    distort the P&L verdict it supplements."""
    if not text:
        return NarrativeFields()
    return NarrativeFields(
        guidance=_guidance(text),
        volume_growth=_volume_growth(text),
        order_inflow=_order_inflow(text),
        fda_status=_fda_status(text),
        dividend=_dividend(text),
        mgmt_tone=_mgmt_tone(text),
        cc_revenue_growth_pct=_cc_revenue(text),
        tcv_usd_mn=_tcv_usd_mn(text),
        attrition_pct=_attrition(text),
        grm_usd_bbl=_grm(text),
        ebitda_per_tonne=_ebitda_per_tonne(text),
        us_sales_growth_pct=_us_sales(text),
        order_book=_order_book(text),
    )
