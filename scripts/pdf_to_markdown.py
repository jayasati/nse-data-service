"""Convert a PDF to a Markdown file using pymupdf4llm.

Layout-aware: preserves headings, lists, and tables where the source PDF
has a real text layer. Falls back to whatever PyMuPDF can pull from
scanned pages (which is usually nothing — those need OCR).

Usage:
  PYTHONPATH=src python scripts/pdf_to_markdown.py <pdf_path> [--out <md_path>]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymupdf4llm


def main(args: argparse.Namespace) -> int:
    pdf_path = Path(args.pdf).resolve()
    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path}", file=sys.stderr)
        return 1

    out_path = Path(args.out).resolve() if args.out else pdf_path.with_suffix(".md")

    print(f"Reading: {pdf_path}")
    md = pymupdf4llm.to_markdown(str(pdf_path), page_chunks=False)
    assert isinstance(md, str)

    out_path.write_text(md, encoding="utf-8")
    print(f"Wrote {len(md):,} chars to {out_path}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", help="Path to the input PDF")
    parser.add_argument("--out", help="Output .md path (default: same name as PDF)")
    sys.exit(main(parser.parse_args()))
