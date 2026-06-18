"""Parse an NSE shareholding-pattern (SHP) XBRL into the institutional ownership
split. Categories are encoded as context IDs; the value element is
`ShareholdingAsAPercentageOfTotalNumberOfShares` (a fraction → ×100 = %).

Clean aggregate contexts (verified MTARTECH 2026-03, reconciles with the master +
the cause engine's 'MFs >20%' note):
  ShareholdingOfPromoterAndPromoterGroup → promoter
  PublicShareholding                     → public
  InstitutionsForeign                    → FII (FPI cat1+cat2)
  InstitutionsDomestic                   → DII
  MutualFundsOrUTI                        → MF (subset of DII)
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

_PCT_ELEM = "ShareholdingAsAPercentageOfTotalNumberOfShares"
_MAP = {
    "ShareholdingOfPromoterAndPromoterGroup": "promoter_pct",
    "PublicShareholding": "public_pct",
    "InstitutionsForeign": "fii_pct",
    "InstitutionsDomestic": "dii_pct",
    "MutualFundsOrUTI": "mf_pct",
}


def parse_shp(xbrl_text: str) -> dict | None:
    """{promoter_pct, public_pct, fii_pct, dii_pct, mf_pct} in % (0-100). None on
    parse failure / nothing found."""
    try:
        root = ET.fromstring(xbrl_text)
    except ET.ParseError:
        return None
    out: dict[str, float] = {}
    for el in root.iter():
        if el.tag.split("}")[-1] != _PCT_ELEM:
            continue
        cref = el.get("contextRef")
        if not cref or el.text is None:
            continue
        ctx = cref.replace("_ContextI", "")
        key = _MAP.get(ctx)
        if key is None or key in out:          # exact aggregate context, first wins
            continue
        try:
            out[key] = round(float(el.text) * 100.0, 2)
        except ValueError:
            continue
    return out or None
