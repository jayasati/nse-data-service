"""Order-amount extraction from order-win announcements → ₹ crore, the foundation
of the Catalyst engine's **Order Impact Ratio** (order value / revenue, / mcap —
"don't treat all orders equally": ₹700cr for a ₹500cr company ≫ ₹50cr for a
₹10,000cr company).

Regex-first and conservative, mirroring the narrative extractor's "never guess"
rule: an amount is returned only when it sits near ORDER context. Two confidence
tiers — ``high`` (amount in an explicit order/contract/award clause) and ``low``
(largest amount in a text already classified as an order-win, no explicit clause).
Handles the phrasings + units that order-win FILINGS actually use (the narrative
patterns only caught "order inflow of ₹X crore" from earnings commentary):
crore/cr, lakh/lac, million/mn, billion/bn; ₹/Rs/INR prefix optional.

    from nse_data.parsers.order_amount import extract_order_value_cr
    extract_order_value_cr("Bagged a work order worth Rs 1,250 crore from NHAI")
        -> (1250.0, 'high', 'order worth Rs 1,250 crore')
"""
from __future__ import annotations

import re

_RS = r"(?:₹|rs\.?|inr)\s*"
_NUM = r"(\d[\d,]*(?:\.\d+)?)"
# multiplier from the matched unit → ₹ crore (1 cr = 10 mn = 100 lakh)
_UNIT_CR = {
    "crore": 1.0, "crores": 1.0, "cr": 1.0,
    "lakh": 0.01, "lakhs": 0.01, "lac": 0.01, "lacs": 0.01,
    "million": 0.1, "mn": 0.1,
    "billion": 100.0, "bn": 100.0,
}
# Indian units (crore/lakh) are unambiguously ₹ → prefix optional. International
# units (million/billion) are usually USD/EUR in Indian filings → REQUIRE a ₹/Rs
# prefix, else "USD 50 million" would be mis-read as ₹.
_AMT_IN = rf"{_RS}?{_NUM}\s*(crores?|cr\.?|lakhs?|lacs?)\b"
_AMT_INTL = rf"{_RS}{_NUM}\s*(million|mn|billion|bn)\b"
_AMTS = (_AMT_IN, _AMT_INTL)            # each: group(1)=number, group(2)=unit

# Words that establish ORDER context (so a stray "Rs 2 lakh fee" isn't read as the
# order value). 'won/bagged/awarded/secured' are order-win verbs; 'aggregating'
# catches the total of several orders in one filing.
_ORDER_CTX = (r"(?:orders?|contracts?|work\s+order|letter\s+of\s+award|\bLOA\b|"
              r"purchase\s+order|tender|project|awarded|bagg?ed|secured|won|"
              r"received|aggregat\w+)")
_CTX_RX = tuple((re.compile(rf"{_ORDER_CTX}[^.\n]{{0,80}}?{a}", re.I),
                 re.compile(rf"{a}[^.\n]{{0,40}}?{_ORDER_CTX}", re.I)) for a in _AMTS)
_ANY_RX = tuple(re.compile(a, re.I) for a in _AMTS)
# cumulative figures that are NOT the new order value — exclude from the high tier
_BOOK = re.compile(r"order\s+book|backlog|outstanding\s+order|order\s+pipeline|"
                   r"total\s+order|order\s+inflow\s+for|year\s+to\s+date|\bYTD\b", re.I)


def _to_cr(num_s: str, unit_s: str) -> float | None:
    try:
        v = float(num_s.replace(",", ""))
    except ValueError:
        return None
    mult = _UNIT_CR.get(unit_s.lower().rstrip("."))
    return round(v * mult, 2) if mult else None


def extract_order_value_cr(text: str | None) -> tuple[float, str, str] | None:
    """Order value in ₹ crore from order-win text → (value_cr, confidence, snippet),
    or None. confidence ∈ {'high','low'}: 'high' = amount in an order/contract/award
    clause; 'low' = largest amount in the text (caller should already have confirmed
    the filing is an order win, e.g. via is_order_win_subject). Picks the LARGEST
    qualifying amount — the headline order value is typically the biggest figure."""
    if not text:
        return None
    cands: list[tuple[float, str]] = []
    for ctx_then, amt_then in _CTX_RX:
        for rx in (ctx_then, amt_then):
            for m in rx.finditer(text):
                if _BOOK.search(m.group(0)):      # order-book / backlog, not the new order
                    continue
                cr = _to_cr(m.group(1), m.group(2))
                if cr:
                    cands.append((cr, m.group(0)))
    conf = "high"
    if not cands:
        conf = "low"
        for rx in _ANY_RX:
            for m in rx.finditer(text):
                cr = _to_cr(m.group(1), m.group(2))
                if cr:
                    cands.append((cr, m.group(0)))
    if not cands:
        return None
    cr, snip = max(cands)
    return cr, conf, re.sub(r"\s+", " ", snip).strip()[:120]
