#!/usr/bin/env python3
"""Interactive accuracy check for the vision PDF financial extractor.

For each result PDF it: runs the extractor, prints the extracted numbers in the
terminal, opens the PDF so you can eyeball the source, and records your verdict
(correct / wrong / partial / skip). At the end it tallies accuracy. Resumable —
already-judged PDFs are skipped on re-run.

Usage (run it yourself in the terminal so the prompts + PDF viewer work):
    python scripts/verify_extraction.py                  # fixtures corpus, vision-first
    python scripts/verify_extraction.py --symbol FORCEMOT
    python scripts/verify_extraction.py --limit 10 --mode vision
    python scripts/verify_extraction.py --dir /path/to/pdfs --no-open
    python scripts/verify_extraction.py --no-prompt --limit 1   # non-interactive smoke

Each PDF costs one gpt-4o call (~$0.01–0.12); the LLMClient daily cap still applies.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

from nse_data.parsers import financial_extractor as fe  # noqa: E402

FIXTURES = ROOT / "tests/financial_extraction/fixtures"
METADATA = FIXTURES / "metadata.json"
DEFAULT_LOG = ROOT / "scripts/extraction_verify_log.jsonl"

FIELDS = [
    "revenue_cr", "other_income_cr", "total_income_cr", "total_expenses_cr",
    "pbt_cr", "tax_cr", "pat_cr", "total_comprehensive_income_cr",
    "eps_basic", "eps_diluted",
]

# ----- ANSI colours (skip if not a tty) -----
_C = sys.stdout.isatty()
def c(s, code): return f"\033[{code}m{s}\033[0m" if _C else s
def bold(s): return c(s, "1")
def green(s): return c(s, "32")
def red(s): return c(s, "31")
def yellow(s): return c(s, "33")
def cyan(s): return c(s, "36")
def dim(s): return c(s, "2")


# --------------------------------------------------------------------------- #
# corpus loading
# --------------------------------------------------------------------------- #

# Subjects that actually carry a Statement of P&L (worth verifying). Press
# releases / presentations / transcripts are *about* results but have no standard
# statement — including them just produces (correct) nulls with nothing to check.
_STATEMENT_SUBJECTS = ("financial result", "outcome of board meeting",
                       "audited financial", "unaudited financial")
_EXCLUDE_SUBJECTS = ("press release", "media release", "investor presentation",
                     "presentation", "transcript", "earnings call", "analyst",
                     "newspaper", "press conference")


def load_fixture_pdfs(symbol: str | None, *, all_subjects: bool = False) -> list[dict]:
    """Result-statement fixtures (symbol, company, subject, url, pdf path).

    By default keeps only filings whose subject carries a P&L statement; pass
    ``all_subjects=True`` to include everything (e.g. to probe press releases).
    """
    meta = json.loads(METADATA.read_text())
    out = []
    for f in meta["fixtures"]:
        subj = f.get("subject", "").lower()
        blob = (f.get("subject", "") + " " + f.get("details", "")).lower()
        if not all_subjects:
            if any(x in subj for x in _EXCLUDE_SUBJECTS):
                continue
            if not any(x in subj for x in _STATEMENT_SUBJECTS):
                continue
            # a board-meeting outcome must actually be about results (not a
            # dividend / fundraise / buyback meeting, which carry no P&L)
            if "result" not in blob:
                continue
        if symbol and f["symbol"].upper() != symbol.upper():
            continue
        out.append({
            "key": f["fingerprint"],
            "symbol": f["symbol"],
            "company": f.get("company_name", ""),
            "subject": f.get("subject", ""),
            "broadcast_dt": f.get("broadcast_dt", ""),
            "url": f.get("attachment_url", ""),
            "path": str(ROOT / f["pdf_path"]),
        })
    return out


def load_dir_pdfs(directory: str) -> list[dict]:
    pdfs = sorted(Path(directory).glob("*.pdf"))
    return [{
        "key": p.stem, "symbol": p.stem, "company": "", "subject": "",
        "broadcast_dt": "", "url": "", "path": str(p),
    } for p in pdfs]


# --------------------------------------------------------------------------- #
# extraction + display
# --------------------------------------------------------------------------- #

def run_extraction(item: dict, mode: str):
    if mode == "vision":
        return _extract_vision_only(item)
    return fe.extract(
        item["path"], use_llm_fallback=True,
        symbol=item["symbol"], subject=item["subject"],
        broadcast_dt=item["broadcast_dt"],
    )


def _extract_vision_only(item: dict):
    """Force the vision tier (render located P&L pages → gpt-4o vision)."""
    from nse_data.parsers import pdf_render, pdf_text
    from nse_data.parsers.extractors.vision_financial import extract_via_vision

    data = Path(item["path"]).read_bytes()
    pages = pdf_text.page_texts(data)
    idx = fe._locate_pnl_pages(pages)
    images = pdf_render.render_pages(data, idx or None)
    out = extract_via_vision(
        images, symbol=item["symbol"], subject=item["subject"],
        broadcast_dt=item["broadcast_dt"],
    )
    if out is None:
        return fe.ExtractionResult(strategy="vision_unavailable")
    warnings = fe._run_validations(out["fields"])
    return fe.ExtractionResult(
        fields=out["fields"], consolidated=out.get("consolidated", {}),
        confidence=fe._confidence(out["fields"], warnings), strategy="vision",
        units_phrase=out.get("units_phrase"), period_ending=out.get("period_ending"),
        warnings=warnings, llm_cost_usd=out.get("cost_usd", 0.0),
    )


def _fmt(v):
    return "—" if v is None else f"{v:,.2f}"


def print_result(item: dict, res, idx: int, total: int):
    print("\n" + "=" * 70)
    print(bold(f"[{idx}/{total}] {item['symbol']}") + dim(f"  {item['company']}"))
    if item["subject"]:
        print(dim(f"  {item['subject']}  ·  filed {item['broadcast_dt']}"))
    print(dim(f"  PDF:    {item['path']}"))
    if item["url"]:
        print(dim(f"  Source: {item['url']}"))
    print("-" * 70)
    tag = green if res.strategy in ("vision", "text_llm") else red
    print(f"  strategy={tag(res.strategy)}  confidence={res.confidence:.2f}  "
          f"units={res.units_phrase!r}  period={res.period_ending}  "
          f"cost=${res.llm_cost_usd:.4f}")
    if res.warnings:
        print(yellow("  warnings: " + "; ".join(res.warnings)))

    has_cons = bool(res.consolidated)
    hdr = f"  {'field':<32}{'standalone':>15}"
    if has_cons:
        hdr += f"{'consolidated':>15}"
    print(cyan(hdr))
    for k in FIELDS:
        sv = res.fields.get(k)
        line = f"  {k:<32}{_fmt(sv):>15}"
        if has_cons:
            line += f"{_fmt(res.consolidated.get(k)):>15}"
        print(line)

    # quick internal-consistency hints (not authoritative — just flags)
    _consistency(res.fields)


def _consistency(f: dict):
    checks = []
    if all(k in f for k in ("total_income_cr", "revenue_cr", "other_income_cr")):
        ok = abs(f["total_income_cr"] - (f["revenue_cr"] + f["other_income_cr"])) < max(0.25, abs(f["total_income_cr"]) * 0.05)
        checks.append(("total_income = revenue + other_income", ok))
    if all(k in f for k in ("pbt_cr", "tax_cr", "pat_cr")):
        ok = abs((f["pbt_cr"] - f["tax_cr"]) - f["pat_cr"]) < max(0.25, abs(f["pat_cr"]) * 0.10)
        checks.append(("pbt - tax = pat", ok))
    for label, ok in checks:
        print(("  " + (green("✓ ") if ok else red("✗ ")) + dim(label)))


# --------------------------------------------------------------------------- #
# open the PDF
# --------------------------------------------------------------------------- #

def open_pdf(path: str) -> bool:
    """Best-effort open in the OS default viewer (WSL/Linux/mac)."""
    opener = os.environ.get("PDF_OPENER")
    try:
        if opener:
            subprocess.Popen([opener, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        if shutil.which("wslview"):                       # WSL (wslu)
            subprocess.Popen(["wslview", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        if shutil.which("explorer.exe"):                  # WSL fallback
            win = subprocess.run(["wslpath", "-w", path], capture_output=True, text=True).stdout.strip()
            subprocess.Popen(["explorer.exe", win], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        if shutil.which("xdg-open"):                      # Linux desktop
            subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        if shutil.which("open"):                          # macOS
            subprocess.Popen(["open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    except Exception as e:
        print(red(f"  (could not open PDF: {e})"))
        return False
    print(yellow("  (no PDF opener found — open the path/URL above manually)"))
    return False


# --------------------------------------------------------------------------- #
# verdict log
# --------------------------------------------------------------------------- #

def load_judged(log: Path) -> dict:
    if not log.exists():
        return {}
    out = {}
    for line in log.read_text().splitlines():
        try:
            r = json.loads(line)
            out[r["key"]] = r
        except Exception:
            continue
    return out


def append_verdict(log: Path, record: dict) -> None:
    with log.open("a") as f:
        f.write(json.dumps(record) + "\n")


VERDICTS = {"c": "correct", "w": "wrong", "p": "partial", "n": "na", "s": "skip"}
# 'na' = not a result statement / no P&L to verify (excluded from accuracy).


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", help="Directory of PDFs (default: fixtures corpus)")
    ap.add_argument("--symbol", help="Only this symbol (fixtures mode)")
    ap.add_argument("--limit", type=int, default=None, help="Max PDFs to check")
    ap.add_argument("--mode", choices=["auto", "vision"], default="auto",
                    help="auto = production path (vision→text fallback); vision = force vision tier")
    ap.add_argument("--no-open", action="store_true", help="Don't open the PDF viewer")
    ap.add_argument("--no-prompt", action="store_true", help="Just extract+print, no verdict (smoke)")
    ap.add_argument("--out", default=str(DEFAULT_LOG), help="Verdict log (jsonl)")
    ap.add_argument("--redo", action="store_true", help="Re-judge already-logged PDFs")
    ap.add_argument("--all-subjects", action="store_true",
                    help="Include non-statement filings too (press releases etc.)")
    args = ap.parse_args()

    log = Path(args.out)
    items = (load_dir_pdfs(args.dir) if args.dir
             else load_fixture_pdfs(args.symbol, all_subjects=args.all_subjects))
    if not items:
        print(red("No PDFs found."))
        return 1

    judged = {} if args.redo else load_judged(log)
    queue = [it for it in items if it["key"] not in judged]
    if args.limit:
        queue = queue[:args.limit]

    print(bold(f"\n{len(items)} result PDFs · {len(judged)} already judged · "
               f"{len(queue)} to check  (mode={args.mode})"))
    if not queue:
        print(green("Nothing left to verify.")); _summary(log); return 0

    tally = {"correct": 0, "wrong": 0, "partial": 0, "na": 0, "skip": 0}
    total_cost = 0.0
    for i, item in enumerate(queue, 1):
        try:
            res = run_extraction(item, args.mode)
        except Exception as e:
            print(red(f"\n[{i}/{len(queue)}] {item['symbol']} — extraction error: {e}"))
            continue
        total_cost += res.llm_cost_usd
        print_result(item, res, i, len(queue))

        if not args.no_open:
            open_pdf(item["path"])

        if args.no_prompt:
            continue

        if not res.fields and not res.consolidated:
            print(yellow("  (no P&L extracted — if this PDF isn't a result "
                         "statement, that's correct: mark [n]/a)"))
        verdict = None
        while verdict is None:
            print(bold("\n  Verify against the PDF → "
                       "[c]orrect  [w]rong  [p]artial  [n]/a  [s]kip  [q]uit"))
            try:
                ans = input("  verdict: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = "q"
            if ans[:1] == "q":
                verdict = "quit"
            elif ans[:1] in VERDICTS:
                verdict = VERDICTS[ans[:1]]
            else:
                print(dim("  (enter one of c / w / p / n / s / q)"))
        if verdict == "quit":
            print("stopping."); break
        note = ""
        if verdict in ("wrong", "partial"):
            try:
                note = input("  note (which field/value is off?): ").strip()
            except (EOFError, KeyboardInterrupt):
                pass
        tally[verdict] += 1
        append_verdict(log, {
            "key": item["key"], "symbol": item["symbol"], "verdict": verdict,
            "note": note, "strategy": res.strategy, "confidence": round(res.confidence, 2),
            "period_ending": res.period_ending,
            "fields": res.fields, "consolidated": res.consolidated,
        })
        print(green("  saved."))

    print(dim(f"\nLLM cost this session: ${total_cost:.4f}"))
    _summary(log)
    return 0


def _summary(log: Path) -> None:
    judged = load_judged(log)
    if not judged:
        return
    tally = {"correct": 0, "wrong": 0, "partial": 0, "na": 0, "skip": 0}
    for r in judged.values():
        v = r.get("verdict", "skip")
        tally[v] = tally.get(v, 0) + 1
    decided = tally["correct"] + tally["wrong"] + tally["partial"]
    print("\n" + bold("ACCURACY SO FAR"))
    print(f"  judged: {len(judged)}   "
          f"{green('correct ' + str(tally['correct']))}  "
          f"{red('wrong ' + str(tally['wrong']))}  "
          f"{yellow('partial ' + str(tally['partial']))}  "
          f"{dim('n/a ' + str(tally['na']))}  "
          f"{dim('skip ' + str(tally['skip']))}")
    if decided:
        acc = tally["correct"] / decided * 100
        lenient = (tally["correct"] + 0.5 * tally["partial"]) / decided * 100
        print(f"  strict accuracy:  {acc:.1f}%   "
              f"(partial-credit: {lenient:.1f}%)   over {decided} decided")
    print(dim(f"  log: {log}"))


if __name__ == "__main__":
    raise SystemExit(main())
