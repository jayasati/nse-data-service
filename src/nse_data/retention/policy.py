"""Decide what to do with a downloaded PDF, based on priority.

Pure function — no file I/O. The decision is returned as a small data
class; the caller (retention/archive.py) actually moves bytes around.

Why pure: retention decisions are deterministic from priority. Separating
the decision from the I/O makes both trivially testable and lets us run
'what would happen' simulations (the --dry-run mode of backfill).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml


@dataclass(frozen=True)
class RetentionDecision:
    """Outcome of policy evaluation for one PDF.

    Fields:
        action:
            'archive_permanent' - keep PDF forever in pdfs/<YYYY>/<MM>/<DD>/
            'archive_temp_30d'  - keep PDF in pdfs_temp/ for 30 days
            'discard'           - don't write the PDF (text is still retained)
            'do_not_download'   - priority='skip', don't even fetch

        archive_path:
            Absolute target path for the PDF file, or None if not archived.

        retention_policy_label:
            Short string written to raw_announcements.retention_policy.
            Helps the cleanup job and ops queries identify what was applied.
    """

    action: str
    archive_path: Optional[Path]
    retention_policy_label: str

    def will_write_file(self) -> bool:
        return self.action in ("archive_permanent", "archive_temp_30d")


@lru_cache(maxsize=1)
def _load_retention_config(
    config_path: str = "config/retention.yaml",
) -> dict:
    path = Path(config_path)
    with path.open() as f:
        return yaml.safe_load(f)


def decide_retention(
    priority: str,
    fingerprint: str,
    broadcast_dt: str,
    archive_root: Path,
) -> RetentionDecision:
    """Return the retention decision for one PDF.

    Args:
        priority: 'high' | 'medium' | 'low' | 'skip'
        fingerprint: announcement fingerprint, used in the filename
        broadcast_dt: NSE-format date string, e.g. '21-May-2026 19:40:44'
                      Used to bucket archives by year/month/day.
        archive_root: base directory for archives (data/archive)
    """
    cfg = _load_retention_config()["pdf_retention"].get(priority, {})

    if priority == "skip" or cfg.get("parse") is False:
        return RetentionDecision(
            action="do_not_download",
            archive_path=None,
            retention_policy_label="skip",
        )

    if cfg.get("keep_pdf") == "forever":
        date_path = _date_subpath(broadcast_dt)
        target = (
            archive_root
            / cfg["archive_subdir"]
            / date_path
            / f"{fingerprint}.pdf"
        )
        return RetentionDecision(
            action="archive_permanent",
            archive_path=target,
            retention_policy_label="high_forever",
        )

    if cfg.get("keep_pdf") == "30d":
        target = (
            archive_root
            / cfg["archive_subdir"]
            / f"{fingerprint}.pdf"
        )
        return RetentionDecision(
            action="archive_temp_30d",
            archive_path=target,
            retention_policy_label="medium_30d",
        )

    # keep_pdf is false or unset — extract text then discard PDF
    return RetentionDecision(
        action="discard",
        archive_path=None,
        retention_policy_label="low_textonly",
    )


def _date_subpath(broadcast_dt: str) -> Path:
    """Turn NSE broadcast_dt into a YYYY/MM/DD subpath. Fallback safely."""
    try:
        dt = datetime.strptime(broadcast_dt.split()[0], "%d-%b-%Y")
        return Path(dt.strftime("%Y")) / dt.strftime("%m") / dt.strftime("%d")
    except (ValueError, IndexError, AttributeError):
        return Path("unknown_date")