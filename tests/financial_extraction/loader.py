"""Load fixtures and ground truth for the Layer 3 eval suite."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / "tests" / "financial_extraction" / "fixtures"
GT_ROOT = ROOT / "tests" / "financial_extraction" / "ground_truth"
METADATA_PATH = FIXTURE_ROOT / "metadata.json"


@dataclass
class Fixture:
    fingerprint: str
    symbol: str
    company_name: str | None
    subject: str
    details: str
    segment: str | None
    broadcast_dt: str
    broadcast_month: str | None
    attachment_url: str
    pdf_path: Path
    size_bytes: int
    ground_truth: dict[str, Any] | None


def _gt_path_for(symbol: str, fingerprint: str) -> Path:
    fp_short = fingerprint[:8] if fingerprint else "nofp"
    return GT_ROOT / f"{symbol}_{fp_short}.yaml"


def load_fixtures() -> list[Fixture]:
    if not METADATA_PATH.exists():
        return []
    with METADATA_PATH.open() as f:
        meta = json.load(f)
    out: list[Fixture] = []
    for e in meta.get("fixtures", []):
        gt_path = _gt_path_for(e["symbol"], e["fingerprint"])
        gt = None
        if gt_path.exists():
            with gt_path.open() as f:
                gt = yaml.safe_load(f)
        out.append(Fixture(
            fingerprint=e["fingerprint"],
            symbol=e["symbol"],
            company_name=e.get("company_name"),
            subject=e.get("subject", ""),
            details=e.get("details", ""),
            segment=e.get("segment"),
            broadcast_dt=e.get("broadcast_dt", ""),
            broadcast_month=e.get("broadcast_month"),
            attachment_url=e["attachment_url"],
            pdf_path=ROOT / e["pdf_path"],
            size_bytes=e.get("size_bytes", 0),
            ground_truth=gt,
        ))
    return out


def coverage_summary() -> dict[str, Any]:
    fixtures = load_fixtures()
    by_month: dict[str, int] = {}
    by_subject: dict[str, int] = {}
    by_symbol: dict[str, int] = {}
    labeled = 0
    for f in fixtures:
        if f.broadcast_month:
            by_month[f.broadcast_month] = by_month.get(f.broadcast_month, 0) + 1
        by_subject[f.subject] = by_subject.get(f.subject, 0) + 1
        by_symbol[f.symbol] = by_symbol.get(f.symbol, 0) + 1
        if f.ground_truth is not None:
            labeled += 1
    return {
        "total_fixtures": len(fixtures),
        "labeled_fixtures": labeled,
        "unique_symbols": len(by_symbol),
        "by_month": dict(sorted(by_month.items(), reverse=True)),
        "by_subject": dict(sorted(by_subject.items(), key=lambda kv: -kv[1])[:15]),
        "top_symbols_by_count": dict(sorted(by_symbol.items(), key=lambda kv: -kv[1])[:10]),
    }