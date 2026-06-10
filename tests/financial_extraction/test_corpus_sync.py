"""Guard against corpus/label drift.

The fixture corpus (metadata.json + fixtures/pdfs/) and the labels
(ground_truth/, drafts/) are all keyed by announcement fingerprint. If the corpus
is re-mined with different params, labels can be orphaned — their fixture PDF
vanishes and loader.py silently loads zero ground truth. That happened once; see
README.md §5. These tests fail loudly if it happens again.

Recovery when these fail: PYTHONPATH=src python scripts/rehydrate_corpus.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
METADATA_PATH = HERE / "fixtures" / "metadata.json"
GT_DIR = HERE / "ground_truth"
DRAFT_DIR = HERE / "drafts"


def _metadata_by_fp() -> dict[str, dict]:
    if not METADATA_PATH.exists():
        return {}
    with METADATA_PATH.open() as f:
        data = json.load(f)
    return {e["fingerprint"]: e for e in data.get("fixtures", [])}


def _label_fps(directory: Path) -> set[str]:
    return {p.stem for p in directory.glob("*.yaml")} if directory.exists() else set()


@pytest.mark.parametrize("label_dir", [GT_DIR, DRAFT_DIR], ids=["ground_truth", "drafts"])
def test_every_label_has_a_metadata_entry(label_dir: Path) -> None:
    """Each label's fingerprint must appear in metadata.json."""
    meta = _metadata_by_fp()
    orphans = sorted(_label_fps(label_dir) - set(meta))
    assert not orphans, (
        f"{len(orphans)} label(s) in {label_dir.name}/ have no metadata.json entry: "
        f"{orphans[:10]}... Run: python scripts/rehydrate_corpus.py"
    )


def test_every_labeled_pdf_exists_on_disk() -> None:
    """Each labeled fixture's PDF (per metadata.json pdf_path) must exist."""
    meta = _metadata_by_fp()
    labeled = _label_fps(GT_DIR) | _label_fps(DRAFT_DIR)
    missing = []
    for fp in sorted(labeled):
        entry = meta.get(fp)
        if entry is None:
            continue  # covered by the metadata-entry test
        if not (ROOT / entry["pdf_path"]).exists():
            missing.append(fp)
    assert not missing, (
        f"{len(missing)} labeled PDF(s) missing on disk: {missing[:10]}... "
        f"Run: python scripts/rehydrate_corpus.py"
    )
