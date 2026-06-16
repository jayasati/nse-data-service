"""Probe how far back NSE serves corporate announcements (feasibility for backfill).

The live collector polls /api/corporate-announcements with no date params (recent
feed only) — which is why raw_announcements only has 2026. NSE's endpoint also
takes from_date/to_date (dd-mm-yyyy). This probes a series of historical windows
to learn: (a) does the archive query work at all, (b) how far back is served,
(c) the max per-request span. Read-only — fetches, counts, prints; writes nothing.

    PYTHONPATH=src .venv/bin/python -u scripts/probe_announcement_archive.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

REFERER = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"


def _span(rows):
    """(min, max) broadcast date string across rows, for a quick depth check."""
    dts = [(_d(r)) for r in rows if isinstance(r, dict)]
    dts = [d for d in dts if d]
    return (min(dts), max(dts)) if dts else (None, None)


def _d(r):
    return (r.get("an_dt") or r.get("sort_date") or r.get("exchdisstime") or "").strip()


def main() -> int:
    from nse_data.session.manager import SessionManager
    s = SessionManager()

    # (label, from_date, to_date) — recent sanity, then progressively older + a
    # wide span to test the per-request cap.
    windows = [
        ("recent ~10d",   None,          None),
        ("2026-04 (1mo)", "01-04-2026", "30-04-2026"),
        ("2025-06 (1mo)", "01-06-2025", "30-06-2025"),
        ("2024-06 (1mo)", "01-06-2024", "30-06-2024"),
        ("2023-06 (1mo)", "01-06-2023", "30-06-2023"),
        ("2022-06 (1mo)", "01-06-2022", "30-06-2022"),
        ("2024 full year", "01-01-2024", "31-12-2024"),
        ("2024-Q2 (3mo)", "01-04-2024", "30-06-2024"),
    ]

    print(f"{'window':<18}{'rows':>7}  date span returned")
    print("-" * 64)
    for label, fd, td in windows:
        params = {"index": "equities"}
        if fd:
            params["from_date"], params["to_date"] = fd, td
        try:
            data = s.get_json("announcements", "/api/corporate-announcements",
                              referer=REFERER, params=params)
            if isinstance(data, dict):
                data = data.get("data") or data.get("rows") or []
            n = len(data) if isinstance(data, list) else 0
            lo, hi = _span(data) if isinstance(data, list) else (None, None)
            print(f"{label:<18}{n:>7}  {lo}  →  {hi}")
        except Exception as e:  # noqa: BLE001
            print(f"{label:<18}{'ERR':>7}  {type(e).__name__}: {str(e)[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
