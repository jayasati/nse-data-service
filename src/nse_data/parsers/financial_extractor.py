"""Vision-first financial-statement extractor (Week 17 rewrite).

Pulls the headline quarterly numbers (revenue, PAT, EPS, …) out of an NSE result
PDF and returns them normalized to **crore**, in the canonical field names of
``config/financial_aliases.yaml``.

Pipeline (vision-first):
  1. Cheap text pre-pass (``pdf_text.page_texts``) to (a) detect the reporting
     unit and (b) LOCATE the P&L page(s) via anchor labels.
  2. Render just those pages to images (``pdf_render``) and read the P&L straight
     from the layout with gpt-4o vision (``vision_financial.extract_via_vision``).
     Reading the real layout is what avoids the deterministic parser's failures:
     the model picks the current-quarter column by its header date and never
     confuses a Notes column or the year-ago quarter.
  3. If vision is unavailable (no creds / cap / no image) or finds nothing, fall
     back to sending the extracted P&L text to gpt-4o
     (``vision_financial.extract_via_text``).
  4. Validate the result against accounting identities; failures downgrade
     confidence (they don't reject).

Because there is no free deterministic path, every extraction needs an LLM call.
``use_llm_fallback`` is therefore the master switch: when False, ``extract``
returns an empty result without spending anything (so a default eval run is
free); pass True (eval's ``--llm``) to actually extract.

Public API:
    result = extract(pdf_path, use_llm_fallback=True, symbol=..., broadcast_dt=...)
    result.fields         # {"revenue_cr": 1234.5, "pat_cr": 89.0, "eps_basic": 4.2, ...}
    result.consolidated   # same, consolidated scope (or {})
    result.confidence     # 0.0 – 1.0
    result.strategy       # "vision" | "text_llm" | "llm_disabled" | "none"
    result.warnings       # validation messages
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import structlog
import yaml

from . import pdf_render, pdf_text

log = structlog.get_logger()

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "financial_aliases.yaml"

# Fields whose presence signals the right table was found (confidence scoring).
_CORE_FIELDS = ("revenue_cr", "pat_cr", "total_income_cr", "pbt_cr")

# A "complete" P&L read has all of these. A vision result missing any is treated
# as under-extracted (the BEL case: revenue/pbt/pat only) and gap-filled from the
# text path. EPS is intentionally excluded — some filings genuinely omit it.
_REQUIRED_FOR_COMPLETE = (
    "revenue_cr", "other_income_cr", "total_income_cr", "total_expenses_cr",
    "pbt_cr", "tax_cr", "pat_cr",
)

# A page hitting >= this many distinct anchors is treated as a P&L page.
_PNL_ANCHOR_MIN = 2
# Cap on how many pages we send to vision (cost/latency bound).
_MAX_VISION_PAGES = 6


@dataclass
class ExtractionResult:
    fields: dict[str, float] = field(default_factory=dict)        # standalone scope
    consolidated: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    strategy: str = "none"
    units_factor: float = 1.0
    units_phrase: str | None = None
    period_ending: str | None = None
    warnings: list[str] = field(default_factory=list)
    llm_cost_usd: float = 0.0


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=1)
def _load_config() -> dict:
    with _CONFIG_PATH.open() as f:
        return yaml.safe_load(f)


# --------------------------------------------------------------------------- #
# text pre-pass: units + P&L page location
# --------------------------------------------------------------------------- #

def _detect_units(text: str) -> tuple[float, str | None]:
    """Find the reporting unit from PDF text → (factor_to_crore, phrase)."""
    cfg = _load_config()
    low = (text or "").lower()
    for hint in cfg.get("units_hints", []):
        phrase = hint["phrase"].lower()
        if phrase in low:
            return float(hint["factor"]), hint["phrase"]
    return 1.0, None


def _locate_pnl_pages(pages: list[str]) -> list[int]:
    """Indices of pages that look like the P&L, plus the following page.

    Scores each page by how many distinct anchor labels it contains; pages at or
    above the threshold are P&L candidates. The P&L frequently continues onto the
    next page (EPS / comprehensive-income rows), so we include each candidate's
    successor. Returns [] when nothing matches (scanned/empty text), signalling
    the caller to render the first pages instead.
    """
    cfg = _load_config()
    anchors = [a.lower() for a in cfg.get("pnl_anchors", [])]
    scored: list[tuple[int, int, int]] = []   # (page_index, anchor_hits, char_count)
    for i, page in enumerate(pages):
        low = (page or "").lower()
        hits = sum(1 for a in anchors if a in low)
        if hits:
            scored.append((i, hits, len(low)))
    if not scored:
        return []
    # Normally a P&L page hits >= 2 anchors. But dense tables (banks/NBFCs) garble
    # the anchor phrases in the text layer and may hit only 1 — so when nothing
    # reaches the threshold, fall back to the best-scoring pages (by hits, then
    # density) rather than giving up and rendering the whole document.
    max_hits = max(h for _, h, _ in scored)
    threshold = _PNL_ANCHOR_MIN if max_hits >= _PNL_ANCHOR_MIN else 1
    best = sorted((s for s in scored if s[1] >= threshold), key=lambda s: (-s[1], -s[2]))
    selected: set[int] = set()
    for i, _, _ in best:
        selected.add(i)
        if i + 1 < len(pages):       # P&L often continues onto the next page
            selected.add(i + 1)
        if len(selected) >= _MAX_VISION_PAGES:
            break
    return sorted(selected)[:_MAX_VISION_PAGES]


# --------------------------------------------------------------------------- #
# validation (failures downgrade confidence, never reject) + confidence
# --------------------------------------------------------------------------- #

def _run_validations(fields: dict[str, float]) -> list[str]:
    cfg = _load_config()
    warnings: list[str] = []
    safe = dict(fields)
    for v in cfg.get("validations", []):
        rule = v["rule"]
        try:
            ok = bool(eval(rule, {"__builtins__": {"abs": abs}}, safe))  # noqa: S307
        except (NameError, ZeroDivisionError, TypeError):
            # A field the rule needs wasn't extracted — can't judge it, skip.
            continue
        if not ok:
            warnings.append(v.get("message", rule))
    return warnings


def _confidence(fields: dict[str, float], warnings: list[str]) -> float:
    if not fields:
        return 0.0
    core_hits = sum(1 for f in _CORE_FIELDS if f in fields)
    base = core_hits / len(_CORE_FIELDS)
    base += min(0.15, 0.02 * (len(fields) - core_hits))   # coverage bonus, capped
    penalty = 0.12 * len(warnings)
    return max(0.0, min(1.0, base - penalty))


# --------------------------------------------------------------------------- #
# LLM path wrappers (lazy import keeps openai off the hot path)
# --------------------------------------------------------------------------- #

def _vision(images: list[bytes], **ctx) -> dict | None:
    try:
        from .extractors.vision_financial import extract_via_vision
    except Exception as e:  # noqa: BLE001
        log.warning("vision_import_failed", error=str(e))
        return None
    return extract_via_vision(images, **ctx)


def _text_llm(text: str, **ctx) -> dict | None:
    try:
        from .extractors.vision_financial import extract_via_text
    except Exception as e:  # noqa: BLE001
        log.warning("text_llm_import_failed", error=str(e))
        return None
    return extract_via_text(text, **ctx)


# Batched-vision scan: pages per vision call, and how deep into the doc to scan.
# Small batches keep each call fast (no timeout) and stop at the first P&L found.
_SCAN_BATCH = 4
_SCAN_MAX_PAGES = 16


def _vision_scan(data: bytes, n_pages: int, ctx: dict) -> dict | None:
    """Find the P&L *by sight*: render the document in small image batches over
    the first pages and return the first batch vision reads a P&L from.

    For filings whose dense table is garbled in the text layer (long *merged*
    bank/NBFC PDFs), anchor-based location aims at the wrong pages — but vision
    can still read the table from the rendered image. Returns the hit (with
    accumulated cost), the last empty result (for cost accounting), else None.
    """
    total_cost = 0.0
    last: dict | None = None
    limit = min(n_pages, _SCAN_MAX_PAGES)
    for start in range(0, limit, _SCAN_BATCH):
        idx = list(range(start, min(start + _SCAN_BATCH, limit)))
        out = _vision(pdf_render.render_pages(data, idx), **ctx)
        if out is None:
            continue
        total_cost += out.get("cost_usd", 0.0)
        if out.get("fields"):
            out["cost_usd"] = total_cost
            log.info("vision_scan_hit", pages=idx)
            return out
        last = out
    if last is not None:
        last["cost_usd"] = total_cost
    return last


def units_factor_from(phrase: str | None) -> float:
    from .extractors.vision_financial import units_factor

    return units_factor(phrase)


def _is_incomplete(fields: dict[str, float]) -> bool:
    """True if a P&L read is missing any required line (under-extracted)."""
    return any(f not in fields for f in _REQUIRED_FOR_COMPLETE)


def _merge_fill(primary: dict, secondary: dict) -> dict:
    """Keep every value from ``primary``; fill only its gaps from ``secondary``."""
    out = dict(primary)
    for k, v in (secondary or {}).items():
        out.setdefault(k, v)
    return out


def _gapfilled(vis: dict, txt: dict) -> dict:
    """Merge a sparse vision read with the text read — vision wins, text fills."""
    return {
        "fields": _merge_fill(vis["fields"], txt.get("fields", {})),
        "consolidated": _merge_fill(vis.get("consolidated", {}), txt.get("consolidated", {})),
        "units_phrase": vis.get("units_phrase") or txt.get("units_phrase"),
        "period_ending": vis.get("period_ending") or txt.get("period_ending"),
        "cost_usd": vis.get("cost_usd", 0.0) + txt.get("cost_usd", 0.0),
    }


def _result_from_llm(out: dict, strategy: str, prior_cost: float) -> ExtractionResult:
    warnings = _run_validations(out["fields"])
    return ExtractionResult(
        fields=out["fields"],
        consolidated=out.get("consolidated", {}),
        confidence=_confidence(out["fields"], warnings),
        strategy=strategy,
        units_factor=units_factor_from(out.get("units_phrase")),
        units_phrase=out.get("units_phrase"),
        period_ending=out.get("period_ending"),
        warnings=warnings,
        llm_cost_usd=prior_cost + out.get("cost_usd", 0.0),
    )


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #

def extract(
    pdf_path: str | Path,
    data: bytes | None = None,
    *,
    use_llm_fallback: bool = False,
    symbol: str | None = None,
    subject: str | None = None,
    broadcast_dt: str | None = None,
) -> ExtractionResult:
    """Extract headline financials from a result PDF (vision-first).

    Args:
        pdf_path: path to the PDF on disk.
        data: optional pre-read PDF bytes; read from ``pdf_path`` if not given.
        use_llm_fallback: master switch for LLM use. False ⇒ return empty (free).
        symbol/subject/broadcast_dt: context passed to the model prompt.
    """
    pdf_path = str(pdf_path)
    if data is None:
        try:
            data = Path(pdf_path).read_bytes()
        except OSError as e:
            log.warning("pdf_read_failed", path=pdf_path, error=str(e))
            return ExtractionResult(strategy="error")

    pages = pdf_text.page_texts(data)
    full_text = "\n".join(pages).strip()
    uf, up = _detect_units(full_text)

    if not use_llm_fallback:
        # No free deterministic path exists; don't spend without opt-in.
        return ExtractionResult(strategy="llm_disabled", units_factor=uf, units_phrase=up)

    ctx = {"symbol": symbol, "subject": subject, "broadcast_dt": broadcast_dt}

    # --- primary: vision over the located P&L page(s) ---
    pnl_idx = _locate_pnl_pages(pages)
    images = pdf_render.render_pages(data, pnl_idx or None)
    vis = _vision(images, **ctx)
    if vis is not None and vis["fields"]:
        # Gap-fill: vision sometimes under-extracts (reads only the headline rows
        # and nulls the rest). If a required line is missing, run the text path
        # and backfill the gaps — vision's values win, text only fills holes.
        if full_text and _is_incomplete(vis["fields"]):
            txt = _text_llm(full_text, **ctx)
            if txt is not None and txt.get("fields"):
                return _result_from_llm(_gapfilled(vis, txt), "vision+text", 0.0)
        return _result_from_llm(vis, "vision", 0.0)

    prior_cost = vis.get("cost_usd", 0.0) if vis else 0.0

    # --- batched vision scan: the located pages had no P&L (anchors aimed wrong
    # because the table is garbled in the text layer, e.g. merged bank filings).
    # Render the doc in small batches and let vision find the P&L by sight. ---
    scan = _vision_scan(data, len(pages), ctx)
    if scan is not None and scan.get("fields"):
        if full_text and _is_incomplete(scan["fields"]):
            txt = _text_llm(full_text, **ctx)
            if txt is not None and txt.get("fields"):
                return _result_from_llm(_gapfilled(scan, txt), "vision_scan+text", prior_cost)
        return _result_from_llm(scan, "vision_scan", prior_cost)
    prior_cost += scan.get("cost_usd", 0.0) if scan else 0.0

    # --- fallback: gpt-4o over the extracted text ---
    if full_text:
        txt = _text_llm(full_text, **ctx)
        if txt is not None and txt["fields"]:
            return _result_from_llm(txt, "text_llm", prior_cost)
        prior_cost += txt.get("cost_usd", 0.0) if txt else 0.0

    return ExtractionResult(
        strategy="none", units_factor=uf, units_phrase=up, llm_cost_usd=prior_cost
    )
