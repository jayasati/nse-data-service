"""Audit parse_xbrl against NSE's OWN headline numbers across the universe.

The NSE integrated-filing API returns, per result, the company's headline figures
(total income, PBT, PAT, EPS — in rupee lakhs) AND the XBRL URL. We treat those
headline numbers as ground truth, download each XBRL, run parse_xbrl, and flag any
must-have field the parser misses or gets wrong. On a miss we dump the raw numeric
tags actually present in the XBRL so the taxonomy variant can be mapped.

    .venv/bin/python -u scripts/audit_xbrl_fields.py            # whole universe
    .venv/bin/python -u scripts/audit_xbrl_fields.py --step 6   # every 6th (fast)
    .venv/bin/python -u scripts/audit_xbrl_fields.py --symbols SBIN,HDFCBANK,INFY
"""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# canonical field -> (API headline key, is_eps). These are the MUST-HAVE fields:
# NSE's own result summary is exactly these four, so if parse_xbrl misses any the
# stored result will not reconcile against the company's filing.
_HEADLINE = {
    "total_income_cr": ("gfrTotalIncome", False),
    "pbt_cr": ("gfrProBefTax", False),
    "pat_cr": ("gfrNetProLoss", False),
    "eps_basic": ("gfrErnPerShare", True),
}


def _f(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _candidate_tags(data: bytes) -> list[str]:
    """Numeric P&L-ish tag local-names present anywhere in the instance."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    kw = ("income", "revenue", "profit", "loss", "tax", "earningspershare",
          "interest", "expense", "operating", "provision")
    seen = Counter()
    for el in root.iter():
        lt = _local(el.tag)
        if any(k in lt.lower() for k in kw) and _f(el.text) is not None:
            seen[lt] += 1
    return [t for t, _ in seen.most_common()]


def _fetch_xbrl(get, url, parse, *, tries=3):
    """Download + parse, retrying transient fetch/blocked responses. Returns
    (parsed_or_None, raw_or_None, err). A blocked page parses to None -> retry."""
    import time
    err = None
    for k in range(tries):
        try:
            raw = get(url)
            parsed = parse(raw)
            if parsed and parsed.get("fields"):
                return parsed, raw, None
            err = "empty-parse"          # likely a blocked/HTML response
        except Exception as e:  # noqa: BLE001
            err = str(e)
        time.sleep(0.6 * (k + 1))
    return None, None, err


def _close(a, b, *, eps=False) -> bool:
    if a is None or b is None:
        return False
    if eps:
        return abs(a - b) <= 0.05
    return abs(a - b) <= max(0.5, 0.01 * abs(b))   # 1% or 0.5cr


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, default=1, help="sample every Nth symbol")
    ap.add_argument("--symbols", help="comma list, overrides universe")
    ap.add_argument("--limit", type=int, default=0, help="stop after N symbols")
    ap.add_argument("--universe", default="config/universe_top1000.txt")
    ap.add_argument("--sleep", type=float, default=0.25, help="seconds between symbols")
    args = ap.parse_args()

    from nse_data.session.manager import SessionManager
    from nse_data.parsers.xbrl_extract import _http_get, _api_date, _file_ts
    from nse_data.parsers.xbrl_financials import parse_xbrl

    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        lines = Path(args.universe).read_text().split()
        syms = [s.strip().upper() for s in lines if s.strip() and not s.startswith("#")]
        syms = syms[::args.step]
    if args.limit:
        syms = syms[:args.limit]

    s = SessionManager()
    n_ok = n_miss = n_noxbrl = n_err = 0
    miss_by_field = Counter()      # field -> times missing/wrong
    scale_issues = []             # (symbol, scope, field, parsed, truth)
    variant_tags = Counter()      # raw tag seen when a field was missing
    fail_rows = []                # detailed per-symbol failures

    import time
    fetch_fail_syms = []
    print(f"auditing {len(syms)} symbols (step={args.step})\n", flush=True)
    for i, sym in enumerate(syms, 1):
        time.sleep(args.sleep)   # be gentle on NSE to avoid blocked responses
        from urllib.parse import quote
        try:
            data = s.get_json(
                "nextapi",
                f"/api/NextApi/apiClient/GetQuoteApi?functionName=getIntegratedFilingData&symbol={quote(sym, safe='')}",
                referer=f"https://www.nseindia.com/get-quote/equity/{sym}")
        except Exception as e:  # noqa: BLE001
            n_err += 1
            print(f"[{i}/{len(syms)}] {sym:14s} API-ERR {e}", flush=True)
            continue
        if not data:
            print(f"[{i}/{len(syms)}] {sym:14s} no-filings", flush=True)
            continue
        # latest quarter present
        latest = max((_api_date(r.get("gfrQuaterEnded")) for r in data
                      if _api_date(r.get("gfrQuaterEnded"))), default=None)
        recs = [r for r in data if _api_date(r.get("gfrQuaterEnded")) == latest
                and r.get("gfrXbrlFname")]
        # keep only the newest filing per scope (a re-filed correction supersedes)
        newest: dict = {}
        for r in sorted(recs, key=lambda r: _file_ts(r.get("gfSystym")), reverse=True):
            sc = "consolidated" if str(r.get("gfrConsolidated", "")).lower().startswith("cons") \
                else "standalone"
            newest.setdefault(sc, r)
        recs = list(newest.values())
        if not recs:
            n_noxbrl += 1
            print(f"[{i}/{len(syms)}] {sym:14s} no-xbrl", flush=True)
            continue

        sym_fail = []
        for rec in recs:
            scope = "consolidated" if str(rec.get("gfrConsolidated", "")).lower().startswith("cons") \
                else "standalone"
            parsed, raw, ferr = _fetch_xbrl(_http_get, rec["gfrXbrlFname"], parse_xbrl)
            if parsed is None:
                # Couldn't fetch/parse the XBRL at all — a transient/blocked fetch,
                # NOT a taxonomy gap. Count separately, don't pollute variant tags.
                n_err += 1
                sym_fail.append(f"{scope}: FETCH-FAIL ({ferr})")
                continue
            pf = parsed.get("fields", {})
            for canon, (api_key, is_eps) in _HEADLINE.items():
                truth = _f(rec.get(api_key))
                if truth is None:
                    continue
                if not is_eps:
                    truth = round(truth / 100.0, 2)   # lakh -> crore
                got = pf.get(canon)
                if _close(got, truth, eps=is_eps):
                    continue
                miss_by_field[canon] += 1
                # scale (off by 10/100/1000)?
                if got is not None and truth and abs(got) > 1e-9:
                    ratio = truth / got
                    if any(abs(ratio - m) < 0.02 for m in (10, 100, 1000, 0.1, 0.01, 0.001)):
                        scale_issues.append((sym, scope, canon, got, truth))
                        sym_fail.append(f"{scope}.{canon}: SCALE got={got} truth={truth} (x{ratio:.0f})")
                        continue
                sym_fail.append(f"{scope}.{canon}: got={got} truth={truth}")
                for t in _candidate_tags(raw):
                    variant_tags[t] += 1

        if any("FETCH-FAIL" in f for f in sym_fail):
            fetch_fail_syms.append(sym)
        if sym_fail:
            n_miss += 1
            fail_rows.append((sym, sym_fail))
            print(f"[{i}/{len(syms)}] {sym:14s} MISS  " + " | ".join(sym_fail), flush=True)
        else:
            n_ok += 1
            print(f"[{i}/{len(syms)}] {sym:14s} ok", flush=True)

    print("\n" + "=" * 70)
    real_miss = [s for s, fails in fail_rows if any("FETCH-FAIL" not in f for f in fails)]
    print(f"OK {n_ok} | MISMATCH {n_miss} | no-xbrl {n_noxbrl} | fetch-fails {n_err}")
    print(f"REAL taxonomy mismatches ({len(real_miss)}): {', '.join(real_miss) or 'none'}")
    if fetch_fail_syms:
        print(f"\nfetch-fail symbols (re-run with --symbols): {','.join(fetch_fail_syms)}")
    print("\nmismatch count by must-have field:")
    for f, c in miss_by_field.most_common():
        print(f"   {f:24s} {c}")
    if scale_issues:
        print(f"\nSCALE issues ({len(scale_issues)}):")
        for sym, sc, f, got, truth in scale_issues[:40]:
            print(f"   {sym} {sc}.{f}: got={got} truth={truth}")
    if variant_tags:
        print("\nraw numeric tags seen in mismatching XBRLs (candidate variants):")
        for t, c in variant_tags.most_common(40):
            print(f"   {c:4d}  {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
