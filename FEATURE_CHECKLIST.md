# NSE Trading System — Execution Guide

**What this file is:** Your daily work order. Open this, find your current phase,
find your current week, execute the tasks in order. No cross-referencing other docs.

**When to advance a week:** all tasks in the current week are done AND working correctly.
**When to advance a phase:** all exit criteria at the bottom of the phase are met.
**When you finish:** move the ✅ yourself — this file is a living checklist.

**Task status:**
- [ ] Not started
- [~] In progress
- [x] Done

---

## CURRENT POSITION → Phase 1, Week 1

Update this line every time you start a new phase or week.

---
---

# PHASE 0 — Foundation
## Status: ✅ COMPLETE

32 collectors live. All raw_* tables populated. nse.db ≈ 5.4 GB.
Nothing to do here. Phase 1 is the active work.

---
---

# PHASE 1 — Always-On + First Live Alert
**Duration:** 4–6 weeks
**Goal:** First real Telegram alert fires during market hours, from a VPS that never sleeps, with correct numbers, logged to paper trades.
**Why this matters:** Everything from Phase 2 onward is meaningless on a sleeping laptop. This phase retires the #1 blocker and proves the full pipeline end-to-end.

---

## Week 1 — VPS Setup (THE GATE)

Nothing in Phase 1 proceeds until this week is complete. Do not start Week 2 until the VPS runs 5 clean trading days with no laptop dependency.

### Tasks

- [x] **1.1** Provision cloud VPS — 4 vCPU, 8 GB RAM, 100 GB SSD, Ubuntu 24.04. Providers: Hetzner CX32 (cheapest), DigitalOcean Droplet, or AWS t3.medium → *AWS EC2 `m7i-flex.large` (2 vCPU / 8 GB), 100 GB gp3, ap-south-1 (Mumbai), Ubuntu 24.04.4. Instance `Stock-Bot` i-0a2677d417ab9109c.*
- [x] **1.2** Install dependencies on VPS: Python 3.12, Redis, Git, tmux/screen → *Python 3.12.3, redis 7.0.15, git, tmux, rsync, sqlite3.*
- [x] **1.3** Transfer `data/nse.db` to VPS (use `rsync` with compression — file is ~5.4 GB) → *5.1 GB via `scripts/transfer_db.sh` (sqlite3 .backup snapshot + rsync); `PRAGMA integrity_check` = ok, 25,956 announcements verified.*
- [x] **1.4** Transfer full project codebase to VPS via Git or rsync → *`git clone` to `/opt/nse-data-service`.*
- [x] **1.5** Configure `.env` on VPS — all API keys, DB path, Redis URL → *`.env` copied from laptop. NOTE: code reads only AZURE_OPENAI_*/GROWW_* — DB path is hardcoded `data/nse.db`, Redis uses default localhost:6379 (no URL vars). See `.env.example`.*
- [x] **1.6** Set up Redis on VPS and verify it starts on boot → *`redis-server` enabled on boot; `redis-cli ping` = PONG; collector logs `dedup_cache_redis_ok`.*
- [x] **1.7** Create systemd unit file for the data service — start on boot, restart on crash. Save as `/etc/systemd/system/nse-data.service` → *Implemented as `nse-collector@.service` (`%i`-templated) + `nse-dashboard@.service`, not `nse-data.service`.*
- [x] **1.8** Enable and start the service: `systemctl enable nse-data && systemctl start nse-data` → *`systemctl enable --now nse-collector@ubuntu` (+ dashboard). Status active (running); NSE accepting the Mumbai IP (200 OK).*
- [ ] **1.9** Verify all 32 collectors fire on their schedules for **one full trading day** → *clock starts Mon 2026-06-01 (deployed Sat, a non-trading day).*
- [ ] **1.10** Spot-check 10 values by hand against NSE website — prices, OI numbers, VIX level
- [ ] **1.11** Repeat for **4 more consecutive trading days** (5 total)
- [x] **1.12** Write `docs/DEPLOY.md` — step-by-step reproduction of everything above

### Week 1 gate
All 32 collectors running 5 consecutive trading days. Zero laptop dependency. Data spot-checked.

---

## Week 2 — Intraday Candle Builder

> **As-built note (reconciled 2026-05-31).** The intent below — 1m+5m candles for
> the whole universe with session VWAP — is met, but via a **read-time synthesis**
> architecture rather than a persisted minute-cadence candle-builder. History
> comes from a broker backfill into `raw_intraday_candles` (1-min, kept
> permanently); *today's* candles are synthesized on demand from
> `raw_equity_quotes` and resampled to 5m at read time
> (`indicators/intraday_ohlcv.py`). This avoids a stateful minute-writer and
> double storage. The original task text (persisted `bar_time/timeframe/vwap`
> table, 30-day candle retention) is superseded; the boxes below reflect reality.

### Tasks

- [x] **2.1** Intraday candle table — `migrations/025_intraday_candles.sql`
  (`raw_intraday_candles`: `symbol, interval, ts(epoch), open, high, low, close,
  volume, source`). Broker-backfilled 1-min history; 5m derived by resampling.
  *(No `vwap`/`session_date` columns — VWAP is its own indicator table, see 2.3.)*
- [x] **2.2** Candle source — `indicators/intraday_ohlcv.py:read_intraday_5m`
  merges broker-backfilled 1-min history with today's live 1-min bars synthesized
  from `raw_equity_quotes`, deduped per day, resampled to 5m. Live indicators run
  over the F&O ∪ Nifty500 universe (`indicators/universe.py`).
- [x] **2.3** Session-anchored VWAP — `indicators/volume/vwap_intraday.py` +
  `migrations/034_indicator_vwap_5m.sql` (table `indicator_vwap_5m`).
  `vwap = cumsum(typical_price × volume) / cumsum(volume)`, reset at 09:15 IST.
  Registered as an intraday indicator; tests in `tests/indicators/test_vwap_intraday.py`.
- [x] **2.4** Every-minute market-hours job — `indicators/live_job.py`
  (`register_live_job`, wired in `main.py`), `IntervalTrigger(60s)`, gated by
  `is_market_open()`. Recomputes all intraday indicators (RSI/MACD/VWAP).
- [x] **2.5** Retention — `indicators/retention.py` sweeps intraday *indicator*
  tables to a rolling 30 days nightly. The backfilled *candle* history is kept on
  purpose (it's the historical record), so candles are not pruned.
- [ ] **2.6** Verify: spot-check RELIANCE 10:15 candle + VWAP against the NSE
  chart during a live session. *(Programmatic sanity done 2026-05-31: VWAP stays
  within each session's [low, high] and tracks price; live-chart eyeball still
  pending a trading day.)*

### Week 2 gate
1m and 5m candles building correctly for all symbols. VWAP resetting at 09:15 each
session. **Met** via the as-built architecture; only the live-session eyeball (2.6)
remains.

---

## Week 3 — Live Indicator Job + Phase-1 Indicators

The three indicators required before any signal can be built: VWAP slope, ATR, and regime tags.

### Tasks

- [ ] **3.1** Write migration `migrations/0XX_indicator_live.sql`:
  ```sql
  CREATE TABLE IF NOT EXISTS indicator_live (
    symbol TEXT PRIMARY KEY,
    updated_at TEXT NOT NULL,
    -- VWAP
    vwap REAL,
    vwap_slope REAL,        -- (vwap_now - vwap_30min_ago) / 6 bars
    price_vs_vwap TEXT,     -- 'above' or 'below'
    -- ATR
    atr_14_daily REAL,
    atr_14_5m REAL,
    -- RSI (already exists in indicator_rsi_5m — join or copy)
    rsi_5m REAL,
    -- Regime
    trend_regime TEXT,      -- strong_uptrend/uptrend/sideways/downtrend/strong_downtrend
    momentum_state TEXT     -- overbought_extreme/.../oversold_extreme
  );
  ```
- [ ] **3.2** Write `indicators/live_job.py` — APScheduler interval job, every 1 minute, gated on `is_market_open()`. For each symbol: compute ATR(14) from last 14 daily candles, compute VWAP slope (current VWAP vs 30 min ago), classify trend_regime from SMA relationships, classify momentum_state from RSI, write to `indicator_live`
- [ ] **3.3** VWAP slope formula: `slope = (vwap_current - vwap_6_bars_ago) / 6`. Positive = rising anchor = bullish context. Negative = falling anchor = bearish context
- [ ] **3.4** trend_regime classification:
  - `price > sma50 > sma200 AND adr > 0` → strong_uptrend
  - `price > sma50 AND price > sma200` → uptrend
  - `price between sma50 and sma200` → sideways
  - `price < sma50 AND price < sma200` → downtrend
  - `price < sma50 < sma200` → strong_downtrend
- [ ] **3.5** momentum_state from RSI(5m): > 80 = overbought_extreme, 70–80 = overbought, 55–70 = bullish, 45–55 = neutral, 30–45 = bearish, 20–30 = oversold, < 20 = oversold_extreme
- [ ] **3.6** Write `pre_market_loader.py` — runs at 08:45 IST daily (DBJob): loads last 250 rows of bhavcopy per symbol into RAM, seeds `indicator_live` with previous session's final values, writes blacklist + quality flags to Redis with 6h TTL
- [ ] **3.7** Write Redis write path in `live_job.py` — after each symbol's indicators update, flush to Redis hash `ind:{symbol}`. TTL = 5 minutes (stale detection)
- [ ] **3.8** Register `live_job.py` in scheduler: every 1 minute, `market_hours_only`
- [ ] **3.9** Register `pre_market_loader.py` in scheduler: daily 08:45, `trading_day_only`
- [ ] **3.10** Verify: at 10:30 AM, query `indicator_live` for HDFCBANK — all fields populated, values sensible

### Week 3 gate
`indicator_live` populating every minute. Pre-market loader running at 08:45. ATR, VWAP, regime all computing correctly.

---

## Week 4 — Signal Engine MVP

### Tasks

- [ ] **4.1** Write migrations:
  ```sql
  -- signals table
  CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    signal_type TEXT NOT NULL,   -- 'long_buildup', 'breakout_52wh'
    detected_at TEXT NOT NULL,
    price REAL,
    oi_change_pct REAL,
    price_change_pct REAL,
    volume_ratio REAL,
    confidence REAL,
    dispatched INTEGER DEFAULT 0,
    dispatched_at TEXT
  );
  -- signal_features snapshot (for ML later)
  CREATE TABLE IF NOT EXISTS signal_features (
    signal_id INTEGER PRIMARY KEY,
    symbol TEXT,
    detected_at TEXT,
    -- copy ALL live indicator values at signal time as columns
    vwap REAL, vwap_slope REAL, rsi_5m REAL,
    trend_regime TEXT, momentum_state TEXT,
    atr_14_daily REAL,
    volume_ratio REAL,
    market_regime TEXT,
    -- ... add more as indicators grow
    FOREIGN KEY (signal_id) REFERENCES signals(id)
  );
  -- paper trades (start from day one)
  CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER,
    symbol TEXT,
    signal_type TEXT,
    entry_price REAL,
    sl_price REAL,
    t1_price REAL,
    entry_time TEXT,
    exit_price REAL,
    exit_time TEXT,
    exit_reason TEXT,  -- 'hit_t1', 'hit_sl', 'forced_flat', 'manual'
    gross_pnl REAL,
    net_pnl REAL,      -- after full cost model
    status TEXT        -- 'open', 'closed'
  );
  -- signal outcomes (nightly labeler fills this)
  CREATE TABLE IF NOT EXISTS signal_outcomes (
    signal_id INTEGER PRIMARY KEY,
    symbol TEXT,
    detected_at TEXT,
    ret_30m REAL, ret_2h REAL, ret_eod REAL, ret_1d REAL, ret_3d REAL,
    mae REAL,          -- max adverse excursion
    mfe REAL,          -- max favorable excursion
    hit_t1 INTEGER,    -- 1 or 0
    hit_sl INTEGER,    -- 1 or 0
    labeled_at TEXT
  );
  ```

- [ ] **4.2** Write `signals/compute.py` — pure functions:
  - `compute_oi_change(symbol)` — reads `raw_oi_spurts`, returns (oi_change_pct, prev_oi, curr_oi)
  - `compute_price_change(symbol)` — reads `raw_equity_quotes`, returns (price_change_pct, price)
  - `compute_volume_ratio(symbol)` — current 5m volume / 20d avg 5m volume from bhavcopy

- [ ] **4.3** Write `signals/detect.py` — main dispatcher, runs every 1 minute, gated on `is_market_open()`:
  - Loads all hard gate lists from Redis (blacklist, quality flags)
  - For each symbol in F&O ∪ Nifty500:
    - Apply hard gates (see §4.4)
    - Compute signal metrics
    - If `long_buildup` conditions met → write signal
    - If `breakout_52wh` conditions met → write signal

- [ ] **4.4** Implement hard gates in `signals/detect.py` — these are binary kills. If any triggers, skip silently:
  - Blacklisted? (Redis `blacklist:` key) → skip
  - Newly listed < 30 days? → skip
  - Promoter pledge > 50%? → skip (Phase 4 adds more fundamentals; for now use what exists)
  - Price band ≤ 2%? (from `raw_price_bands`) → skip for longs
  - T2T series? (series BE/BZ/ST in `raw_price_bands`) → skip
  - In 09:15–09:30 window? → skip
  - In lunch zone 11:30–13:30? → skip for now (revisit confidence threshold in Phase 2)

- [ ] **4.5** `long_buildup` signal rule (NOTE from LEARNINGS: oi_spurts has NO price field — must JOIN with raw_equity_quotes):
  ```
  oi_change_pct >= +3.0
  AND price_change_pct >= +1.0
  AND volume_ratio >= 1.5
  ```

- [ ] **4.6** `breakout_52wh` signal rule:
  ```
  new 52w high today (from raw_high_low_52w)
  AND volume_ratio >= 1.5
  ```

- [ ] **4.7** Write `signals/dedup.py` — Redis-based fingerprint. Key: `sigdedup:{symbol}:{signal_type}`. TTL: 30 minutes. If key exists, do not re-fire the same signal. This prevents the same setup flooding alerts every minute.

- [ ] **4.8** Write `signals/enrich.py` — reads Redis `ind:{symbol}` hash, attaches live indicator context to the signal row before writing to DB

- [ ] **4.9** Write `signals/feature_store.py` — after a signal is written to `signals`, snapshot ALL live indicator values into `signal_features`. This is the ML training archive. **Must start from day one.**

- [ ] **4.10** Register `signals/detect.py` in scheduler: every 1 minute, `market_hours_only`

### Week 4 gate
Signals appearing in `signals` table during market hours. No duplicate signals within 30 minutes for same symbol+type. Features being snapshotted in `signal_features`.

---

## Week 5 — Paper Trades + Telegram Dispatcher

### Tasks

- [ ] **5.1** Write `signals/outcome_labeler.py` — runs nightly at 19:30. For each signal from the previous session: reads bhavcopy for T+1d price, computes returns (T+30m from intraday candles, T+2h, T+EOD, T+1d), computes MAE and MFE from intraday candles, writes to `signal_outcomes`

- [ ] **5.2** Write cost model function `costs/model.py` — pure function, takes (entry_price, exit_price, quantity, trade_type) and returns net P&L after all costs:
  - Brokerage: min(₹20, 0.03% × trade_value) per leg
  - STT: 0.025% of sell value (intraday equity)
  - Exchange charges: 0.00345% per leg
  - SEBI: ₹10 per crore
  - Stamp duty: 0.003% of buy value
  - GST: 18% of (brokerage + exchange + SEBI)
  - Slippage: 1 tick minimum + 1bps
  - Returns gross P&L and net P&L both

- [ ] **5.3** Write `signals/paper_tracker.py` — runs every minute. For each open paper_trade: check if T1 or SL has been hit using latest quote from `raw_equity_quotes`. If hit, close the trade with net P&L from cost model. At 15:20 force-flat all remaining open trades

- [ ] **5.4** SL calculation for Phase 1: `SL = entry_price - 1.5 × atr_14_daily`. T1 = `entry_price + 1.5 × atr_14_daily` (1R target). Simple ATR-based sizing is enough for Phase 1. Phase 8 adds structure-based SL

- [ ] **5.5** Write basic confidence scorer `signals/confidence.py` — Phase 1 version uses only 4 inputs:
  - Base score: 0.50
  - VWAP alignment: price above VWAP AND slope positive → +0.10; price below → −0.10
  - RSI zone: 50–65 (healthy) → +0.10; > 75 (overbought) → −0.10; > 80 → −0.20
  - Trend regime: strong_uptrend → +0.10; uptrend → +0.05; downtrend → −0.10; strong_downtrend → −0.20
  - Volume: ratio > 3× → +0.05; ratio < 1× → −0.10
  - Output: normalised 0–1

- [ ] **5.6** Write Telegram bot `bot/dispatcher.py` — polls `signals` table every minute for undispatched signals. For each: apply hard gates again, compute confidence, if confidence > 0.65 → send Telegram message, mark `dispatched=1`. Reads SQLite directly (no FastAPI yet)

- [ ] **5.7** Bot must read `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` from `.env`

- [ ] **5.8** Phase-1 message format (simple — polished in Phase 8):
  ```
  🟢 {SYMBOL} — {Signal Type}
  OI: {oi_change_pct}% | Price: {price_change_pct}% | Vol: {volume_ratio}×
  VWAP: {above/below} {↑/↓} | RSI(5m): {rsi_5m} | Trend: {trend_regime}
  Confidence: {confidence:.2f}
  SL: ₹{sl_price} | T1: ₹{t1_price} | Flat by: 15:20
  ```

- [ ] **5.9** Create systemd unit for bot: `/etc/systemd/system/nse-bot.service`. Runs as a separate process from the data service

- [ ] **5.10** First real test: during market hours, watch a signal fire and arrive on Telegram. Verify the numbers are correct against NSE website

### Week 5 gate
At least one real alert delivered to Telegram with correct numbers. Paper trades logging in `paper_trades`. Outcome labeler running nightly and populating `signal_outcomes`.

---

## Week 6 — Stability, Ops, and Phase Gate

### Tasks

- [ ] **6.1** Run both services (data + bot) for 5 consecutive trading days. No crashes, no missed alerts due to technical failures

- [ ] **6.2** Set up nightly SQLite backup: `cron` job at 02:00 IST:
  ```bash
  sqlite3 /data/nse.db ".backup /data/archive/db_backups/nse_$(date +%Y%m%d).db"
  # Keep 30 days, delete older
  find /data/archive/db_backups/ -name "*.db" -mtime +30 -delete
  ```

- [ ] **6.3** Set up Telegram alert for collector failures: write `ops/health_check.py` — runs every 15 minutes during market hours. If any 5-minute collector hasn't run successfully in 15 minutes, send a Telegram alert to a separate ops chat (or same chat with a 🔴 prefix)

- [ ] **6.4** Spot-check `signal_outcomes` data manually: take 5 signals from the past week. Manually verify that the T+30m return, T+EOD return, and MAE/MFE values are correct against the actual intraday data

- [ ] **6.5** Spot-check `paper_trades`: verify that P&L numbers match what you would have made/lost if you had actually traded those signals (including costs)

- [ ] **6.6** Review every alert that fired. For each: does the message make sense? Was the signal genuine or noise? Log any false signals with notes in `LEARNINGS.md`

- [ ] **6.7** Check: is `pre_market_loader.py` running at 08:45 every day? Is `indicator_live` fully seeded before 09:15?

### Phase 1 exit criteria (ALL must be met before moving to Phase 2)
- [ ] VPS running all 32 collectors for 5 consecutive trading days with zero laptop dependency
- [ ] At least one real Telegram alert fired with correct numbers
- [ ] Zero false alerts on blacklisted stocks
- [ ] `paper_trades` table has at least 1 week of data
- [ ] `signal_outcomes` populating T+30m through T+3d returns
- [ ] Nightly DB backup running
- [ ] Ops health check alerting on collector failures
- [ ] `LEARNINGS.md` updated with anything new discovered this phase

---
---

# PHASE 2 — Market Context
**Duration:** 3–4 weeks
**Goal:** Every alert now knows what the market is doing. Alerts include regime and sector context. Morning brief arrives at 09:00 every trading day.
**Prerequisite:** Phase 1 fully stable for 5+ trading days.

---

## Week 7 — Market Regime Classifier

### Tasks

- [ ] **7.1** Write migration `migrations/0XX_market_state.sql`:
  ```sql
  CREATE TABLE IF NOT EXISTS market_state (
    as_of TEXT PRIMARY KEY,
    nifty_direction TEXT,      -- 'up'/'flat'/'down'
    nifty_return_pct REAL,
    vix_level REAL,
    vix_state TEXT,            -- 'low'/'normal'/'elevated'/'high'/'extreme'
    vix_direction TEXT,        -- 'falling'/'flat'/'rising'
    gift_nifty_signal TEXT,    -- 'aligned_bull'/'neutral'/'aligned_bear'
    advance_decline_ratio REAL,
    pct_above_vwap REAL,
    fii_partial_day REAL,
    overall_regime TEXT,       -- 'risk_on'/'neutral'/'risk_off'/'panic'
    regime_confidence REAL,
    updated_at TEXT
  );
  ```

- [ ] **7.2** Write `market/regime_job.py` — DBJob, runs every 5 minutes, `market_hours_only`:
  - Reads `raw_india_vix` for VIX level and direction (vs 30 min ago)
  - Reads `raw_indices` for Nifty level and direction
  - Reads `raw_gift_nifty` for last pre-open reading vs previous close gap
  - Reads `raw_advances_declines` for advance/decline ratio
  - Reads `indicator_live` for % of symbols where `price_vs_vwap = 'above'`
  - Reads `raw_fii_dii` for today's partial FII flow estimate (when available)
  - Classifies `overall_regime`:
    - Nifty up AND VIX falling AND AD > 1.5 AND > 60% above VWAP → risk_on
    - Nifty down AND VIX rising AND AD < 0.7 → risk_off
    - VIX > 25 → panic (regardless of other factors)
    - Otherwise → neutral
  - Upserts to `market_state`

- [ ] **7.3** VIX state thresholds:
  - VIX < 12 → low (complacent, option sellers win, mean-reversion works)
  - VIX 12–18 → normal
  - VIX 18–22 → elevated (caution)
  - VIX 22–28 → high (trending moves, reduce size)
  - VIX > 28 → extreme (panic, full defensive)

- [ ] **7.4** Expiry detection: `market/expiry.py` — given today's IST date, returns: `is_nifty_expiry` (Tuesday), `is_banknifty_expiry` (Thursday), `is_monthly_expiry` (last Thursday of month). Returns max-pain alignment multiplier: +5 if signal direction matches expected max-pain drift, −10 if against

- [ ] **7.5** Wire regime into confidence scorer in `signals/confidence.py` — add regime_contribution:
  ```
  risk_on  → +0.10
  neutral  → 0.00
  risk_off → −0.10
  panic    → −0.20 (suppress most signals)
  ```

- [ ] **7.6** Register `market/regime_job.py`: every 5 minutes, `market_hours_only`

### Week 7 gate
`market_state` table updating every 5 minutes. Confidence scores changing based on regime.

---

## Week 8 — Sector Radar

### Tasks

- [ ] **8.1** Write migration `migrations/0XX_sector_state.sql`:
  ```sql
  CREATE TABLE IF NOT EXISTS sector_state (
    sector_name TEXT NOT NULL,
    as_of TEXT NOT NULL,
    rs_ratio REAL,             -- sector_return / nifty_return today
    rs_rank INTEGER,           -- 1 (best) to 11 (worst)
    rs_trend TEXT,             -- 'improving'/'flat'/'deteriorating'
    volume_state TEXT,         -- 'above_avg'/'normal'/'below_avg'
    sector_return_pct REAL,
    PRIMARY KEY (sector_name, as_of)
  );
  ```

- [ ] **8.2** Write `market/sector_radar_job.py` — DBJob, every 5 minutes, `market_hours_only`:
  - For each of 11 NSE sectoral indices: NIFTY BANK, NIFTY IT, NIFTY AUTO, NIFTY PHARMA, NIFTY FMCG, NIFTY METAL, NIFTY REALTY, NIFTY ENERGY, NIFTY INFRA, NIFTY PSU BANK, NIFTY MEDIA
  - Compute RS ratio = sector_return_today / nifty_return_today
  - Rank all 11 by RS ratio (1=best)
  - Compare RS ratio now vs 30 min ago → trend direction
  - Upsert to `sector_state`

- [ ] **8.3** Write sector-to-stock mapping: `config/sector_mapping.yaml` — maps each symbol to its sector. Use `raw_quote_metadata` sector field as source

- [ ] **8.4** Wire sector into confidence scorer — add sector_contribution:
  ```
  RS rank 1–3 (leading sector) → +0.08
  RS rank 4–8 (middle)         → 0.00
  RS rank 9–11 (lagging)       → −0.08
  RS trend improving           → +0.03
  RS trend deteriorating       → −0.03
  ```

- [ ] **8.5** Register `market/sector_radar_job.py`: every 5 minutes, `market_hours_only`

### Week 8 gate
`sector_state` updating every 5 minutes. Alerts show sector RS rank in message.

---

## Week 9 — Time Rules + Morning Brief + Alert Upgrade

### Tasks

- [ ] **9.1** Write `market/time_rules.py` — given current IST time, returns `time_window` and `confidence_multiplier`:
  ```
  09:15–09:30  NO_TRADE              → suppress all signals
  09:30–11:00  PRIME_WINDOW          → multiplier 1.00
  11:00–11:30  FIRST_EXHAUSTION      → multiplier 0.90
  11:30–13:30  LUNCH_ZONE            → multiplier 0.80 (only > 0.72 confidence passes)
  13:30–14:30  SECOND_WINDOW         → multiplier 1.00
  14:30–15:00  CLOSING_APPROACH      → multiplier 0.90
  15:00–15:20  CLOSING_PRESSURE      → multiplier 0.75 (scalp only)
  15:20+       NO_NEW_TRADES          → suppress all signals
  ```

- [ ] **9.2** Wire time rules into confidence scorer — multiply final confidence by `time_multiplier` after all other contributions

- [ ] **9.3** Upgrade alert message to include regime + sector:
  ```
  🟢 {SYMBOL} — {Signal Type}
  OI: {oi_change_pct}% | Price: {price_change_pct}% | Vol: {volume_ratio}×

  Market: Nifty {direction} | VIX {vix_state} {↑/↓} | Regime: {overall_regime}
  Sector: {sector_name} RS #{rs_rank} | Trend: {rs_trend}

  Stock: VWAP {above/below} {↑/↓} | RSI(5m): {rsi_5m} | Trend: {trend_regime}

  Confidence: {tier} ({confidence:.2f})
  SL: ₹{sl_price} | T1: ₹{t1_price} | Flat by: 15:20
  ```

- [ ] **9.4** Write `bot/morning_brief.py` — DBJob, runs at 09:00 IST every trading day. Reads all available data and sends a single brief message:
  ```
  🌅 Market Brief — {date}
  ━━━━━━━━━━━━━━━━━━━
  GIFT Nifty: {direction} {pct}% → Nifty ~{expected_open}
  US: S&P {us_return}% | Nasdaq {nasdaq_return}%
  Crude: ${brent_price} ({brent_change}%)

  Today's regime: {overall_regime}
  → {posture recommendation}

  Overnight events:
  {list of overnight announcements from raw_announcements since 15:30 yesterday}

  Expiry: {expiry note or "Not expiry day"}
  Nifty support: {s1} | Resistance: {r1}
  ━━━━━━━━━━━━━━━━━━━
  ```

- [ ] **9.5** Register `bot/morning_brief.py`: daily 09:00, `trading_day_only`

- [ ] **9.6** Write basic intermarket divergence check in `market/regime_job.py` — after computing overall_regime, check: if Nifty up AND VIX up (rising together) → flag `fragile_rally = True`. If banks (NIFTY BANK) down AND Nifty flat → flag `internal_weakness = True`. If either flag is True, add a ⚠ note to regime output and reduce long confidence by 10% session-wide

### Phase 2 exit criteria (ALL must be met)
- [ ] Morning brief landing every trading day at 09:00 with correct GIFT Nifty and US data
- [ ] Alerts show regime (`overall_regime`) and sector RS rank
- [ ] Signals suppressed during 09:15–09:30 and 15:20+
- [ ] Lunch-zone signals reduced in confidence
- [ ] Confidence score visibly different in risk_on vs risk_off markets (spot check manually)
- [ ] All Phase 1 stability criteria still met (no regressions)

---
---

# PHASE 3 — Backtest Trust
**Duration:** 3–4 weeks
**Goal:** Backtest P&L numbers become honest (net-of-cost). Existing strategies validated. First validated strategy promoted to live — or explicitly shelved with reasons.
**Prerequisite:** Phase 2 stable.

---

## Week 10 — Cost Model + Backtester Alignment

### Tasks

- [ ] **10.1** Verify the existing backtester code. Confirm it has zero cost model. Confirm P&L is gross

- [ ] **10.2** Integrate `costs/model.py` (written in Phase 1 Week 5) into the backtester. Every simulated trade must pass through this function. Recompute all historical P&L numbers

- [ ] **10.3** Verify the backtester uses the same indicator definitions as the live engine — specifically SMA 20/50/200, RSI 14. If there are any differences, fix the backtester to match live

- [ ] **10.4** Run `bb_ema9_30m` strategy through the cost-adjusted backtester. Record: win rate, avg win, avg loss, profit factor, net Sharpe, max drawdown

- [ ] **10.5** Run `macd_willr_daily` strategy through the cost-adjusted backtester. Record same metrics

- [ ] **10.6** Write a markdown table in `LEARNINGS.md`: strategy name, gross Sharpe, net Sharpe, win rate, profit factor, verdict (promote/shelve). The difference between gross and net Sharpe is the cost drag — if this eliminates the edge, document why

### Week 10 gate
Both strategies have honest net-of-cost P&L numbers. Results recorded.

---

## Week 11 — Validation + Promotion or Shelve

### Tasks

- [ ] **11.1** Set up the experiment registry: create `backtest_registry` table:
  ```sql
  CREATE TABLE IF NOT EXISTS backtest_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT,
    strategy_name TEXT,
    param_hash TEXT,          -- hash of strategy params
    date_range TEXT,
    net_sharpe REAL,
    gross_sharpe REAL,
    win_rate REAL,
    profit_factor REAL,
    max_drawdown_pct REAL,
    n_trades INTEGER,
    cost_drag_pct REAL,       -- gross_sharpe - net_sharpe
    verdict TEXT,             -- 'promoted'/'shelved'/'needs_work'
    notes TEXT
  );
  ```

- [ ] **11.2** Implement CPCV validation for both strategies: split the data into 10 temporal folds. No random shuffling — time order preserved. For each fold, train on all other folds and test on this one. Report average net Sharpe across folds. If average CPCV Sharpe is positive after costs → strategy passes

- [ ] **11.3** Decision on each strategy:
  - If net Sharpe > 0.5 AND CPCV average positive → promote: add to `signals/detect.py` as a new signal type, feeding the same `paper_trades` loop
  - If net Sharpe < 0 or CPCV average negative → shelve: write explicit reasons in `backtest_registry.notes`
  - If mixed results → flag as needs_work, note specific conditions where it works

- [ ] **11.4** If promoting: add the new signal type to `signals/detect.py`, enrich it the same way as `long_buildup`, write paper trades from day one of promotion

- [ ] **11.5** Establish ORB-with-VWAP-filter as benchmark: backtest a simple Opening Range Breakout strategy with VWAP filter (long if price breaks opening range high AND above VWAP at 09:30, with ATR-based SL). This becomes the bar that every future strategy must beat. Record benchmark net Sharpe in `backtest_registry`

### Phase 3 exit criteria (ALL must be met)
- [ ] Backtester P&L is net-of-cost for all strategies
- [ ] Experiment registry seeded and being used for all runs
- [ ] Both existing strategies are either promoted (gate cleared) or shelved with documented reasons
- [ ] ORB-with-VWAP-filter benchmark backtest recorded
- [ ] All prior phase stability criteria still met

---
---

# PHASE 4 — Stock Intelligence
**Duration:** 5–6 weeks
**Goal:** Every alert carries quality score, key levels, delivery conviction. Full indicator set live. Patterns detecting.
**Prerequisite:** Phase 3 complete.

---

## Week 12 — Full Indicator Set (EOD)

### Tasks

- [ ] **12.1** Add to the nightly EOD indicator compute job (`indicators/compute.py`):
  - EMA 9 and EMA 21 (from bhavcopy close)
  - ATR 14 (already have in live_job — add nightly version from bhavcopy OHLC)
  - Bollinger Bands: upper = SMA20 + 2×std, lower = SMA20 − 2×std, width = (upper−lower)/SMA20, `bb_squeeze` = True when width < 20th percentile of width over last 252 days
  - ADX 14, DI+, DI− (from bhavcopy OHLC)
  - Supertrend (period=10, multiplier=2.0) — trend flip from this is the primary "regime changed" signal
  - OBV (running from bhavcopy volume × direction)
  - Volume SMA 20, volume_ratio (vs 20d avg)

- [ ] **12.2** Add to `indicator_live` table (new columns via migration):
  - `ema9`, `ema21`, `bb_upper`, `bb_lower`, `bb_squeeze`, `adx`, `supertrend_direction`, `obv`

- [ ] **12.3** Upgrade trend_regime classifier to use EMA9/21 in addition to SMA: if `ema9 > ema21 > sma50 > sma200` = strong_uptrend (more precise than SMA-only)

- [ ] **12.4** Add 5-min intraday indicators to `live_job.py`: Supertrend (intraday), Volume Delta (buy vol − sell vol, approximated from candle direction and volume)

### Week 12 gate
Full indicator set computing in EOD job. EMA, BB, ADX, Supertrend all in `indicator_live`. BB squeeze flag working.

---

## Week 13 — Levels + Delivery Conviction

### Tasks

- [ ] **13.1** Write migration `migrations/0XX_indicator_levels.sql`:
  ```sql
  CREATE TABLE IF NOT EXISTS indicator_levels (
    symbol TEXT NOT NULL,
    session_date TEXT NOT NULL,
    high_52w REAL, low_52w REAL, days_since_52w_high INTEGER, days_since_52w_low INTEGER,
    pdh REAL,      -- prior day high
    pdl REAL,      -- prior day low
    range_5d_high REAL, range_5d_low REAL,
    range_20d_high REAL, range_20d_low REAL,
    nearest_round_number REAL, dist_from_round_pct REAL,
    round_number_prior_failures INTEGER,  -- how many times rejected at this round number
    r1 REAL, r2 REAL, s1 REAL, s2 REAL,  -- pivot points from prior day HLC
    PRIMARY KEY (symbol, session_date)
  );
  ```

- [ ] **13.2** Write `indicators/levels.py` — runs nightly at 19:00 from bhavcopy:
  - PDH, PDL from yesterday's bhavcopy HIGH/LOW
  - 52w high/low and days-since from `raw_high_low_52w`
  - 5d/20d high-low ranges from last N rows of bhavcopy
  - Round number proximity: nearest of {50, 100, 200, 500, 1000, 2000, 5000}
  - Prior round number failure count: how many times in last 20 sessions did price approach within 0.5% of this round number and fail to break it
  - Pivot points: P = (H+L+C)/3 from yesterday. R1 = 2P−L, R2 = P+(H−L), S1 = 2P−H, S2 = P−(H−L)

- [ ] **13.3** Add levels to `pre_market_loader.py` — loads today's levels into Redis `levels:{symbol}` hash at 08:45. Static for the session.

- [ ] **13.4** Write migration `migrations/0XX_delivery_conviction.sql`:
  ```sql
  CREATE TABLE IF NOT EXISTS delivery_conviction (
    symbol TEXT NOT NULL,
    session_date TEXT NOT NULL,
    delivery_ratio REAL,          -- deliv_qty / traded_qty
    delivery_ratio_5d_avg REAL,
    delivery_ratio_z_score REAL,  -- vs 20d avg
    delivery_trend TEXT,          -- 'rising'/'flat'/'falling'
    delivery_conviction_score REAL,
    PRIMARY KEY (symbol, session_date)
  );
  ```

- [ ] **13.5** Write `indicators/delivery_tracker.py` — nightly at 18:30 from `raw_bhavcopy_cm`:
  - `delivery_ratio = DELIV_QTY / TOTTRDQTY` per symbol per day
  - 5d rolling avg, z-score vs 20d
  - Trend direction (rising if today > 5d avg by >5%)
  - `delivery_conviction_score` = composite:
    - High delivery + price up → score = 0.8 (accumulation)
    - High delivery + price down → score = 0.3 (distribution or capitulation — check trend)
    - Low delivery + price up → score = 0.4 (weak-hands chase)
    - High z-score (> 2) → bonus +0.1

- [ ] **13.6** Add levels and delivery to alert message:
  ```
  Stock: Quality n/a | Delivery: {trend} ({ratio:.0%})
  Tech:  VWAP {side} | RSI {rsi_5m} | {trend_regime}
         PDH: {pdh} | 52w High: {high_52w}
  ```

### Week 13 gate
Levels computed nightly for all symbols. Delivery conviction scores available. Alert message shows levels.

---

## Week 14 — Fundamentals + Quality Score

### Tasks

- [ ] **14.1** Write `fundamentals/quality_score.py` — composite 0–100 score using data already in DB:
  - Revenue growth YoY (from `raw_financial_results` — available even without PDF extraction)
  - P/E ratio (from `raw_quote_metadata`)
  - Market cap (proxy for size/liquidity)
  - Promoter holding % (from `raw_shareholding_pattern`)
  - Promoter pledge % (from `raw_shareholding_pattern` — if available)
  - ROE (from `raw_fundamentals_screener`)
  - ROCE (from `raw_fundamentals_screener`)
  - D/E ratio (from `raw_fundamentals_screener`)
  - 3y revenue CAGR (from `raw_fundamentals_screener`)

- [ ] **14.2** Score each component 0–10, weight them, sum to 0–100:
  - Revenue growth > 15% → 10pts; 10–15% → 7; 5–10% → 4; < 5% or negative → 0–2
  - ROCE > 20% → 10pts; 15–20% → 7; 10–15% → 4; < 10% → 1
  - ROE > 15% → 10pts; similarly graded
  - D/E < 0.3 → 10pts; 0.3–1 → 7; 1–2 → 3; > 2 → 0
  - Promoter holding > 50% → 8pts; 30–50% → 5; < 30% → 2
  - Pledge > 25% → deduct 15pts; > 50% → hard kill (already in gates)
  - P/E below sector avg → 5pts; above → 0–2

- [ ] **14.3** Write `fundamentals/table.sql` migration and populate nightly:
  ```sql
  CREATE TABLE IF NOT EXISTS stock_fundamentals (
    symbol TEXT PRIMARY KEY,
    quality_score REAL,
    revenue_growth_yoy REAL,
    roe REAL, roce REAL, debt_equity REAL,
    pe_ratio REAL, market_cap REAL,
    promoter_holding REAL, promoter_pledge REAL,
    loss_making INTEGER,  -- 1 or 0
    high_debt INTEGER,    -- D/E > 2
    updated_date TEXT
  );
  ```

- [ ] **14.4** Add quality score to hard gates: quality_score < 30 AND long signal → kill

- [ ] **14.5** Add quality score to confidence scorer (Layer 3):
  ```
  quality_score > 70 → +0.10
  quality_score 50–70 → +0.05
  quality_score 30–50 → 0.00
  quality_score < 30  → −0.15 (long signals)
  ```

- [ ] **14.6** Add quality score to alert message: `Quality: {quality_score}/100`

### Week 14 gate
`stock_fundamentals` table populated for all watchlist + F&O symbols. Quality score in every alert.

---

## Week 15 — Patterns + Stock Profile

### Tasks

- [ ] **15.1** Write `indicators/patterns.py` — per-minute DBJob during market hours:
  - Inside bar: today's high < yesterday's high AND today's low > yesterday's low
  - Volume dry-up: current 5m volume < 50% of 20-bar avg 5m volume
  - Support proximity: price within 0.5% of S1 or S2 from levels table
  - Resistance proximity: price within 0.5% of R1 or R2 from levels table
  - Higher-high: last bar high > prior bar high (simple momentum check)
  - Lower-low: last bar low < prior bar low

- [ ] **15.2** Add RSI–price divergence detector:
  - Bullish divergence: price making lower low BUT RSI making higher low (over last 10 bars)
  - Bearish divergence: price making higher high BUT RSI making lower high
  - Write to `patterns` with `pattern_type = 'bullish_divergence'` / `'bearish_divergence'`

- [ ] **15.3** Add fake-breakout filter to `breakout_52wh` signal: if `wick_rejection > 50%` (close is less than 50% of the way from low to high) AND volume < 1.2× avg → add `fake_breakout_risk = True` flag to signal. Reduce confidence by 0.10 when this flag is set

- [ ] **15.4** Write `profile/builder.py` — nightly DBJob at 19:30. For each symbol: join all Layer 4 outputs into a single row in `stock_profile_daily`. Include: quality_score, trend_regime, momentum_state, delivery_conviction_score, bb_squeeze, adx, levels (pdh/pdl/52w), pattern flags. This table is the ML training archive

- [ ] **15.5** Write migration for `stock_profile_daily` (~60 columns)

### Phase 4 exit criteria (ALL must be met)
- [ ] Quality score in every alert
- [ ] Key levels (PDH, PDL, 52w High) visible in alert action section
- [ ] Delivery conviction trend in every alert
- [ ] `stock_profile_daily` populating nightly
- [ ] Pattern flags feeding into confidence (divergence reduces confidence, BB squeeze boosts it)
- [ ] All prior stability criteria still met

---
---

# PHASE 5 — Event Intelligence
**Duration:** 4–6 weeks
**Goal:** PDF financial extraction working. Rating action alerts fire. Result beat/miss alerts fire. Pre-event risk flags suppress inappropriate trades.
**Prerequisite:** Phase 4 stable.

---

## Week 16 — PDF Pipeline Foundation + Rating Extractor

### Tasks

- [ ] **16.1** Write `config/priority.yaml` — subject → priority mapping. High priority includes: "Outcome of Board Meeting", "Dividend", "Acquisition", "Credit Rating", "Order Win", "Quarterly Results", "MD & CEO Change". Medium: "Investor Presentation", "Press Release". Low/skip: "Trading Window"

- [ ] **16.2** Write `parsers/classify.py` — reads `raw_announcements`, for each unclassified announcement: matches subject against `priority.yaml` patterns, writes priority to `raw_announcements.priority` column. Also sets `skip = True` for skip subjects

- [ ] **16.3** Extend announcements collector window to **21:30 IST** (from current 19:00). Most credit rating actions arrive 17:00–21:35. This is a scheduler config change in `endpoints.yaml`

- [ ] **16.4** Write `parsers/pdf_text.py` — takes PDF bytes, returns extracted text string:
  - Primary: pdfplumber (handles text-based PDFs — majority of NSE filings)
  - Fallback: pymupdf (for edge cases, corrupted PDFs)
  - Scanned PDF detection: if character count < 100 for a multi-page PDF → flag `pdf_error='ocr_required'` and return empty string (no OCR in this phase)

- [ ] **16.5** Write `parsers/rating_extractor.py` — reads `raw_announcements` where subject matches credit rating patterns. For each: calls `pdf_text.py` on the attachment, parses the text to extract:
  - Agency: look for "CRISIL", "ICRA", "CARE", "India Ratings", "Acuité", "Brickwork", "INFOMERICS"
  - Action: "reaffirmed"/"upgraded"/"downgraded"/"placed on watch"/"withdrawn"
  - Old rating and new rating from text patterns like "from A−/Stable to BBB+/Stable"
  - Instrument type: "Long Term", "Short Term", "NCD", "Commercial Paper"
  - `is_junk_downgrade`: True if new_rating is BB+/BB/B or below

- [ ] **16.6** Write migration `migrations/0XX_rating_actions.sql`:
  ```sql
  CREATE TABLE IF NOT EXISTS raw_rating_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    agency TEXT,
    action TEXT,         -- 'upgrade'/'downgrade'/'reaffirm'/'watch_negative'/'assigned'
    old_rating TEXT,
    new_rating TEXT,
    instrument_type TEXT,
    is_junk_downgrade INTEGER DEFAULT 0,
    broadcast_dt TEXT,
    announcement_fingerprint TEXT UNIQUE
  );
  ```

- [ ] **16.7** Backfill: run `rating_extractor.py` against the ~291 credit-rating PDFs already in `raw_announcements` with status `text_extracted`. Verify outputs on 10 samples manually

- [ ] **16.8** Write `signals/detect.py` addition — `credit_downgrade` signal:
  - Reads `raw_rating_actions` for unprocessed rows since last run
  - If action = 'downgrade': write signal. If `is_junk_downgrade = True`: signal_type = 'credit_downgrade_junk' (highest urgency)
  - If action = 'upgrade': write signal_type = 'credit_upgrade'
  - If action = 'watch_negative': write signal_type = 'credit_watch_negative'

- [ ] **16.9** Write rating alert message template (different format from intraday signals):
  ```
  🔴 {SYMBOL} — Credit Downgrade
  Agency: {agency} | Action: DOWNGRADE
  {old_rating} → {new_rating} | {instrument_type}
  Filed: {broadcast_dt}

  ⚠ Both LT and ST downgraded — serious stress signal
  ⚠ Next notch = junk territory

  Watch tomorrow open:
  Check pre-open IEP at 09:08 for gap direction
  If gap down > 3% and holds → short setup at 09:30
  If gap fills immediately → avoid
  ```

### Week 16 gate
Rating extractor processing new announcements. `raw_rating_actions` populating. Rating alert messages arriving on Telegram for genuine downgrades.

---

## Week 17 — Financial Extractor (Main Investment)

This is the hardest week in the entire roadmap. Budget 2–3 weeks if needed.

### Tasks

- [ ] **17.1** Download 50+ result PDFs from NSE for 50+ different F&O companies across the last 4 quarters. Store in `tests/financial_extraction/fixtures/`

- [ ] **17.2** Hand-label ground truth for all 50 PDFs. Create `tests/financial_extraction/ground_truth.yaml`:
  ```yaml
  RELIANCE_Q4FY26:
    revenue_cr: 234567
    pat_cr: 18900
    eps: 28.4
    yoy_revenue_growth: 12.3
  HDFCBANK_Q4FY26:
    net_interest_income_cr: 28900
    ...
  ```

- [ ] **17.3** Write `parsers/financial_extractor.py` — multi-strategy ensemble:
  - Strategy 1: `camelot.read_pdf(lattice=True)` — for bordered tables (most result PDFs)
  - Strategy 2: `camelot.read_pdf(stream=True)` — for whitespace-separated tables
  - Strategy 3: `pdfplumber` table extraction
  - Strategy 4: pymupdf text + regex — last resort for narrative-embedded numbers
  - For each strategy: returns `(numbers_dict, confidence_score)`
  - Ensemble: pick highest-confidence non-empty result

- [ ] **17.4** Write `config/field_aliases.yaml` — maps every variant label to canonical names. Build this from looking at actual PDFs:
  ```yaml
  revenue_cr:
    - "Total Income from Operations"
    - "Revenue from Operations"
    - "Net Revenues"
    - "Total Revenue"
    - "Gross Revenue"
  pat_cr:
    - "Profit After Tax"
    - "PAT"
    - "Net Profit"
    - "Profit for the period"
  ```

- [ ] **17.5** Write eval script `tests/financial_extraction/eval.py` — runs all 50 fixtures through the extractor, compares against ground truth, reports:
  - Accuracy per field (what % of PDFs got revenue correct within 2%)
  - Accuracy per strategy (which strategy works for which company types)
  - Failure cases list (which PDFs failed and why)
  - Target: 95% accuracy on F&O result PDFs

- [ ] **17.6** Write validation layer in `financial_extractor.py`:
  - Revenue > 0 (warn if negative)
  - PAT magnitude < revenue magnitude (warn otherwise)
  - If extracted YoY growth differs from company-stated YoY by > 5% → flag low confidence

- [ ] **17.7** Add earnings quality extraction alongside the main numbers:
  - CFO (cash flow from operations) if present in the PDF
  - If CFO/PAT > 1.0 → real profits. If < 0.5 → accounting concern
  - Receivables change YoY (if balance sheet available)

- [ ] **17.8** Write per-company quirk slot: `extractors/quirks/` — empty `__init__.py` plus one example quirk file for the most common failure case you find in the eval. This is the pattern for handling companies whose PDFs don't follow standard format

### Week 17 gate
Financial extractor achieving ≥ 90% accuracy on the 50-fixture eval set (targeting 95% — iterate until you get there).

---

## Week 18 — Event Signals + Pre-Event Gating

### Tasks

- [ ] **18.1** Write `events/calendar.py` — reads `raw_board_meetings`, populates `pending_events` table with: symbol, event_type ('result'/'dividend'/'agm'), expected_date, confidence ('confirmed'/'inferred')

- [ ] **18.2** Write `events/pre_event_risk.py` — runs nightly. For each stock with a pending event in next 10 days:
  - Compute `pre_event_run_5d` = (today_close − close_5d_ago) / close_5d_ago × 100
  - Compute `pre_event_run_10d` = (today_close − close_10d_ago) / close_10d_ago × 100
  - Classify: BUY_RUMOR_IN_PLAY (run > +8%), MILD_ANTICIPATION (+3–8%), NORMAL (±3%), FEAR_PRICED (−8% to −15%), SELL_RUMOR_IN_PLAY (< −15%)

- [ ] **18.3** Add to hard gates: if `BUY_RUMOR_IN_PLAY` AND `days_to_event <= 3` AND signal is `long` → suppress long signal, generate `BUY_RUMOR_WARNING` message instead

- [ ] **18.4** Write `result_beat` signal: reads newly parsed financial results from previous session (when `financial_extractor` runs overnight). If YoY revenue growth > +15% AND sentiment positive → signal. Include earnings quality flag

- [ ] **18.5** Write `result_miss` signal: YoY revenue growth < −10% OR strong negative sentiment → signal

- [ ] **18.6** Write result alert message format:
  ```
  🟢 {SYMBOL} — Result Beat
  Revenue: ₹{revenue}Cr (+{yoy}% YoY)
  PAT: ₹{pat}Cr | EPS: ₹{eps}
  Earnings Quality: {HIGH/LOW}

  RSI(5m): {rsi} | Regime: {regime}
  Confidence: {tier} ({score})
  ```

- [ ] **18.7** Write `parsers/analyst_ratings.py` — new scraper. Scrapes Moneycontrol analyst ratings page, 30-min cadence during market hours. Extracts: brokerage, symbol, old call, new call, old target, new target. Matches brokerage against `config/brokerage_tiers.yaml`. Writes to `raw_analyst_ratings`

- [ ] **18.8** Add `analyst_upgrade_tier1` and `analyst_downgrade_tier1` signals reading from `raw_analyst_ratings`

### Phase 5 exit criteria (ALL must be met)
- [ ] Credit rating alerts firing with correct agency/action/rating details
- [ ] Analyst Tier-1 upgrade/downgrade alerts firing
- [ ] `result_beat` and `result_miss` alerts firing from extracted PDF data
- [ ] BUY_RUMOR_WARNING suppressing long signals before exhausted pre-result stocks
- [ ] Announcements collector running until 21:30 IST
- [ ] Financial extractor eval harness showing ≥ 90% accuracy
- [ ] All prior stability criteria still met

---
---

# PHASE 6 — Psychological Layer
**Duration:** 3–4 weeks
**Goal:** System detects buy-rumor/sell-news, FOMO, capitulation, stop-hunts. Psychological state visible in every alert. Wrong-environment signals suppressed.
**Prerequisite:** Phase 5 stable.

---

## Week 19 — Psychological State Classifier

### Tasks

- [ ] **19.1** Add measurements to `indicator_live` (new columns via migration): `consecutive_up_days`, `consecutive_down_days`, `pre_event_run_5d`, `pre_event_run_10d`

- [ ] **19.2** Write `psychology/state_classifier.py` — runs every 5 minutes, `market_hours_only`. For each symbol, reads live data and classifies into one of 8 states:
  - `FOMO_EUPHORIA`: consecutive_up_days > 5 AND volume rising each day AND rsi_5m > 78 AND price > 3% above VWAP
  - `BUY_RUMOR`: pre_event_run_10d > +8% AND days_to_event ≤ 5 AND iv_vs_avg > 1.3
  - `NEUTRAL_TRENDING`: none of the extreme conditions
  - `SELL_NEWS`: event arrived (checked from pending_events) AND SPIKE_AND_FADE pattern detected in last 30 min
  - `FEAR_BUILDING`: consecutive_down_days ≥ 3 AND rsi_5m < 40 AND volume rising
  - `CAPITULATION`: consecutive_down_days > 4 AND rsi_5m < 22 AND price > 3% below VWAP AND delivery_ratio rising
  - `RELIEF_BOUNCE`: pre_event_run_10d < −10% AND event just resolved (today) AND price rising
  - `DEAD_CAT_BOUNCE`: 5d return < −8% AND today up AND current volume < prior down-day avg volume

- [ ] **19.3** Write psychological state to Redis `ind:{symbol}` hash as `psych_state` field and to `indicator_live`

- [ ] **19.4** Add psychological alignment score to confidence scorer (Layer 7):
  ```
  NEUTRAL_TRENDING + long  → +0.05
  FOMO_EUPHORIA + long     → −0.20
  BUY_RUMOR + long         → −0.10
  CAPITULATION + long      → +0.15
  SELL_NEWS + short        → +0.15
  RELIEF_BOUNCE + long     → +0.10
  DEAD_CAT_BOUNCE + long   → −0.15
  FEAR_BUILDING + long     → −0.08
  ```

- [ ] **19.5** Add psychological state to alert message: `Psychology: {psych_state}`

### Week 19 gate
Psychological states classifying correctly. FOMO_EUPHORIA visibly reducing long confidence. Morning brief mentions any FOMO or CAPITULATION stocks.

---

## Week 20 — Buy-Rumor/Sell-News + FOMO/Capitulation Alerts

### Tasks

- [ ] **20.1** Write `psychology/announcement_tracker.py` — fires when any new row appears in `raw_announcements` for a high-priority subject. Records price at T+0, T+5m, T+15m, T+30m from `raw_equity_quotes`. After T+5m is available, classifies pattern:
  - `SPIKE_AND_HOLD`: initial jump > 2% AND still > 1.5% after 5 minutes → genuine reaction
  - `SPIKE_AND_FADE`: initial jump > 2% AND fell back to < 0.8% after 5 minutes → sell-the-news
  - `NO_REACTION`: price moved < 1% in either direction → fully priced in
  - `REVERSE_REACTION`: news was positive BUT price fell > 1% → sell-the-news confirmed

- [ ] **20.2** Generate `sell_the_news_confirmed` signal when SPIKE_AND_FADE or REVERSE_REACTION detected after a positive announcement

- [ ] **20.3** Generate `better_than_feared_reversal` signal when: negative announcement was expected (pre_event_run was negative) AND price rises after the announcement

- [ ] **20.4** Write FOMO warning message template:
  ```
  ⚠ {SYMBOL} — FOMO Warning
  State: FOMO_EUPHORIA

  {consecutive_up_days} consecutive up days
  RSI(5m): {rsi} (overbought extreme)
  Price: {pct_above_vwap:.1f}% above VWAP

  DO NOT CHASE. Smart money sells into this.
  Watch for reversal near ₹{resistance}
  ```

- [ ] **20.5** Write capitulation watch message template:
  ```
  🟡 {SYMBOL} — Capitulation Zone
  State: CAPITULATION

  {consecutive_down_days} consecutive down days
  RSI(5m): {rsi} (oversold extreme)
  Price: {pct_below_vwap:.1f}% below VWAP
  Delivery: {delivery_trend} (long-term holders exiting)

  Potential reversal zone forming.
  Wait for stabilisation + volume dry-up.
  Entry only on confirmed base: RSI turns, volume falls
  ```

- [ ] **20.6** Write `psychology/exhaustion_detector.py` — runs every minute. Generates FOMO_WARNING and CAPITULATION_WATCH signals when conditions are met. Apply dedup (same stock, same type, 2-hour cooldown)

### Week 20 gate
Sell-the-news alerts firing on announcement + SPIKE_AND_FADE detection. FOMO and capitulation alerts generating. Manually verify 3–5 of each type against chart.

---

## Week 21 — Stop-Hunt Detection + Stability

### Tasks

- [ ] **21.1** Write `psychology/stop_hunt_detector.py` — checks on every 5-min candle close:
  - Detect pattern: price dips below obvious support (PDL, round number, or recent low within 0.5%) AND volume on that candle > 2× avg AND the same candle or next candle closes BACK ABOVE the support AND volume drops after recovery
  - If pattern detected → generate `LIQUIDITY_GRAB_LONG` signal
  - SL for this signal: below the wick low. Entry: on recovery above support level

- [ ] **21.2** Run psychological layer for 5 consecutive trading days. For each alert generated: review manually — was the state classification correct? Log any misclassifications in `LEARNINGS.md`

- [ ] **21.3** Tune thresholds if needed based on real data (e.g. if FOMO_EUPHORIA is triggering too often, raise the consecutive_up_days threshold)

### Phase 6 exit criteria (ALL must be met)
- [ ] Psychological state in every alert message
- [ ] FOMO_EUPHORIA visibly killing or reducing long signals in running stocks
- [ ] Sell-the-news pattern detecting correctly on announcement reactions (manually verified)
- [ ] Capitulation watch alerts arriving when a stock is genuinely exhausted
- [ ] Stop-hunt detector firing approximately 2–5 times per week (not every minute)
- [ ] All prior stability criteria still met

---
---

# PHASE 7 — Institutional + Derivatives
**Duration:** 4–6 weeks
**Goal:** Smart money score in every alert. Block deals show institution tier. GEX/max-pain computed. Index rebalancing alerts live.
**Prerequisite:** Phase 6 stable.

---

## Week 22 — Participant OI + Smart Money Score

### Tasks

- [ ] **22.1** Write `collectors/participant_oi.py` — downloads NSE F&O daily participant report (~19:00 IST). Extracts: FII/DII/Pro/Client × Index Futures/Stock Futures/Index Options/Stock Options, long contracts, short contracts, net. Writes to `raw_participant_oi`

- [ ] **22.2** Write `collectors/slb.py` — downloads SLB daily data. Writes to `raw_slb`. Fields: symbol, lending_qty, lending_rate, borrowing_qty

- [ ] **22.3** Write `collectors/fno_ban.py` — downloads F&O ban list daily. Writes to `raw_fno_ban`. Add to hard gates: if symbol in `raw_fno_ban` → kill signal

- [ ] **22.4** Write `collectors/index_rebalancing.py` — checks IISL (NSE Indices) website for index composition changes. When addition/removal announced: write to `raw_index_rebalancing` with effective_date. Generate `index_rebalancing_addition` or `index_rebalancing_removal` signal

- [ ] **22.5** Write `smart_money/score.py` — daily, runs at 20:00:
  - FII cash flow today (from `raw_fii_dii`): net positive = 0.7, neutral = 0.5, net negative = 0.3. Weight 25%
  - FII derivative positioning (from `raw_participant_oi`): FII adding index call longs → 0.7; FII adding index put longs → 0.3. Weight 20%
  - DII activity: net positive → 0.65. Weight 15%
  - Block deal tier (from `raw_large_deals` + `institution_database.yaml`): Tier-1 buying → 0.75; Tier-1 selling → 0.35; neutral → 0.5. Weight 15%
  - Promoter activity (from `raw_insider_trading`): open market buying → 0.80; selling → 0.40; neutral → 0.5. Weight 15%
  - SLB: falling lend qty (shorts covering) → 0.65; rising → 0.40. Weight 10%
  - Writes to `smart_money_daily` table

- [ ] **22.6** Write `config/institution_database.yaml`:
  ```yaml
  tier1:
    - LIC
    - SBI Mutual Fund
    - HDFC Mutual Fund
    - ICICI Prudential MF
    - Goldman Sachs
    - Morgan Stanley
    - BofA Securities
    - Citigroup
    - Jefferies
    - Temasek
    - GIC Singapore
    - Norges Bank
    - CPPIB
  tier2:
    - Motilal Oswal
    - Kotak Mahindra
    - Axis Mutual Fund
    - DSP Mutual Fund
  ```

- [ ] **22.7** Add smart_money_score to confidence scorer (Layer 8):
  ```
  score > 0.70 → +0.10 (accumulation)
  score 0.40–0.70 → 0.00 (mixed)
  score < 0.40 → −0.08 (distribution)
  ```

- [ ] **22.8** Add to alert message: `Smart money: {score:.2f} ({accumulating/mixed/distributing})`

### Week 22 gate
Smart money score computing daily. F&O ban list hard gate working. Participant OI data flowing.

---

## Week 23 — Options Greeks + GEX + Max Pain

### Tasks

- [ ] **23.1** Install `mibian` library: `pip install mibian`

- [ ] **23.2** Write `options/greeks.py` — DBJob, runs every 5 minutes alongside option chain collector. For each symbol with option chain data:
  - For each strike: compute Black-Scholes delta, gamma, theta, vega using current spot, strike, time-to-expiry (days), risk-free rate (use 6.5% as proxy for Indian 10yr), IV from option chain
  - For index options (NIFTY/BANKNIFTY/FINNIFTY): use Black-76 model (futures-settled)

- [ ] **23.3** Compute GEX (Net Dealer Gamma Exposure) per symbol:
  - For each strike: `dealer_gamma_CE = −gamma_CE × OI_CE × lot_size × spot` (dealer is short CE = short gamma)
  - For each strike: `dealer_gamma_PE = +gamma_PE × OI_PE × lot_size × spot` (dealer is long PE = long gamma)
  - `GEX_at_strike = dealer_gamma_CE + dealer_gamma_PE`
  - `total_GEX = sum(GEX_at_strike across all strikes)`
  - total_GEX > 0 → positive GEX → mean-reverting tape (market makers buy dips, sell rallies → range-bound)
  - total_GEX < 0 → negative GEX → trending tape (market makers chase moves → directional)
  - Write `gex_total`, `gex_sign`, `gex_flip_level` to `market_state` table

- [ ] **23.4** Compute max pain per expiry:
  - For each possible settlement price (range from −10% to +10% of spot in 0.5% steps):
  - Total loss = sum across all strikes of: max(0, settlement−strike) × OI_CE + max(0, strike−settlement) × OI_PE (× lot size)
  - Max pain = settlement price that minimises total loss
  - Write to `options_max_pain` table

- [ ] **23.5** Add GEX to market regime: include `gex_sign` in morning brief. If negative GEX + risk_on → momentum tape (amplify trend confidence). If positive GEX + VIX low → mean-revert tape (reduce momentum confidence)

- [ ] **23.6** Add `max_pain_drift` signal: on expiry days (Tue/Thu), if current Nifty price is > 1.5% away from max pain with < 3 hours to expiry → signal that price may drift toward max pain. Include expected drift direction and max pain level in message

- [ ] **23.7** Add PCR signals (already partially available from option chain): PCR < 0.7 → `pcr_extreme_low` (too many calls, contrarian bearish). PCR > 1.3 → `pcr_extreme_high` (too many puts, contrarian bullish)

### Week 23 gate
GEX computing from option chain data. Max pain level updating every 5 minutes on expiry days. PCR extremes generating signals.

---

## Week 24 — Block Deal Intelligence + Stability

### Tasks

- [ ] **24.1** Upgrade `large_deals` processor: add institution tier lookup. For each block deal, match `client_name` field against `config/institution_database.yaml`. Add `institution_tier` column to `raw_large_deals`

- [ ] **24.2** Short squeeze detector — write `smart_money/short_squeeze.py`:
  - Condition: stock fallen > 20% over 15 days AND SLB lending was high (> 2× avg) last week AND this week SLB lending falling AND delivery ratio rising AND price recovering
  - Generate `short_squeeze` signal — these are among the most explosive moves possible

- [ ] **24.3** Run the full stack for 5 consecutive trading days. No crashes. Spot-check 10 values manually. Verify smart money score makes intuitive sense (FII heavy buying day = score > 0.70)

### Phase 7 exit criteria (ALL must be met)
- [ ] Smart money score in every alert
- [ ] Block deals show institution tier (Tier-1/Tier-2/retail)
- [ ] GEX computed and visible in morning brief (mean-revert vs trending tape note)
- [ ] Max pain computing for each expiry; drift signal firing on expiry days
- [ ] Index rebalancing alerts active
- [ ] F&O ban list hard gate working
- [ ] All prior stability criteria still met

---
---

# PHASE 8 — Confidence Engine + Message Quality + Risk
**Duration:** 4–5 weeks
**Goal:** Confidence score uses all 9 layers. Messages become the polished final product. Risk rules enforced in paper simulation. Morning brief and EOD summary in their final form.
**Prerequisite:** Phase 7 stable.

---

## Week 25 — Full 9-Layer Confidence Engine

### Tasks

- [ ] **25.1** Rewrite `signals/confidence.py` into `confidence_engine.py` — the full 9-layer model:
  ```
  Layer 0: Hard gates (binary kills — already in detect.py, just verify)
  Layer 1: Market regime contribution (−0.15 to +0.15)
  Layer 2: Sector alignment (−0.10 to +0.10)
  Layer 3: Fundamental quality (−0.15 to +0.10)
  Layer 4: Technical alignment (−0.15 to +0.15)
  Layer 5: Signal-specific quality (0 to +0.10)
  Layer 6: Confluence bonus (0 to +0.10)
  Layer 7: Psychological alignment (−0.20 to +0.15)
  Layer 8: Institutional (−0.08 to +0.10)
  Layer 9: Event proximity (−0.15 to +0.08)

  final_confidence = 0.50 (base)
                   + sum of all layer contributions
                   (clipped to 0–1)
  ```

- [ ] **25.2** Layer 5 signal-specific quality (per signal type):
  - `long_buildup`: OI change ≥ 5% → +0.05, ≥ 10% → +0.08. Price change ≥ 2% → +0.03. Volume ≥ 3× → +0.05
  - `breakout_52wh`: clean break (no long wick) → +0.05. Volume ≥ 2× → +0.05
  - `credit_downgrade_junk`: always +0.10 (highest signal quality — clear catalyst)
  - `result_beat` with HIGH earnings quality: +0.08. With LOW quality: +0.02

- [ ] **25.3** Layer 6 confluence bonus — write `confluence/detector.py`:
  - `long_buildup` firing AND `volume_surge` on same stock within 30 min → +0.08
  - `breakout_52wh` AND `result_beat` within 2 days → +0.12
  - `credit_upgrade` AND technical breakout same day → +0.10
  - smart_money_score > 0.70 AND `long_buildup` AND sector RS rank ≤ 3 → +0.12
  - More than one independent signal type on same stock within 30 min → +0.06 flat bonus

- [ ] **25.4** Layer 9 event proximity: `pre_event_run_10d > +8%` AND `days_to_event ≤ 5` → −0.12. `iv_vs_avg > 2.0` (IV doubled) → −0.08 (uncertainty priced in). `SPIKE_AND_FADE detected` → +0.10 for short signals

- [ ] **25.5** Write `intermarket/divergence.py` — runs every 5 minutes:
  - Nifty up > 0.5% AND VIX up > 5% simultaneously → `FRAGILE_RALLY` flag → −0.10 all longs
  - NIFTY BANK down > 0.5% AND Nifty flat → `INTERNAL_WEAKNESS` flag → −0.08 all longs
  - Crude up > 2% AND energy sector (NIFTY ENERGY) flat/down → `CRUDE_DIVERGENCE` flag → note in morning brief
  - When divergence flag active → add ⚠ note to every alert

### Week 25 gate
9-layer confidence engine computing correctly. Confidence scores changing based on all 9 factors. Confluence bonus visibly activating on multi-signal days.

---

## Week 26 — Confidence Calibration

### Tasks

- [ ] **26.1** Write `calibration/calibrate.py` — reads `signal_outcomes` table (now with several months of data from Phase 1). For each confidence bucket (0.50–0.60, 0.60–0.70, 0.70–0.80, 0.80–0.90, 0.90+): compute actual win rate and actual avg return. Compare to expected (a signal scored 0.75 should win ~75% of the time)

- [ ] **26.2** If a bucket is systematically miscalibrated (e.g. signals scored 0.80 only winning 55%): adjust that layer's contribution weights. Document the adjustment in `backtest_registry`

- [ ] **26.3** Run calibration report and store as `calibration_results` table: date, bucket, signal_count, actual_win_rate, expected_win_rate, calibration_error

- [ ] **26.4** Add historical context to alert message (if sample size ≥ 30 for this signal type + regime combo):
  ```
  History (this setup, 90 days):
  Win {win_rate}% | Avg gain {avg_gain}% | PF {profit_factor} | n={sample_size}
  ```

### Week 26 gate
Calibration report generated. At least one layer weight adjusted based on data. Historical context appearing in alerts.

---

## Week 27 — Message Templates + EOD Summary

### Tasks

- [ ] **27.1** Rewrite `bot/dispatcher.py` to use proper templates via `bot/message_builder.py`. Every alert type gets its own template function. All templates follow the 6-section standard:
  ```
  ━━━━━━━━━━━━━━━━━━━━━━━━
  {emoji} {SYMBOL} — {Signal Type}
  Confidence: {tier} ({score:.2f})
  ━━━━━━━━━━━━━━━━━━━━━━━━

  Trigger: {trigger facts}

  Context:
  ✓ {green factor 1}
  ✓ {green factor 2}
  ✓ {green factor 3}

  {⚠ Risk flags if any}

  Action:
  Entry: ₹{entry_low}–{entry_high}
  SL:    ₹{sl} ({sl_pct}% risk)
  T1:    ₹{t1} (1R — exit 40%)
  T2:    ₹{t2} (2R — trail 60%)
  Flat by: 15:20 IST

  Summary: {1–2 sentence plain English explanation}
  ━━━━━━━━━━━━━━━━━━━━━━━━
  ```

- [ ] **27.2** Build structure-based SL: `SL = max(structure_SL, atr_SL)` where `structure_SL = PDL - (0.1% buffer)` for longs. Use the TIGHTER of structure vs ATR(1.5×)

- [ ] **27.3** Build chandelier trail for T2: `trail_price = rolling_highest_high(22 bars) - 3 × ATR(22)`

- [ ] **27.4** Write `bot/eod_summary.py` — runs at 18:00 IST every trading day:
  ```
  📊 EOD Summary — {date}
  ━━━━━━━━━━━━━━━━━━━━━━━━
  Nifty: {nifty_return}% | Volume: {vs_avg}×
  Sectors: Best = {best_sector} (+{pct}%) | Worst = {worst_sector} ({pct}%)

  Today's signals: {total} alerts
  {breakdown by signal type}

  Paper trades today: {n} | Net PnL: ₹{net_pnl}

  Overnight watch:
  {list of upcoming events tomorrow}
  {any after-hours rating actions from today}

  Tomorrow's known events:
  {list from pending_events}
  ━━━━━━━━━━━━━━━━━━━━━━━━
  ```

- [ ] **27.5** Upgrade morning brief to final form: include GEX sign (mean-revert or trending tape), smart money score, overnight rating actions, tomorrow's results schedule

- [ ] **27.6** Risk rules in paper simulation: add to `signals/paper_tracker.py`:
  - Daily kill-switch: if paper portfolio down −2% today → stop taking new paper trades
  - Consecutive loss check: 3 consecutive losses → reduce next trade size to half
  - VIX > 22: reduce position size by 40% in paper simulation

### Phase 8 exit criteria (ALL must be met)
- [ ] 9-layer confidence engine live and computing
- [ ] Calibration report shows reasonable alignment between score and actual win rate
- [ ] All alerts using polished 6-section template
- [ ] Historical context appearing in alerts (at least for `long_buildup` — the first signal with enough data)
- [ ] Morning brief in final form, arriving every trading day at 09:00
- [ ] EOD summary arriving every trading day at 18:00
- [ ] Risk rules enforced in paper simulation
- [ ] Structure-based SL + chandelier trail working in paper tracker
- [ ] All prior stability criteria still met

---
---

# PHASE 9 — Learning System
**Duration:** Ongoing from month 6
**Goal:** System learns from its own history. Historical odds appear in alerts. Bad signals get killed by data. Good signals get amplified.
**Prerequisite:** Phase 8 stable AND at least 90 days of paper_trades data accumulated (from Phase 1 start).

---

## Month 6+ — Analysis and Tuning

### Tasks (no strict weekly ordering — work through these over 1–2 months)

- [ ] **P9.1** Write `learning/performance_dashboard.py` — generates a report (printed or saved as HTML):
  - Per signal type: win rate (last 30/90/all days), avg win, avg loss, profit factor, Sharpe, best regime, best time of day
  - Strategy health scorecard: green (win rate > 55%, PF > 1.3), amber (45–55% OR PF 1.0–1.3), red (< 45% OR PF < 1.0)
  - Kill list: signals with win rate < 45% after 50+ samples

- [ ] **P9.2** Execute manual tuning decisions based on the dashboard:
  - Kill any signal in the red zone with > 50 sample size
  - For signals in green zone: lower confidence threshold from 0.65 to 0.60 to capture more (only if PF > 1.5)
  - Add regime filters for signals that clearly fail in specific conditions

- [ ] **P9.3** Write `learning/trade_clustering.py` — clusters losing trades by (signal_type, market_regime, time_of_day). Uses KMeans (3 clusters). Report: "What conditions does {signal_type} consistently lose in?" → add as hard gate if pattern is clear

- [ ] **P9.4** Survival analysis: for each signal type, compute Kaplan-Meier "time until trade becomes a loser" distribution. This tells you the optimal holding time. Write result to `signal_holding_profile` table and use in the paper tracker's time-based exit rule

- [ ] **P9.5** Historical context upgrade: add to alert message for each signal type once ≥ 30 samples per specific context (regime + signal type combination). "This setup in risk_on markets: win 73%, avg gain 2.2%, n=28"

### Phase 9 ongoing criteria
- [ ] Performance dashboard running weekly
- [ ] At least 1 signal killed or threshold adjusted based on data
- [ ] Historical context appearing in alerts for all major signal types
- [ ] Trade clustering identifying at least one avoidable losing pattern

---
---

# PHASE 10 — ML Enhancement
**Duration:** Month 12+
**Gate:** ≥ 500 labeled examples per signal type, multi-regime coverage, all prior phases stable.

---

## Pre-ML Data Readiness Check (do before starting any ML work)

- [ ] **ML.0** Count labeled examples per signal type from `signal_outcomes`. STOP if any signal type targeted for ML has < 500 samples
- [ ] **ML.1** Check regime coverage: are samples spread across risk_on, neutral, risk_off, and panic? If > 80% from one regime, wait for more data
- [ ] **ML.2** Verify all features in `signal_features` are point-in-time correct (no lookahead). Sample 20 rows and manually verify each feature was available at signal time

---

## Month 12 — Triple-Barrier Labels + First Model

### Tasks

- [ ] **ML.3** Write `ml/labeler.py` — triple-barrier labeling:
  - For each signal in `signal_features`: upper barrier = entry + 2×ATR, lower barrier = entry − 1×ATR, vertical = 15:20 IST
  - Label: +1 if upper hit first, −1 if lower hit first, 0 if time hit first
  - Add uniqueness weight: count overlapping labels (signals within 60 min of each other on same symbol), weight = 1/count

- [ ] **ML.4** Run CPCV validation setup: split data into 10 temporal folds. Verify purge gap (5 days) and embargo (2 days) between each fold

- [ ] **ML.5** Train LightGBM model for `long_buildup` signal type only:
  - Features: all columns from `signal_features` at detection time
  - Label: triple-barrier label
  - Validation: CPCV 10-fold
  - Report: CPCV Sharpe, win rate, profit factor, Deflated Sharpe
  - Must beat ORB benchmark (Sharpe 1.16) to be considered useful

- [ ] **ML.6** If model clears the benchmark: implement meta-labeling. Rule engine outputs side (long). LightGBM outputs P(profitable). If P > 0.55, take the trade at full size. If P 0.40–0.55, take at half size. If P < 0.40, skip.

- [ ] **ML.7** Calibrate model output: fit isotonic regression on OOS CPCV probabilities

- [ ] **ML.8** Set up drift monitoring: weekly PSI calculation on all input features. Alert if PSI > 0.25 on any feature

### Phase 10 ongoing criteria
- [ ] At least one signal type has a validated model beating ORB benchmark net of costs
- [ ] Deployed as size-adjusting secondary scorer
- [ ] Drift monitoring running and alerting

---
---

# QUICK REFERENCE

## Phase Summary

| Phase | Key deliverable | Gate to advance |
|---|---|---|
| 0 | ✅ Done | — |
| 1 | First Telegram alert on VPS | 5 clean trading days, alert fires, paper trades logging |
| 2 | Market regime + sector in alerts + morning brief | Morning brief arriving daily, regime visible |
| 3 | Honest backtest P&L + strategy verdict | Cost model in, at least one strategy promoted or shelved |
| 4 | Quality score + levels + delivery in alerts | All three visible in every alert |
| 5 | Rating + result alerts + pre-event gating | Rating alerts firing, result beats firing, BUY_RUMOR suppressing |
| 6 | Psychological state in alerts + FOMO/cap detection | Psych state visible, FOMO killing inappropriate longs |
| 7 | Smart money + GEX + max pain | Smart money in alerts, max pain drift firing on expiry days |
| 8 | Full 9-layer confidence + polished messages | All templates live, morning brief + EOD summary arriving |
| 9 | Data-driven signal tuning | At least 1 signal killed or adjusted by data |
| 10 | ML meta-labeler on first signal type | Model beats ORB benchmark, deployed as size scaler |

## Files to update when you ship something

1. **This file** — change `[ ]` to `[x]` on completed tasks
2. **FEATURE_CHECKLIST_IMPROVED.md** — move 📋 → ✅
3. **LEARNINGS.md** — append anything new discovered
4. **backtest_registry** table — every backtest run, no exceptions

## Things that are NEVER acceptable

- Advancing a phase before exit criteria are met
- Trusting any P&L number that isn't net of the full cost model
- Building a feature without a way to test that it's correct
- Using current-bar values as features (must always shift(1))
- Sending an alert on a blacklisted stock

## When something breaks

1. Check `endpoint_health` table — which collector failed?
2. Check `fetch_log` — what error was returned?
3. Check `LEARNINGS.md` — has this happened before?
4. Fix the bug, spot-check the data for that period, restart the service
5. Append to `LEARNINGS.md` what broke and why

---

*Update "CURRENT POSITION" at the top of this file every time you start a new phase or week.*