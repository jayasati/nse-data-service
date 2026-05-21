"""Shared helpers for parsers/. Pure functions only."""
from __future__ import annotations

import re


def safe_filename(s: str, max_len: int = 80) -> str:
    """Make a string safe for use as a filename segment."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:max_len] if len(s) > max_len else s