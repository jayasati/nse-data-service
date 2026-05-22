"""Tests for the PDF downloader.

Uses a fake SessionManager — we don't make live NSE calls in unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from nse_data.parsers.pdf_downloader import download_pdf, MAX_PDF_SIZE_BYTES


@dataclass
class FakeSession:
    """Minimal SessionManager stand-in for testing."""

    response: Optional[bytes] = None
    raise_error: Optional[Exception] = None

    def get_bytes(self, endpoint_name, url, referer=None):
        if self.raise_error:
            raise self.raise_error
        return self.response


def _fake_pdf(payload: bytes = b"fake pdf content") -> bytes:
    """Build minimal valid-looking PDF bytes."""
    return b"%PDF-1.4\n" + payload + b"\n%%EOF"


def test_successful_download():
    pdf_bytes = _fake_pdf()
    session = FakeSession(response=pdf_bytes)

    result = download_pdf(session, "https://example.com/a.pdf")

    assert result.success
    assert result.data == pdf_bytes
    assert result.sha256 is not None
    assert result.size_bytes == len(pdf_bytes)
    assert result.error is None


def test_empty_url_fails():
    session = FakeSession()
    assert not download_pdf(session, "").success
    assert not download_pdf(session, "   ").success


def test_empty_response_fails():
    session = FakeSession(response=b"")
    result = download_pdf(session, "https://example.com/a.pdf")
    assert not result.success
    assert "empty" in result.error


def test_none_response_fails():
    session = FakeSession(response=None)
    result = download_pdf(session, "https://example.com/a.pdf")
    assert not result.success


def test_html_disguised_as_pdf_fails():
    session = FakeSession(response=b"<!DOCTYPE html><html>error page</html>")
    result = download_pdf(session, "https://example.com/a.pdf")
    assert not result.success
    assert "not_a_pdf" in result.error


def test_oversized_pdf_fails():
    huge = b"%PDF" + b"x" * (MAX_PDF_SIZE_BYTES + 1)
    session = FakeSession(response=huge)
    result = download_pdf(session, "https://example.com/a.pdf")
    assert not result.success
    assert "too_large" in result.error


def test_network_exception_propagates_as_error():
    session = FakeSession(raise_error=RuntimeError("connection refused"))
    result = download_pdf(session, "https://example.com/a.pdf")
    assert not result.success
    assert "fetch_failed" in result.error


def test_sha256_stable():
    pdf = _fake_pdf(b"same content")
    s1 = FakeSession(response=pdf)
    s2 = FakeSession(response=pdf)
    assert download_pdf(s1, "x").sha256 == download_pdf(s2, "x").sha256