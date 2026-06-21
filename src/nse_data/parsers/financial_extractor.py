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

import re
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
# Banks pack a dense multi-column P&L; render sharper than the 144 default so the
# model can resolve the columns/rows it confuses at low DPI. 300 materially
# improved current-quarter level reads (other_income, interest_expended) in
# testing; in-filing COMPARATIVE columns stay unreliable regardless, so growth is
# computed from stored history (fundamentals/from_results.quarter_growth), not
# from the model's comparative read.
_BFSI_RENDER_DPI = 300


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
    # YoY/QoQ revenue & PAT growth, computed from the PDF's own comparative
    # columns (preceding quarter + year-ago quarter) — no stored history needed.
    growth: dict = field(default_factory=dict)
    growth_consolidated: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=1)
def _load_config() -> dict:
    with _CONFIG_PATH.open() as f:
        return yaml.safe_load(f)


def _symbol_is_bfsi(symbol: str | None) -> bool:
    """True if the symbol is a bank/NBFC, so the BFSI line items are requested.

    Best-effort: a missing sector map simply yields False (generic P&L read)."""
    if not symbol:
        return False
    try:
        from nse_data.market.sector_map import is_bfsi
        return is_bfsi(symbol)
    except Exception:  # noqa: BLE001 — never let sector lookup break extraction
        return False


def _looks_like_bfsi(text: str) -> bool:
    """Content fallback for banks the sector map misses (CENTRALBK, IDBI, …): only a bank's
    Schedule-III P&L carries BOTH 'Interest Earned' and 'Interest Expended' as line items."""
    low = (text or "").lower()
    return "interest earned" in low and "interest expended" in low


def _pct_after(text: str, *label_variants: str) -> float | None:
    """First NPA-style percentage after a label, tolerant of OCR noise.

    The asset-quality rows survive in the text layer but garbled — e.g.
    ``"% of gross NPAS 1 49o/o"`` (space for the decimal point, ``o/o`` for %).
    We find the label then read the first ``D[.| ]DD`` token as ``D.DD`` and
    accept it only as a sane NPA ratio (0–30%)."""
    low = text.lower()
    for lab in label_variants:
        i = low.find(lab.lower())
        if i == -1:
            continue
        seg = text[i + len(lab): i + len(lab) + 30]
        m = re.search(r"(\d{1,2})\s*[.\s]\s*(\d{2})", seg)
        if m:
            val = float(f"{m.group(1)}.{m.group(2)}")
            if 0.0 <= val <= 30.0:
                return val
    return None


_AMOUNT_RE = re.compile(r"\d[\d,]*\.\d{2}")


def _amount_after(text: str, *label_variants: str, window: int = 60) -> float | None:
    """First Indian-format decimal amount after a label (e.g. '1,40,411.77').

    Used to anchor a subtotal the vision model misreads on dense bank tables but
    that survives cleanly (with its label) in the text layer."""
    low = text.lower()
    for lab in label_variants:
        i = low.find(lab.lower())
        if i == -1:
            continue
        seg = text[i + len(lab): i + len(lab) + window]
        m = _AMOUNT_RE.search(seg)
        if m:
            try:
                return float(m.group(0).replace(",", ""))
            except ValueError:
                continue
    return None


_ROW_NUM_RE = re.compile(r"-?\d[\d,]*\.\d{2}")

# current-quarter field -> growth-key base (matches earnings_quality / vision growth)
_GROWTH_TARGETS = (
    ("operating_profit_cr", "ppop"),
    ("pat_cr", "pat"),
    ("other_income_cr", "other_income"),
    ("revenue_cr", "revenue"),
    ("provisions_cr", "provisions"),
    ("net_interest_income_cr", "nii"),
    ("pbt_cr", "pbt"),               # non-bank core line: PBT − other income
    ("tax_cr", "tax"),               # tax-write-back prop guard
    ("depreciation_cr", "depreciation"),   # + finance below → operating EBITDA
    ("finance_cost_cr", "finance_cost"),
)

# Row-label fallback for fields whose CURRENT cell is OCR-garbled (so the value
# anchor can't find the row) but whose label + comparative columns survive — we
# then pair the model's reliable current value with the text row's comparatives.
_GROWTH_LABELS = {
    "other_income": ("other income",),
    "pat": ("net profit for the period", "profit for the period"),
    "pbt": ("profit before tax", "profit before exceptional"),
    "tax": ("total tax expense",),
}


def _pdf_text_growth(full_text: str, fields: dict) -> dict:
    """YoY/QoQ from the PDF's OWN comparative columns — no stored history.

    The filing prints current / preceding-quarter / year-ago columns on each row.
    Vision reads the *current* value reliably, so we locate that value in the text
    layer and read the SAME ROW's next two numbers as the comparatives. Each
    comparative is sanity-checked against the current value (a bank's quarter
    column is ~1/4 of its full-year column), so an OCR-garbled or wrong-scale cell
    is dropped rather than producing a bogus growth — which is exactly why this is
    robust where the vision model's own column assignment is not.
    """
    from .extractors.vision_financial import _pct, _plausible_comparative

    if not full_text:
        return {}
    lines = full_text.splitlines()
    low_lines = [ln.lower() for ln in lines]
    out: dict[str, float] = {}
    for field, base in _GROWTH_TARGETS:
        cur = fields.get(field)
        if cur is None:
            continue
        prev_q = year_ago = None
        # (1) value anchor: find the current value in a row, take its next columns.
        for line in lines:
            nums = [float(m.replace(",", "")) for m in _ROW_NUM_RE.findall(line)]
            idx = next((i for i, x in enumerate(nums[:3])
                        if abs(x - cur) <= max(0.02, 0.001 * abs(cur))), None)
            if idx is None:
                continue
            prev_q = _plausible_comparative(cur, nums[idx + 1] if idx + 1 < len(nums) else None)
            year_ago = _plausible_comparative(cur, nums[idx + 2] if idx + 2 < len(nums) else None)
            break
        # (2) label anchor (current cell garbled): pair the model's current value
        # with the row's plausible comparatives, in column order [prev_q, year_ago].
        if prev_q is None and year_ago is None:
            for lab in _GROWTH_LABELS.get(base, ()):
                hit = next((lines[i] for i, ll in enumerate(low_lines) if lab in ll), None)
                if hit is None:
                    continue
                nums = [float(m.replace(",", "")) for m in _ROW_NUM_RE.findall(hit)]
                plausible = [n for n in nums if _plausible_comparative(cur, n) is not None]
                if len(plausible) >= 2:
                    prev_q, year_ago = plausible[0], plausible[1]
                elif len(plausible) == 1:
                    year_ago = plausible[0]   # only the year-ago survived
                break
        qoq, yoy = _pct(cur, prev_q), _pct(cur, year_ago)
        if qoq is not None:
            out[f"qoq_{base}_pct"] = qoq
        if yoy is not None:
            out[f"yoy_{base}_pct"] = yoy
    return out


def _apply_bfsi_text_overrides(vis: dict, full_text: str) -> None:
    """Correct BFSI fields the vision model misreads, using the text layer (which
    keeps the labelled subtotal rows even when vision confuses dense columns).

    Two corrections, both seen on the real SBI filing:
      * GNPA/NNPA % — off the main P&L grid; vision read 2.78/0.67 vs filed 1.49/0.39.
      * TOTAL INCOME — vision summed the interest sub-components low (123,097 →
        103,411), dragging interest-earned and NII down. TOTAL INCOME survives
        cleanly in the text, so anchor it and re-derive interest-earned
        (= total income − other income) and NII (= interest earned − interest
        expended). other_income/interest_expended are read reliably by vision.
    """
    if not full_text:
        return
    fields = vis.get("fields") or {}
    gnpa = _pct_after(full_text, "% of gross npa", "of gross npa")
    nnpa = _pct_after(full_text, "% of net npa", "of net npa")
    if gnpa is not None:
        fields["gross_npa_pct"] = gnpa
    if nnpa is not None:
        fields["net_npa_pct"] = nnpa

    factor = units_factor_from(vis.get("units_phrase"))
    ti_text = _amount_after(full_text, "total income")
    # Units sanity (BFSI): "in lakh" routinely appears in the NPA / segment / shareholding
    # sub-schedules while the P&L itself is in crore, so whole-doc unit detection can latch
    # onto lakh and shrink the entire P&L 100×. The P&L's own total-income magnitude is the
    # disambiguator — a bank / large NBFC that files quarterly results has total income in the
    # hundreds-to-thousands of crore, so a sub-crore factor that yields < ₹500 cr total income
    # is a misdetect. Treat the P&L as crore and rescale the already-factored vision fields up.
    _peak = max((abs(_v) for _k, _v in fields.items()
                 if _k.endswith("_cr") and isinstance(_v, (int, float))), default=0.0)
    if factor < 1.0 and 0 < _peak < 500:        # BFSI filer's top P&L line is ≫₹500cr in crore
        rescale = 1.0 / factor
        for _k, _v in list(fields.items()):
            if _k.endswith("_cr") and isinstance(_v, (int, float)):
                fields[_k] = round(_v * rescale, 2)
        factor = 1.0
        vis["units_phrase"] = "INR crore (units-corrected)"
    if ti_text is not None:
        ti_cr = ti_text * factor
        oi = fields.get("other_income_cr")
        # Accept only a sane total: above other income, and not absurdly large vs
        # the model's read (guards against grabbing a wrong number/column).
        if oi is not None and ti_cr > oi > 0 and 0.5 <= ti_cr / max(fields.get("total_income_cr") or ti_cr, 1.0) <= 2.0:
            fields["total_income_cr"] = round(ti_cr, 2)
            ie = round(ti_cr - oi, 2)                 # interest earned = total income − other income
            fields["revenue_cr"] = ie
            fields["interest_earned_cr"] = ie
            iex = fields.get("interest_expended_cr")
            if iex is not None:
                fields["net_interest_income_cr"] = round(ie - iex, 2)
    vis["fields"] = fields

    # Recompute growth from the PDF's OWN comparative columns (text-anchored on
    # the corrected current values) — this is the reliable comparative source, so
    # it overrides the vision model's own (often wrong-column) comparative read;
    # any key it can't recover falls back to the vision growth.
    text_growth = _pdf_text_growth(full_text, fields)
    if text_growth:
        merged = dict(vis.get("growth") or {})
        merged.update(text_growth)
        vis["growth"] = merged


def _apply_generic_growth(vis: dict, full_text: str) -> None:
    """Non-BFSI: recompute growth from the PDF's OWN comparative columns
    (text-anchored, reliable) and derive the core operating line ex-other-income
    (PBT − other income). Puts ``operating_ex_oi`` into growth_json so the
    energy/generic sector rules can read the operating line at detection time —
    the fix for the revenue-proxy weakness that mislabelled ONGC."""
    if not full_text:
        return
    fields = vis.get("fields") or {}
    text_growth = _pdf_text_growth(full_text, fields)
    merged = dict(vis.get("growth") or {})
    if text_growth:
        merged.update(text_growth)
    from ..fundamentals.from_results import derive_core_operating, derive_ebitda
    merged.update(derive_core_operating(merged, fields))
    merged.update(derive_ebitda(merged, fields))
    if merged:
        vis["growth"] = merged


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


def _vision_scan(data: bytes, n_pages: int, ctx: dict, *, dpi: int | None = None) -> dict | None:
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
    render_kw = {"dpi": dpi} if dpi else {}
    for start in range(0, limit, _SCAN_BATCH):
        idx = list(range(start, min(start + _SCAN_BATCH, limit)))
        out = _vision(pdf_render.render_pages(data, idx, **render_kw), **ctx)
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
        "growth": _merge_fill(vis.get("growth", {}), txt.get("growth", {})),
        "growth_consolidated": _merge_fill(
            vis.get("growth_consolidated", {}), txt.get("growth_consolidated", {})),
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
        growth=out.get("growth", {}),
        growth_consolidated=out.get("growth_consolidated", {}),
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

    is_bfsi = _symbol_is_bfsi(symbol) or _looks_like_bfsi(full_text)
    ctx = {
        "symbol": symbol, "subject": subject, "broadcast_dt": broadcast_dt,
        "is_bfsi": is_bfsi,
    }
    # Banks pack a dense 10-column P&L; render sharper so the model can resolve
    # the columns it was confusing at the default DPI.
    render_dpi = _BFSI_RENDER_DPI if is_bfsi else pdf_render.RENDER_DPI

    # --- primary: vision over the located P&L page(s) ---
    # For a bank, render ONLY the first located P&L page: the successor page
    # (notes / asset-quality) measurably degraded column reads in testing.
    pnl_idx = _locate_pnl_pages(pages)
    render_idx = (pnl_idx[:1] if (is_bfsi and pnl_idx) else (pnl_idx or None))
    images = pdf_render.render_pages(data, render_idx, dpi=render_dpi)
    vis = _vision(images, **ctx)
    if vis is not None and vis.get("fields"):
        if is_bfsi:
            _apply_bfsi_text_overrides(vis, full_text)
        else:
            _apply_generic_growth(vis, full_text)
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
    scan = _vision_scan(data, len(pages), ctx, dpi=render_dpi)
    if scan is not None and scan.get("fields"):
        if is_bfsi:
            _apply_bfsi_text_overrides(scan, full_text)
        else:
            _apply_generic_growth(scan, full_text)
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
