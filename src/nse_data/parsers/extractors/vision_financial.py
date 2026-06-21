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
# per-share and ratio fields pass through untouched.
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
    # Non-bank operating-EBITDA inputs (P3): EBITDA = pbt + finance_cost +
    # depreciation − other_income. NULL for banks.
    "depreciation": "depreciation_cr",
    "finance_cost": "finance_cost_cr",
    # --- BFSI (banks/NBFCs): the operating lines a generic P&L read discards.
    # Requested only when the caller flags the symbol as BFSI (S1). ---
    "interest_earned": "interest_earned_cr",
    "interest_expended": "interest_expended_cr",
    "net_interest_income": "net_interest_income_cr",
    "operating_profit": "operating_profit_cr",          # pre-provision op profit (PPOP)
    "provisions": "provisions_cr",                       # provisions & contingencies
    "profit_on_sale_of_investments": "profit_on_sale_of_investments_cr",  # treasury line
    "gross_npa_pct": "gross_npa_pct",
    "net_npa_pct": "net_npa_pct",
    "slippages": "slippages_cr",
}
_RUPEE_FIELDS = {"eps_basic", "eps_diluted"}
# Ratios/per-share values that are NOT scaled by the source-unit factor.
_UNSCALED_FIELDS = _RUPEE_FIELDS | {"gross_npa_pct", "net_npa_pct"}

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
    "cfo": <number or null>,
    "depreciation": <non-bank, number or null>, "finance_cost": <non-bank, number or null>,
    "interest_earned": <BFSI only, number or null>,
    "interest_expended": <BFSI only, number or null>,
    "net_interest_income": <BFSI only, number or null>,
    "operating_profit": <BFSI only, number or null>,
    "provisions": <BFSI only, number or null>,
    "profit_on_sale_of_investments": <BFSI only, number or null>,
    "gross_npa_pct": <BFSI only, number or null>, "net_npa_pct": <BFSI only, number or null>,
    "slippages": <BFSI only, number or null>,
    "prev_quarter": {"revenue": <number or null>, "pat": <number or null>,
      "net_interest_income": <BFSI or null>, "operating_profit": <BFSI or null>,
      "provisions": <BFSI or null>, "other_income": <number or null>},
    "year_ago_quarter": {"revenue": <number or null>, "pat": <number or null>,
      "net_interest_income": <BFSI or null>, "operating_profit": <BFSI or null>,
      "provisions": <BFSI or null>, "other_income": <number or null>}
  },
  "consolidated": { <same fields incl. prev_quarter / year_ago_quarter, or null if absent> },
  "units_in_source_pdf": "<'INR million' | 'INR lakh' | 'INR crore' | 'INR thousand' | 'INR'>",
  "period_ending": "<YYYY-MM-DD of the quarter you read, or null>",
  "table_found": <true if you found the P&L>,
  "notes": "<brief observations>"
}"""

_COMMON_RULES = """Field definitions & rules:
- "revenue" = Revenue from operations (the operating-revenue subtotal). If it is
  split (Sale of products / services / other operating revenue), SUM those lines.
  Do NOT use "Total income" as revenue.
- NON-BANK operating lines (fill for a normal company; leave null for banks/NBFCs):
  * depreciation = the "Depreciation, depletion, amortisation (and impairment)"
    expense line within Expenses.
  * finance_cost = the "Finance costs" expense line within Expenses.
  These let us compute operating EBITDA. Read them from the Expenses section; do
  NOT confuse finance_cost with a bank's Interest Expended.
- BANKS / NBFCs (BFSI) have a DIFFERENT P&L layout — map it carefully, do NOT
  treat it like a normal company:
  * revenue = "Interest Earned" (item 1, the a+b+c+d subtotal). NEVER use Total
    Income as revenue here.
  * other_income = "Other Income" (item 2).
  * total_income = "Total Income (1+2)".
  * total_expenses = Total Income MINUS Profit-before-tax — i.e. EVERYTHING between
    them: Interest Expended + Operating Expenses + Provisions & Contingencies. Do
    NOT use "Total Expenditure (excluding provisions)" alone; it omits provisions,
    so total_income − total_expenses would not equal PBT.
  * pbt = "Profit/(Loss) before Tax" (the line AFTER provisions, not Operating
    Profit).
  * pat = "Net Profit for the period".
  * eps_basic/eps_diluted = the "Basic/Diluted EPS" line in the ratios section.
  * BFSI OPERATING LINES (fill these ONLY for a bank/NBFC; leave null otherwise):
    - interest_earned = item 1 (same value as revenue here).
    - interest_expended = "Interest Expended" (item 3).
    - net_interest_income = Interest Earned − Interest Expended. If the filing
      prints "Net Interest Income" directly, use that; else leave null (it is
      derived later).
    - operating_profit = "Operating Profit (before Provisions & Contingencies)"
      — the PRE-PROVISION profit line. This is NOT pbt; pbt is after provisions.
    - provisions = "Provisions and Contingencies" (the total provisions line,
      excluding tax). Report POSITIVE.
    - profit_on_sale_of_investments = the treasury line inside Other Income
      ("Profit/(Loss) on sale of investments" / "on revaluation of investments").
      A LOSS is NEGATIVE (parentheses). null if not separately printed.
    - gross_npa_pct / net_npa_pct = "% of Gross/Net NPA" from the asset-quality
      block (a percentage like 1.49, NOT the rupee NPA amount). null if absent.
    - slippages = fresh slippages for the quarter, if disclosed (rupee amount).
      null if not printed.
  * BFSI COLUMN LAYOUT (CRITICAL — this is where reads go wrong): a bank result
    table prints, left to right: Particulars, then the FIVE STANDALONE columns
    [current quarter, preceding quarter, year-ago quarter, current FULL-YEAR,
    previous FULL-YEAR], then the SAME FIVE CONSOLIDATED columns. Therefore for
    the STANDALONE block: current quarter = 1st data column, prev_quarter = 2nd,
    year_ago_quarter = 3rd. NEVER take a standalone value or its comparatives
    from a full-year column (4th/5th) or any consolidated column (6th–10th). For
    the CONSOLIDATED block use ONLY the consolidated columns, same current/
    preceding/year-ago sub-order. Sanity check: a quarter figure is ~1/4 of the
    full-year figure on the same row — if your "quarter" value is close to the
    annual one, you read a full-year column; re-read.
- COLUMN: read the MOST RECENT QUARTER. Identify it by the column whose header
  date is the latest QUARTER-end (e.g. 31-03-2026) — NOT a full-year/"year ended"
  column that may share that date, and NOT the year-ago quarter (31-03-2025).
  Ignore any "Note"/"Notes" reference column (small integers like 2, 13).
- COMPARATIVES (for growth): NSE result tables also print the preceding quarter
  and the year-ago quarter as further columns. Fill prev_quarter / year_ago_quarter
  with the SAME-ROW values from those columns: always Revenue-from-operations and
  PAT, and for a BANK/NBFC also net_interest_income, operating_profit, provisions
  and other_income (same rows you read for the current quarter). prev_quarter =
  the IMMEDIATELY PRECEDING quarter (e.g. 31-12-2025), year_ago_quarter = the SAME
  quarter one year earlier (e.g. 31-03-2025). Do NOT use full-year columns here.
  Leave a value null if that comparative column isn't printed.
- SCOPE: standalone and consolidated are SEPARATE statements, each under its OWN
  heading. Match each block to the statement whose heading matches: the STANDALONE
  block comes from the statement headed "Standalone" (or with no "Consolidated"
  qualifier); the CONSOLIDATED block comes from the statement headed "Consolidated".
  Most filings print BOTH — find and extract BOTH; their numbers DIFFER (consolidated
  is usually larger, it includes subsidiaries). If your standalone and consolidated
  come out IDENTICAL, you mis-read — they are two different tables, go back and find
  the standalone one. If only ONE statement is printed, put it in "standalone" and set
  "consolidated" to null. NEVER fabricate or copy one scope into the other.
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
    "Extract the canonical fields below from the MOST RECENT QUARTER (banks/NBFCs: also the BFSI operating lines), for BOTH "
    "the standalone and consolidated statements when both are present.\n\n"
    "Return ONLY a JSON object:\n" + _SCHEMA_BLOCK + "\n\n" + _COMMON_RULES
)

TEXT_PROMPT = (
    "You are a financial data extractor for Indian quarterly result PDFs filed "
    "with NSE. You receive TEXT extracted from the PDF. The table is flattened "
    "into reading order: each row label is followed by its column values, one per "
    "line. OCR errors are common — tolerate near-miss labels.\n\n"
    "Extract the canonical fields below from the MOST RECENT QUARTER (banks/NBFCs: also the BFSI operating lines), for BOTH "
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
        out[canon] = num if canon in _UNSCALED_FIELDS else num * factor
    # Derive NII when the model gave the components but not the subtotal: for a
    # bank, revenue == Interest Earned, so NII = Interest Earned − Interest Expended.
    if "net_interest_income_cr" not in out:
        ie, iex = out.get("revenue_cr"), out.get("interest_expended_cr")
        if ie is not None and iex is not None:
            out["net_interest_income_cr"] = round(ie - iex, 2)
    return out


def _pct(current, prior) -> float | None:
    """Sign-aware percent change; None if not computable (incl. prior == 0)."""
    c, p = _coerce_number(current), _coerce_number(prior)
    if c is None or p is None or p == 0:
        return None
    return round((c - p) / abs(p) * 100.0, 2)


def _plausible_comparative(cur, comp, *, lo: float = 0.35, hi: float = 2.5):
    """Return ``comp`` only if it's a plausible same-line comparative of ``cur``.

    A bank's quarter column is ~1/4 of its full-year column, so a comparative
    that's >2.5× (or <0.35×) the current quarter is almost certainly a wrong
    column — a full-year or consolidated value the model grabbed by mistake.
    Nulling it makes the growth uncomputable rather than wildly wrong."""
    c, p = _coerce_number(cur), _coerce_number(comp)
    if c is None or p is None or c == 0:
        return comp
    return comp if lo <= abs(p / c) <= hi else None


def _correct_block_identities(block) -> None:
    """In-place deterministic fixes on a raw block before mapping/growth (BFSI).

    Two identities hold exactly in these statements and are cheaper/safer to
    enforce than to trust the model on the dense bank table:
      * other_income = total_income − revenue
      * net_interest_income = interest_earned − interest_expended  (banks)
    We override the model's value only when it's missing or materially off, which
    is exactly the SBI failure mode (NII / other-income read from a wrong column)."""
    if not isinstance(block, dict):
        return
    rev = _coerce_number(block.get("revenue"))
    ti = _coerce_number(block.get("total_income"))
    if rev is not None and ti is not None:
        comp = round(ti - rev, 2)
        oi = _coerce_number(block.get("other_income"))
        if oi is None or abs(oi - comp) > max(0.05 * abs(ti), 1.0):
            block["other_income"] = comp
    iearn = _coerce_number(block.get("interest_earned"))
    if iearn is None:
        iearn = rev   # for a bank, Interest Earned == revenue
    iex = _coerce_number(block.get("interest_expended"))
    if iearn is not None and iex is not None:
        comp = round(iearn - iex, 2)
        nii = _coerce_number(block.get("net_interest_income"))
        if nii is None or abs(nii - comp) > 0.02 * abs(comp or 1.0):
            block["net_interest_income"] = comp


def _growth_from_block(block) -> dict:
    """YoY/QoQ revenue & PAT growth from the comparative columns IN THE SAME PDF.

    Growth is a ratio, so the source unit cancels — no scaling needed. Each
    comparative is sanity-checked against the current quarter so a wrong-column
    (full-year/consolidated) pick yields no growth rather than a bogus one."""
    if not block or not isinstance(block, dict):
        return {}
    pq = block.get("prev_quarter") or {}
    ya = block.get("year_ago_quarter") or {}

    def cmp_q(field):   # plausible preceding-quarter comparative for `field`
        return _plausible_comparative(block.get(field), pq.get(field))

    def cmp_y(field):   # plausible year-ago comparative for `field`
        return _plausible_comparative(block.get(field), ya.get(field))
    out = {
        "qoq_revenue_pct": _pct(block.get("revenue"), cmp_q("revenue")),
        "qoq_pat_pct": _pct(block.get("pat"), cmp_q("pat")),
        "yoy_revenue_pct": _pct(block.get("revenue"), cmp_y("revenue")),
        "yoy_pat_pct": _pct(block.get("pat"), cmp_y("pat")),
        # BFSI operating lines — the divergence inputs (S3). Null for non-banks.
        "qoq_nii_pct": _pct(block.get("net_interest_income"), cmp_q("net_interest_income")),
        "yoy_nii_pct": _pct(block.get("net_interest_income"), cmp_y("net_interest_income")),
        "qoq_ppop_pct": _pct(block.get("operating_profit"), cmp_q("operating_profit")),
        "yoy_ppop_pct": _pct(block.get("operating_profit"), cmp_y("operating_profit")),
        "yoy_provisions_pct": _pct(block.get("provisions"), cmp_y("provisions")),
        "qoq_other_income_pct": _pct(block.get("other_income"), cmp_q("other_income")),
        "yoy_other_income_pct": _pct(block.get("other_income"), cmp_y("other_income")),
    }
    return {k: v for k, v in out.items() if v is not None}


def _to_result(data: dict, cost_usd: float) -> dict:
    """Shared post-processing of a parsed model JSON into the return shape."""
    phrase = data.get("units_in_source_pdf")
    factor = units_factor(phrase)
    # Deterministic identity fixes on the raw blocks BEFORE mapping + growth, so
    # both consume the corrected values (fixes wrong-column NII/other-income).
    _correct_block_identities(data.get("standalone"))
    _correct_block_identities(data.get("consolidated"))
    standalone = _map_block(data.get("standalone"), factor)
    consolidated = _map_block(data.get("consolidated"), factor)
    return {
        "fields": standalone,
        "consolidated": consolidated,
        "units_phrase": phrase,
        "period_ending": data.get("period_ending"),
        "cost_usd": cost_usd,
        "table_found": bool(data.get("table_found", bool(standalone or consolidated))),
        # YoY/QoQ computed from the PDF's own comparative columns (no history needed)
        "growth": _growth_from_block(data.get("standalone")),
        "growth_consolidated": _growth_from_block(data.get("consolidated")),
    }


def _context_text(symbol, subject, broadcast_dt, *, image: bool, is_bfsi: bool = False) -> str:
    where = "page images" if image else "PDF text"
    bfsi = (
        "\nThis is a BANK / NBFC (BFSI): read the bank P&L layout and ALSO fill the "
        "BFSI operating lines (interest_expended, net_interest_income, "
        "operating_profit, provisions, profit_on_sale_of_investments, "
        "gross_npa_pct, net_npa_pct, slippages) and their comparatives."
        if is_bfsi else ""
    )
    return (
        f"Company: {symbol or '?'}\n"
        f"Filing date: {broadcast_dt or '?'}\n"
        f"Subject: {subject or '?'}\n\n"
        f"Read the Statement of Profit & Loss from the {where}.{bfsi}"
    )


def extract_via_vision(
    images: list[bytes],
    *,
    symbol: str | None = None,
    subject: str | None = None,
    broadcast_dt: str | None = None,
    is_bfsi: bool = False,
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
        "text": _context_text(symbol, subject, broadcast_dt, image=True, is_bfsi=is_bfsi),
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
            timeout=120.0,        # multi-page image reads need more than the 60s default
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
    is_bfsi: bool = False,
    client: LLMClient | None = None,
) -> dict | None:
    """Fallback: read the P&L from extracted text via gpt-4o (no images)."""
    if not text or not text.strip():
        return None
    client = client or _get_client()
    if client is None:
        return None

    user_msg = (
        _context_text(symbol, subject, broadcast_dt, image=False, is_bfsi=is_bfsi)
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
