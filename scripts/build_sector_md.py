"""Fetch NSE per-stock sector metadata for the focus universe and emit a
sector-grouped markdown reference (SECTOR_UNIVERSE.md).

The doc divides all top-1000 symbols by NSE's macro sector (Financial Services,
Industrials, Healthcare, ...) and, per sector, documents the result-reading
signal focus + which engine sector-class (fundamentals/sectors/) handles it.
It is the human-readable companion to config/sector_mapping.yaml: a reviewer can
see, for any quarter, which KPIs the sector-wise analysis should weigh.

Source of truth is raw_quote_metadata (NSE /api/quote-equity per symbol). This
fills the gaps first (only symbols missing metadata, unless --refresh), then
writes the MD.

    PYTHONPATH=src .venv/bin/python -u scripts/build_sector_md.py
    .venv/bin/python -u scripts/build_sector_md.py --no-fetch   # MD only
    .venv/bin/python -u scripts/build_sector_md.py --refresh    # re-fetch all
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# NSE macro sector -> (engine sector-class, built?, signal focus bullets).
# "built" = the engine has a validated per-sector rule; otherwise it falls back
# to the generic operating-quality read (revenue/EBITDA, with other-income/tax
# props guarded). See SECTOR_RESULT_PLAYBOOK.md and fundamentals/sectors/.
SECTOR_SIGNALS: dict[str, dict] = {
    "Financial Services": {
        "engine": "bfsi (lenders) / generic (AMC, broker, exchange)", "built": True,
        "signals": [
            "NII & NIM (margin trend), PPOP — the pre-provision earning power",
            "Provisions & credit cost, GNPA/NNPA, slippages — asset quality",
            "Loan & deposit growth, CASA; NBFC: AUM growth + credit cost",
            "Lender guard: only banks/NBFC/HFC use the BFSI rule; fee-income "
            "financials (AMC/exchange/broker) read as generic",
        ],
    },
    "Information Technology": {
        "engine": "it", "built": True,
        "signals": [
            "Constant-currency revenue growth & sequential momentum",
            "Deal TCV / net new bookings, book-to-bill",
            "EBIT margin trend, guidance, attrition",
        ],
    },
    "Energy": {
        "engine": "energy", "built": True,
        "signals": [
            "EBITDA operating line — GRM / marketing & refining margin, realizations",
            "Other-income prop & tax prop GUARDS (ONGC false-LONG fix)",
            "Volumes / throughput, capex cycle",
        ],
    },
    "Utilities": {
        "engine": "energy / generic", "built": False,
        "signals": [
            "Regulated RoE, PLF / plant load factor, capacity additions",
            "Operating EBITDA ex other-income; tariff & receivables",
        ],
    },
    "Healthcare": {
        "engine": "pharma", "built": False,
        "signals": [
            "US / regulated-market sales, base business vs one-offs",
            "Gross margin, R&D spend, USFDA status (narrative)",
            "Operating quality read (generic fallback until pharma rule is built)",
        ],
    },
    "Fast Moving Consumer Goods": {
        "engine": "fmcg", "built": False,
        "signals": [
            "Underlying volume growth (UVG) vs price-led growth",
            "Gross & EBITDA margin (input-cost cycle), A&P spend",
        ],
    },
    "Consumer Discretionary": {
        "engine": "auto / realty / generic", "built": False,
        "signals": [
            "Auto: volume growth + EBITDA margin + realization/ASP",
            "Realty: pre-sales / bookings, collections, net debt",
            "Retail & durables: SSSG, store adds, gross margin",
        ],
    },
    "Industrials": {
        "engine": "capgoods / generic", "built": False,
        "signals": [
            "Order inflow & closing order book, book-to-bill",
            "Execution revenue, EBITDA margin, working-capital cycle",
        ],
    },
    "Commodities": {
        "engine": "metals / generic", "built": False,
        "signals": [
            "Realization & EBITDA per tonne, sales volumes",
            "Input-cost spreads (coking coal, ore); cement: realization/bag",
        ],
    },
    "Telecommunication": {
        "engine": "generic", "built": False,
        "signals": [
            "ARPU trend, subscriber net adds, churn",
            "EBITDA margin, capex / 5G rollout, net debt",
        ],
    },
    "Services": {
        "engine": "generic", "built": False,
        "signals": [
            "Segment revenue & EBITDA; read as generic operating quality",
        ],
    },
}
_UNKNOWN = {"engine": "generic", "built": False,
            "signals": ["No NSE macro sector on file — generic operating-quality read."]}


def _fetch_missing(conn, session, syms, refresh: bool) -> int:
    from nse_data.collectors.quote_metadata import QuoteMetadata
    have = {r[0].upper() for r in conn.execute(
        "SELECT symbol FROM raw_quote_metadata WHERE sector IS NOT NULL")}
    todo = syms if refresh else [s for s in syms if s not in have]
    print(f"metadata: {len(have)} on file, fetching {len(todo)}\n", flush=True)
    qm = QuoteMetadata()
    cols = ("symbol", "company_name", "isin", "industry", "sector", "listing_date",
            "face_value", "is_fno", "series", "trading_status", "last_price",
            "pe_ratio", "market_cap_cr", "fetched_at")
    n = 0
    from nse_data.collectors.base import Request
    for i, sym in enumerate(todo, 1):
        time.sleep(0.35)
        try:
            data = session.get_json(
                "quote_equity", "/api/quote-equity", params={"symbol": sym},
                referer=f"https://www.nseindia.com/get-quotes/equity?symbol={sym}")
            rows = qm.normalize(data, Request(
                path_or_url="/api/quote-equity", params={"symbol": sym},
                response_type="json", meta={"symbol": sym}))
        except Exception as e:  # noqa: BLE001
            print(f"[{i}/{len(todo)}] {sym:<14} ERR {e!r}", flush=True)
            continue
        if not rows or not rows[0].get("sector"):
            print(f"[{i}/{len(todo)}] {sym:<14} no-sector", flush=True)
            continue
        r = rows[0]
        conn.execute(
            f"INSERT OR REPLACE INTO raw_quote_metadata ({', '.join(cols)}) "
            f"VALUES ({', '.join(['?']*len(cols))})", [r[c] for c in cols])
        conn.commit()
        n += 1
        if i % 25 == 0 or i == len(todo):
            print(f"[{i}/{len(todo)}] {sym:<14} {r['sector']} / {r['industry']}", flush=True)
    print(f"\nfetched {n} symbols\n", flush=True)
    return n


def _build_md(conn, syms) -> str:
    rows = {r[0].upper(): r for r in conn.execute(
        "SELECT symbol, sector, industry, company_name FROM raw_quote_metadata")}
    by_sector: dict[str, list] = {}
    for s in syms:
        r = rows.get(s)
        sector = (r[1] if r and r[1] else None) or "Unclassified"
        by_sector.setdefault(sector, []).append((s, (r[2] if r else None) or "—"))

    order = sorted(by_sector, key=lambda k: (-len(by_sector[k]), k))
    out: list[str] = []
    out.append("# Sector Universe — top-1000 result-signal map\n")
    out.append("> Generated by `scripts/build_sector_md.py` from NSE per-stock "
               "metadata (`raw_quote_metadata`). Each stock is grouped by NSE's "
               "macro sector; per sector we note the **result-reading signal "
               "focus** and which engine class in `fundamentals/sectors/` handles "
               "it. Companion to `config/sector_mapping.yaml` and "
               "`SECTOR_RESULT_PLAYBOOK.md`. Re-run when the universe changes.\n")
    classified = sum(len(v) for k, v in by_sector.items() if k != "Unclassified")
    out.append(f"**Coverage:** {classified}/{len(syms)} classified across "
               f"{len([k for k in by_sector if k!='Unclassified'])} sectors.\n")

    # summary table
    out.append("## Summary\n")
    out.append("| Sector | Stocks | Engine class | Validated rule |")
    out.append("|---|---:|---|:--:|")
    for sec in order:
        meta = SECTOR_SIGNALS.get(sec, _UNKNOWN)
        out.append(f"| {sec} | {len(by_sector[sec])} | {meta['engine']} | "
                   f"{'✅' if meta['built'] else '⏳ generic'} |")
    out.append("")

    # per-sector detail
    for sec in order:
        meta = SECTOR_SIGNALS.get(sec, _UNKNOWN)
        members = sorted(by_sector[sec])
        out.append(f"## {sec}  ({len(members)})\n")
        out.append(f"- **Engine class:** `{meta['engine']}` "
                   f"({'validated rule' if meta['built'] else 'generic fallback'})")
        out.append("- **Signal focus:**")
        for s in meta["signals"]:
            out.append(f"  - {s}")
        out.append("")
        # group members by NSE industry for readability
        by_ind: dict[str, list[str]] = {}
        for sym, ind in members:
            by_ind.setdefault(ind, []).append(sym)
        for ind in sorted(by_ind):
            out.append(f"- *{ind}* — {', '.join(sorted(by_ind[ind]))}")
        out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--universe", default="config/universe_top1000.txt")
    ap.add_argument("--out", default="SECTOR_UNIVERSE.md")
    ap.add_argument("--no-fetch", action="store_true", help="skip metadata fetch")
    ap.add_argument("--refresh", action="store_true", help="re-fetch all symbols")
    args = ap.parse_args()

    from nse_data.storage.db import open_db
    from nse_data.session.manager import SessionManager

    conn = open_db(args.db)
    syms = [l.strip().upper() for l in Path(args.universe).read_text().splitlines()
            if l.strip() and not l.startswith("#")]

    if not args.no_fetch:
        _fetch_missing(conn, SessionManager(), syms, args.refresh)

    md = _build_md(conn, syms)
    Path(args.out).write_text(md)
    conn.close()
    print(f"wrote {args.out} ({len(md.splitlines())} lines)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
