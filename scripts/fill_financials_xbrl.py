"""Bulk-fill extracted_financials for the whole focus universe from XBRL only.

Deterministic and free: for each top-1000 symbol we ask NSE's NextApi for the
recent result quarters, run the (audited) XBRL parser on each, persist the
standalone + consolidated P&L, then compute YoY/QoQ growth from the stored
history. No PDF and no LLM are involved, so this never touches the Azure cap.

Why multiple quarters: the dashboard reads YoY/QoQ from each row's stored
``growth_json``, which ``quarter_growth`` derives by comparing a quarter to the
prior-year quarter (12m back) and prior quarter (3m back) ALSO stored in
extracted_financials. A single latest-quarter fill therefore shows YoY = "—";
filling several recent quarters gives the comparison base. The integrated-filing
API returns up to ~8 quarters in ONE call, so this costs one request per symbol
plus the per-XBRL fetches.

Idempotent: persist_extraction upserts on (symbol, period_ending, scope) and the
growth pass re-UPDATEs growth_json, so re-running only refreshes. Strategy is
stamped ``xbrl`` (confidence 1.0).

    PYTHONPATH=src .venv/bin/python -u scripts/fill_financials_xbrl.py
    # subset / smoke:
    .venv/bin/python -u scripts/fill_financials_xbrl.py --symbols SBIN,INFY
    .venv/bin/python -u scripts/fill_financials_xbrl.py --limit 25 --quarters 6

`-u` (unbuffered) keeps the per-symbol progress streaming live over SSH.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--universe", default="config/universe_top1000.txt")
    ap.add_argument("--symbols", help="comma list, overrides the universe file")
    ap.add_argument("--limit", type=int, default=0, help="cap symbols (0 = all)")
    ap.add_argument("--quarters", type=int, default=12,
                    help="quarters to backfill per symbol (deeper = YoY/QoQ on more "
                         "displayed quarters; merges integrated + financial feeds)")
    ap.add_argument("--sleep", type=float, default=0.4,
                    help="seconds between symbols (be gentle on NSE)")
    args = ap.parse_args()

    from nse_data.storage.db import open_db
    from nse_data.session.manager import SessionManager
    from nse_data.parsers.xbrl_extract import _http_get, _api_date, _file_ts
    from nse_data.parsers.xbrl_financials import parse_xbrl
    from nse_data.fundamentals.from_results import persist_extraction, quarter_growth

    conn = open_db(args.db)
    session = SessionManager()

    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        lines = Path(args.universe).read_text().splitlines()
        syms = [l.strip().upper() for l in lines if l.strip() and not l.startswith("#")]
    if args.limit:
        syms = syms[: args.limit]

    print(f"filling {len(syms)} symbols from XBRL "
          f"({args.quarters} quarters each, sleep={args.sleep}s)\n", flush=True)

    n_ok = n_nofiling = n_err = 0
    rows_stored = grown = 0
    started = time.time()
    for i, sym in enumerate(syms, 1):
        time.sleep(args.sleep)   # gentle on NSE to avoid blocked responses
        t0 = time.time()
        esym = quote(sym, safe="")
        ref = f"https://www.nseindia.com/get-quote/equity/{sym}"
        try:
            data = session.get_json(
                "nextapi",
                f"/api/NextApi/apiClient/GetQuoteApi?functionName=getIntegratedFilingData&symbol={esym}",
                referer=ref)
        except Exception as e:  # noqa: BLE001
            n_err += 1
            print(f"[{i:>4}/{len(syms)}] {sym:<14} API-ERR {e!r}", flush=True)
            continue

        # Group result filings by quarter-end; keep the newest filing per scope
        # for each quarter (a re-filed correction supersedes). recs: qe -> [rows].
        # The integrated feed carries only the recent ~5 quarters; the older
        # financial-results feed extends history back years (its xbrl_attachment
        # parses with the same parser) — merge it so YoY/QoQ compute for the
        # PREVIOUS quarters too, not just the latest. Records are normalised to
        # the integrated shape; integrated wins on overlap (modern, both scopes).
        by_q: dict = {}
        for r in (data or []):
            qe = _api_date(r.get("gfrQuaterEnded"))
            if qe is not None and r.get("gfrXbrlFname"):
                by_q.setdefault(qe, []).append(r)
        if len(by_q) < args.quarters:
            try:
                fin = session.get_json(
                    "nextapi",
                    f"/api/NextApi/apiClient/GetQuoteApi?functionName=getFinancialResultData"
                    f"&symbol={esym}&marketApiType=equities&noOfRecords=16",
                    referer=ref) or []
            except Exception:  # noqa: BLE001 — older feed is best-effort
                fin = []
            for r in fin:
                qe = _api_date(r.get("to_date"))
                if qe is None or not r.get("xbrl_attachment") or qe in by_q:
                    continue
                by_q.setdefault(qe, []).append({
                    "gfrQuaterEnded": r.get("to_date"),
                    "gfrConsolidated": r.get("consolidated"),
                    "gfrXbrlFname": r.get("xbrl_attachment"),
                    "gfSystym": r.get("creation_date") or r.get("submission_date") or "",
                })
        if not by_q:
            n_nofiling += 1
            print(f"[{i:>4}/{len(syms)}] {sym:<14} no-filing", flush=True)
            continue

        periods_done: list[tuple[str, str]] = []   # (period_ending, scope)
        for qe in sorted(by_q, reverse=True)[: args.quarters]:
            bdt = qe.strftime("%d-%b-%Y")
            # Candidates per scope, newest filing first. A quarter is often
            # RE-FILED (corrections), and the newest entry's XBRL URL sometimes
            # 404s (a dead *_WEB.xml) while an older filing still parses — so try
            # each candidate in order and take the first that parses, rather than
            # dropping the quarter (this is what cost LT its year-ago YoY base).
            cands: dict[str, list] = {"standalone": [], "consolidated": []}
            for r in sorted(by_q[qe], key=lambda r: _file_ts(r.get("gfSystym")), reverse=True):
                sc = "consolidated" if str(r.get("gfrConsolidated", "")).lower().startswith("cons") \
                    else "standalone"
                cands[sc].append(r)
            for scope_cands in cands.values():
                for rec in scope_cands:
                    try:
                        parsed = parse_xbrl(_http_get(rec["gfrXbrlFname"]))
                    except Exception:  # noqa: BLE001 — try the next (older) re-filing
                        continue
                    if not parsed or not parsed.get("fields") or not parsed.get("period_ending"):
                        continue
                    scope = "consolidated" if parsed.get("scope") == "consolidated" else "standalone"
                    persist_extraction(
                        conn, symbol=sym, period_ending=parsed["period_ending"], scope=scope,
                        fields=parsed["fields"], units_phrase="xbrl", confidence=1.0,
                        strategy="xbrl", source_fingerprint=None, broadcast_dt=bdt,
                    )
                    rows_stored += 1
                    periods_done.append((parsed["period_ending"], scope))
                    break   # this scope is filled; don't parse older re-filings

        if not periods_done:
            n_err += 1
            print(f"[{i:>4}/{len(syms)}] {sym:<14} no-parseable-xbrl", flush=True)
            continue

        # Growth pass: now that the history is on file, compute YoY/QoQ per row
        # and write growth_json (what the dashboard reads).
        for period, scope in periods_done:
            g = quarter_growth(conn, sym, period, scope)
            if g:
                import json
                conn.execute(
                    "UPDATE extracted_financials SET growth_json=? "
                    "WHERE symbol=? AND period_ending=? AND scope=?",
                    (json.dumps(g, sort_keys=True), sym, period, scope))
                grown += 1
        conn.commit()

        n_ok += 1
        quarters = sorted({p for p, _ in periods_done}, reverse=True)
        print(f"[{i:>4}/{len(syms)}] OK  {sym:<14} rows={len(periods_done)} "
              f"quarters={len(quarters)} latest={quarters[0]} "
              f"({time.time()-t0:.1f}s)", flush=True)

    conn.close()
    print(f"\nDONE in {time.time()-started:.0f}s: {n_ok} symbols filled "
          f"({rows_stored} rows, {grown} with growth), "
          f"{n_nofiling} no-filing, {n_err} errors", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
