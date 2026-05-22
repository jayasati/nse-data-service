"""Map an announcement subject to a priority bucket.

Reads config/priority.yaml. Pure function — no I/O at call time once the
config is loaded.

Priority semantics:
  high    — extract financial numbers, archive PDF forever, full pipeline
  medium  — extract text + sentiment, keep PDF for 30 days
  low     — extract text only, discard PDF immediately after
  skip    — don't even download the PDF
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml

LOG = logging.getLogger(__name__)

VALID_PRIORITIES = frozenset({"high", "medium", "low", "skip"})
DEFAULT_PRIORITY = "medium"


@lru_cache(maxsize=1)
def _load_priority_map(config_path: str = "config/priority.yaml") -> dict[str, str]:
    """Build subject -> priority dict from the YAML config.

    Cached for the process lifetime. To reload after editing the config,
    call _load_priority_map.cache_clear().
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Priority config missing: {path}")

    with path.open() as f:
        cfg = yaml.safe_load(f)

    out: dict[str, str] = {}
    for priority, subjects in cfg.get("announcement_priority", {}).items():
        if priority not in VALID_PRIORITIES:
            LOG.warning("ignoring unknown priority bucket: %r", priority)
            continue
        for subject in subjects or []:
            if subject in out:
                LOG.warning(
                    "subject %r in multiple buckets; %r overrides %r",
                    subject, priority, out[subject],
                )
            out[subject] = priority

    LOG.info("loaded %d subject->priority mappings", len(out))
    return out


# Track unknown subjects per process so we don't log the same one repeatedly.
_unknown_subjects_seen: set[str] = set()


def classify_subject(subject: str | None) -> str:
    """Return priority bucket for an announcement subject.

    Unknown subjects default to 'medium' and are logged once per process
    so they can be reviewed and added to config/priority.yaml.
    """
    if subject is None or not subject.strip():
        return DEFAULT_PRIORITY

    priority_map = _load_priority_map()
    subject = subject.strip()

    if subject in priority_map:
        return priority_map[subject]

    if subject not in _unknown_subjects_seen:
        _unknown_subjects_seen.add(subject)
        LOG.warning(
            "unknown subject defaulting to %r: %r", DEFAULT_PRIORITY, subject,
        )
    return DEFAULT_PRIORITY


def get_unknown_subjects() -> set[str]:
    """Return all subjects this process has classified as 'unknown'."""
    return frozenset(_unknown_subjects_seen)


def reset_unknown_tracking() -> None:
    """For tests: clear the in-memory unknown-subjects set."""
    _unknown_subjects_seen.clear()