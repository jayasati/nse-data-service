"""Parse an NSE shareholding-pattern (SHP) XBRL into the institutional ownership
split. The value element is `ShareholdingAsAPercentageOfTotalNumberOfShares`,
repeated once per category; the category is identified by the context's
`explicitMember` on the `CategoryOfShareholdersAxis` dimension.

We key on that MEMBER, not the context-ID string, because the string is not
stable across filing variants (verified 2026-06-18 across RELIANCE + HDFCBANK):
  - current filings:   ...Promoter..._ContextI
  - pre-2025 filings:  ...PromoterI  (bare 'I' suffix, no '_Context')
  - bank/widely-held:  aggregate contexts renamed again
The dimensional member IS stable; we normalise it (drop ':' prefix + trailing
'Member', lowercase) so spelling drift like MutualFundsOrUti/UTI collapses.

Aggregate members we keep (sub-category members never collide with these):
  shareholdingofpromoterandpromotergroup → promoter
  publicshareholding                     → public
  institutionsforeign                    → FII (FPI cat1+cat2)
  institutionsdomestic                   → DII
  mutualfundsoruti                        → MF (subset of DII)

SCALE IS NOT CONSISTENT either: some filings encode the value as a fraction
(0.5007) and some as an already-formatted percent (50.07). We detect the scale
per document from the invariant that promoter + public ≈ 100% of shares: raw sum
≈ 1 → fraction (×100); ≈ 100 → already percent (×1). Getting this wrong stores
everything 100× off, so don't assume.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

_PCT_ELEM = "ShareholdingAsAPercentageOfTotalNumberOfShares"
_AXIS = "CategoryOfShareholders"   # ...:CategoryOfShareholdersAxis
_MAP = {
    "shareholdingofpromoterandpromotergroup": "promoter_pct",
    "publicshareholding": "public_pct",
    "institutionsforeign": "fii_pct",
    "institutionsdomestic": "dii_pct",
    "mutualfundsoruti": "mf_pct",
}


def _norm_member(text: str) -> str:
    """':'-qualified member → bare lowercase category, trailing 'Member' dropped."""
    name = text.split(":")[-1].strip()
    if name.endswith("Member"):
        name = name[:-len("Member")]
    return name.lower()


def parse_shp(xbrl_text: str) -> dict | None:
    """{promoter_pct, public_pct, fii_pct, dii_pct, mf_pct} in % (0-100). None on
    parse failure / nothing found."""
    try:
        root = ET.fromstring(xbrl_text)
    except ET.ParseError:
        return None

    # context id -> our field key, via the CategoryOfShareholders explicitMember.
    ctx_key: dict[str, str] = {}
    for ctx in root.iter():
        if ctx.tag.split("}")[-1] != "context":
            continue
        cid = ctx.get("id")
        if not cid:
            continue
        for m in ctx.iter():
            if m.tag.split("}")[-1] != "explicitMember":
                continue
            if _AXIS not in (m.get("dimension") or "") or not m.text:
                continue
            key = _MAP.get(_norm_member(m.text))
            if key:
                ctx_key[cid] = key

    raw: dict[str, float] = {}
    for el in root.iter():
        if el.tag.split("}")[-1] != _PCT_ELEM:
            continue
        key = ctx_key.get(el.get("contextRef") or "")
        if key is None or key in raw or el.text is None:   # aggregate, first wins
            continue
        try:
            raw[key] = float(el.text)
        except ValueError:
            continue
    if not raw:
        return None
    return {k: round(v * _scale(raw), 2) for k, v in raw.items()}


def _scale(raw: dict[str, float]) -> float:
    """100 if the values are fractions (0-1), 1 if already percentages (0-100).

    Anchor on promoter+public (≈ all shares); fall back to the largest single
    value. A fraction anchor is ≈1 and a percent anchor is ≈100, so a 1.5 cutoff
    separates them with wide margin (the only way to land near 1.5 is a degenerate
    filing, where either scaling is equally meaningless)."""
    if "promoter_pct" in raw and "public_pct" in raw:
        anchor = raw["promoter_pct"] + raw["public_pct"]
    else:
        anchor = max(raw.values())
    return 100.0 if anchor <= 1.5 else 1.0
