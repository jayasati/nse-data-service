# Week 17 — Financial Extractor: corpus, storage & schema

Design + runbook for the financial-extraction eval pipeline. This is the
authoritative reference for **how result PDFs are downloaded, where they live on
disk, and the schema of the ground-truth labels**. Read this before touching the
fixture corpus or the labels — the two drifted apart once already (see
"Lesson learned" below) and this doc exists to prevent a repeat.

---

## 0. The fingerprint is the one true key

Everything is keyed by the **announcement fingerprint** — the 16-hex digest
computed in the collectors layer from the announcement identity (symbol, subject,
broadcast time, attachment). It is stable: the same filing always hashes to the
same fingerprint, whether seen by the live collector or re-mined months later.

Consequences:
- Fixture PDF, draft label, ground-truth label, and the production archive copy
  **all share one filename stem = the fingerprint**.
- Labels are anchored to fingerprints, not to file paths or symbols, so a label
  is never orphaned as long as its fingerprint's PDF exists *somewhere*.

---

## 1. How PDFs are downloaded

Two download paths exist by design — keep both.

### A. Production path — `src/nse_data/parsers/pdf_downloader.py`
Used by the live parser job for real-time announcements.
- Goes through Layer-1 `SessionManager` (NSE cookies, rate limiting, circuit
  breaker, retries).
- Validates: non-empty body, `%PDF` magic bytes (NSE serves HTML error pages
  with a `.pdf` extension), 50 MB hard cap.
- Returns `DownloadResult(success, data, sha256, size_bytes, error)`.

### B. Fixture-mining path — `scripts/mine_announcement_fixtures.py`
Builds the eval corpus.
- **Source = `raw_announcements`** (NOT `raw_financial_results`, which holds
  metadata only and has no PDF URLs — result PDFs arrive as announcements with
  an `attachment_url`).
- Selection: F&O-filtered (`JOIN raw_fno_list`), bucketed across the last N
  broadcast months, soft per-symbol cap (default 3, relaxed if under target).
  Flags: `--target`, `--per-symbol`, `--months-back`, `--include-non-fno`,
  `--subjects "Outcome of Board Meeting,Press Release"`, `--dry-run`.
- Same validation as production. Idempotent — skips fingerprints already present.

> **Prefer re-hydrating from the archive over re-downloading from NSE.** The
> production archive (`data/archive/`) already holds the PDFs for every label we
> have, keyed by fingerprint. Re-mining from NSE risks (a) fingerprint/selection
> drift that orphans labels and (b) filings that have since been pulled. A
> corpus built from the archive is reproducible and offline.

> **Deprecated:** `scripts/mine_fixtures.py` writes to a *different* location
> (`tests/parsers/fixtures/`) with a `{symbol}_{date}_{fp8}` naming scheme and a
> `manifest.csv`. It belongs to the Layer-2 parser tests, not Week 17. Do not use
> it for the financial-extraction corpus.

---

## 2. How PDFs are stored

### Production archive — `src/nse_data/storage/files.py` (three retention tiers)
```
data/archive/pdfs/<YYYY>/<MM>/<DD>/<fingerprint>.pdf   high   — kept forever, date-bucketed
data/archive/pdfs_temp/<fingerprint>.pdf               medium — flat, cleaned after 30d
data/archive/scratch/<fingerprint>.pdf                 low    — parsed then deleted
```
Atomic writes (`.tmp` → `os.replace`). This is the **source of truth** the
fixture corpus should hydrate from.

### Fixture corpus — `tests/financial_extraction/`
```
fixtures/pdfs/<fingerprint>.pdf        # the PDF (see naming note below)
fixtures/metadata.json                 # the manifest (schema_version 2)
drafts/<fingerprint>.yaml              # LLM auto-labels — RAW source units, with _meta cost
ground_truth/<fingerprint>.yaml        # reviewed/promoted labels — normalized to crore
```

> **Naming note / open cleanup:** the miner currently writes fixture PDFs as
> `{symbol}_{fp8}.pdf`, while labels, the archive, and the loader key on the full
> fingerprint. Standardize fixture PDFs on `<fingerprint>.pdf` to match
> everything else and remove the symbol-rename fragility. The loader already
> resolves `pdf_path` from `metadata.json`, so the on-disk name only needs to be
> consistent with what the manifest records.

---

## 3. Schema of the table

### Fixture manifest — `fixtures/metadata.json` (`schema_version: 2`)
```jsonc
{
  "schema_version": 2,
  "source": "raw_announcements",
  "generated_count": <int>,
  "fixtures": [
    {
      "fingerprint": "b5335898acda76df",   // 16-hex, the join key
      "symbol": "360ONE",
      "company_name": "360 ONE WAM LIMITED",
      "subject": "Acquisition",            // NSE announcement subject
      "details": "<=500 chars",
      "segment": "equities",
      "broadcast_dt": "30-Apr-2026 17:19:41",
      "broadcast_month": "2026-04",
      "attachment_url": "https://nsearchives.nseindia.com/...pdf",
      "pdf_path": "tests/financial_extraction/fixtures/pdfs/<...>.pdf",
      "size_bytes": 3701910
    }
  ]
}
```

### Ground-truth label — one YAML per fixture, keyed by fingerprint
We keep the **richer per-file schema** (decided over the flat single-file form):
it preserves standalone vs consolidated, source-unit provenance, and a labeling
audit trail. Drafts store **raw source units**; promotion to ground truth
**normalizes to crore** (`*_cr`).

```yaml
standalone:                      # primary result block (numbers in crore)
  revenue_cr:
  other_income_cr:
  total_income_cr:
  total_expenses_cr:
  pbt_cr:
  tax_cr:
  pat_cr:
  total_comprehensive_income_cr:
  eps_basic:
  eps_diluted:
  # BFSI variant — present only for banks/NBFCs:
  net_interest_income_cr:        # (NII) in place of revenue for banks
consolidated: null               # same shape as `standalone`, or null if absent
yoy_revenue_growth:              # company-stated YoY %, if printed in the PDF (else null)
period_label: Q4-FY26
period_ending: '2026-03-31'
units_in_source_pdf: INR lakh    # provenance: what the PDF reported in
notes: <free text>
_meta:
  fingerprint: 00aa0d3e4077df9e
  symbol: BHAGYANGR
  subject: Outcome of Board Meeting
  broadcast_dt: 30-Apr-2026 11:58:26
  reviewed: true                 # false/absent for un-promoted drafts
  draft_cost_usd: 0.03181        # gpt-4o cost to generate the draft
```

Notes:
- `*_cr` everywhere → the extractor and eval compare in one unit; conversion from
  `units_in_source_pdf` happens once, at promotion.
- `consolidated: null` is meaningful (= the PDF had no consolidated statement),
  distinct from a missing key.
- `net_interest_income_cr` and `yoy_revenue_growth` are added for BFSI coverage
  and for the validation layer's company-stated-vs-extracted YoY cross-check
  (17.6); both are optional and null when the PDF doesn't print them.

---

## 4. Labeling pipeline (how a label is born)

```
mine_announcement_fixtures.py   → fixtures/pdfs + metadata.json   (corpus)
llm_label_drafts.py             → drafts/<fp>.yaml                (raw units, gpt-4o, with cost)
review_labels.py / verify_drafts.py → ground_truth/<fp>.yaml      (normalized, reviewed:true)
loader.py / show_fixture_coverage.py → coverage + eval input
```

The corpus is **subject-agnostic** by default (mined by F&O + recency), so most
PDFs are not result statements. The eval set (17.5) must filter to result-bearing
subjects (e.g. "Outcome of Board Meeting", "Press Release", "Investor
Presentation") or to fixtures whose label has `table_found: true`.

---

## 5. Lesson learned — keep corpus and labels in sync

At one point `metadata.json` was re-mined with different selection params. Result:
of 28 reviewed ground-truth labels and 61 drafts, only **4** and **7**
respectively still matched a fingerprint in the manifest — the rest were
orphaned (their fixture PDFs had been replaced). **All 28 + 61 were still
recoverable from `data/archive/`**, because the archive keys on fingerprint and
keeps high-priority PDFs forever.

Guardrails this motivates:
1. **Hydrate the fixture corpus from `data/archive/` by fingerprint**, not by
   re-downloading from NSE. Same key → no drift.
2. A **sync check** (script/test) that fails if any `ground_truth/*.yaml` or
   `drafts/*.yaml` fingerprint is absent from `metadata.json`.
3. Never delete a fixture PDF that has a label pointing at it.

---

## 6. Remaining Week-17 work (status)

| Task | Status |
|---|---|
| 17.1 corpus download | ✅ miner built; 264 PDFs mined (mostly non-result — needs result-subject filter) |
| 17.2 ground-truth labels | 🟡 schema defined; 28 reviewed + 61 drafts, but most orphaned — reconnect via archive, then label to ≥50 results |
| 17.3 `financial_extractor.py` ensemble | 🟡 file exists — audit vs the 4-strategy spec |
| 17.4 `config/field_aliases.yaml` | ⬜ build from real PDFs |
| 17.5 `eval.py` | ⬜ loader fixed (was loading 0 labels); eval script not yet written |
| 17.6 validation layer | ⬜ |
| 17.7 earnings-quality (CFO, receivables) | ⬜ |
| 17.8 per-company quirks | ⬜ |

**Gate:** ≥90% (target 95%) accuracy on the 50-fixture eval set.

### Immediate next actions
1. Write a `rehydrate_corpus.py` that copies/links archive PDFs for every
   labeled fingerprint into `fixtures/pdfs/` (full-fp naming) and rebuilds
   `metadata.json` — reconnecting the 24 + 54 orphaned labels.
2. Add the corpus↔labels sync check.
3. Filter/extend the corpus to ≥50 *result* PDFs across F&O names and label them.
