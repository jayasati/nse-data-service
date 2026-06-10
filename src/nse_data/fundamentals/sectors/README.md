# `fundamentals/sectors/` — per-sector result-reading

Implements the build roadmap in [`SECTOR_RESULT_PLAYBOOK.md`](../../../../SECTOR_RESULT_PLAYBOOK.md).
Generalises the binary `is_bfsi` switch into **one spec per sector** so adding a
sector is *one schema + one rule + one real-PDF regression* — nothing else.

## The contract

```
sector_class  →  SectorSpec(operating_line, classify, built, kpis)  →  QualityVerdict
```

`classify_result(symbol, growth, fields)` (in `__init__.py`) routes a result to
its sector's spec and applies the **P1 built guard**: an unbuilt sector is always
downgraded to an out-of-scope neutral, so the engine is confident only where it
has been proven. *This is what fixes the ONGC false LONG.*

## Files

| File | Playbook | Status |
|---|---|---|
| `base.py` | §4 P1+P2 | `SectorClass`, `SectorSpec`, generic operating line (true EBITDA → core-ex-OI → revenue), `classify_operating_quality` + `verdict_from_operating`, `other_income_propped`/`tax_propped` guards (P1), `unbuilt_spec` |
| `bfsi.py` | §2.1 | ✅ **built** — wraps the validated `earnings_quality` rule (SBI/Axis/HDFC) |
| `energy.py` | §2.2 / P3 | ✅ **built** — EBITDA operating line + other-income/tax props, real-ONGC regression |
| `it_services.py` | §2.3 / P4 | ✅ **built** — shared operating-quality verdict, hardened vs real INFY/TCS filings; KPIs (guidance/TCV/cc-rev) pending P7 |
| `fmcg.py` | §2.4 / P5 | ⏳ stub — volume, gross margin |
| `auto.py` | §2.5 / P5 | ⏳ stub — volume, EBITDA margin |
| `pharma.py` | §2.6 / P5 | ⏳ stub — US sales, FDA status |
| `metals.py` | §2.7 / P5 | ⏳ stub — EBITDA/tonne |
| `capital_goods.py` | §2.8 / P5 | ⏳ stub — order book, book-to-bill |

Cross-cutting roadmap items landing elsewhere:
- **P6 consensus (S8):** `migrations/058_consensus_estimates.sql` + a live source — the open external dependency.
- **P7 narrative ingestion:** [`parsers/narrative/`](../../parsers/narrative/) — guidance / volumes / order book / FDA live in the press release, not the P&L.

## Adding a sector (the repeatable step)

1. Write `<sector>.py` — define its `operating_line`, its `classify` rule, set `built=False`.
2. Register it: one line in `REGISTRY` (`__init__.py`) and the index→class row in `INDEX_TO_CLASS` (`base.py`).
3. Add a real-PDF regression in `tests/fundamentals/sectors/` (fixtures in `tests/fundamentals/fixtures/result_pdfs/`).
4. When the regression passes, flip `built=True`. The router starts emitting its verdict.
