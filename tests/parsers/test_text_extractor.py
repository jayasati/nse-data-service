"""Tests for the text extractor.

Uses real pymupdf to make synthetic PDFs, then runs extraction.
"""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from nse_data.parsers.text_extractor import extract_text


def _make_text_pdf(path: Path, text: str, pages: int = 1) -> None:
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 50), text, fontsize=12)
    doc.save(path)
    doc.close()


def _make_empty_pdf(path: Path) -> None:
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    doc.save(path)
    doc.close()


def test_extracts_simple_text(tmp_path: Path):
    pdf = tmp_path / "hello.pdf"
    _make_text_pdf(pdf, "Hello world from quarterly results.")
    result = extract_text(pdf)
    assert result.success
    assert "Hello world" in result.text
    assert result.page_count == 1
    assert result.text_length > 0


def test_multi_page_concatenated(tmp_path: Path):
    pdf = tmp_path / "multi.pdf"
    _make_text_pdf(pdf, "Quarterly revenue 1234 crore.", pages=3)
    result = extract_text(pdf)
    assert result.success
    assert result.page_count == 3
    # Same text repeated 3 times
    assert result.text.count("Quarterly revenue") == 3


def test_missing_file_returns_error(tmp_path: Path):
    result = extract_text(tmp_path / "does-not-exist.pdf")
    assert not result.success
    assert "file_not_found" in result.error


def test_empty_pdf_returns_no_text_layer(tmp_path: Path):
    pdf = tmp_path / "empty.pdf"
    _make_empty_pdf(pdf)
    result = extract_text(pdf)
    assert not result.success
    assert result.error == "no_text_layer"
    assert result.page_count == 1   # page count still set