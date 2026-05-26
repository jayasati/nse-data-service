# NSE Trading System — Feature Checklist

Working inventory of every capability the system needs, mapped against current build state.

**Legend:**
- ✅ **Implemented** — shipping, tested, in production scheduler
- 🔧 **Needs polish** — built but has known gaps, edge cases, or refactor debt
- 📋 **To do — planned** — in architecture spec, not yet built
- 🔬 **Research / deferred** — flagged in research doc as Phase 9+, deliberately not chasing
- ❌ **Deprecated / superseded** — was planned, but the need is covered elsewhere or the source died; kept for history

**Phase anchor (as of 20-May-2026):** Layers 1–2 complete (32 collectors live). Layer 3 (PDF parsing + retention) is the active work. Layers 4–7 unblocked but unstarted.

---

## 1. Foundation & Infrastructure

### Layer 1 — Session Manager

- ✅ Single `SessionManager` as the only NSE network boundary
- ✅ 3-hop cookie warm-up (`/` → market-data page → `/option-chain`)
- ✅ 15-minute cookie TTL with automatic re-warm
- ✅ 401/403 retry with session re-warm
- ✅ 429 exponential backoff with jitter, respects `Retry-After`
- ✅ Global concurrency semaphore (4 in-flight max)
- ✅ Per-endpoint rate limiter
- ✅ Circuit breaker per endpoint name
- ✅ `get_json` tolerates empty bodies / parse failures (returns `None`)
- ✅ `get_text` for CSV endpoints
- ✅ `get_bytes` for ZIPs and PDFs (handles `nsearchives.nseindia.com`)
- ✅ Brotli decompression support
- 🔧 Header rotation pool — single UA today; rotate when NSE tightens fingerprinting
- 📋 TLS fingerprint fallback via `curl_cffi` — only if NSE escalates bot detection

### Collector Framework

- ✅ Five archetypes (Snapshot, Event, CSV, Reference, Fanout) covering all 32 collectors
- ✅ Pure-function `normalize()` contract (no side effects)
- ✅ Per-call error isolation in `FanoutCollector`
- ✅ Run reports with insert/update/dedup/remove tracking
- ✅ Market-hours gating
- ✅ Holiday calendar (`scheduler/market_hours._TRADING_HOLIDAYS`)
- ✅ Thread-portable DB pattern (each runner opens own connection)
- ✅ **Scheduler job-registration layer** (`scheduler/jobs.py:register_jobs` + `_load_collector`) — maps each enabled `endpoints.yaml` entry → APScheduler `CronTrigger(timezone=IST)`. Handles sub-minute (`15s/30s` → `CronTrigger(second="*/N")`, tight grace — for GIFT Nifty), interval cadences (`3m/5m/10m/30m/1h`, hour-bounded to the active window), daily/weekly `run_at`, and a **multi-time `run_at` list** (one job per time, id-suffixed `@HH:MM` — ready for `call_auction` 09:05 + 10:05). Runtime gate ANDs `market_hours_only` / `trading_day_only` / `active_hours`, evaluated against `now_ist()` so holidays the cron can't see are still skipped. Malformed entries are logged and skipped, not fatal. Tested in `tests/scheduler/test_register_jobs.py`.
- ✅ Timezone-aware cron triggers (every job carries `ZoneInfo("Asia/Kolkata")`)
- 📋 New "DBJob" archetype for Layer 3+ (DB → DB jobs, not network → DB)
- 📋 Yearly NSE holiday refresh automation (currently manual)

### Storage Layer

- ✅ SQLite with migrations applied at scheduler boot
- ✅ Four write strategies: `upsert_many`, `insert_ignore`, `replace_all`, `diff_upsert`
- ✅ Fingerprint-based dedup for event collectors
- ✅ `endpoint_health` and `fetch_log` for observability
- ✅ Redis-backed dedup hot-set (`storage/cache.py`) — `MemoryDedupCache`/`RedisDedupCache`, wired into `EventCollector`; SQLite stays source of truth so cache loss is perf-only. (`redis` is an optional dep; falls back to memory.)
- ✅ File layout helpers (`storage/files.py`) for PDF archive routing — single source of truth for tier paths (`pdfs/<YYYY>/<MM>/<DD>/`, `pdfs_temp/`, `scratch/`), date bucketing, and atomic write; `retention/` + `parsers/job.py` delegate to it
- ✅ `scripts/migrate.py` — apply pending migrations against the live DB without a scheduler restart (`--status` to inspect). Closes the manual-restart annoyance for the common case.
- 🔧 Migration *auto*-application still boot-only — `scripts/migrate.py` is manual. **When to implement:** fold into the new "DBJob" archetype as a periodic `apply_migrations` job once that archetype lands (Layer 3+ framework item), so a hot-added migration self-applies without any human step.
- 📋 Postgres migration path documented. **When to implement:** when SQLite shows write-lock contention under WAL or any raw table passes ~5–10M rows (option_chain / announcements archive are first candidates). Doc must cover dialect diffs (`AUTOINCREMENT`, `strftime` → `EXTRACT`/`to_timestamp`, `INSERT OR IGNORE` → `ON CONFLICT DO NOTHING`, `executescript` multi-statement), a `models.py`/`db.py` connection abstraction, and a CSV/`pgloader` data-export plan.
- 📋 Hot-path read-through cache for query responses (distinct from the dedup hot-set above). **When to implement:** alongside Layer 7 API — cache hot `GET /profile/{symbol}`, `/blacklist`, `/universe` reads in Redis with short TTLs. No value before the API exists.

---

## 2. Data Collection (Layer 2)

### Equity Live Market

- ✅ `live_equity_nifty50` — 5m intraday quotes
- ✅ `indices` + `raw_advances_declines` — 5m
- ✅ `gainers`, `losers` — 5m
- ✅ `most_active_volume`, `most_active_value` — 5m
- ✅ `high_52w`, `low_52w` — 10m
- ✅ `price_band_upper`, `price_band_lower` — 3m
- ✅ `pre_open` (`/api/market-data-pre-open?key=ALL`) — 09:08 IST gap detection; SnapshotCollector → `raw_pre_open`, per-symbol IEP/change/ATO. Scheduled via `register_jobs` (daily 09:08, `trading_day_only`). Built + tested.
- ✅ `call_auction` (illiquid-securities **list**, `/api/live-watch-call-auction`) — daily ReferenceCollector (diff, `key_cols=(symbol,)`) → `raw_call_auction`, capturing the symbols under periodic call auction + per-session price/qty. Membership add/remove is the "started/stopped being illiquid" signal for Layer 6's exclude-from-signals flag. Scheduled daily 15:45 (`trading_day_only`, after sessions close). Endpoint discovered from the `stocks-in-call-auction` page XHR. Built + tested.

### Derivatives

- ✅ `oi_spurts` — 5m (live signal substrate)
- ✅ `option_chain` (NIFTY, BANKNIFTY, FINNIFTY, watchlist) — 5m via `/api/option-chain-v3`
- ✅ `most_active_fno_volume`, `most_active_fno_value` — 5m, **per-contract** granularity via `/api/snapshot-derivatives-equity?index=contracts` (rows carry instrument/expiry/strike/option_type → `raw_most_active_fno`). This *is* the "most-active F&O contracts (not underlyings)" item — confirmed live 26-May-2026.
- ❌ `derivatives_watch` — **deprecated / superseded** (decided 26-May-2026). The original broad live-derivatives snapshot (`/api/liveEquity-derivatives`, no params) is gone: the endpoint now only serves `index=top20_contracts` (+`top20_spread_contracts`) — confirmed via `equityDerivatives.js` — which is redundant with `most_active_fno`. Per-underlying (`index=<SYMBOL>`) → 500; old `/api/quote-derivative?symbol=` → 404; `/api/snapshot-derivatives-equity` only exposes most-active slices. A per-underlying futures watch would need a per-symbol fanout, but the existing surface already covers the need: **`option_chain`** (full chains for watchlist) + **`oi_spurts`** (underlying OI) + **`most_active_fno`** (top contracts). Empty stub `collectors/derivatives_watch.py` removed. Revisit only if a single all-underlyings futures feed reappears or per-symbol futures (LTP/OI/basis) becomes a hard requirement.

### Large Deals & Flow

- ✅ `large_deals` (live snapshot, 30m) — captures bulk + block + **short** deals from `/api/snapshot-capital-market-largedeal` (`SHORT_DEALS_DATA` → `raw_large_deals` with `deal_type='short'`).
- ✅ `fii_dii` — daily 19:00
- ✅ Short selling stats (EOD) — **covered by `large_deals`**, not a separate collector. Verified 26-May-2026: no dedicated endpoint exists (`/api/short-selling` and `/json/short-selling.json` both 404); the data is the `SHORT_DEALS_DATA` block in the large-deals snapshot (per-stock `{date, symbol, name, qty}`, ~92 rows/day, T-1). Query `raw_large_deals WHERE deal_type='short'`. *(If Layer 6 ever needs richer short-interest fields than qty, revisit — but the EOD short qty per stock is captured.)*

### Corporate Filings

- ✅ `announcements_equity` — 5m, full archive feed
- ✅ `board_meetings` — 30m
- ✅ `corporate_actions` — 1h
- ✅ `financial_results` — 1h (returns ~3,800 row archive each call; dedup handles it)
- ✅ `insider_trading` — 1h
- ✅ SME announcements feed (10m) — `SmeAnnouncements(Announcements)` with `index=sme`/`segment='sme'` → `raw_announcements` (reuses the equity table + Layer 3 PDF pipeline). `Announcements` parameterized on `index`/`segment`; equity behavior unchanged. Built + tested 26-May-2026.
- ✅ Debt announcements feed (30m) — `DebtAnnouncements` (`index=debt`) → shared table **`raw_nonequity_announcements`** (segment='debt'; migration 015), not `raw_announcements` (whose `symbol` is NOT NULL; debt rows are symbol-null). **Metadata-only** — keeps `attachment_url` but stays out of the Layer 3 PDF pipeline (low equity signal). Built + tested 26-May-2026.
- ✅ MF announcements feed (1h) — `MfAnnouncements` (`index=mf`) → same `raw_nonequity_announcements` table (segment='mf'). Shares a `_NonEquityAnnouncements` base with debt. Discovery quirks handled: mf ETF rows **do** carry a `symbol` (e.g. `ITBEES`, kept), and the feed **reuses `seq_id`** across an ETF-tagged + untagged variant of one disclosure — so the fingerprint is a content tuple (segment|seq_id|symbol|company|subject|broadcast_dt|attachment), not seq_id alone. Built + tested 26-May-2026.
- ✅ `shareholding_pattern` — endpoint **found** (the arch path had a typo): `/api/corporate-share-holdings-master?index=equities|sme` (hyphenated `share-holdings`; the old `shareholdings` 404s). `ShareholdingPattern` ReferenceCollector (diff, key=symbol) over both segments → `raw_shareholding_pattern` (migration 017): promoter% (`pr_and_prgrp`) / public% / employee-trust% / qe-date / ISIN / XBRL per symbol (~2,300 equity + SME). Promoter-stake moves surface as `diff` updates; feeds §5 Fundamentals. Weekly Sun 07:30. Built + tested 26-May-2026.
- ✅ Integrated filings (financial + governance) — weekly. `IntegratedFilings` issues both `type=` requests (`Integrated Filing- Financials` + `…- Governance`) to `/api/integrated-filing-results?size=500` → `raw_integrated_filings` (migration 016), dedup by `filing_type|seq_id`. Each type is a ~20k newest-first archive; pulls the latest `page_size` (500, tunable) per run and dedups, like `financial_results`. Captures iXBRL/XBRL URLs, qe_date, audited/consolidated, Original-vs-Revision. Scheduled `Sun 08:00`. Built + tested 26-May-2026.

### Surveillance (Blacklist Source)

- ✅ `surveillance_gsm` — daily 20:00
- ✅ `surveillance_asm_lt` — daily 20:05
- ✅ `surveillance_asm_st` — daily 20:10
- ✅ `blacklist` SQL view over the three surveillance tables
- ✅ Price band stages (2/5/10/20%) — `PriceBandMaster` ReferenceCollector reads the daily CSV `nsearchives.../content/equities/sec_list.csv` (fixed URL) → `raw_price_bands` (symbol+series, band INT, remarks). Pulled 08:45 pre-open, `trading_day_only`. diff surfaces a security tightening (band 20→2). Built + tested 26-May-2026.
- ✅ T2T segment — **covered by `raw_price_bands.series`**: the same sec_list.csv carries the trade-for-trade / restricted classification (EQ = rolling; `BE`/`BZ`/`ST` = T2T). Query `raw_price_bands WHERE series IN ('BE','BZ','ST')`. No separate feed needed.
- ✅ Unsolicited messages / SEBI pump-dump flags — `UnsolicitedWatchlist` reads the XLSX `nsearchives.../inline-files/Current_list_of_symbols_1.xlsx` (openpyxl) → `raw_unsolicited_watchlist`, **unioned into the `blacklist` view** (feed='UNSOLICITED'). Daily 20:15, `trading_day_only`. The watchlist is often empty, so this collector sets `persist_empty=True` (new base flag) — an empty fetch clears the table/blacklist instead of being skipped (guarded against transient fetch failures). Built + tested 26-May-2026. *(Related `/api/rumourVerification` noted but not the same thing.)*
- 📋 Suspended securities list — daily CSV on the surveillance page (no JSON API). Needs the sec_list-style download URL captured before building (likely a fixed `nsearchives.../content/equities/*.csv`).
- 📋 Delisting list — same as suspended: a downloadable CSV; needs the file URL.
- 🔧 `blacklist` stays a SQL view — now unions **4** feeds (GSM + ASM-LT + ASM-ST + UNSOLICITED; migration 019 rebuilt it). Still ~250 rows, far under the ~10K materialize threshold. `raw_price_bands` (~3,300 rows) is a *classification* table, deliberately **not** unioned in — Layer 6 JOINs it for band/series filtering (e.g. exclude band≤2 or T2T series) rather than treating every banded security as blacklisted.

### Reference Data

- ✅ `fno_list` — weekly Sun
- ✅ `index_members` (multiple indices) — weekly Sun
- ✅ `quote_metadata` (sector, ISIN, market cap) — weekly Sun
- 📋 All-securities master (`/market-data/securities-available-for-trading`) — weekly
- 📋 NSE holiday master pull (`/api/holiday-master?type=trading`) — yearly

### Primary Market

- ✅ `primary_market` (upcoming IPO/OFS/rights/NCB) — daily 07:00
- ✅ `new_listings` — daily 09:00 (with auto-blacklist 30d intent)
- 🔧 Auto-blacklist for new listings — collector lands rows; the "exclude from signals for 30 days" rule isn't wired yet (Layer 6 work)

### End of Day

- ✅ `bhavcopy_cm` — daily 17:30 (the truth source)
- ✅ `bhavcopy_fo` — daily 18:00 (uses new udiff `BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv.zip` URL)
- ✅ `volatility_report` — daily 18:30

### External / Macro Sources

- ✅ Macro adapter for USDINR, Brent, Gold, US indices (S&P/Nasdaq/Dow) — daily 18:00 → `raw_macro` (for macro_regime). `MacroCollector` fetches Yahoo's JSON chart API (`query1.finance.yahoo.com/v8/finance/chart/<sym>`) **directly via httpx, not the `yfinance` library** — avoids a heavy scraping dep, httpx is already in the stack. Lives outside the NSE `SessionManager` (Yahoo ≠ NSE; overrides `run()` with per-ticker error isolation). Intentionally **not** `trading_day_only` — global markets move on NSE holidays, which is when macro matters. Keyed (asset, as_of_date); upserts per day. Built + tested 26-May-2026.
- ✅ `screener_in` scraper — weekly fundamentals → `raw_fundamentals_screener`. `ScreenerFundamentals` scrapes `screener.in/company/<SYM>/` (robots-permitted) via httpx, **watchlist-scoped** (universe.yaml `watchlist`), polite (weekly, sequential, 1s gap). Regex-parses #top-ratios (ROCE, ROE, P/E, market cap, book value, dividend yield) + `ranges-table` 3y CAGR (sales + profit) — no parser dep. External (overrides `run()`, per-symbol isolation). Sun 08:30. Built + tested 26-May-2026. **Parse-health guard:** if a fetched page lacks `#top-ratios` or the block yields no recognized ratios (markup drift), it raises `ScreenerParseError` → recorded as a per-symbol failure on the RunReport instead of silently inserting an all-NULL row (a partial page missing one metric still yields a row). Makes scraper breakage loud/monitorable. **Gap:** `debt_to_equity` only captured when screener lists it; balance-sheet-derived D/E is a later enhancement. **Most-robust alternative (deferred):** compute ROE/ROCE/D/E from NSE XBRL (`integrated_filings`/`financial_results`) — authoritative, non-scraped, but a Layer-4 build and 3y CAGR needs accumulated history.
- ✅ **GIFT Nifty** (NSE IX) — every 30s during 06:30–09:15 IST (research Pillar 2). `GiftNifty` polls `nseix.com/api/nifty-market-rate` (REST, no token) via httpx (external, outside the NSE session) → `raw_gift_nifty` (tick series keyed `(index_name, as_of)`; CURRVALUE/open/close/change/pct). Required adding **sub-minute cadence** to `register_jobs` (`30s`/`15s` → `CronTrigger(second="*/N")`, tight 15s misfire grace), gated to the 06:30–09:15 window (`trading_day_only`). Built + tested 26-May-2026.
- ✅ **India VIX with expected-range computation** — `IndiaVix` SnapshotCollector reads `/api/allIndices` (same payload as `indices`; carries both `INDIA VIX` and `NIFTY 50` spot) → `raw_india_vix` (migration 023). On every poll it derives the 1σ daily move (`expected_move_pct = VIX/√252`) and the 1σ/2σ price envelopes around the Nifty spot (`sigmaN_upper/lower`). `expected_move_pct` stored raw so downstream can rescale to a calendar-day √365 convention without re-fetching. NSE-session collector (normal pipeline), 5m `market_hours_only`. Built + tested 26-May-2026.
- ✅ NSDL FPI custodian flow — EOD. `NsdlFpi` scrapes NSDL's "Daily Trends in FPI Investments" (`fpi.nsdl.co.in/web/Reports/Latest.aspx`, HTML, no JSON API) via httpx (external, outside the NSE session, like `screener`/`macro`) → `raw_nsdl_fpi_daily` (migration 024). Captures the full asset-class × investment-route grid — Equity / Debt-General / Debt-VRR / Debt-FAR / Hybrid / Mutual Funds / AIFs, each split Stock-Exchange / Primary-market / Sub-total, plus grand Total — with gross purchase/sale + net (₹ Cr), net (US$ mn), and the day's USD→INR rate. Negatives arrive parenthesized (`(193.61)`); rowspan'd date/asset/conversion cells handled by cell-count row classification. Daily 20:30 EOD, `trading_day_only`; idempotent upsert on (date, asset_class, route) so a confirmed revision overwrites the provisional. **Parse-health guard:** `NsdlFpiParseError` if the report date/table is missing or yields zero rows (markup drift) → recorded as a run failure, never a silent empty persist. Built + tested 26-May-2026 against a captured fixture. **Note:** NSDL has NO daily *per-custodian* flow — that's the **monthly AUC** report (`ReportDetail.aspx?RepID=22`, holdings not flow), a separate future item; this is the daily custody-side flow and is the value-add over the exchange-side `fii_dii` (debt/MF breakdown + USD).
- 📋 Mutual fund daily portfolio disclosures (AMFI) — daily/monthly
- 📋 SLB (Securities Lending & Borrowing) — daily, short-interest proxy
- 📋 Participant-wise OI report (FII/DII/Pro/Client × instrument) — EOD ~19:00 IST
- 📋 MWPL trajectory per F&O stock — intraday + EOD
- 📋 F&O ban list daily — EOD, hard exclude from intraday universe

### Collector Hygiene

- ✅ `_none_if_dash` for NSE's `"-"` NULL convention — used per-collector
- 🔧 Lift `_none_if_dash` and numeric coercers (`_f`, `_i`) to `collectors/_utils.py` (next time a collector needs them)
- ✅ Field-name fallback chains (`item.get("comapnyName") or item.get("companyName")`)
- ✅ Date strings stored verbatim in raw tables, parsed only in Layer 4+
- ✅ `scripts/verify_endpoints.py` daily sanity check
- 📋 Automated alert when an endpoint's response shape changes (field-set diff vs last known)

---

## 3. Parsing & Retention (Layer 3) — Active Work

### PDF Pipeline Foundation

- 📋 `parsers/classify.py` — subject → priority bucket (high/medium/low/skip)
- 📋 `config/priority.yaml` template populated with NSE subject vocabulary
- 📋 `config/retention.yaml` with three-tier rules
- 📋 PDF downloader with size cap (some result PDFs are 100+ MB)
- 📋 Hot cache so multiple parsers see the same bytes once
- 📋 Three-tier archive routing (`pdfs/<YYYY>/<MM>/<DD>/`, `pdfs_temp/`, discard)
- 📋 `pdf_status` state machine (`pending → parsed → archived → discarded`)
- 📋 Nightly cleanup job at 02:00 IST (delete medium-priority PDFs > 30d)

### Text Extraction

- 📋 `parsers/pdf_text.py` — pdfplumber + pymupdf primary path
- 📋 Scanned-PDF detection → flag with `pdf_error='ocr_required'`, skip OCR for now
- 📋 Always-retained `pdf_text` column populated for all parsed PDFs

### Financial Number Extraction (the main investment per `layer3` notes)

- 📋 Multi-strategy extractor: camelot lattice → camelot stream → pdfplumber tables → pymupdf+regex
- 📋 Confidence scoring per strategy; ensemble picks highest non-empty
- 📋 `extraction_failed` flag when all strategies < threshold
- 📋 Schema-aware extraction via `field_aliases.yaml` (canonical names per row label)
- 📋 Validation layer (revenue > 0, profit magnitude < revenue, segment sum ≈ total)
- 📋 `extractors/quirks/<symbol>.py` slot for per-company override patterns
- 📋 Eval harness: 50+ hand-labeled fixture PDFs, accuracy per company / field / strategy
- 📋 Target: 95% accuracy on F&O result PDFs against labeled fixtures

### Sentiment

- 📋 `parsers/sentiment.py` v1 — Loughran-McDonald lexicon (cheap, fast)
- 📋 v2 — FinBERT swap-in
- 🔬 LLM-based sentiment for ambiguous cases — Phase 9+ (cost, latency, dependency concerns)
- 🔬 Vision-LLM PDF reading (GPT-4V / Claude Vision) — Phase 9+ experiment, not foundation

---

## 4. Indicators, Patterns, Levels (Layer 4)

### Technical Indicators

- 📋 `indicators/compute.py` nightly job from `raw_bhavcopy_cm`
- 📋 Trend: SMA 20/50/200, EMA 9/21
- 📋 Momentum: RSI 14, MACD + signal + histogram
- 📋 Volatility: ATR 14, Bollinger Bands (upper/lower/width)
- 📋 Trend strength: ADX 14, DI+, DI−
- 📋 Volume: volume SMA 20, OBV, volume ratio
- 📋 Use `pandas-ta` for the 130+ indicator library
- 📋 Per-stock incremental compute (don't recompute history nightly)

### Patterns

- 📋 `indicators/patterns.py` — inside bar, outside bar, gaps, consolidation
- 📋 Higher-high/higher-low/lower-high/lower-low structural detection
- 📋 Volume dry-up detection
- 📋 Support/resistance proximity flags
- 🔬 ICT/SMC liquidity zones, fair value gaps, order blocks — research-flag only

### Levels

- 📋 `indicators/levels.py` — 52w high/low + days since
- 📋 PDH/PDL, 5d/20d high-low ranges
- 📋 Round-number proximity
- 📋 Anchored VWAPs (from prior swing, gap fill, earnings) — research Pillar 1 #4

### Regime Classifiers

- 📋 `indicators/regime.py` — trend_regime (strong_uptrend / uptrend / sideways / downtrend / strong_downtrend / choppy)
- 📋 `momentum_state` (overbought_extreme / overbought / bullish / neutral / bearish / oversold / oversold_extreme)
- 📋 `volatility_state` (compression / normal / expansion)
- 📋 `market_regime` (risk_on / risk_off / neutral) — depends on macro adapter
- 🔬 Hidden Markov Model regime states with transition probabilities — Phase 9+

### Relative Strength

- 📋 `indicators/relative_strength.py` — stock vs index, sector RS rank
- 📋 5-day RS-ratio for top-quintile / bottom-quintile filtering
- 📋 Daily RRG (RS-Ratio + RS-Momentum) for 11 NSE sectoral indices — research Pillar 1 #6

### Options Greeks (currently missing — research priority)

- 📋 Greeks computation on every 3-min option chain snapshot via `mibian` or `blackscholes`
- 📋 Delta, gamma, theta, vega, vanna, charm per strike
- 📋 Net dealer gamma (GEX) per strike — sign drives intraday tape regime
- 📋 Total vega, total charm, delta-adjusted OI as Layer 5 features
- 📋 Black-76 model for futures-settled index options
- 📋 Max-pain strike computation per expiry

---

## 5. Fundamentals (Layer 4)

- 📋 `fundamentals/from_nse_quote.py` — sector, P/E, market cap from `/api/quote-equity`
- 📋 `fundamentals/from_results.py` — latest quarter from your own parsed result PDFs (depends on Layer 3 extractor)
- 📋 `fundamentals/from_screener.py` — weekly ROE, ROCE, debt/equity, 3y CAGR
- 📋 `fundamentals/quality_score.py` — composite 0–100 rating
- 📋 Loss-making / high-debt / pledged flags
- 📋 Promoter holding / promoter pledge / FII / DII / public split
- 📋 YoY and QoQ growth rates for revenue, profit, margins

---

## 6. Events (Layer 4/5)

- 📋 `events/calendar.py` — populate `pending_events` from board meetings
- 📋 `events/matcher.py` — match incoming filing to pending event
- 📋 `events/pre_screen.py` — T-1 technical pre-screen for watchlist events
- 📋 `technical_setup_score` populated by pre-screen
- 📋 Event proximity columns in `stock_profile_daily` (next_event_type / next_event_date / days_to_next_event)
- 📋 Auto-avoid trading rule for stocks within N days of earnings — bot decision layer

---

## 7. Stock Profile (Layer 5)

- 📋 `profile/builder.py` — nightly composer reading indicators + patterns + fundamentals + sector RS + flags
- 📋 `stock_profile_daily` table populated with ~60 columns per stock per day
- 📋 Profile versioning (`profile_version` column for replay)
- 📋 Index on `(as_of_date, quality_score DESC)` and `(as_of_date, trend_regime)`
- 📋 `GET /profile/{symbol}` API
- 📋 `GET /profile/{symbol}/history?days=30`

---

## 8. Signal Engine (Layer 6)

### Live Signal Detection

- 📋 `signals/detect.py` — main dispatcher
- 📋 `signals/compute.py` — OI delta, volume ratio, breakout math
- 📋 `signals/dedup.py` — alert-level fingerprinting
- 📋 `signals/enrich.py` — JOIN with `stock_profile_daily` at detection time
- 📋 `signals/feature_store.py` — write `signal_features` row at detection (snapshot for ML)

### Derivative Patterns

- 📋 `long_buildup`, `short_buildup`, `long_unwinding`, `short_covering`
- 📋 Requires Layer 4 JOIN (oi_spurts gives OI snapshot only, no price change — see LEARNINGS)

### Price-Action

- 📋 `breakout_52wh`, `breakdown_52wl`
- 📋 `volume_surge`
- 📋 `upper_circuit`, `lower_circuit`
- 📋 `gap_up_go`, `gap_up_fade`
- 📋 `inside_bar_breakout`
- 📋 **Fake-breakout filter**: wick rejection >50%, volume <1.2× 20-bar avg, VWAP-slope check — research Pillar 1 #3

### Options Signals

- 📋 `pcr_extreme_low`, `pcr_extreme_high`
- 📋 `max_pain_drift` (last 4h of expiry day)
- 📋 `iv_spike`
- 📋 `gamma_squeeze_risk`
- 📋 `heavy_ce_writing`, `heavy_pe_writing`

### Institutional Flow

- 📋 `block_buy_institutional`, `block_sell_institutional` (requires named-institution recognition)
- 📋 `bulk_accumulation`
- 📋 `fii_heavy_day`

### Event-Driven

- 📋 `result_beat`, `result_miss` (depends on Layer 3 extractor)
- 📋 `dividend_announced`
- 📋 `order_win`
- 📋 `acquisition`
- 📋 `mgmt_change`
- 📋 `qip_announcement`
- 📋 `pre_result_accumulation` (pending event + technical_setup_score > 0.7 + OI buildup)

### Microstructure

- 📋 `liquidity_grab_long`, `liquidity_grab_short`
- 📋 `climax_top`, `capitulation_bottom`
- 📋 `dead_cat_bounce`

### Multi-Timeframe Confluence Gate

- 📋 Daily-trend + 1H-structure + 15m-setup alignment check (research Pillar 1 #1)
- 📋 Reject any long where daily trend is bearish and vice versa

---

## 9. ML / Labeling Infrastructure (Layer 6)

### Outcome Labeling

- 📋 `signals/outcome_labeler.py` — nightly: T+30m, T+2h, T+EOD, T+1d, T+3d returns
- 📋 MAE / MFE per signal (research Pillar 3 — distributions drive optimal SL/TP)
- 📋 `paper_trades` table — every alert logged as if traded
- 📋 Hit-1pct-target-by-2h, hit-stop-by-2h binary outcomes

### Labeling — Production Methods (from research)

- 📋 **Triple-barrier labeling** (López de Prado Ch. 3) with dynamic σ_t (intraday 20-bar realized vol)
- 📋 Vertical barrier at 15:20 IST forced flat
- 📋 Uniqueness-of-label weighting (down-weight overlapping labels)
- 📋 Return-attribution weighting (emphasize trades that mattered)

### Validation

- 📋 **CPCV (Combinatorial Purged CV)** as default — `mlfinlab` or `timeseriescv`
- 📋 Purge horizon = max label span
- 📋 Embargo buffer after each test segment
- 📋 **Deflated Sharpe Ratio** alongside raw Sharpe (Bailey & López de Prado)
- 📋 **Probability of Backtest Overfitting (PBO)** reported per strategy
- 📋 Walk-forward as sanity check only, not primary validator

### Models

- 📋 **LightGBM** as primary (one model per signal type, never one mega-model)
- 📋 CatBoost backup for high-cardinality categoricals
- 📋 Logistic regression baseline on top features (sanity check)
- 🔬 Transformers / TFT / N-BEATS — research doc explicitly says skip; 10× compute mistake for tabular intraday
- 🔬 Reinforcement learning (entry/exit/sizing) — FinRL contest organizers themselves flag policy instability; Phase 9+ only, and only for execution slicing
- 🔬 Genetic algorithm strategy search — high overfit risk, no Indian benchmark
- 🔬 Graph Neural Networks for cross-stock spillover — research, no proven retail intraday alpha

### Meta-Labeling

- 📋 Primary model = rule engine produces {long, short, skip}
- 📋 Secondary LightGBM model = P(trade was profitable | features)
- 📋 Secondary decides *size*, not side
- 📋 Take trade only if calibrated P > 0.55

### Calibration

- 📋 **Isotonic regression** on held-out CPCV out-of-fold predictions
- 📋 `sklearn.calibration.CalibratedClassifierCV(method='isotonic', cv='prefit')`

### Drift Monitoring

- 📋 PSI / KS on each feature, weekly
- 📋 Rolling 30-trade hit rate, profit factor, avg MAE
- 📋 Rolling 50-trade Sharpe vs training Sharpe (retrain if <50%)
- 📋 NannyML for univariate + multivariate drift + performance estimation without ground truth
- 📋 Monthly minimum retraining; PSI breach triggers immediate retrain
- 📋 Challenger model in shadow mode for 2 weeks before promotion

### Feature Engineering Hygiene

- 📋 `pd.shift(1)` on every rolling stat that uses current bar
- 📋 Point-in-time correctness — every feature must be computable at decision time
- 📋 Closed bars only (5-min features for 1-min model use only closed 5-min bars)
- 📋 Point-in-time NSE security master (track delisted symbols: DHFL, Vakrangee, IL&FS-linked)
- 📋 ISIN → symbol → corporate-action history table

### Feature Discovery

- 📋 `tsfresh` with `EfficientFCParameters` profile (never the full ~1,558 set)
- 📋 Aggressive pruning via FRESH or LightGBM importance
- 📋 SHAP-based feature importance (OOS only)
- 🔬 `featuretools` deep feature synthesis — research says oversold; skip

### Feature Store

- 📋 Roll-your-own: Parquet/DuckDB (offline) + Redis (online)
- 📋 Feature registry YAML (source, lag, transform, validation per feature)
- 📋 Same code path for offline training and online inference
- 🔬 Feast / Hopsworks — research says don't deploy at solo scale

### Model Registry & Versioning

- 📋 `ml_models` table populated (already in schema)
- 📋 Walk-forward AUC, sample count, train range stored
- 📋 `is_production` flag for current model per signal type

---

## 10. API & Bot Interface (Layer 7)

- 📋 FastAPI server with pydantic schemas
- 📋 `GET /health`, `GET /status`
- 📋 `GET /signals?since=&types=&limit=` (and per-class variants)
- 📋 `GET /announcements?since=&priority=`
- 📋 `GET /announcements/{fingerprint}`
- 📋 `GET /board-meetings/upcoming?days=7`
- 📋 `GET /corporate-actions/upcoming?days=30`
- 📋 `GET /financial-results?period=Q4FY26`
- 📋 `GET /events/pending?date=tomorrow`
- 📋 `GET /profile/{symbol}`, `GET /profile/{symbol}/history`
- 📋 `GET /quote/{symbol}`, `GET /option-chain/{symbol}`
- 📋 `GET /blacklist`, `GET /blacklist/changes`
- 📋 `GET /universe?type=fno|nifty500`
- 📋 `GET /fundamentals/{symbol}`
- 📋 `GET /reports/fii-dii`, `GET /reports/bhavcopy`
- 📋 `POST /webhooks` for push subscribers
- 📋 `GET /admin/endpoint-health`, `POST /admin/replay`
- 📋 Cursor pagination via `next_cursor`

---

## 11. Bot — Decision Engine

- 📋 `alert-bot` separate repo / venv / process
- 📋 `client.py` — typed HTTP wrapper for the service API
- 📋 **Layer 1: hard filters** (blacklist, pledge > 25%, loss-making + long)
- 📋 **Layer 2: quality gate** (`quality_score < 30` and long → reject)
- 📋 **Layer 3: regime alignment** per signal type (penalty/bonus matrix)
- 📋 **Layer 4: quality boost** (`quality_score > 70 → +0.05`)
- 📋 **Layer 5: model probability blend** (`0.6 × rule + 0.4 × model`) — when models exist
- 📋 **Layer 6: final threshold + dedup + throttle**
- 📋 Strategy files: `breakout_long.py`, `result_beat.py`, `oi_buildup.py`, etc. (one per setup)
- 📋 No-trade output is a first-class outcome (don't force alerts in choppy regimes)

### Strategy-Specific Filters (from research)

- 📋 ORB-style filters: skip days where opening range < 40 Nifty points
- 📋 Reject longs where price below falling session VWAP
- 📋 Require breakout volume > 1.2× 20-day average
- 📋 MWPL ban list hard exclude
- 📋 Stock above 80% MWPL → no fresh longs

---

## 12. Bot — Risk & Position Sizing

### Stops

- 📋 Structure-based SL (prior swing point) as primary
- 📋 ATR-based SL fallback (`entry - k×ATR(14, 5m)`, k=1.5–2.0 trend / 0.75–1.0 reversion)
- 📋 Use tighter of structure or ATR
- 📋 Chandelier exit trail (`highest_high - 3×ATR(22)` for longs)
- 📋 Time-based SL: exit if <0.5R favorable in 30 min
- 📋 Volatility-bracketed SL = `max(structure, 0.75 × VIX-implied 1σ)`

### Targets & Scale-Out

- 📋 33% off at 1R (trail rest to entry), 33% at 1.5R, 33% chandelier-trail to 15:20 IST
- 📋 Dynamic target via ATR + India-VIX expected-range envelope

### Position Sizing

- 📋 **Quarter-Kelly to half-Kelly**, capped at 2% account risk per trade
- 📋 Recompute Kelly fraction every 50–100 trades
- 📋 ATR-based equal-risk: `position = (account × risk%) / (k × ATR)`
- 📋 Volatility-targeted gross (12% annualized → ~0.76% daily); cut gross 40–50% when VIX > 22
- 📋 Correlation-aware cluster sizing (3 PSU banks ≠ 3 independent positions)

### Time-of-Day Rules (IST)

- 📋 09:15–09:30 — record opening range only, no trades
- 📋 09:30–11:30 — prime momentum window
- 📋 11:30–13:30 — reduce activity (lunch chop)
- 📋 13:30–15:00 — second momentum window
- 📋 15:00–15:20 — scalp + partial exits
- 📋 15:20 — mandatory flat for MIS

### Daily Kill-Switches

- 📋 –2% day → hard stop, close laptop 
- 📋 3 consecutive losses → 30-min pause, resume at half size
- 📋 5 consecutive losses → stop for the day
- 📋 Weekly: –5% account or 3 losing days → paper-only rest of week

### MAE / MFE Analysis

- 📋 Per-setup MAE distribution → 80th percentile sets optimal SL
- 📋 Per-setup MFE distribution → target you're leaving on the table
- 📋 Per-trade attribution: SHAP delta of features → P&L

### Overnight Hedge

- 📋 OTM Nifty put (delta ≈ −0.30) for ~1% of position notional on overnight holds
- 📋 OTM put-spread on Nifty for long-only books (capital-efficient alternative)

---

## 13. Bot — Delivery & UX

- 📋 Telegram delivery primary
- 📋 Email fallback
- 📋 Webhook subscriber model
- 📋 Quiet hours / throttle (max alerts per hour)
- 📋 Explainability card per alert: top reasons, confidence, historical analog, risk note
- 📋 SHAP delta summary in alert payload
- 🔬 Personalization layer (aggressive vs conservative) — Phase 9+
- 🔬 Trading psychology detection (revenge/overtrading flags) — Phase 9+

---

## 14. Backtesting & Cost Realism

### Cost Model (CRITICAL — research says this is highest-leverage single intervention)

- 📋 Brokerage: ₹20 per executed order OR 0.03%, whichever lower (Zerodha/Upstox-style)
- 📋 STT: 0.025% on sell side of intraday equity
- 📋 Exchange transaction charges: ~0.00345% per leg (NSE)
- 📋 SEBI charges: ~₹10 / crore
- 📋 Stamp duty: 0.003% on buy side
- 📋 GST: 18% on (brokerage + exchange + SEBI)
- 📋 F&O STT: 0.1% on option sell-side premium (post-Oct 2024 hike)
- 📋 Slippage: min 1 tick + 1 bps (liquid) / 5 bps (mid-cap), scaling with `trade_size / 1-min_volume`
- 📋 Use OpenAlgo 4-segment fee model as template

### Backtest Stack

- 📋 `vectorbt` for research and parameter sweeps
- 📋 `nautilus_trader` for production-grade fill simulation
- 📋 `OpenAlgo` for paper trading and broker bridge
- 📋 `ops/replay.py` — recompute signals from raw with new `signal_version` (already specced)
- 📋 ORB-with-VWAP-filter as the benchmark baseline (research target: Sharpe 1.16, win 48.7% — your ML must beat this)

### Stage Gates (from research)

- 📋 Stage 1 → Stage 2: 60 consecutive paper-trading days at ≥ 1.0 Sharpe net of all costs
- 📋 Stage 2 → Stage 3: ML meta-labeling reduces trade frequency 30–40% while maintaining/improving Sharpe vs rules-only
- 📋 Stage 3 → scale: 3 months live at ≥ 0.7× paper Sharpe (30%+ shrinkage live-vs-paper is normal; worse = leak)

---

## 15. Observability & Ops

- ✅ Structured JSON logging via systemd journal
- ✅ `endpoint_health` table per-collector tracking
- ✅ `fetch_log` per-call audit trail
- ✅ Run reports with success/fail/insert/dedup counts
- 📋 Prometheus `/metrics` endpoint (counters, latency histograms, circuit gauges)
- 📋 Grafana dashboard (optional)
- 📋 Nightly SQLite backup to `data/archive/db_backups/`, 30-day rotation
- 📋 Bhavcopy CSVs kept forever (the historical truth)
- 📋 Alert when collector hasn't run successfully in N minutes during market hours
- 🔧 `failed=0` is not "all OK" — `errors[] == [] AND persist.inserted > 0` is the real signal; dashboards should reflect this (per LEARNINGS)
- 🔧 Migration application — boot applies all pending; `scripts/migrate.py` now applies against the live DB without restart (no more manual SQL). Remaining: periodic auto-apply via the DBJob archetype (see Storage Layer). (per LEARNINGS)

---

## 16. Universe & Watchlist Management

- ✅ `universe.yaml` watchlist
- ✅ F&O list collector + table
- ✅ Index members collector
- ✅ `user_exclusions` table
- 📋 Daily watchlist ranking (top breakout, top swing, top reversal candidates) — Layer 6+
- 📋 Sector-exposure / correlation guardrails before adding to watchlist
- 📋 Auto-add and auto-remove from watchlist based on liquidity + quality_score thresholds

---

## 17. Documentation Hygiene

- ✅ `FINAL_ARCHITECTURE.md` as single source of truth
- ✅ `LAYER1_2_REFERENCE.md` — contract for downstream layers
- ✅ `LEARNINGS.md` — NSE quirks, mistakes made, design tradeoffs
- 📋 `LAYER3_REFERENCE.md` after Phase 8 closes
- 📋 ADR-style decision log (architecture changes with rationale)
- 📋 Per-collector README in `collectors/` (response shape, known quirks, fixture path)

---

## 18. Phase 9+ Research Backlog (deferred, deliberately not chasing now)

These are interesting but the research doc / architecture explicitly cautions against doing them now. Re-evaluate after Layer 6 ships and you have 6+ months of labeled outcomes.

- 🔬 Market-memory retrieval (embed `stock_profile_daily` rows, k-NN historical setups)
- 🔬 Vision LLMs for ambiguous PDFs
- 🔬 OCR for scanned filings
- 🔬 RL for execution slicing (not entry/exit)
- 🔬 News NLP scraping (Moneycontrol, ET, BSE keywords)
- 🔬 Twitter/X paid API sentiment
- 🔬 Google Trends regime signal
- 🔬 Multi-agent voting models
- 🔬 Bayesian live confidence updating
- 🔬 Monte Carlo trade simulation pre-entry
- 🔬 Synthetic data generation for tail scenarios
- 🔬 Online / continual learning instead of batch retrains
- 🔬 TabPFN-style tabular foundation models (track Hollmann et al. progression)

---

## Quick Status Summary

| Layer | Done | Polish | Todo |
|---|---|---|---|
| 1 — Session | 12 | 1 | 2 |
| 2 — Collectors | 32 | 3 | ~25 |
| 3 — Parsers | 0 | 0 | ~20 |
| 4 — Indicators / Fundamentals / Events | 0 | 0 | ~35 |
| 5 — Stock Profile | 0 | 0 | 6 |
| 6 — Signals + ML | 0 | 0 | ~60 |
| 7 — API | 0 | 0 | ~20 |
| Bot | 0 | 0 | ~40 |
| Ops | 4 | 2 | ~10 |

**Active phase:** Layer 3 (Phase 8, ~6 weeks per `layer3` plan).
**Next unlock:** Layer 4 indicators + Layer 5 profile composer (parallelizable with Layer 3).
**Realistic time to first live signal alerts:** 12–16 weeks from today, assuming Layer 3 lands on schedule.

---

**Maintenance rule:** when you ship something, move it from 📋 → ✅. When you find a gap in a shipped feature, move it from ✅ → 🔧 and write the gap inline. Don't delete entries; the history of what didn't make the cut is as valuable as the checklist itself.
