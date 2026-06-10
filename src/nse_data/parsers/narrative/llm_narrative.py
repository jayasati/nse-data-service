"""LLM-first narrative extraction (P7 accuracy pass).

The regex extractor (``narrative_extractor``) is precise but phrasing-bound; a
press release that words its guidance unusually slips past it. This module
layers gpt-4o on top — the same client/budget machinery the P&L vision
extractor uses — and reconciles the two reads:

  * **Categorical fields** (guidance, fda_status, mgmt_tone): the LLM wins —
    judging "did they raise or cut?" from context is exactly what it's better at.
  * **Numeric fields**: the LLM value is accepted, but when the regex also
    found the number and the two disagree materially (>30%), the regex wins —
    it is anchored on the literal printed phrase, so a disagreement usually
    means the LLM slipped on units (₹ cr vs $ mn, lakh-crore grouping).
  * Regex fills any field the LLM returned null for; with no LLM configured
    the result degrades to the pure regex read (offline/test path).

``extract_narrative_vision`` is the same prompt over rendered page images, for
image-only siblings (investor decks with no text layer).
"""
from __future__ import annotations

import base64

import structlog

from .narrative_extractor import extract_narrative

log = structlog.get_logger()

# Categorical fields where the LLM's contextual read is preferred outright.
_CATEGORICAL = ("guidance", "fda_status", "mgmt_tone")
# Numeric fields, reconciled against the regex read (regex wins on material
# disagreement — it can't make a unit mistake, it read the printed phrase).
_NUMERIC = (
    "volume_growth", "order_inflow", "dividend",
    "cc_revenue_growth_pct", "tcv_usd_mn", "attrition_pct",
    "grm_usd_bbl", "ebitda_per_tonne", "us_sales_growth_pct", "order_book",
)
_DISAGREE_REL = 0.30      # >30% apart = material disagreement → trust the regex
_GUIDANCE_LABELS = {"raised", "cut", "maintained"}
_FDA_LABELS = {"import_alert", "warning_letter", "observation", "clean"}
_TONE_LABELS = {"positive", "negative"}

# Truncation bound for the text call: the narrative lives in the first pages of
# a press release; 30k chars ≈ 8k tokens keeps cost and latency tight without
# losing the management commentary.
_MAX_TEXT_CHARS = 30_000
_MAX_VISION_PAGES = 8

_PROMPT = """\
You are reading an Indian listed company's quarterly-result narrative \
(press release / outcome letter / investor presentation){context}.

Return a JSON object with EXACTLY these keys. Use null for any value the text \
does not state explicitly — never infer, estimate, or compute (unit conversion \
is the only arithmetic allowed). Numbers must be plain JSON numbers.

  "guidance": "raised" | "cut" | "maintained" | null
      — full-year revenue/margin guidance action stated in THIS document.
  "volume_growth": number | null — underlying/UVG volume growth, % YoY.
  "order_inflow": number | null — fresh order inflow/wins THIS quarter, in INR crore
      (convert: 1 lakh crore = 100000 crore; never report $ values here).
  "order_book": number | null — total order book / backlog, in INR crore.
  "fda_status": "import_alert" | "warning_letter" | "observation" | "clean" | null
      — worst USFDA action mentioned ("clean" only for EIR / no observations).
  "dividend": number | null — declared dividend, INR per share.
  "mgmt_tone": "positive" | "negative" | null — overall management commentary tone
      (null if mixed or absent).
  "cc_revenue_growth_pct": number | null — revenue growth in CONSTANT CURRENCY, % YoY.
  "tcv_usd_mn": number | null — deal TCV in USD MILLION (convert $X billion → X*1000).
  "attrition_pct": number | null — LTM attrition, %.
  "grm_usd_bbl": number | null — gross refining margin, USD per barrel.
  "ebitda_per_tonne": number | null — EBITDA per tonne, INR.
  "us_sales_growth_pct": number | null — US business revenue growth, % YoY
      (negative if it declined).

Read carefully for units: Indian filings mix INR crore, INR lakh crore, USD \
million and USD billion in one paragraph. Quarter figures only — ignore \
half-year/full-year numbers."""


def _get_client():
    """The shared, budget-capped gpt-4o client (lazy; None when unconfigured)."""
    from ..extractors.vision_financial import _get_client as _vision_client

    return _vision_client()


def _context(symbol: str | None, sector: str | None) -> str:
    bits = [b for b in (symbol, f"sector: {sector}" if sector else None) if b]
    return f" for {', '.join(bits)}" if bits else ""


def _coerce(parsed: dict) -> dict:
    """Validate the LLM's JSON into a NarrativeFields-shaped dict; anything
    malformed or out of vocabulary becomes None rather than propagating."""
    out: dict = {}
    for key, vocab in (("guidance", _GUIDANCE_LABELS), ("fda_status", _FDA_LABELS),
                       ("mgmt_tone", _TONE_LABELS)):
        v = parsed.get(key)
        out[key] = v if isinstance(v, str) and v in vocab else None
    for key in _NUMERIC:
        v = parsed.get(key)
        out[key] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    # Percentages beyond ±500% are a misread, not a result.
    for key in ("volume_growth", "cc_revenue_growth_pct", "attrition_pct",
                "us_sales_growth_pct"):
        if out[key] is not None and abs(out[key]) > 500:
            out[key] = None
    return out


def _llm_json(messages: list[dict]) -> tuple[dict | None, float]:
    client = _get_client()
    if client is None:
        return None, 0.0
    try:
        res = client.chat_completion(
            messages=messages, response_format={"type": "json_object"},
            max_tokens=600,
        )
    except Exception as e:  # noqa: BLE001 — incl. DailyCapExceeded
        log.warning("narrative_llm_failed", error=str(e))
        return None, 0.0
    if not res.success or not isinstance(res.parsed_json, dict):
        log.warning("narrative_llm_failed", error=res.error)
        return None, res.cost_usd
    return _coerce(res.parsed_json), res.cost_usd


def extract_narrative_llm(
    text: str, *, symbol: str | None = None, sector: str | None = None,
) -> tuple[dict | None, float]:
    """One JSON-mode text call. Returns (fields dict | None, cost_usd)."""
    if not text:
        return None, 0.0
    messages = [{
        "role": "user",
        "content": (
            _PROMPT.format(context=_context(symbol, sector))
            + "\n\n--- DOCUMENT TEXT ---\n" + text[:_MAX_TEXT_CHARS]
        ),
    }]
    return _llm_json(messages)


def extract_narrative_vision(
    pdf_bytes: bytes, *, symbol: str | None = None, sector: str | None = None,
) -> tuple[dict | None, float]:
    """The same read over rendered page images — for image-only siblings
    (investor decks whose PDF has no text layer)."""
    from ..pdf_render import render_pages

    pngs = render_pages(pdf_bytes, max_pages=_MAX_VISION_PAGES)
    if not pngs:
        return None, 0.0
    content: list[dict] = [
        {"type": "text", "text": _PROMPT.format(context=_context(symbol, sector))}
    ]
    for png in pngs:
        b64 = base64.b64encode(png).decode("ascii")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"},
        })
    return _llm_json([{"role": "user", "content": content}])


def _reconcile(llm: dict, rx: dict) -> dict:
    """Merge the LLM and regex reads per the module-docstring policy."""
    out: dict = {}
    for key in _CATEGORICAL:
        out[key] = llm.get(key) if llm.get(key) is not None else rx.get(key)
    for key in _NUMERIC:
        lv, rv = llm.get(key), rx.get(key)
        if lv is None:
            out[key] = rv
        elif rv is None:
            out[key] = lv
        elif abs(lv - rv) > _DISAGREE_REL * max(abs(lv), abs(rv)):
            log.warning("narrative_llm_regex_disagree", field=key, llm=lv, regex=rv)
            out[key] = rv        # the regex read the printed phrase — units safe
        else:
            out[key] = lv
    return out


def narrative_fields(
    text: str, *, use_llm: bool = True,
    symbol: str | None = None, sector: str | None = None,
) -> tuple[dict | None, float]:
    """The narrative read for one document: LLM-first, regex-reconciled.

    Returns (NarrativeFields-shaped dict | None-if-empty, llm_cost_usd).
    Degrades to the pure regex read when the LLM is off/unconfigured/failing —
    the narrative layer must never block the P&L flow it supplements."""
    if not text:
        return None, 0.0
    rx = extract_narrative(text).as_dict()
    cost = 0.0
    if use_llm:
        llm, cost = extract_narrative_llm(text, symbol=symbol, sector=sector)
        merged = _reconcile(llm, rx) if llm else rx
    else:
        merged = rx
    return (merged if any(v is not None for v in merged.values()) else None), cost
