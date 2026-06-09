"""Vision-first financial extraction via gpt-4o (Week 17 rewrite).

The primary path renders the P&L page(s) to images and asks gpt-4o to read the
Statement of Profit & Loss directly — the layout is preserved, so the model
picks the right column by its header date and never confuses a Notes column or
the year-ago quarter (the failure modes that broke the deterministic parser).

A cheaper text fallback (``extract_via_text``) sends the extracted P&L text
instead of images; the orchestrator uses it when rendering/vision is
unavailable. Both share one canonical schema and one mapping to ``*_cr`` fields,
so the orchestrator treats their results interchangeably.

Returned shape (or ``None`` when the LLM is unavailable / fails)::

    {"fields": {...standalone *_cr...}, "consolidated": {...|{}},
     "units_phrase": str|None, "period_ending": str|None,
     "cost_usd": float, "table_found": bool}
"""
from __future__ import annotations

import base64

import structlog

from .llm_client import DailyCapExceeded, LLMClient

log = structlog.get_logger()

# Raw model field -> canonical extractor field. Amounts get the unit factor;
# per-share fields pass through untouched.
_FIELD_MAP = {
    "revenue": "revenue_cr",
    "other_income": "other_income_cr",
    "total_income": "total_income_cr",
    "total_expenses": "total_expenses_cr",
    "pbt": "pbt_cr",
    "tax": "tax_cr",
    "pat": "pat_cr",
    "total_comprehensive_income": "total_comprehensive_income_cr",
    "eps_basic": "eps_basic",
    "eps_diluted": "eps_diluted",
    "cfo": "cfo_cr",      # cash flow from operations (earnings-quality input, 17.7)
}
_RUPEE_FIELDS = {"eps_basic", "eps_diluted"}

# Source-unit phrase -> multiplier to crore. Substring match, most specific
# first. EPS is never scaled.
_UNIT_FACTORS = [
    ("crore", 1.0),
    ("million", 0.1),
    ("lakh", 0.01),
    ("lac", 0.01),
    ("thousand", 1e-4),
]

# The schema both prompts demand. Kept identical so the two paths are
# interchangeable and match tests/.../ground_truth/*.yaml.
_SCHEMA_BLOCK = """{
  "standalone": {
    "revenue": <number or null>, "other_income": <number or null>,
    "total_income": <number or null>, "total_expenses": <number or null>,
    "pbt": <number or null>, "tax": <number or null>, "pat": <number or null>,
    "total_comprehensive_income": <number or null>,
    "eps_basic": <number or null>, "eps_diluted": <number or null>,
    "cfo": <number or null>
  },
  "consolidated": { <same 11 fields, or null if absent> },
  "units_in_source_pdf": "<'INR million' | 'INR lakh' | 'INR crore' | 'INR thousand' | 'INR'>",
  "period_ending": "<YYYY-MM-DD of the quarter you read, or null>",
  "table_found": <true if you found the P&L>,
  "notes": "<brief observations>"
}"""

_COMMON_RULES = """Field definitions & rules:
- "revenue" = Revenue from operations (the operating-revenue subtotal). If it is
  split (Sale of products / services / other operating revenue), SUM those lines.
  Do NOT use "Total income" as revenue. For banks/NBFCs use "Interest Earned" /
  "Net Interest Income" as revenue.
- COLUMN: read the MOST RECENT QUARTER. Identify it by the column whose header
  date is the latest QUARTER-end (e.g. 31-03-2026) — NOT a full-year/"year ended"
  column that may share that date, and NOT the year-ago quarter (31-03-2025).
  Ignore any "Note"/"Notes" reference column (small integers like 2, 13).
- SCOPE: standalone and consolidated are SEPARATE statements. Return a block ONLY
  for a statement physically printed in the filing; set the other to null. NEVER
  fabricate or copy one scope into the other. Consolidated numbers are usually
  larger (they include subsidiaries).
- SIGN: numbers in (parentheses) are negative, e.g. "(166.79)" -> -166.79. PAT can
  be a loss (negative) — return it negative.
- TAX SIGN: report tax as a POSITIVE expense in the normal case. Parentheses on the
  tax line usually just mean "subtracted from PBT" — still positive. Tax is negative
  ONLY for a genuine net tax credit. Check: PBT - tax = PAT.
- MAGNITUDE: PAT magnitude < revenue; EPS is a small decimal. If violated you read
  the wrong row/column — re-read.
- "cfo" = Net cash flow from OPERATING activities, ONLY if a Cash Flow Statement is
  printed (usual in half-year/annual results, absent in most quarterly P&L-only
  filings). If there is no cash-flow statement, set cfo to null — do NOT infer it.
- Return raw values exactly as printed (keep the PDF's unit; identify the unit
  separately). NEVER guess — use null if unsure. Return ONLY the JSON object."""

VISION_PROMPT = (
    "You are a financial data extractor for Indian quarterly result PDFs filed "
    "with NSE. You are shown IMAGES of the result filing's pages. Find the "
    "Statement of Profit & Loss and read the numbers visually.\n\n"
    "Extract the 10 canonical fields below from the MOST RECENT QUARTER, for BOTH "
    "the standalone and consolidated statements when both are present.\n\n"
    "Return ONLY a JSON object:\n" + _SCHEMA_BLOCK + "\n\n" + _COMMON_RULES
)

TEXT_PROMPT = (
    "You are a financial data extractor for Indian quarterly result PDFs filed "
    "with NSE. You receive TEXT extracted from the PDF. The table is flattened "
    "into reading order: each row label is followed by its column values, one per "
    "line. OCR errors are common — tolerate near-miss labels.\n\n"
    "Extract the 10 canonical fields below from the MOST RECENT QUARTER, for BOTH "
    "the standalone and consolidated statements when both are present.\n\n"
    "Return ONLY a JSON object:\n" + _SCHEMA_BLOCK + "\n\n" + _COMMON_RULES
)


# A lazily-constructed shared client so repeated calls reuse one connection and
# one spend log.
_CLIENT: LLMClient | None = None
_CLIENT_FAILED = False


def _get_client() -> LLMClient | None:
    global _CLIENT, _CLIENT_FAILED
    if _CLIENT is not None:
        return _CLIENT
    if _CLIENT_FAILED:
        return None
    try:
        _CLIENT = LLMClient()
    except Exception as e:  # noqa: BLE001 — missing creds / import issues
        log.warning("llm_unavailable", error=str(e))
        _CLIENT_FAILED = True
        return None
    return _CLIENT


def units_factor(phrase: str | None) -> float:
    low = (phrase or "").lower()
    for word, factor in _UNIT_FACTORS:
        if word in low:
            return factor
    return 1.0


def _coerce_number(v) -> float | None:
    """Parse a model value to float. Handles Indian-format strings like
    ``"2,54,972"`` / ``"(1,234)"`` — strip grouping commas, currency, and
    parentheses (negative)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("₹", "").replace(" ", "")
    if not s or s.lower() in {"-", "--", "—", "na", "n/a", "null", "none", "nil"}:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        x = float(s)
    except ValueError:
        return None
    return -x if neg else x


def _map_block(block, factor: float) -> dict[str, float]:
    """Map one raw statement block (standalone/consolidated) to canonical *_cr."""
    if not block or not isinstance(block, dict):
        return {}
    out: dict[str, float] = {}
    for raw_name, canon in _FIELD_MAP.items():
        num = _coerce_number(block.get(raw_name))
        if num is None:
            continue
        out[canon] = num if canon in _RUPEE_FIELDS else num * factor
    return out


def _to_result(data: dict, cost_usd: float) -> dict:
    """Shared post-processing of a parsed model JSON into the return shape."""
    phrase = data.get("units_in_source_pdf")
    factor = units_factor(phrase)
    standalone = _map_block(data.get("standalone"), factor)
    consolidated = _map_block(data.get("consolidated"), factor)
    return {
        "fields": standalone,
        "consolidated": consolidated,
        "units_phrase": phrase,
        "period_ending": data.get("period_ending"),
        "cost_usd": cost_usd,
        "table_found": bool(data.get("table_found", bool(standalone or consolidated))),
    }


def _context_text(symbol, subject, broadcast_dt, *, image: bool) -> str:
    where = "page images" if image else "PDF text"
    return (
        f"Company: {symbol or '?'}\n"
        f"Filing date: {broadcast_dt or '?'}\n"
        f"Subject: {subject or '?'}\n\n"
        f"Read the Statement of Profit & Loss from the {where}."
    )


def extract_via_vision(
    images: list[bytes],
    *,
    symbol: str | None = None,
    subject: str | None = None,
    broadcast_dt: str | None = None,
    client: LLMClient | None = None,
) -> dict | None:
    """Read the P&L from rendered page images via gpt-4o vision."""
    if not images:
        return None
    client = client or _get_client()
    if client is None:
        return None

    content: list[dict] = [{
        "type": "text",
        "text": _context_text(symbol, subject, broadcast_dt, image=True),
    }]
    for png in images:
        b64 = base64.b64encode(png).decode("ascii")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        })

    try:
        result = client.chat_completion(
            messages=[
                {"role": "system", "content": VISION_PROMPT},
                {"role": "user", "content": content},
            ],
            response_format={"type": "json_object"},
            max_tokens=2000,
            temperature=0.0,
        )
    except DailyCapExceeded as e:
        log.warning("vision_capped", error=str(e))
        return None

    if not result.success or not result.parsed_json:
        log.warning("vision_failed", error=result.error)
        return None
    return _to_result(result.parsed_json, result.cost_usd)


def extract_via_text(
    text: str,
    *,
    symbol: str | None = None,
    subject: str | None = None,
    broadcast_dt: str | None = None,
    client: LLMClient | None = None,
) -> dict | None:
    """Fallback: read the P&L from extracted text via gpt-4o (no images)."""
    if not text or not text.strip():
        return None
    client = client or _get_client()
    if client is None:
        return None

    user_msg = (
        _context_text(symbol, subject, broadcast_dt, image=False)
        + f"\n\nPDF text:\n---\n{text}\n---"
    )

    try:
        result = client.chat_completion(
            messages=[
                {"role": "system", "content": TEXT_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            max_tokens=2000,
            temperature=0.0,
        )
    except DailyCapExceeded as e:
        log.warning("text_llm_capped", error=str(e))
        return None

    if not result.success or not result.parsed_json:
        log.warning("text_llm_failed", error=result.error)
        return None
    return _to_result(result.parsed_json, result.cost_usd)
