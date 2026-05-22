"""PDF classifier — routes incoming PDFs to one of four pipelines.

Deterministic, heuristic, fast. Opens a PDF with pymupdf, samples up to the
first N pages, computes five features, and returns a classification result.

Designed to run in <500ms on typical PDFs. Larger documents may take up to
the configured timeout (default 2s) but still classify successfully.

The classifier is intentionally not learned. Heuristics are debuggable,
deterministic, and don't require labeled training data at scale. If
accuracy plateaus below target, add features rather than switching to ML.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz  # pymupdf

# --- Configuration (defaults; can be overridden by config/pdf_classifier.yaml)

DEFAULT_SAMPLE_PAGES = 3
DEFAULT_THRESHOLDS = {
    "scanned_chars_max": 100,
    "scanned_word_validity_min": 0.5,
    "presentation_aspect_min": 1.3,
    "hybrid_image_ratio_min": 0.2,
    "hybrid_image_ratio_max": 0.7,
    "hybrid_chars_min": 500,
}

# Conservative "looks like an English/financial word" check.
# Allows: letters, hyphens, apostrophes. Length 2+. Catches OCR garbage
# like "rn4@p!" or single-char noise that scanned PDFs often produce.
_WORD_PATTERN = re.compile(r"^[A-Za-z][A-Za-z\-']{1,}$")

# Tokens we don't count in word_validity. Numbers and currency are fine
# in financial docs but skew the validity ratio if treated as "non-words."
_SKIP_TOKEN_PATTERN = re.compile(
    r"^("
    r"\d+([.,]\d+)*"            # 1234, 1,234.56
    r"|[₹$€£¥%]"                # currency symbols
    r"|[+\-]?\d+(\.\d+)?[KMBTkmbt]?"  # numeric with suffixes
    r"|[(){}[\]<>,;:.!?\"'/\\|@#&*=]+"  # punctuation runs
    r")$"
)


@dataclass
class ClassificationResult:
    """Outcome of running the classifier on one PDF."""

    pdf_type: str                   # native_text | presentation | scanned | hybrid
    page_count: int
    file_size_bytes: int
    features: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0         # heuristic confidence; 1.0 for clear cases
    classified_at: int = 0          # unix seconds
    duration_ms: int = 0
    error: str | None = None        # set if classification fell back

    def is_extractable(self) -> bool:
        """Whether deterministic text extraction will likely work."""
        return self.pdf_type in ("native_text", "presentation", "hybrid")


def classify_pdf(
    path: Path | str,
    *,
    sample_pages: int = DEFAULT_SAMPLE_PAGES,
    thresholds: dict[str, Any] | None = None,
    max_seconds: float = 2.0,
) -> ClassificationResult:
    """Classify a PDF into one of four types.

    Args:
        path: Path to the PDF file.
        sample_pages: How many pages from the start to sample for features.
        thresholds: Override default classification thresholds.
        max_seconds: Soft cap on runtime. Logged but not enforced as hard kill.

    Returns:
        ClassificationResult with type, features, and timing.

    Raises:
        FileNotFoundError: If path doesn't exist.
        Does NOT raise on parse failures — returns a fallback result with
        pdf_type="scanned" and an error string.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    started = time.perf_counter()
    classified_at = int(time.time())
    file_size = path.stat().st_size

    try:
        doc = fitz.open(path)
    except Exception as e:
        # Corrupt or unreadable. Treat as scanned so downstream OCR pipeline
        # gets a chance (it has its own robustness).
        return ClassificationResult(
            pdf_type="scanned",
            page_count=0,
            file_size_bytes=file_size,
            features={},
            confidence=0.3,
            classified_at=classified_at,
            duration_ms=int((time.perf_counter() - started) * 1000),
            error=f"pymupdf_open_failed: {e!r}",
        )

    try:
        features = _compute_features(doc, sample_pages)
        pdf_type, confidence = _classify_from_features(features, thresholds)
        result = ClassificationResult(
            pdf_type=pdf_type,
            page_count=doc.page_count,
            file_size_bytes=file_size,
            features=features,
            confidence=confidence,
            classified_at=classified_at,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    finally:
        doc.close()

    if result.duration_ms > max_seconds * 1000:
        # Soft warning — bake into features dict for ops visibility.
        result.features["_warning"] = (
            f"classification took {result.duration_ms}ms "
            f"(threshold {int(max_seconds * 1000)}ms)"
        )

    return result


def _compute_features(doc: fitz.Document, sample_pages: int) -> dict[str, Any]:
    """Extract the five classification features from sampled pages."""
    n_sample = min(sample_pages, doc.page_count)
    if n_sample == 0:
        return {
            "avg_chars_per_page": 0.0,
            "image_area_ratio": 0.0,
            "aspect_ratio": 1.0,
            "word_validity": 0.0,
            "font_diversity": 0,
            "sampled_pages": 0,
        }

    total_chars = 0
    total_image_area = 0.0
    total_page_area = 0.0
    fonts: set[str] = set()
    all_tokens: list[str] = []
    first_page_aspect = 1.0
    substantial_image_count = 0

    for i in range(n_sample):
        page = doc[i]
        rect = page.rect
        page_area = rect.width * rect.height
        total_page_area += page_area

        if i == 0 and rect.height > 0:
            first_page_aspect = rect.width / rect.height

        # Text length
        text = page.get_text()
        total_chars += len(text)

        # Tokens for word validity
        if text.strip():
            all_tokens.extend(text.split())

        # Image area — sum bounding box areas of all images on the page

        # Also track count of "substantial" images (not tiny logos/signatures).
        for img_info in page.get_image_info():
            bbox = img_info.get("bbox")
            if bbox:
                w = bbox[2] - bbox[0]
                h = bbox[3] - bbox[1]
                area = max(0.0, w * h)
                total_image_area += area
                # An image counts as "substantial" if it's at least 10% of
                # the page area. Filters out logos, signature stamps, icons.
                if page_area > 0 and area / page_area > 0.10:
                    substantial_image_count += 1

        # Fonts in use
        for font in page.get_fonts():
            # get_fonts() returns tuples; font name is element 3
            try:
                fonts.add(font[3])
            except (IndexError, TypeError):
                pass

    avg_chars = total_chars / n_sample
    image_ratio = (
        total_image_area / total_page_area if total_page_area > 0 else 0.0
    )
    # Cap at 1.0 — overlapping images can sum past page area
    image_ratio = min(image_ratio, 1.0)
    word_validity = _word_validity_score(all_tokens)

    return {
        "avg_chars_per_page": round(avg_chars, 1),
        "image_area_ratio": round(image_ratio, 3),
        "aspect_ratio": round(first_page_aspect, 3),
        "word_validity": round(word_validity, 3),
        "font_diversity": len(fonts),
        "substantial_image_count": substantial_image_count,
        "sampled_pages": n_sample,
    }


def _word_validity_score(tokens: list[str]) -> float:
    """Fraction of tokens that look like real English/financial words.

    Numbers, currency, and punctuation tokens are excluded from the
    denominator — they're legitimate content in financial PDFs and
    shouldn't be counted as "not words."

    Returns 1.0 for empty input (avoids false-positive "scanned" verdict
    on otherwise-empty PDFs that are also short).
    """
    if not tokens:
        return 1.0

    countable = 0
    valid = 0
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        if _SKIP_TOKEN_PATTERN.match(tok):
            continue
        countable += 1
        if _WORD_PATTERN.match(tok):
            valid += 1

    if countable == 0:
        # All tokens were numbers/punctuation. Common in numeric-heavy
        # tables. Don't penalize as scanned.
        return 1.0
    return valid / countable


def _classify_from_features(
    features: dict[str, Any], thresholds: dict[str, Any]
) -> tuple[str, float]:
    """Apply classification logic to feature values.

    Order matters: scanned check first (catches no-text PDFs before any
    other heuristic), then presentation (aspect-based, definitive), then
    hybrid (text + heavy images), then native_text as the default.

    Returns (pdf_type, confidence). Confidence is heuristic:
      - 1.0: features land clearly on one side of all thresholds
      - 0.7: borderline on one feature
      - 0.5: borderline on multiple features
    """
    chars = features["avg_chars_per_page"]
    img_ratio = features["image_area_ratio"]
    aspect = features["aspect_ratio"]
    validity = features["word_validity"]

    # Scanned: little/no text, OR text looks like OCR garbage
    if chars < thresholds["scanned_chars_max"]:
        return "scanned", 1.0
    if validity < thresholds["scanned_word_validity_min"]:
        # Has text but it's garbage. Likely a pre-OCR'd PDF with bad output.
        return "scanned", 0.8

    # Presentation: landscape orientation
    if aspect > thresholds["presentation_aspect_min"]:
        # High aspect + high image area = definitely a deck
        if img_ratio > thresholds["hybrid_image_ratio_min"]:
            return "presentation", 1.0
        # Landscape but mostly text — still treat as presentation
        return "presentation", 0.7

    # Hybrid: portrait, has text, but image-heavy
    substantial_images = features.get("substantial_image_count", 0)
    if (
        thresholds["hybrid_image_ratio_min"]
        < img_ratio
        < thresholds["hybrid_image_ratio_max"]
        and chars > thresholds["hybrid_chars_min"]
        and substantial_images >= 1
    ):
        return "hybrid", 0.9

    # Default: text-dominant portrait PDF
    confidence = 1.0
    # Lower confidence if we're close to a boundary
    if chars < thresholds["hybrid_chars_min"]:
        confidence = 0.7
    if img_ratio > thresholds["hybrid_image_ratio_min"]:
        confidence = min(confidence, 0.7)

    return "native_text", confidence