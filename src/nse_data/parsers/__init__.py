"""Layer 3 — PDF parsers, classifiers, and financial extractors.

Public surface is intentionally small. Most modules are internal and
should be imported via their own paths, not re-exported here.
"""

from nse_data.parsers.pdf_classifier import classify_pdf, ClassificationResult

__all__ = ["classify_pdf", "ClassificationResult"]