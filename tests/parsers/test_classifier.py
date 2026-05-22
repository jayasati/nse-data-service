"""Tests for the PDF classifier.

Two layers:
  1. Synthetic-PDF unit tests (always run; no external fixtures needed)
  2. Labeled-fixture accuracy test (skipped if labels.yaml is empty)

Acceptance criteria for Phase 1:
  - Overall accuracy on labeled fixtures >= 90%
  - Per-type recall >= 80% (no type systematically misclassified)
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

import fitz
import pytest
import yaml

from nse_data.parsers.pdf_classifier import classify_pdf

LABELS_PATH = Path("tests/parsers/fixtures/labels.yaml")
FIXTURE_DIR = Path("tests/parsers/fixtures/pdfs")


# ---------------------------------------------------------------------------
# Synthetic PDF generation helpers
# ---------------------------------------------------------------------------

def _make_native_text_pdf(out_path: Path) -> None:
    """Portrait, text-heavy, no images."""
    doc = fitz.open()
    for _ in range(3):
        page = doc.new_page(width=595, height=842)   # A4 portrait
        text = (
            "Outcome of Board Meeting. The Board of Directors approved "
            "the audited financial results for the quarter ended March 2026. "
            "Revenue from operations was Rs 1,234.56 crore, an increase of "
            "12.5% year-on-year. Profit after tax stood at Rs 234.10 crore. "
            "Earnings per share was Rs 5.67. The Board also recommended a "
            "final dividend of Rs 2.50 per equity share. "
        ) * 8
        page.insert_text((50, 50), text, fontsize=10)
    doc.save(out_path)
    doc.close()


def _make_presentation_pdf(out_path: Path) -> None:
    """Landscape 16:9, mix of text blocks and real embedded images."""
    doc = fitz.open()
    # Build a small PNG in memory to embed (so get_image_info() picks it up)
    img_doc = fitz.open()
    img_page = img_doc.new_page(width=400, height=300)
    img_page.draw_rect(img_page.rect, color=(0.7, 0.7, 0.9),
                       fill=(0.7, 0.7, 0.9))
    pix = img_page.get_pixmap()
    img_bytes = pix.tobytes("png")
    img_doc.close()

    for _ in range(3):
        page = doc.new_page(width=960, height=540)   # 16:9 landscape
        # Realistic slide content — enough text to clear scanned threshold,
        # but laid out as discrete blocks like a real deck
        page.insert_text((50, 50),
                         "Quarterly Performance Review FY26",
                         fontsize=22)
        page.insert_text((50, 120),
                         "Revenue from operations grew sharply across "
                         "all business segments during the quarter, "
                         "driven by strong domestic demand and improved "
                         "operational efficiency.",
                         fontsize=12)
        page.insert_text((50, 220), "Revenue: Rs 12,345 Crore",
                         fontsize=16)
        page.insert_text((50, 260), "EBITDA Margin expanded to 24.5%",
                         fontsize=16)
        page.insert_text((50, 300),
                         "Profit after tax stood at Rs 1,890 Crore, "
                         "an increase of 18% year on year.",
                         fontsize=12)
        page.insert_text((50, 360),
                         "Outlook remains positive for the next quarter "
                         "with strong order book visibility.",
                         fontsize=12)
        # Embed a real image (clears 30% of slide area, gets counted)
        page.insert_image(fitz.Rect(550, 100, 900, 450),
                          stream=img_bytes)
    doc.save(out_path)
    doc.close()


def _make_hybrid_pdf(out_path: Path) -> None:
    """Portrait, dense text + real embedded image (chart-like)."""
    doc = fitz.open()
    # Build an embeddable image
    img_doc = fitz.open()
    img_page = img_doc.new_page(width=400, height=200)
    img_page.draw_rect(img_page.rect, color=(0.6, 0.8, 0.6),
                       fill=(0.6, 0.8, 0.6))
    pix = img_page.get_pixmap()
    img_bytes = pix.tobytes("png")
    img_doc.close()

    for _ in range(3):
        page = doc.new_page(width=595, height=842)   # A4 portrait
        text = (
            "Quarterly results summary. Total income from operations grew "
            "to Rs 5,432 crore in the current quarter versus Rs 4,876 "
            "crore in the corresponding quarter of the previous year. "
            "Profit before tax was Rs 876 crore. The Company continues "
            "to see strong demand across its core operating segments. "
            "Earnings per share for the quarter was Rs 12.45 against "
            "Rs 10.23 in the same quarter last year. The Board has "
            "approved an interim dividend of Rs 5 per equity share. "
        ) * 3
        page.insert_text((50, 50), text, fontsize=10)
        # Embed a real image covering ~30% of page area
        page.insert_image(fitz.Rect(50, 500, 545, 800),
                          stream=img_bytes)
    doc.save(out_path)
    doc.close()

def _make_scanned_pdf(out_path: Path) -> None:
    """Portrait, no text layer — pure image pages."""
    doc = fitz.open()
    for _ in range(2):
        page = doc.new_page(width=595, height=842)
        # Fill page with a grey rectangle, no text
        page.draw_rect(page.rect, color=(0.8, 0.8, 0.8), fill=(0.8, 0.8, 0.8))
    doc.save(out_path)
    doc.close()





# ---------------------------------------------------------------------------
# Unit tests on synthetic PDFs
# ---------------------------------------------------------------------------

@pytest.fixture
def synthetic_pdfs(tmp_path: Path) -> dict[str, Path]:
    """Build one PDF of each archetype in a temp dir."""
    paths = {
        "native_text": tmp_path / "native.pdf",
        "presentation": tmp_path / "deck.pdf",
        "scanned": tmp_path / "scanned.pdf",
        "hybrid": tmp_path / "hybrid.pdf",
    }
    _make_native_text_pdf(paths["native_text"])
    _make_presentation_pdf(paths["presentation"])
    _make_scanned_pdf(paths["scanned"])
    _make_hybrid_pdf(paths["hybrid"])
    return paths


def test_classifier_returns_result(synthetic_pdfs):
    """Classifier returns a ClassificationResult, doesn't crash."""
    result = classify_pdf(synthetic_pdfs["native_text"])
    assert result.pdf_type in {"native_text", "presentation", "scanned", "hybrid"}
    assert result.page_count > 0
    assert result.file_size_bytes > 0
    assert "avg_chars_per_page" in result.features
    assert result.duration_ms >= 0


def test_classifier_identifies_native_text(synthetic_pdfs):
    result = classify_pdf(synthetic_pdfs["native_text"])
    assert result.pdf_type == "native_text", (
        f"Expected native_text, got {result.pdf_type}. "
        f"Features: {result.features}"
    )


def test_classifier_identifies_presentation(synthetic_pdfs):
    result = classify_pdf(synthetic_pdfs["presentation"])
    assert result.pdf_type == "presentation", (
        f"Expected presentation, got {result.pdf_type}. "
        f"Features: {result.features}"
    )


def test_classifier_identifies_scanned(synthetic_pdfs):
    result = classify_pdf(synthetic_pdfs["scanned"])
    assert result.pdf_type == "scanned", (
        f"Expected scanned, got {result.pdf_type}. "
        f"Features: {result.features}"
    )


def test_classifier_handles_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        classify_pdf(tmp_path / "does-not-exist.pdf")


def test_classifier_performance_native(synthetic_pdfs):
    """Classification should be fast on small native PDFs."""
    result = classify_pdf(synthetic_pdfs["native_text"])
    assert result.duration_ms < 500, (
        f"Classification took {result.duration_ms}ms; target <500ms"
    )


# ---------------------------------------------------------------------------
# Accuracy test against hand-labeled fixtures
# ---------------------------------------------------------------------------

def _load_labels() -> dict:
    if not LABELS_PATH.exists():
        return {}
    with LABELS_PATH.open() as f:
        return yaml.safe_load(f) or {}


@pytest.mark.skipif(
    not LABELS_PATH.exists() or not _load_labels(),
    reason="No labels.yaml — run scripts/label_fixtures.py first",
)
def test_classifier_accuracy_on_labeled_fixtures():
    """Classifier hits >=90% accuracy and >=80% per-type recall."""
    labels = _load_labels()
    assert len(labels) >= 30, (
        f"Need >=30 labeled fixtures for the accuracy test; have {len(labels)}"
    )

    correct = 0
    confusion: dict[tuple[str, str], int] = defaultdict(int)
    per_type_total: Counter = Counter()
    per_type_correct: Counter = Counter()

    for fixture_key, label_data in labels.items():
        pdf_path = FIXTURE_DIR / f"{fixture_key}.pdf"
        if not pdf_path.exists():
            pytest.fail(f"Labeled fixture missing on disk: {pdf_path}")

        truth = label_data["pdf_type"]
        result = classify_pdf(pdf_path)
        predicted = result.pdf_type

        per_type_total[truth] += 1
        confusion[(truth, predicted)] += 1
        if predicted == truth:
            correct += 1
            per_type_correct[truth] += 1

    total = len(labels)
    accuracy = correct / total

    # Per-type recall
    recall = {
        t: per_type_correct[t] / per_type_total[t]
        for t in per_type_total
    }

    # Build a nice diagnostic if it fails
    diag_lines = [
        f"Overall accuracy: {correct}/{total} = {accuracy:.1%}",
        f"Per-type recall:",
    ]
    for t, r in sorted(recall.items()):
        diag_lines.append(
            f"  {t:15s} : {per_type_correct[t]}/{per_type_total[t]} "
            f"= {r:.1%}"
        )
    diag_lines.append("Confusion matrix (truth -> predicted):")
    for (truth, pred), n in sorted(confusion.items()):
        marker = "OK" if truth == pred else "MISS"
        diag_lines.append(f"  [{marker}] {truth:15s} -> {pred:15s} : {n}")
    diagnostic = "\n".join(diag_lines)

    print("\n" + diagnostic)

    assert accuracy >= 0.90, (
        f"Accuracy {accuracy:.1%} below 90% target.\n{diagnostic}"
    )
    for t, r in recall.items():
        assert r >= 0.80, (
            f"Recall on {t} = {r:.1%} below 80% target.\n{diagnostic}"
        )