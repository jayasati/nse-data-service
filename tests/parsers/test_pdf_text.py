"""Tests for parsers.pdf_text — extraction + scanned-PDF detection (16.4)."""

from __future__ import annotations

import fitz   # pymupdf, available in the env

from nse_data.parsers.pdf_text import extract_text


def _pdf(pages_text: list[str]) -> bytes:
    doc = fitz.open()
    for txt in pages_text:
        page = doc.new_page()
        if txt:
            page.insert_text((72, 72), txt)
    return doc.tobytes()


def test_extracts_text_pdf():
    body = "CRISIL has downgraded the rating to BB from BBB for the issuer."
    text, err = extract_text(_pdf([body]))
    assert err is None
    assert "downgraded" in text


def test_scanned_multipage_flagged_ocr_required():
    # two empty pages -> multi-page with < 100 chars -> ocr_required
    text, err = extract_text(_pdf(["", ""]))
    assert err == "ocr_required" and text == ""


def test_empty_bytes():
    assert extract_text(b"") == ("", "extract_failed")
