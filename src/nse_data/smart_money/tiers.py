"""Institution tiering for block/bulk deals (FEATURE_CHECKLIST Week 24, task 24.1).

Classify a deal's `client_name` against `config/institution_database.yaml` into tier1 / tier2
/ None (retail/unknown), case-insensitive substring match. Used by the smart-money score and
to break the R11 ownership-flow block read into "who" — tier-1 net buying is a stronger
accumulation tell than aggregate block flow.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def load_tiers() -> dict:
    import yaml
    p = Path(__file__).resolve().parents[3] / "config" / "institution_database.yaml"
    try:
        data = yaml.safe_load(p.read_text()) or {}
    except Exception:  # noqa: BLE001 — missing/bad file → no tiering (everything retail)
        return {"tier1": [], "tier2": []}
    return {"tier1": [s.lower() for s in (data.get("tier1") or [])],
            "tier2": [s.lower() for s in (data.get("tier2") or [])]}


def tier_of(client_name: str | None, tiers: dict | None = None) -> str | None:
    """'tier1' / 'tier2' / None for a deal client. None = retail or unrecognised."""
    if not client_name:
        return None
    t = tiers or load_tiers()
    name = client_name.lower()
    if any(p in name for p in t.get("tier1", [])):
        return "tier1"
    if any(p in name for p in t.get("tier2", [])):
        return "tier2"
    return None
