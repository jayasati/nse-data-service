#!/usr/bin/env python3
"""Generate authoritative ground-truth labels from NSE XBRL.

For each result-PDF fixture, find the matching XBRL filing(s) in
raw_financial_results (standalone + consolidated are separate documents), fetch
and parse them, and write a canonical ground_truth/<fingerprint>.yaml. These
labels are the company's own structured submission — model-independent, so the
eval stops being self-confirming.

Never overwrites a human-verified label; instead it CROSS-CHECKS against it and
reports any mismatch (a disagreement means either the parser or the human verdict
needs a look).

    python scripts/xbrl_ground_truth.py --symbol BEL          # one symbol
    python scripts/xbrl_ground_truth.py --limit 20            # first 20 fixtures
    python scripts/xbrl_ground_truth.py --dry-run             # fetch + compare, write nothing
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from nse_data.parsers.xbrl_financials import parse_xbrl  # noqa: E402

DB = ROOT / "data/nse.db"
META = ROOT / "tests/financial_extraction/fixtures/metadata.json"
GT_DIR = ROOT / "tests/financial_extraction/ground_truth"
_UA = "Mozilla/5.0 (compatible; nse-data-service/1.0)"
_AMOUNTS = ("revenue_cr", "pat_cr", "total_income_cr", "pbt_cr")   # cross-check keys


def _d(s: str) -> dt.date | None:
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s.strip()[:len(fmt) + 4], fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def _target_quarter_end(broadcast: dt.date) -> dt.date:
    """Most recent quarter-end strictly before the filing date."""
    ends = [(3, 31), (6, 30), (9, 30), (12, 31)]
    cands = [dt.date(broadcast.year, m, d) for m, d in ends]
    cands.append(dt.date(broadcast.year - 1, 12, 31))
    return max(c for c in cands if c < broadcast)


def _fetch(url: str) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()
    except Exception as e:  # noqa: BLE001
        print(f"    fetch failed: {e}")
        return None


def _xbrl_urls(conn: sqlite3.Connection, symbol: str, qend: dt.date) -> list[str]:
    urls = []
    for to_date, url in conn.execute(
        "SELECT to_date, xbrl_url FROM raw_financial_results "
        "WHERE symbol=? AND xbrl_url IS NOT NULL AND xbrl_url<>''", (symbol,)
    ):
        d = _d(to_date)
        if d == qend:
            urls.append(url)
    return urls


def _period_label(qend: dt.date) -> str:
    q = {3: "Q4", 6: "Q1", 9: "Q2", 12: "Q3"}[qend.month]
    fy = (qend.year + 1) if qend.month >= 4 else qend.year
    return f"{q}-FY{fy % 100:02d}"


def _existing(fp: str) -> dict | None:
    p = GT_DIR / f"{fp}.yaml"
    if not p.exists():
        return None
    try:
        return yaml.safe_load(p.read_text())
    except Exception:  # noqa: BLE001
        return None


def _crosscheck(symbol: str, human: dict, xbrl_std: dict) -> None:
    """Print mismatches between a human-verified label and the XBRL numbers."""
    hs = (human or {}).get("standalone") or {}
    diffs = []
    for k in _AMOUNTS:
        hv, xv = hs.get(k), xbrl_std.get(k)
        if hv is None or xv is None:
            continue
        if abs(hv - xv) > max(abs(xv) * 0.02, 0.25):   # 2% / 0.25cr tolerance
            diffs.append(f"{k}: human {hv} vs XBRL {xv}")
    tag = "✓ match" if not diffs else "✗ MISMATCH"
    print(f"    cross-check vs human-verified: {tag}")
    for d in diffs:
        print(f"      {d}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--overwrite-human", action="store_true",
                    help="(unsafe) replace human-verified labels too")
    args = ap.parse_args()

    if not DB.exists():
        print("no data/nse.db — needs the result-filings DB"); return 1
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    fixtures = json.loads(META.read_text())["fixtures"]

    written = checked = skipped = 0
    for f in fixtures:
        if args.symbol and f["symbol"].upper() != args.symbol.upper():
            continue
        if args.limit and checked >= args.limit:
            break
        subj = f.get("subject", "").lower()
        if "result" not in subj and "board meeting" not in subj:
            continue
        bdate = _d(f.get("broadcast_dt", ""))
        if not bdate:
            continue
        checked += 1
        symbol, fp = f["symbol"], f["fingerprint"]
        qend = _target_quarter_end(bdate)
        urls = _xbrl_urls(conn, symbol, qend)
        print(f"\n{symbol}  ({fp})  quarter {qend.isoformat()}  · {len(urls)} XBRL")
        if not urls:
            skipped += 1
            continue

        blocks: dict[str, dict] = {}
        for url in urls:
            data = _fetch(url)
            if not data:
                continue
            parsed = parse_xbrl(data)
            if parsed and parsed["fields"]:
                blocks[parsed["scope"]] = parsed["fields"]
        if "standalone" not in blocks and "consolidated" not in blocks:
            print("    no parseable P&L"); skipped += 1
            continue

        existing = _existing(fp)
        human = existing if (existing or {}).get("_meta", {}).get("source") == "human_verified" else None
        if human and "standalone" in blocks:
            _crosscheck(symbol, human, blocks["standalone"])
        if human and not args.overwrite_human:
            print("    keeping human-verified label (cross-checked above)")
            continue

        doc = {
            "standalone": blocks.get("standalone") or {},
            "consolidated": blocks.get("consolidated") or None,
            "period_label": _period_label(qend),
            "period_ending": qend.isoformat(),
            "units_in_source_pdf": "INR (XBRL, normalised to crore)",
            "notes": "authoritative XBRL label",
            "_meta": {"fingerprint": fp, "symbol": symbol,
                      "reviewed": True, "source": "xbrl"},
        }
        std = blocks.get("standalone", {})
        print(f"    standalone: rev={std.get('revenue_cr')} pat={std.get('pat_cr')} "
              f"eps={std.get('eps_basic')}  | consolidated: {'yes' if blocks.get('consolidated') else 'no'}")
        if not args.dry_run:
            GT_DIR.mkdir(parents=True, exist_ok=True)
            (GT_DIR / f"{fp}.yaml").write_text(
                yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))
            written += 1

    print(f"\n{'(dry-run) ' if args.dry_run else ''}checked {checked} · "
          f"written {written} · skipped {skipped} (no XBRL match)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
