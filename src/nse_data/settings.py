"""Configuration loaders. YAML files under config/ are read through here."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_endpoints(path: str | Path = "config/endpoints.yaml") -> dict[str, dict]:
    """Load and return the `endpoints` mapping from config/endpoints.yaml."""
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
    endpoints = cfg.get("endpoints", {})
    if not isinstance(endpoints, dict):
        raise ValueError(
            f"endpoints.yaml: top-level 'endpoints' must be a mapping, got {type(endpoints).__name__}"
        )
    return endpoints