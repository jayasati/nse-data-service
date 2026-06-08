"""
Interactive rating-PDF reviewer (FEATURE_CHECKLIST Phase 5, Week 16, task 16.7).

Steps through parsed rating actions one at a time: prints what the extractor
produced, opens the actual PDF so you can eyeball it, and waits for you before
moving on. Verify action + grades match the document.

    PYTHONPATH=src python scripts/rating_review.py                 # all, newest first
    PYTHONPATH=src python scripts/rating_review.py --action downgrade
    PYTHONPATH=src python scripts/rating_review.py --limit 10

Keys at each prompt:  Enter = next · o = re-open PDF · u = open source URL · q = quit
Opener: auto-detects WSL (explorer.exe) / xdg-open / open; override with --opener.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from nse_data.storage.db import open_db   # noqa: E402


def _open(target: str, opener: str | None) -> None:
    """Open a local file or URL with the platform viewer (best-effort)."""
    is_url = target.startswith("http")
    path = target
    if not is_url:
        p = Path(target)
        if not p.exists():
            print(f"    (file not found: {target})")
            return
        path = str(p.resolve())

    try:
        if opener:
            subprocess.run([opener, path], check=False)
        elif shutil.which("explorer.exe"):                       # WSL
            if is_url:
                subprocess.run(["explorer.exe", path], check=False)
            else:
                win = subprocess.run(["wslpath", "-w", path],
                                     capture_output=True, text=True).stdout.strip()
                subprocess.run(["explorer.exe", win], check=False)
        elif shutil.which("xdg-open"):
            subprocess.run(["xdg-open", path], check=False)
        elif shutil.which("open"):
            subprocess.run(["open", path], check=False)
        else:
            print(f"    open manually: {path}")
    except Exception as e:
        print(f"    couldn't open ({e}): {path}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Review rating PDFs one by one")
    p.add_argument("--db", default="data/nse.db")
    p.add_argument("--action", help="filter to one action (downgrade/upgrade/…)")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--opener", help="override the PDF opener command")
    args = p.parse_args(argv)

    conn = open_db(args.db)
    where = "WHERE r.worst_action = ?" if args.action else ""
    params = ([args.action] if args.action else []) + [args.limit]
    rows = conn.execute(
        "SELECT r.symbol, r.agencies, r.worst_action, r.min_lt_grade, "
        "       r.credit_quality_score, r.is_junk_downgrade, r.n_instruments, "
        "       r.broadcast_dt, a.pdf_path, a.attachment_url, a.subject "
        "FROM raw_rating_actions r "
        "LEFT JOIN raw_announcements a ON a.fingerprint = r.announcement_fingerprint "
        f"{where} ORDER BY r.id DESC LIMIT ?",
        params,
    ).fetchall()
    conn.close()

    if not rows:
        print("No rating actions to review (run scripts/rating_qa.py --extract first).")
        return 0

    print(f"Reviewing {len(rows)} rating action(s). Enter=next · o=reopen · u=url · q=quit\n")
    for i, (sym, agencies, action, grade, score, junk, n, bdt, pdf_path, url, subject) in enumerate(rows, 1):
        junk_tag = "  ⚠JUNK" if junk else ""
        sc = f"  quality {score:.0f}/100" if score is not None else ""
        print("=" * 64)
        print(f"[{i}/{len(rows)}] {sym}  —  {subject}")
        print(f"    parsed:  {agencies or '?'} | {action or '?'} | "
              f"worst grade {grade or '?'}{sc} | {n or 0} instruments{junk_tag}")
        print(f"    filed:   {bdt or '?'}")

        target = pdf_path or url
        if target:
            _open(target, args.opener)
        else:
            print("    (no pdf_path or url on this announcement)")

        while True:
            key = input("    > ").strip().lower()
            if key in ("", "n"):
                break
            if key == "q":
                print("done."); return 0
            if key == "o" and pdf_path:
                _open(pdf_path, args.opener)
            elif key == "u" and url:
                _open(url, args.opener)
            else:
                print("    keys: Enter=next · o=reopen pdf · u=open url · q=quit")
        print()

    print("Reviewed all.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
