"""Phase 3+ — financial number extractors.

Each strategy module exposes a `extract(pdf_path, financial_aliases)` function
returning a partial result dict + confidence. The ensemble combines them.
"""

__all__ = []  # populated as strategies land