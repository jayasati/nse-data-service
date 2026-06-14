"""Divide the top-1000 universe by NSE sector and emit SECTOR_UNIVERSE.md.

Source of truth is NSE's own index-constituent CSVs on nsearchives (NIFTY Total
Market — the broadest 750-name index — carries an ``Industry`` column = NSE's
~21 macro-sector taxonomy). These load from any IP (unlike /api/quote-equity,
which NSE 403s), so the doc is reproducible on laptop or server.

Per sector the doc documents the **result-reading signal focus** and which engine
class in ``fundamentals/sectors/`` handles it — the human-readable companion to
``config/sector_mapping.yaml`` used when reading financial extractions.

Coverage: ~666/1000 fall inside NSE Total Market. ETFs/index funds (no results)
are listed separately. Genuine micro-caps outside every NSE index have no public
sector feed and are grouped as Unclassified (generic operating-quality read).

    PYTHONPATH=src .venv/bin/python -u scripts/build_sector_md.py
"""
from __future__ import annotations

import argparse
import csv
import io
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
# NIFTY Total Market (750) is a superset of Nifty 500 / midcap / smallcap /
# microcap, so it alone gives full index coverage; the others are kept as
# harmless fallbacks in case NSE renames the file.
_INDEX_CSVS = [
    "ind_niftytotalmarket_list.csv",
    "ind_nifty500list.csv",
    "ind_niftymicrocap250_list.csv",
]

# NSE sector -> (engine sector-class, validated-rule?, signal-focus bullets).
# "validated" = the engine has a proven per-sector rule (fundamentals/sectors/);
# others fall back to the generic operating-quality read (revenue/EBITDA with
# other-income & tax props guarded). See SECTOR_RESULT_PLAYBOOK.md.
SECTOR_SIGNALS: dict[str, dict] = {
    "Financial Services": {"engine": "bfsi (lenders) / generic (AMC·broker·exchange)", "built": True, "signals": [
        "NII & NIM trend, PPOP — pre-provision earning power",
        "Provisions & credit cost, GNPA/NNPA, slippages — asset quality",
        "Loan/deposit growth, CASA; NBFC: AUM growth + credit cost",
        "Lender guard: only banks/NBFC/HFC use the BFSI rule; fee-income names read generic"]},
    "Information Technology": {"engine": "it", "built": True, "signals": [
        "Constant-currency revenue growth & sequential momentum",
        "Deal TCV / net-new bookings, book-to-bill",
        "EBIT margin trend, guidance, attrition"]},
    "Oil Gas & Consumable Fuels": {"engine": "energy", "built": True, "signals": [
        "EBITDA operating line — GRM / refining & marketing margin, realizations",
        "Other-income prop & tax prop GUARDS (the ONGC false-LONG fix)",
        "Throughput / volumes, capex cycle"]},
    "Power": {"engine": "energy / generic", "built": False, "signals": [
        "PLF / plant load factor, capacity additions, regulated RoE",
        "Operating EBITDA ex other-income; tariff & receivables"]},
    "Utilities": {"engine": "energy / generic", "built": False, "signals": [
        "Volumes (units sold / distributed), regulated returns",
        "Operating EBITDA ex other-income; receivables cycle"]},
    "Healthcare": {"engine": "pharma", "built": False, "signals": [
        "US / regulated-market sales, base business vs one-offs",
        "Gross margin, R&D spend, USFDA status (narrative)",
        "Generic operating read until the pharma rule is built"]},
    "Fast Moving Consumer Goods": {"engine": "fmcg", "built": False, "signals": [
        "Underlying volume growth (UVG) vs price-led growth",
        "Gross & EBITDA margin (input-cost cycle), A&P spend"]},
    "Automobile and Auto Components": {"engine": "auto", "built": False, "signals": [
        "Volume growth + realization/ASP + EBITDA margin",
        "Mix (EV/premium), commodity (steel/Al) cost pass-through"]},
    "Capital Goods": {"engine": "capgoods", "built": False, "signals": [
        "Order inflow & closing order book, book-to-bill",
        "Execution revenue, EBITDA margin, working-capital cycle"]},
    "Construction": {"engine": "capgoods / generic", "built": False, "signals": [
        "Order book & inflows, execution pace, EBITDA margin",
        "Net debt & working capital (stretched receivables)"]},
    "Metals & Mining": {"engine": "metals", "built": False, "signals": [
        "Realization & EBITDA per tonne, sales volumes",
        "Input-cost spreads (coking coal, ore), inventory effects"]},
    "Construction Materials": {"engine": "metals / generic", "built": False, "signals": [
        "Realization per bag/tonne, volumes, EBITDA/tonne",
        "Fuel & freight cost (pet-coke, diesel)"]},
    "Realty": {"engine": "realty", "built": False, "signals": [
        "Pre-sales / bookings value & area, collections, launches",
        "Net debt, embedded margin; revenue is POCS-lumpy — read bookings not P&L"]},
    "Chemicals": {"engine": "generic", "built": False, "signals": [
        "Realization & volumes, spreads vs raw material",
        "Gross/EBITDA margin (commodity vs specialty mix)"]},
    "Consumer Durables": {"engine": "generic", "built": False, "signals": [
        "Volume growth, gross margin, channel inventory",
        "Seasonality; commodity input cost"]},
    "Consumer Services": {"engine": "generic", "built": False, "signals": [
        "SSSG / same-store growth, store adds, occupancy/ARR-type KPIs",
        "Gross margin & operating leverage"]},
    "Textiles": {"engine": "generic", "built": False, "signals": [
        "Realization & volumes, cotton/yarn spread, capacity utilization",
        "Gross/EBITDA margin, export mix"]},
    "Telecommunication": {"engine": "generic", "built": False, "signals": [
        "ARPU trend, subscriber net adds, churn",
        "EBITDA margin, capex / 5G rollout, net debt"]},
    "Media Entertainment & Publication": {"engine": "generic", "built": False, "signals": [
        "Ad vs subscription revenue, content cost",
        "EBITDA margin, viewership/subscriber trend"]},
    "Services": {"engine": "generic", "built": False, "signals": [
        "Segment revenue & EBITDA; generic operating-quality read"]},
    "Diversified": {"engine": "generic", "built": False, "signals": [
        "Read each segment on its own; consolidated EBITDA & other-income mix"]},
}
_UNKNOWN = {"engine": "generic", "built": False,
            "signals": ["No NSE index sector on file — generic operating-quality read."]}

# Micro-cap names outside every NSE index have no public sector feed; ETFs/funds
# carry no results at all. Pattern-flag the latter so they're excluded, not
# mislabelled.
_ETF_TOKENS = ("BEES", "ETF", "LIQUID", "GOLD", "SILVER", "NIFTY", "SENSEX",
               "CASE", "MAFANG", "MOM", "GSEC", "SDL", "NV20", "ALPHA", "MIDSEL")


def _looks_etf(sym: str) -> bool:
    return any(t in sym for t in _ETF_TOKENS)


def _fetch_index_sectors() -> dict[str, str]:
    sec: dict[str, str] = {}
    for f in _INDEX_CSVS:
        try:
            r = httpx.get(f"https://nsearchives.nseindia.com/content/indices/{f}",
                          headers={"User-Agent": _UA, "Referer": "https://www.nseindia.com/"},
                          timeout=30, follow_redirects=True)
            if r.status_code != 200:
                print(f"  {f}: HTTP {r.status_code}", flush=True)
                continue
            n0 = len(sec)
            for row in csv.DictReader(io.StringIO(r.text)):
                s = (row.get("Symbol") or "").strip().upper()
                ind = (row.get("Industry") or "").strip()
                if s and ind:
                    sec.setdefault(s, ind)
            print(f"  {f}: +{len(sec)-n0} (total {len(sec)})", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  {f}: ERR {e}", flush=True)
    return sec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default="config/universe_top1000.txt")
    ap.add_argument("--out", default="SECTOR_UNIVERSE.md")
    args = ap.parse_args()

    print("fetching NSE index-constituent CSVs:", flush=True)
    sec = _fetch_index_sectors()

    syms = [l.strip().upper() for l in Path(args.universe).read_text().splitlines()
            if l.strip() and not l.startswith("#")]

    by_sector: dict[str, list[str]] = {}
    etfs: list[str] = []
    for s in syms:
        if s in sec:
            by_sector.setdefault(sec[s], []).append(s)
        elif _looks_etf(s):
            etfs.append(s)
        else:
            by_sector.setdefault("Unclassified", []).append(s)

    real_sectors = [k for k in by_sector if k != "Unclassified"]
    classified = sum(len(by_sector[k]) for k in real_sectors)
    order = sorted(real_sectors, key=lambda k: (-len(by_sector[k]), k))
    if "Unclassified" in by_sector:
        order.append("Unclassified")

    out: list[str] = []
    out.append("# Sector Universe — top-1000 result-signal map\n")
    out.append("> Generated by `scripts/build_sector_md.py` from NSE's index-"
               "constituent CSVs (NIFTY Total Market `Industry` column = NSE macro "
               "sector). Per sector: the **result-reading signal focus** and the "
               "engine class in `fundamentals/sectors/` that handles it. Companion "
               "to `config/sector_mapping.yaml` + `SECTOR_RESULT_PLAYBOOK.md`.\n")
    out.append(f"**Coverage:** {classified}/{len(syms)} classified across "
               f"{len(real_sectors)} NSE sectors · "
               f"{len(by_sector.get('Unclassified', []))} unclassified micro-caps · "
               f"{len(etfs)} ETFs/funds excluded.\n")

    out.append("## Summary\n")
    out.append("| Sector | Stocks | Engine class | Validated rule |")
    out.append("|---|---:|---|:--:|")
    for s in order:
        m = SECTOR_SIGNALS.get(s, _UNKNOWN)
        out.append(f"| {s} | {len(by_sector[s])} | {m['engine']} | "
                   f"{'✅' if m['built'] else '⏳ generic'} |")
    out.append("")

    for s in order:
        m = SECTOR_SIGNALS.get(s, _UNKNOWN)
        members = sorted(by_sector[s])
        out.append(f"## {s}  ({len(members)})\n")
        out.append(f"- **Engine class:** `{m['engine']}` "
                   f"({'validated rule' if m['built'] else 'generic fallback'})")
        out.append("- **Signal focus:**")
        for sig in m["signals"]:
            out.append(f"  - {sig}")
        out.append("")
        # 8 per line for compact, scannable membership lists
        for i in range(0, len(members), 8):
            out.append("  " + ", ".join(f"`{x}`" for x in members[i:i + 8]))
        out.append("")

    if etfs:
        out.append(f"## Excluded — ETFs / index funds  ({len(etfs)})\n")
        out.append("> No company financials; should be dropped from "
                   "`config/universe_top1000.txt`.\n")
        es = sorted(etfs)
        for i in range(0, len(es), 8):
            out.append("  " + ", ".join(f"`{x}`" for x in es[i:i + 8]))
        out.append("")

    Path(args.out).write_text("\n".join(out))
    print(f"\nwrote {args.out}: {classified}/{len(syms)} classified, "
          f"{len(by_sector.get('Unclassified', []))} unclassified, "
          f"{len(etfs)} ETFs", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
