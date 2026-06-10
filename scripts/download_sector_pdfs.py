"""Download recent quarterly-result PDFs from NSE, 5-6 per sector, into
tests/fundamentals/fixtures/result_pdfs/<sector>/.

Sector regression fixtures for the per-sector result engine (SECTOR_RESULT_
PLAYBOOK.md). Uses the same SessionManager + download_pdf the collectors use, so
it inherits NSE session warm-up, rate limiting and PDF validation. Idempotent:
skips a symbol whose PDF is already on disk.

    python scripts/download_sector_pdfs.py
    python scripts/download_sector_pdfs.py --from 01-04-2025 --to 31-10-2025

The result PDF is an *announcement attachment* (the statutory P&L), located via
/api/corporate-announcements?symbol=...&from_date=...&to_date=...; the financial-
results API serves no usable PDF link (resultDetailedDataLink is empty).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import re
import time
from pathlib import Path

from nse_data.parsers.pdf_downloader import download_pdf
from nse_data.session.manager import SessionManager

# Curated large-cap leaders per sector (the symbols whose P&L the engine reads).
# 8 sectors x 6 = 48 target filings. Symbols are NSE tickers.
SECTORS: dict[str, list[str]] = {
    "bfsi":     ["HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK", "BAJFINANCE"],
    "energy":   ["RELIANCE", "ONGC", "NTPC", "POWERGRID", "BPCL", "GAIL"],
    "it":       ["TCS", "INFY", "WIPRO", "HCLTECH", "TECHM", "PERSISTENT"],
    "fmcg":     ["HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "DABUR", "TATACONSUM"],
    "auto":     ["MARUTI", "TVSMOTOR", "M&M", "BAJAJ-AUTO", "EICHERMOT", "HEROMOTOCO"],
    "pharma":   ["SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "LUPIN", "AUROPHARMA"],
    "metals":   ["TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL", "JINDALSTEL", "NMDC"],
    "capgoods": ["LT", "BEL", "SIEMENS", "ABB", "BHEL", "CUMMINSIND"],
    "realty":   ["DLF", "GODREJPROP", "OBEROIRLTY", "PRESTIGE", "PHOENIXLTD", "BRIGADE"],
}

OUT_ROOT = Path("tests/fundamentals/fixtures/result_pdfs")
ANN_REFERER = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"

# A result announcement: subject says financial-result / board-outcome, AND the
# attachment looks like the financials (not a dividend notice / presentation).
_SUBJECT_RE = re.compile(
    r"financial result|quarterly result|audited financial|unaudited financial|"
    r"outcome of board",
    re.I,
)
_FILE_RE = re.compile(r"financ|result", re.I)


def _parse_dt(s: str) -> _dt.datetime:
    """NSE 'DD-MMM-YYYY HH:MM:SS' → datetime; epoch on failure (sorts last)."""
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M"):
        try:
            return _dt.datetime.strptime(s.strip()[:20], fmt)
        except (ValueError, TypeError):
            continue
    return _dt.datetime.min


def _safe(sym: str) -> str:
    """Filesystem- & glob-safe symbol prefix (M&M → MANDM, BAJAJ-AUTO unchanged)."""
    return sym.replace("&", "AND").replace("/", "-").replace(" ", "")


def _pick_result(rows: list[dict]) -> dict | None:
    """The best result-PDF announcement: a financials-looking attachment,
    chronologically most recent; falls back to any result-subject row + attachment."""
    cands = []
    for r in rows or []:
        subj = (r.get("desc") or r.get("sm_desc") or "").strip()
        url = (r.get("attchmntFile") or "").strip()
        if not url or not _SUBJECT_RE.search(subj):
            continue
        when = (r.get("an_dt") or r.get("sort_date") or "").strip()
        score = 1 if _FILE_RE.search(url.rsplit("/", 1)[-1]) else 0
        cands.append((score, _parse_dt(when), when, url, subj))
    if not cands:
        return None
    # prefer a financials-looking attachment, then the chronologically latest.
    cands.sort(key=lambda c: (c[0], c[1]), reverse=True)
    _, _, when, url, subj = cands[0]
    return {"url": url, "subject": subj, "when": when}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="from_date", default="01-04-2025",
                    help="DD-MM-YYYY window start (default 01-04-2025)")
    ap.add_argument("--to", dest="to_date", default="31-10-2025",
                    help="DD-MM-YYYY window end (default 31-10-2025)")
    args = ap.parse_args()

    s = SessionManager()
    ok = skipped = failed = 0
    try:
        for sector, symbols in SECTORS.items():
            dest = OUT_ROOT / sector
            dest.mkdir(parents=True, exist_ok=True)
            for sym in symbols:
                safe = _safe(sym)
                # idempotent: files are saved SYMBOL-prefixed, so this is reliable.
                if any(dest.glob(f"{safe}__*")):
                    print(f"  · {sector}/{sym}: already present, skip")
                    skipped += 1
                    continue
                try:
                    out = s.get_json(
                        "announcements", "/api/corporate-announcements", referer=ANN_REFERER,
                        params={"index": "equities", "symbol": sym,
                                "from_date": args.from_date, "to_date": args.to_date},
                    )
                except Exception as e:  # noqa: BLE001
                    print(f"  ✗ {sector}/{sym}: announcements query failed: {e!r}")
                    failed += 1
                    continue
                rows = out.get("data") if isinstance(out, dict) else out
                pick = _pick_result(rows or [])
                if pick is None:
                    print(f"  ✗ {sector}/{sym}: no result PDF in window")
                    failed += 1
                    continue
                res = download_pdf(s, pick["url"], endpoint_name="sector_pdf_download")
                if not res.success or res.data is None:
                    print(f"  ✗ {sector}/{sym}: download failed: {res.error}")
                    failed += 1
                    continue
                fname = f"{safe}__{pick['url'].rsplit('/', 1)[-1]}"
                (dest / fname).write_bytes(res.data)
                print(f"  ✓ {sector}/{sym}: {fname} ({res.size_bytes/1024:.0f} KB) — {pick['when']}")
                ok += 1
                time.sleep(1.0)   # be polite between companies
    finally:
        s.close()

    print(f"\nDONE — {ok} downloaded, {skipped} skipped, {failed} failed "
          f"(target {sum(len(v) for v in SECTORS.values())}).")


if __name__ == "__main__":
    main()
