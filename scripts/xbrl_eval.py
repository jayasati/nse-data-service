#!/usr/bin/env python3
"""Model-independent extractor eval: vision PDF extraction vs authoritative XBRL.

Builds its OWN corpus + labels from data we actually have (the Dec-2024 quarter):
for each filing it downloads the result PDF (`result_url`), runs the vision
extractor, parses the company's own XBRL (`xbrl_url`) as ground truth, and
compares per field. No hand-labeling, no model-generated labels — so the
accuracy number is genuinely independent (the Week-17 gate the memory called for).

    python scripts/xbrl_eval.py --limit 10            # quick run
    python scripts/xbrl_eval.py --limit 50 --fno      # F&O names only (the gate set)
    python scripts/xbrl_eval.py --quarter 31-Dec-2024 --limit 30

Each PDF is one gpt-4o call (~$0.02-0.12); the LLM daily cap still applies.
Resumable: per-filing results are cached in scripts/xbrl_eval_log.jsonl.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

from nse_data.parsers import financial_extractor as fe          # noqa: E402
from nse_data.parsers.xbrl_financials import parse_xbrl          # noqa: E402

DB = ROOT / "data/nse.db"
LOG = ROOT / "scripts/xbrl_eval_log.jsonl"
_UA = "Mozilla/5.0 (compatible; nse-data-service/1.0)"

AMOUNT_FIELDS = ("revenue_cr", "other_income_cr", "total_income_cr",
                 "total_expenses_cr", "pbt_cr", "tax_cr", "pat_cr",
                 "total_comprehensive_income_cr")
EPS_FIELDS = ("eps_basic", "eps_diluted")
ALL_FIELDS = AMOUNT_FIELDS + EPS_FIELDS

REL_TOL = 0.02
AMOUNT_ABS_FLOOR = 0.25     # crore
EPS_ABS_FLOOR = 0.05        # rupees


def _close(got: float, want: float, field: str) -> bool:
    floor = EPS_ABS_FLOOR if field in EPS_FIELDS else AMOUNT_ABS_FLOOR
    return abs(got - want) <= max(abs(want) * REL_TOL, floor)


def _fetch(url: str) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read()
    except Exception as e:  # noqa: BLE001
        print(f"    fetch failed: {e}")
        return None


def _select_filings(conn, quarter: str, limit: int, fno_only: bool) -> list[dict]:
    fno = set()
    if fno_only:
        try:
            fno = {r[0] for r in conn.execute("SELECT symbol FROM raw_fno_list")}
        except sqlite3.OperationalError:
            pass
    rows = conn.execute(
        "SELECT symbol, result_url, xbrl_url FROM raw_financial_results "
        "WHERE to_date=? AND result_url<>'' AND xbrl_url<>'' ORDER BY symbol",
        (quarter,),
    ).fetchall()
    by_sym: dict[str, dict] = {}
    for symbol, result_url, xbrl_url in rows:
        if fno_only and fno and symbol not in fno:
            continue
        e = by_sym.setdefault(symbol, {"symbol": symbol, "result_url": result_url, "xbrl": []})
        if result_url and not e["result_url"]:
            e["result_url"] = result_url
        if xbrl_url and xbrl_url not in e["xbrl"]:
            e["xbrl"].append(xbrl_url)
    return [v for v in by_sym.values() if v["result_url"] and v["xbrl"]][:limit]


def _xbrl_labels(xbrl_urls: list[str]) -> dict[str, dict]:
    """{'standalone': {...}, 'consolidated': {...}} from the filing's XBRL docs."""
    out: dict[str, dict] = {}
    for url in xbrl_urls:
        data = _fetch(url)
        if not data:
            continue
        parsed = parse_xbrl(data)
        if parsed and parsed["fields"]:
            out[parsed["scope"]] = parsed["fields"]
    return out


def _score(extracted: dict, label: dict, per_field: dict, scope: str, fails: list, symbol: str):
    for f in ALL_FIELDS:
        want = label.get(f)
        if want is None:
            continue
        per_field[f]["total"] += 1
        got = extracted.get(f)
        if got is None:
            per_field[f]["missing"] += 1
            fails.append(f"{symbol} [{scope}] {f}: MISSING (xbrl {want})")
        elif _close(got, want, f):
            per_field[f]["correct"] += 1
        else:
            fails.append(f"{symbol} [{scope}] {f}: got {got:.4g} vs xbrl {want:.4g}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quarter", default="31-Dec-2024")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--fno", action="store_true", help="F&O symbols only (the gate set)")
    args = ap.parse_args()

    if not DB.exists():
        print("no data/nse.db"); return 1
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    filings = _select_filings(conn, args.quarter, args.limit, args.fno)
    if not filings:
        print(f"no filings with PDF+XBRL for {args.quarter}"); return 1

    print(f"XBRL eval · {len(filings)} filings · quarter {args.quarter}"
          f"{' · F&O only' if args.fno else ''}\n")
    per_field: dict = defaultdict(lambda: {"correct": 0, "total": 0, "missing": 0})
    fails: list[str] = []
    cost = 0.0
    logf = LOG.open("a")

    for i, fil in enumerate(filings, 1):
        sym = fil["symbol"]
        print(f"[{i}/{len(filings)}] {sym} …", flush=True)
        labels = _xbrl_labels(fil["xbrl"])
        if not labels:
            print("    no parseable XBRL — skip"); continue
        pdf = _fetch(fil["result_url"])
        if not pdf:
            continue
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tf:
            tf.write(pdf); tf.flush()
            res = fe.extract(tf.name, data=pdf, use_llm_fallback=True, symbol=sym,
                             broadcast_dt=args.quarter)
        cost += res.llm_cost_usd
        if "standalone" in labels:
            _score(res.fields, labels["standalone"], per_field, "standalone", fails, sym)
        if "consolidated" in labels and res.consolidated:
            _score(res.consolidated, labels["consolidated"], per_field, "consolidated", fails, sym)
        logf.write(json.dumps({"symbol": sym, "strategy": res.strategy,
                               "extracted": res.fields, "xbrl": labels.get("standalone")}) + "\n")
        logf.flush()

    # ---- report ----
    print("\n" + "=" * 56 + "\nACCURACY PER FIELD (vs XBRL)\n" + "=" * 56)
    gc = gt = 0
    for f in ALL_FIELDS:
        s = per_field[f]
        if not s["total"]:
            continue
        gc += s["correct"]; gt += s["total"]
        pct = 100 * s["correct"] / s["total"]
        print(f"  {f:32} {s['correct']:3}/{s['total']:<3} = {pct:5.1f}%  (missing {s['missing']})")
    overall = 100 * gc / gt if gt else 0
    print(f"\n  OVERALL: {gc}/{gt} = {overall:.1f}%   (gate 90%, target 95%)")
    print(f"  LLM cost: ${cost:.4f}")
    if fails:
        print("\n" + "=" * 56 + f"\nMISMATCHES ({len(fails)})\n" + "=" * 56)
        for x in fails[:40]:
            print("  " + x)
    return 0 if overall >= 90 else 2


if __name__ == "__main__":
    raise SystemExit(main())
