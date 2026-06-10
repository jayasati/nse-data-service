# Sector-Wise Result-Reading Playbook & Build Plan

**What this is:** the framework for reading an NSE quarterly-result PDF *during market
hours* and deciding bullish / bearish / neutral in under 3 minutes — and the
roadmap for teaching this system to do it automatically, sector by sector.

It has two columns of thinking, always side by side:
- **Analyst view** — what a human reads in the PDF and concludes.
- **System view** — what `financial_extractor` should extract and what
  `earnings_quality.classify_quality` should flag for that sector.

Validated on eleven real filings (see `FEATURE_CHECKLIST.md` Week 17.5):
SBI ✅ short (hidden miss) · Axis ✅ short (outright miss) · HDFC ✅ silent (clean beat)
· ONGC ✅ short (the false LONG that motivated the per-sector engine, fixed) ·
INFY/TCS ✅ no false short · ITC ✅ long · Maruti ✅ neutral + tax-prop caveat ·
Cipla ✅ neutral (no false prop) · JSW Steel ✅ long · L&T ✅ long. All 8 mapped
sectors carry a built rule; the open gaps are the per-sector KPIs (P7 plumbing) and
consensus (P6).

---

## 0. The three laws (true for every sector)

1. **Operating line, not the headline.** PAT is the wrong primary metric. Every
   sector has a *core operating profit* — read that. Headline PAT can be propped by
   non-core items; the operating line can't hide.
2. **"Propped by non-core" = low quality.** PAT up while the operating line is flat/down,
   rescued by a one-off, is the universal low-quality signature. The prop is
   sector-specific:
   - Banks → **provision release**, **treasury gains**, **tax write-back**
   - Non-banks → **other income**, **tax write-back**, **exceptional items**
3. **Expectation vs actual, not absolute.** The market reacts to the *surprise*. A +20%
   PAT can fall 10% if the Street wanted +35%. (System: this needs consensus —
   `consensus_estimates` table exists, live source is the open dependency S8.)

> **The catch-the-signal rule (what the system encodes today):**
> SHORT only on a confirmed **operating-line decline**. Corroborating props
> (provisions/treasury/tax/other-income) reinforce a miss but never trigger a short
> alone. A clean two-sided beat stays long/neutral. (This is why HDFC was correctly
> silenced and Axis correctly shorted.)

---

## 1. Top-down sequence (do this in order, every time)

```
Macro  →  Sector  →  Stock  →  Operating line  →  Prop check  →  Expectation  →  Technicals  →  Decision
```

| Step | Read | System signal |
|---|---|---|
| **Macro** | Nifty/BankNifty trend, FII/DII, India VIX, RBI rate path, crude, USDINR | `market/regime`, `macro_rates` (repo + 10Y), `raw_fii_dii`, `india_vix` |
| **Sector strength** | Is the sector index beating Nifty? Money rotating in? | `sector_radar` / `sector_state` relative-strength rank |
| **Stock** | Is it the sector leader or laggard? | `quality_score`, relative strength vs sector |
| **Result** | operating line + prop check + KPIs (below) | `result_quality_low/high` |
| **Expectation** | beat/miss vs consensus | S8 (consensus — pending source) |
| **Technicals** | 50/200 DMA, VWAP, volume, RSI | `indicator_live`, ORB/VWAP signals |

**Rule: sector moves before stock.** Never buy a strong stock in a weak sector.

---

## 2. Sector-by-sector result checklist

Each block: **operating line** (what drives it) → **KPIs to read** → **bullish/bearish
signature** → **the prop to watch** → **system status**.

### 2.1 Banking / NBFC (BFSI) — ✅ BUILT
- **Operating line:** Pre-Provision Operating Profit (**PPOP**) and **NII**.
- **Read:** NII & NIM (margin), GNPA/NNPA (asset quality), credit/loan growth, CASA &
  deposit growth, provisions, slippages, PCR.
- **Bullish:** NII↑, NIM↑/stable, GNPA↓, loan growth↑, provisions steady.
- **Bearish:** **PPOP↓** (the real tell), NIM↓, slippages↑, provisions spike.
- **Prop to watch:** PAT up while **provisions↓** (release), **treasury gain**, or **tax
  write-back** — headline flattered, core weak.
- **Worked:** SBI — PAT +5.6% but PPOP −11.4%, provisions −49% → low-quality short.
  Axis — PAT −0.7%, PPOP −6.9%, provisions +159% → outright miss short. HDFC — PPOP +4.6%
  with provision cut → clean, no short.
- **System:** `is_bfsi` fields (NII/PPOP/provisions/treasury/GNPA/NNPA), flags
  `low_quality_beat`, `result_miss`, `provision_propped`, `treasury_hit`. **Done.**

### 2.2 Energy — Oil & Gas / Power — ✅ BUILT (operating-line verdict; KPI extraction pending)
- **Operating line:** **EBITDA / operating profit**, NOT revenue.
- **Read (E&P — ONGC, OIL):** net **crude realization ($/bbl)**, gas price, production
  volume, statutory levy/cess, **dry-well write-offs**, **dividend**.
- **Read (refiners — RIL, IOC, BPCL):** **GRM (gross refining margin $/bbl)**, throughput,
  inventory gain/loss, petchem margins.
- **Read (power — NTPC, Power Grid):** capacity addition, PLF, regulated RoE, capex,
  receivables.
- **Bullish:** GRM↑/realization↑, EBITDA↑, volume↑, capacity↑.
- **Bearish:** realization↓, EBITDA↓, demand weak, big write-offs.
- **Prop to watch:** PAT up on **higher other income** while core EBITDA flat/down.
- **Worked:** ONGC — PAT +3.1% but the entire gain (₹+553 cr other income) exceeded the
  PAT rise (₹+201 cr) ⇒ **core profit fell**; market gapped down ~1.5%. The engine now
  reads **core operating profit ex-other-income (PBT − other income): −11.9% YoY** and
  shorts it with flags `low_quality_beat` + `other_income_propped` + `tax_propped`
  (deferred-tax write-back). The old revenue-proxy LONG is fixed.
- **System:** ✅ verdict BUILT (`sectors/energy.py`, `built=True`). Operating line =
  **true EBITDA** (`pbt + finance_cost + depreciation − other_income`, extracted +
  derived in `from_results.derive_ebitda`), falling back to core-ex-OI then revenue;
  flows into `growth_json` + `quarter_growth`. Flags `low_quality_beat`/`result_miss`/
  `other_income_propped`/`tax_propped`. Regression: `test_energy_ongc.py` (real filing —
  EBITDA −9.3%, core-ex-OI −11.9%, both short). ⏳ **Still pending:** the energy KPIs —
  crude realization $/bbl, GRM, production, dividend (need P7 text/segment ingestion).

### 2.3 IT Services — ✅ BUILT (operating-line verdict; guidance/TCV pending P7)
- **Operating line:** **EBIT margin** + **constant-currency revenue growth**.
- **Read:** cc revenue, **guidance** (the single biggest mover), **deal wins / TCV**,
  EBIT margin, attrition, USD/INR.
- **Bullish:** guidance raised, TCV↑, margin↑, attrition↓.
- **Bearish:** guidance cut, weak US/Europe demand, margin↓.
- **Prop to watch:** PAT up on **other income / forex / lower tax** while cc-revenue &
  margin flat. Headline EPS flattered by buyback. *(IT is a prime other-income-prop case —
  big cash piles mean treasury income can paper over a soft operating quarter.)*
- **System:** ✅ verdict BUILT (`sectors/it_services.py`, `built=True`) on the shared
  operating-quality rule (EBITDA/EBIT operating line + other-income/tax props), hardened
  against **real filings** (INFY Q2 FY26 clean beat → long; TCS Q1 FY26 QoQ flat →
  neutral) — no false short on a healthy IT print with a +30%/+61% other-income jump.
  ⏳ pending: cc-revenue, EBIT margin, TCV, **guidance** (text/NLP, P7 — the dominant IT
  signal); a real low-quality IT filing would further exercise the short branch.

### 2.4 FMCG — ✅ BUILT (operating-line verdict; volume/margin KPIs pending P7)
- **Operating line:** **volume growth** + **EBITDA / gross margin** (NOT revenue —
  inflation fakes revenue growth).
- **Read:** underlying **volume growth**, gross margin, A&P spend, rural vs urban demand.
- **Bullish:** volume↑ + margin↑.
- **Bearish:** volume flat/down with revenue up only on price; margin squeeze.
- **Prop to watch:** revenue up but **volumes flat** (price-led, not demand-led).
- **System:** ✅ verdict BUILT (`sectors/fmcg.py`, `built=True`) on the shared
  operating-quality rule. Validated on the **real ITC Q2 FY26 filing** — the *inverse*
  of the inflation trap: revenue −2.4% YoY but EBITDA +3.5% → clean long (a revenue
  proxy would have misread a clean quarter as weak); synthetic price-led/margin-squeeze
  case exercises the short branch (`test_fmcg_itc.py`). ⏳ pending: volume growth &
  gross margin (press release / deck → P7; `extract_narrative` already lifts
  "underlying volume growth of X%" / UVG phrasing).

### 2.5 Auto — ✅ BUILT (operating-line verdict; volume/realization KPIs pending)
- **Operating line:** **EBITDA margin** + **volume (units)** + **realization/unit**.
- **Read:** wholesale/retail volumes, EBITDA margin, input costs (steel/alu), EV mix,
  demand commentary, inventory.
- **Bullish:** volume↑ + margin↑ + strong demand.
- **Bearish:** inventory build, margin↓ on input costs, demand soft.
- **Prop to watch:** margin up only on commodity tailwind (not durable); PAT on other
  income.
- **System:** ✅ verdict BUILT (`sectors/auto.py`, `built=True`). Validated on the
  **real Maruti Q2 FY26 filing** — the textbook tax prop: PAT +7.3% but PBT −16.7% with
  tax −52.8% (year-ago one-off deferred-tax hit) and EBITDA flat +0.4% → conservative
  neutral + `tax_propped` caveat, no false long and no false short
  (`test_auto_maruti.py`). ⏳ pending: volumes (separate monthly feed) +
  realization/unit.

### 2.6 Pharma — ✅ BUILT (operating-line verdict; FDA/US-sales KPIs pending P7)
- **Operating line:** **EBITDA margin** + **US sales growth**.
- **Read:** US sales, **USFDA observations / warning letters** (binary, huge), product
  pipeline/launches, R&D, EBITDA margin.
- **Bullish:** US growth↑, margin↑, clean FDA.
- **Bearish:** **FDA warning letter / import alert** (very negative regardless of P&L).
- **Prop to watch:** one-off para-IV/launch upside that won't repeat; other income.
- **System:** ✅ verdict BUILT (`sectors/pharma.py`, `built=True`). Validated on the
  **real Cipla Q2 FY26 filing** — EBITDA flat +0.5% with other income +41% and PAT
  +3.7% → conservative neutral, no false OI-prop (the prop requires a fallen core);
  synthetic US-erosion/one-off case exercises the short branch
  (`test_pharma_cipla.py`). ⏳ pending: US sales & FDA status (news/text → P7;
  `extract_narrative` already lifts warning-letter/import-alert/483/EIR phrasing).

### 2.7 Metals — ✅ BUILT (operating-line verdict; per-tonne KPIs pending)
- **Operating line:** **EBITDA / tonne** + **realization**.
- **Read:** realization price, **EBITDA/tonne**, volumes, global commodity & China
  demand, net debt.
- **Bullish:** EBITDA/tonne↑, volumes↑, debt↓.
- **Bearish:** realization↓ on global prices, debt↑.
- **Prop to watch:** PAT on inventory/forex/other income while EBITDA/tonne falls.
- **System:** ✅ verdict BUILT (`sectors/metals.py`, `built=True`). Validated on the
  **real JSW Steel Q2 FY26 filing** — a genuine cyclical upswing, EBITDA +39.6% YoY →
  clean long; synthetic down-cycle/forex-propped case exercises the short branch
  (`test_metals_jswsteel.py`). ⏳ pending: volumes (tonnes) → EBITDA/tonne &
  realization (deck → P7).

### 2.8 Capital Goods / Infra / Defence / Railways — ✅ BUILT (operating-line verdict; order-book KPIs pending P7)
- **Operating line:** **order book / order inflow** + EBITDA margin + execution.
- **Read:** order inflow, **order book (book-to-bill)**, execution/revenue, margin,
  receivables/working capital.
- **Bullish:** order inflow↑, book-to-bill > 1, margin steady.
- **Bearish:** slowing inflow, margin↓, working-capital stretch.
- **Prop to watch:** revenue from order drawdown without fresh inflow; other income.
- **System:** ✅ verdict BUILT (`sectors/capital_goods.py`, `built=True`). Validated on
  the **real L&T Q2 FY26 filing** — EBITDA +7.0%, PAT +14.1% → clean long (the filing's
  order inflow ₹1,15,784 cr +45% corroborates; systematic capture is P7 and
  `extract_narrative` already lifts the order-inflow phrasing). Routing: no
  constituent-backed capgoods index exists, so leaders (LT/BEL/SIEMENS/ABB/BHEL/…) are
  pinned via `base.SYMBOL_TO_CLASS` — this also fixes ABB/SIEMENS/BHEL being filed
  under NIFTY ENERGY by the index data (`test_capgoods_lt.py`). ⏳ pending: order
  inflow/book wired into the verdict (P7 plumbing).

---

## 3. The 2-minute live workflow (during market hours)

When the result PDF hits NSE:
1. **Operating line first** — bank: PPOP & NII; everyone else: EBITDA / operating profit.
   Up or down, YoY **and** QoQ?
2. **Prop check** — is PAT growth explained by a non-core item (provisions / treasury /
   tax / other income / exceptional)? If yes → discount the headline.
3. **Sector KPI** — the one number that matters (bank NIM, refiner GRM, IT guidance,
   FMCG volume, pharma FDA, metal EBITDA/tonne).
4. **Expectation** — beat or miss vs the Street? (the deciding factor for the move)
5. **Management words** — positive: *strong demand, robust pipeline, margin expansion,
   guidance raised, strong order book*. negative: *headwinds, weak demand, margin
   pressure, slowdown, uncertainty*.
6. **Technical confirm** — VWAP / volume / level before acting.

Decision in <3 min: **operating line + prop + surprise** → bullish / bearish / neutral.

---

## 4. System build roadmap (what to add, in priority order)

The BFSI engine proves the pattern. Generalize it:

- [x] **P1 — Universal non-core prop guard (cheap, all sectors).** ✅ DONE.
  `other_income_propped` + `tax_propped` (`sectors/base.py`); non-BFSI **out-of-scope
  guard** routes unbuilt sectors to a low-confidence neutral (`classify_result`, wired
  into `signals/detect.py` + `bot/result_quality_message.py`). Fixed the ONGC false LONG.
- [x] **P2 — Generic operating line for non-banks.** ✅ DONE. **True operating EBITDA**
  (`pbt + finance_cost + depreciation − other_income`, extracted via migration 063 +
  derived in `from_results.derive_ebitda`), falling back to **core-ex-OI** (PBT − other
  income, `derive_core_operating`) then revenue; both flow into `growth_json` +
  `quarter_growth`. `base.generic_operating_growth` routes EBITDA → core-ex-OI →
  operating profit → revenue. Removes the revenue-proxy weakness.
- [~] **P3 — Energy schema.** ✅ verdict on EBITDA + other-income/tax props
  (`sectors/energy.py`, `built=True`, real-ONGC regression). ⏳ remaining: GRM (refiners),
  crude realization & production (E&P), dividend — the energy KPIs (need P7 text/KPI
  extraction). *(ONGC, RIL, IOC, NTPC.)*
- [~] **P4 — IT schema.** ✅ verdict on the EBITDA/EBIT operating line + props
  (`sectors/it_services.py`, `built=True`). ⏳ remaining: cc-revenue, EBIT margin, TCV,
  **guidance** (text, P7 — the dominant IT signal, raise/cut is the biggest mover).
  Hardened against real INFY/TCS filings; a real low-quality IT print would add the
  short-branch case.
- [~] **P5 — FMCG / Auto / Pharma / Metals / CapGoods schemas.** ✅ verdicts BUILT on the
  shared operating-quality rule, each with a real-PDF regression (the SBI/Axis/HDFC
  method): ITC (clean beat long, revenue fell while EBITDA grew), Maruti (tax-propped
  headline → neutral + caveat), Cipla (flat core + OI jump → no false short), JSW Steel
  (cyclical EBITDA +39.6% → long), L&T (clean execution → long). CapGoods routing via
  `SYMBOL_TO_CLASS` override (no constituent-backed index; also un-files
  ABB/SIEMENS/BHEL from NIFTY ENERGY). All 8 registered sectors now `built=True`
  (`test_all_registered_sectors_are_built`). ⏳ remaining: their sector KPIs (volume,
  FDA, EBITDA/tonne, order book) — P7 plumbing.
- [~] **P6 — Consensus (S8).** ✅ UNBLOCKED — four sources wired (user call:
  implement them all, accuracy first), lookup **merges field-wise** in accuracy
  order (`consensus.SOURCE_RANK`): **manual CSV** (broker previews, migration 065
  NII/NIM) → **news** (broker previews read out of articles: Bing News RSS →
  publisher page → LLM extraction with preview-only framing + year-ago sanity band —
  the *automated* NII/NIM path) → **Moneycontrol** (quarterly rev/PAT/EPS ₹ cr) →
  **Yahoo** (EPS/revenue, INR-guarded). Field-wise merge means news NII never masks
  MC's PAT. Nightly job 20:05 IST for next-10-day reporters; `matcher.py` flips
  `surprise_basis` to 'consensus' automatically. Verified live: MC and Yahoo agree
  to the crore on INFY/TCS revenue; the news LLM read extracts PAT/NII/NIM from
  preview prose while rejecting post-result articles and year-ago comparisons.
  ⏳ remaining: some publishers (business-standard, zeebiz) TLS-block bot fetches —
  accepted misses; real news-yield check comes next results season. Per-sector
  estimate lines beyond NII/NIM (IT guidance numbers, energy EBITDA) and mid-cap
  coverage (manual path is the backstop).
- [~] **P7 — Press-release / deck text ingestion.** ✅ first cut + plumbed:
  `parsers/narrative/extract_narrative` lifts guidance (raised/cut/maintained), volume
  growth %, order inflow ₹cr, FDA status (import-alert > warning-letter > 483 > EIR),
  dividend ₹/share, and §3.5 management tone via conservative regexes (None unless
  plainly stated; `test_narrative_extractor.py`, phrasing from real filings).
  **Wired live:** at extraction, `from_results.narrative_for_fingerprint` reads the
  filing's `raw_announcements.pdf_text` → `narrative_json` on `extracted_financials`
  (migration 064); the detector and alert card pass it into
  `classify_result(..., narrative=...)`, which folds it in via `base.apply_narrative`
  (sector-gated): pharma **FDA warning-letter/import-alert → SHORT regardless of P&L**
  (§2.6); **guidance cut** (non-BFSI) → SHORT on a non-beating quarter / mixed-neutral
  on a beat, **guidance raised** upgrades a clean flat quarter to LONG (never rescues a
  miss or a propped print); FMCG/auto **price-led volume** (revenue up, volumes ≤0) caps
  a long to neutral. Card shows a 📰 narrative line (guidance · volumes · order inflow ·
  FDA · dividend · tone). E2E: `test_narrative_plumbing.py` — a flat INFY P&L + guidance
  cut now fires `result_quality_low`/SHORT where the P&L-only engine stayed silent;
  semantics in `test_narrative_verdict.py`.
  **Accuracy pass (sibling attachments + LLM-first, by user call — accuracy over LLM
  cost):** `narrative_for_filing` merges the result PDF's text with sibling **Press
  Release / Investor Presentation** rows (same symbol, −2h…+6h window; their text is
  already collected at `medium` priority), field-wise by source priority *press release
  → result PDF → deck*, with `_sources`/`_source_fps` provenance.
  Extraction is **LLM-first** (`narrative/llm_narrative.py`, gpt-4o JSON mode, same
  budget-capped client as the P&L vision path) reconciled against the regexes: LLM wins
  categoricals (guidance/FDA/tone), the **regex wins numeric unit disputes** (>30%
  disagreement — it read the printed phrase, so it can't slip on ₹ cr vs $ mn), regex
  fills gaps, and the whole layer degrades to pure regex offline. **Image-only decks**
  go through a vision read (first 8 pages). Late siblings: `refresh_narratives` on the
  intraday 5-min tick re-merges recently extracted rows — the `_source_fps` cache means
  zero LLM spend unless a new sibling actually landed; a verdict that flips inside the
  detector's 30-min lookback fires on its next tick.
  **Sector KPIs** now extracted (cc-revenue %, TCV $mn, attrition %, GRM $/bbl,
  EBITDA/tonne ₹, US-sales %, order book ₹cr) — **card-context only** (📰 line with
  source attribution); promotion to verdict inputs waits on observed live accuracy.
  Tests: `test_llm_narrative.py` (reconciliation policy), `test_narrative_plumbing.py`
  (sibling merge/window/precedence, vision-deck path, refresh + caching).
  ⏳ remaining: validate KPI reads on live result days, then promote the proven ones
  (cc-revenue first) into the sector verdicts.

**Per-sector schema = the S1 pattern repeated:** `sector_class` → canonical operating
fields → sector verdict rule, mirroring `is_bfsi`. Each new sector is one schema + one
rule + one real-PDF regression (the SBI/Axis/HDFC method).

---

## 5. Honest limits (don't let the dashboard imply more)

- **The engine reads operating-line *quality* for all 8 mapped sectors** (PPOP for
  banks, EBITDA/core-ex-OI for non-banks) — BFSI (SBI/Axis/HDFC), Energy (ONGC), IT
  (INFY/TCS), FMCG (ITC), Auto (Maruti), Pharma (Cipla), Metals (JSW), CapGoods (L&T),
  each validated on a real filing. The narrative is read **LLM-first across the result
  PDF + sibling press release / deck** (P7 accuracy pass) — guidance/FDA/volume move
  verdicts; the sector KPIs (cc-revenue, TCV, GRM, EBITDA/tonne, US sales, order book)
  are extracted but **card-context only** until their live accuracy is proven, so a
  result that turns on a KPI the verdict rules don't yet consume can still be read
  neutral. The narrative read is best-effort: with the LLM down it degrades to
  phrasing-bound regexes. Unmapped symbols and unclassified indices (e.g. realty) stay
  out-of-scope neutral (P1 enforces this).
- **No leak/forecast.** "Catch the signal" = react within minutes of disclosure faster
  than full repricing, and flag pre-print sector risk — not predict the print.
- **Beat/miss is now real where estimates exist** (P6: manual > Moneycontrol > Yahoo,
  nightly for upcoming reporters) — but live coverage thins below large-caps and only
  the manual path carries bank NII/NIM; where no estimate row exists the engine still
  falls back to the YoY-trend proxy and reads *quality*, not *surprise vs Street*.
- **Guidance / volumes / FDA / order book aren't in the P&L** — they need text ingestion
  (P7); the P&L-only engine will miss results that turn on those.
