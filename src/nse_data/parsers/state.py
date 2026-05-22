"""Canonical pdf_status vocabulary for the parser pipeline.

Every value that gets written to raw_announcements.pdf_status MUST come
from this module. This prevents typo-driven status drift (the kind of bug
that's invisible until you query and notice ten rows in 'extarcted' state).
"""

from __future__ import annotations


class State:
    """All valid values of raw_announcements.pdf_status.

    Initial state:
      PENDING                 — row created, parser hasn't touched it yet

    Routing decisions:
      CLASSIFIED              — priority set, ready for PDF work
      SKIPPED_LOW_PRIORITY    — priority='skip', no further processing

    Download outcomes:
      DOWNLOADING             — transient, briefly between fetch start and finish
      DOWNLOAD_FAILED         — fetch failed; can be retried

    Post-download:
      TEXT_EXTRACTED          — pdf_text populated, archive written per policy
      TEXT_EXTRACTION_FAILED  — pdf on disk but couldn't extract text
      OCR_REQUIRED            — pdf_type='scanned' and OCR pipeline not built

    Phase 3+ states (declared here so they exist, used later):
      EXTRACTED               — financial numbers extracted, conf >= 0.75
      EXTRACTED_LOW_CONF      — financial numbers extracted, 0.6 <= conf < 0.75
      EXTRACTED_VIA_VISION    — deterministic pipelines failed, vision succeeded
      EXTRACTION_FAILED       — all extraction strategies below threshold
      FLAGGED_FOR_REVIEW      — high-priority + extraction failed
      DISCARDED               — PDF deleted per retention cleanup
    """

    # Initial
    PENDING = "pending"

    # Routing
    CLASSIFIED = "classified"
    SKIPPED_LOW_PRIORITY = "skipped_low_priority"

    # Download
    DOWNLOADING = "downloading"
    DOWNLOAD_FAILED = "download_failed"

    # Text
    TEXT_EXTRACTED = "text_extracted"
    TEXT_EXTRACTION_FAILED = "text_extraction_failed"
    OCR_REQUIRED = "ocr_required"

    # Extraction (Phase 3+)
    EXTRACTED = "extracted"
    EXTRACTED_LOW_CONF = "extracted_low_conf"
    EXTRACTED_VIA_VISION = "extracted_via_vision"
    EXTRACTION_FAILED = "extraction_failed"
    FLAGGED_FOR_REVIEW = "flagged_for_review"

    # Lifecycle
    DISCARDED = "discarded"


# States considered "terminal" for backfill purposes — the parser does not
# pick these up again.
TERMINAL_STATES = frozenset({
    State.SKIPPED_LOW_PRIORITY,
    State.DOWNLOAD_FAILED,
    State.TEXT_EXTRACTED,
    State.TEXT_EXTRACTION_FAILED,
    State.OCR_REQUIRED,
    State.EXTRACTED,
    State.EXTRACTED_LOW_CONF,
    State.EXTRACTED_VIA_VISION,
    State.EXTRACTION_FAILED,
    State.FLAGGED_FOR_REVIEW,
    State.DISCARDED,
})


# States from which the parser will re-attempt work
RETRYABLE_STATES = frozenset({
    State.PENDING,
    State.CLASSIFIED,
    State.DOWNLOAD_FAILED,  # opt-in via flag — retries are expensive
})