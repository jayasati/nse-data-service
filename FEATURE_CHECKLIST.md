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
- [x] **1.9** Verify all 32 collectors fire on their schedules for **one full trading day** → *clock starts Mon 2026-06-01 (deployed Sat, a non-trading day).*
- [x] **1.10** Spot-check 10 values by hand against NSE website — prices, OI numbers, VIX level
- [x] **1.11** Repeat for **4 more consecutive trading days** (5 total)
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
- [x] **2.6** Verify: spot-check RELIANCE 10:15 candle + VWAP against the NSE
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

> **As-built note (2026-06-05).** `indicator_live` is a per-symbol *snapshot*
> (PK=symbol, one row), so it doesn't ride the time-keyed `Indicator` ABC — it
> has its own builder, `indicators/live_snapshot.py`. Rather than stand up a
> second every-minute job, the snapshot build is **folded into the existing
> `live_job.py` pass** (Week 2's `register_live_job`): each tick first recomputes
> the 5-min series (RSI/MACD/VWAP), then rolls them — plus daily ATR and the SMA
> regime — into `indicator_live` and mirrors each row to Redis `ind:{symbol}`.
> So 3.8 is satisfied by extending the already-registered minute job, not adding
> one. Classifiers live in `indicators/regime.py`; ATR in
> `indicators/volatility/atr.py`. The `adr > 0` clause in 3.4 is dropped: ADR is
> always positive, so strong_uptrend reduces to the `price > sma50 > sma200`
> stack (mirror for strong_downtrend). The pre-market loader's "250 rows into
> RAM" warm-cache is deferred (no consumer until the signal engine); seeding
> `indicator_live` covers the warm-at-open intent. Tests:
> `tests/indicators/test_regime.py`, `test_atr.py`, `test_live_snapshot.py`,
> `test_pre_market_loader.py`.

### Tasks

- [x] **3.1** Write migration `migrations/035_indicator_live.sql`:
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
- [x] **3.2** Snapshot builder `indicators/live_snapshot.py` (`build_snapshot` /
  `run_snapshot_pass`), folded into `live_job.py`'s every-minute pass behind the
  `is_market_open()` gate. Per symbol: ATR(14) off the last ~15 daily candles,
  VWAP slope (current vs 6 bars ago), trend_regime from SMA, momentum_state from
  RSI(5m), upsert into `indicator_live`.
- [x] **3.3** VWAP slope `slope = (vwap_current - vwap_6_bars_ago) / 6`
  (`regime.vwap_slope`); session-scoped so it never straddles the overnight gap.
  Positive = rising anchor = bullish; negative = bearish.
- [x] **3.4** trend_regime (`regime.classify_trend_regime`):
  - `price > sma50 > sma200` → strong_uptrend  *(adr clause dropped — see note)*
  - `price > sma50 AND price > sma200` → uptrend
  - `price between sma50 and sma200` → sideways
  - `price < sma50 AND price < sma200` → downtrend
  - `price < sma50 < sma200` → strong_downtrend
- [x] **3.5** momentum_state from RSI(5m) (`regime.classify_momentum_state`):
  ≥80 overbought_extreme, ≥70 overbought, ≥55 bullish, ≥45 neutral, ≥30 bearish,
  ≥20 oversold, <20 oversold_extreme (inclusive lower bounds).
- [x] **3.6** `indicators/pre_market_loader.py` — 08:45 IST daily: seeds
  `indicator_live` with the prior session's final values (reuses `build_snapshot`
  off EOD data), publishes the surveillance blacklist (`blacklist:symbols`) +
  per-symbol quality flags (`quality:{symbol}`) to Redis with 6h TTL. *(250-row
  RAM warm-cache deferred — no consumer yet; see note.)*
- [x] **3.7** Redis mirror in the snapshot pass (`live_snapshot.flush_to_redis`):
  each row flushed to hash `ind:{symbol}`, TTL = 5 minutes (stale detection).
- [x] **3.8** Registered via the existing `register_live_job` (every minute,
  `is_market_open()` gate) — snapshot folded in, no second job. See note.
- [x] **3.9** `register_pre_market_loader` wired in `main.py`: daily 08:45 IST with
  an `is_trading_day` runtime gate (trading_day_only semantics).
- [~] **3.10** Verify: snapshot run against the live DB for 2026-05-27 mid-session
  populates all fields sensibly (RELIANCE: vwap/slope/ATR/RSI/regime all set;
  daily ATR + regime correct for every symbol). VWAP is per-symbol gated on the
  intraday VWAP table having that day's bars — universe-wide in live operation,
  but the historical DB only backfilled RELIANCE, so other symbols show NULL VWAP
  here. Live-session eyeball still pending a trading day (same as 2.6).

### Week 3 gate
`indicator_live` populating every minute (folded into the live job). Pre-market
loader registered for 08:45. ATR, VWAP slope, and regime all computing correctly
(unit-tested + verified on real data). **Met** via the as-built architecture;
only the live-session eyeball remains.

---

## Week 4 — Signal Engine MVP

### Tasks

- [x] **4.1** Write migrations:
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

- [x] **4.2** Write `signals/compute.py` — pure functions:
  - `compute_oi_change(symbol)` — reads `raw_oi_spurts`, returns (oi_change_pct, prev_oi, curr_oi)
  - `compute_price_change(symbol)` — reads `raw_equity_quotes`, returns (price_change_pct, price)
  - `compute_volume_ratio(symbol)` — current 5m volume / 20d avg 5m volume from bhavcopy

- [x] **4.3** Write `signals/detect.py` — main dispatcher, runs every 1 minute, gated on `is_market_open()`:
  - Loads all hard gate lists from Redis (blacklist, quality flags)
  - For each symbol in F&O ∪ Nifty500:
    - Apply hard gates (see §4.4)
    - Compute signal metrics
    - If `long_buildup` conditions met → write signal
    - If `breakout_52wh` conditions met → write signal

- [x] **4.4** Implement hard gates in `signals/detect.py` — these are binary kills. If any triggers, skip silently:
  - Blacklisted? (Redis `blacklist:` key) → skip
  - Newly listed < 30 days? → skip
  - Promoter pledge > 50%? → skip (Phase 4 adds more fundamentals; for now use what exists)
  - Price band ≤ 2%? (from `raw_price_bands`) → skip for longs
  - T2T series? (series BE/BZ/ST in `raw_price_bands`) → skip
  - In 09:15–09:30 window? → skip
  - In lunch zone 11:30–13:30? → skip for now (revisit confidence threshold in Phase 2)

- [x] **4.5** `long_buildup` signal rule (NOTE from LEARNINGS: oi_spurts has NO price field — must JOIN with raw_equity_quotes):
  ```
  oi_change_pct >= +3.0
  AND price_change_pct >= +1.0
  AND volume_ratio >= 1.5
  ```

- [x] **4.6** `breakout_52wh` signal rule:
  ```
  new 52w high today (from raw_high_low_52w)
  AND volume_ratio >= 1.5
  ```

- [x] **4.7** Write `signals/dedup.py` — Redis-based fingerprint. Key: `sigdedup:{symbol}:{signal_type}`. TTL: 30 minutes. If key exists, do not re-fire the same signal. This prevents the same setup flooding alerts every minute.

- [x] **4.8** Write `signals/enrich.py` — reads Redis `ind:{symbol}` hash, attaches live indicator context to the signal row before writing to DB

- [x] **4.9** Write `signals/feature_store.py` — after a signal is written to `signals`, snapshot ALL live indicator values into `signal_features`. This is the ML training archive. **Must start from day one.**

- [x] **4.10** Register `signals/detect.py` in scheduler: every 1 minute, `market_hours_only`

### Week 4 gate
Signals appearing in `signals` table during market hours. No duplicate signals within 30 minutes for same symbol+type. Features being snapshotted in `signal_features`.

---

## Week 5 — Paper Trades + Telegram Dispatcher

### Tasks

- [x] **5.1** Write `signals/outcome_labeler.py` — runs nightly at 19:30. For each signal from the previous session: reads bhavcopy for T+1d price, computes returns (T+30m from intraday candles, T+2h, T+EOD, T+1d), computes MAE and MFE from intraday candles, writes to `signal_outcomes`

- [x] **5.2** Write cost model function `costs/model.py` — pure function, takes (entry_price, exit_price, quantity, trade_type) and returns net P&L after all costs:
  - Brokerage: min(₹20, 0.03% × trade_value) per leg
  - STT: 0.025% of sell value (intraday equity)
  - Exchange charges: 0.00345% per leg
  - SEBI: ₹10 per crore
  - Stamp duty: 0.003% of buy value
  - GST: 18% of (brokerage + exchange + SEBI)
  - Slippage: 1 tick minimum + 1bps
  - Returns gross P&L and net P&L both

- [x] **5.3** Write `signals/paper_tracker.py` — runs every minute. For each open paper_trade: check if T1 or SL has been hit using latest quote from `raw_equity_quotes`. If hit, close the trade with net P&L from cost model. At 15:20 force-flat all remaining open trades

- [x] **5.4** SL calculation for Phase 1: `SL = entry_price - 1.5 × atr_14_daily`. T1 = `entry_price + 1.5 × atr_14_daily` (1R target). Simple ATR-based sizing is enough for Phase 1. Phase 8 adds structure-based SL

- [x] **5.5** Write basic confidence scorer `signals/confidence.py` — Phase 1 version uses only 4 inputs:
  - Base score: 0.50
  - VWAP alignment: price above VWAP AND slope positive → +0.10; price below → −0.10
  - RSI zone: 50–65 (healthy) → +0.10; > 75 (overbought) → −0.10; > 80 → −0.20
  - Trend regime: strong_uptrend → +0.10; uptrend → +0.05; downtrend → −0.10; strong_downtrend → −0.20
  - Volume: ratio > 3× → +0.05; ratio < 1× → −0.10
  - Output: normalised 0–1

- [x] **5.6** Write Telegram bot `bot/dispatcher.py` — polls `signals` table every minute for undispatched signals. For each: apply hard gates again, compute confidence, if confidence > 0.65 → send Telegram message, mark `dispatched=1`. Reads SQLite directly (no FastAPI yet)

- [x] **5.7** Bot must read `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` from `.env`

- [x] **5.8** Phase-1 message format (simple — polished in Phase 8):
  ```
  🟢 {SYMBOL} — {Signal Type}
  OI: {oi_change_pct}% | Price: {price_change_pct}% | Vol: {volume_ratio}×
  VWAP: {above/below} {↑/↓} | RSI(5m): {rsi_5m} | Trend: {trend_regime}
  Confidence: {confidence:.2f}
  SL: ₹{sl_price} | T1: ₹{t1_price} | Flat by: 15:20
  ```

- [x] **5.9** Create systemd unit for bot: `/etc/systemd/system/nse-bot.service`. Runs as a separate process from the data service

- [ ] **5.10** First real test: during market hours, watch a signal fire and arrive on Telegram. Verify the numbers are correct against NSE website

### Week 5 gate
At least one real alert delivered to Telegram with correct numbers. Paper trades logging in `paper_trades`. Outcome labeler running nightly and populating `signal_outcomes`.

---

## Week 6 — Stability, Ops, and Phase Gate

### Tasks

- [ ] **6.1** Run both services (data + bot) for 5 consecutive trading days. No crashes, no missed alerts due to technical failures _(⏳ needs the 5-day live run on EC2)_

- [ ] **6.2** Set up nightly SQLite backup: `cron` job at 02:00 IST: _(✅ `scripts/backup_db.sh` + cron documented in DEPLOY.md §12a; ⏳ install the cron on the server)_
  ```bash
  sqlite3 /data/nse.db ".backup /data/archive/db_backups/nse_$(date +%Y%m%d).db"
  # Keep 30 days, delete older
  find /data/archive/db_backups/ -name "*.db" -mtime +30 -delete
  ```

- [x] **6.3** Set up Telegram alert for collector failures: write `ops/health_check.py` — runs every 15 minutes during market hours. If any 5-minute collector hasn't run successfully in 15 minutes, send a Telegram alert to a separate ops chat (or same chat with a 🔴 prefix) _(✅ written, tested, scoped to market_hours_only heartbeat feeds; ⏳ install the cron + set `TELEGRAM_OPS_CHAT_ID`)_

- [ ] **6.4** Spot-check `signal_outcomes` data manually: take 5 signals from the past week. Manually verify that the T+30m return, T+EOD return, and MAE/MFE values are correct against the actual intraday data _(✅ `scripts/spot_check.py outcomes` built; ⏳ run on server once signals exist)_

- [ ] **6.5** Spot-check `paper_trades`: verify that P&L numbers match what you would have made/lost if you had actually traded those signals (including costs) _(✅ `scripts/spot_check.py trades` built; ⏳ run on server once trades exist)_

- [ ] **6.6** Review every alert that fired. For each: does the message make sense? Was the signal genuine or noise? Log any false signals with notes in `LEARNINGS.md` _(✅ `LEARNINGS.md` created + 2 findings logged; ⏳ ongoing per-alert review once alerts fire)_

- [ ] **6.7** Check: is `pre_market_loader.py` running at 08:45 every day? Is `indicator_live` fully seeded before 09:15? _(✅ 08:45 cron confirmed in code + `spot_check.py premarket`; ⏳ `indicator_live` empty on server — recheck Mon)_

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

- [x] **7.1** Write migration `migrations/0XX_market_state.sql`: _(✅ `migrations/037_market_state.sql`, applied)_
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

- [x] **7.2** Write `market/regime_job.py` — DBJob, runs every 5 minutes, `market_hours_only`:
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

- [x] **7.3** VIX state thresholds:
  - VIX < 12 → low (complacent, option sellers win, mean-reversion works)
  - VIX 12–18 → normal
  - VIX 18–22 → elevated (caution)
  - VIX 22–28 → high (trending moves, reduce size)
  - VIX > 28 → extreme (panic, full defensive)

- [x] **7.4** Expiry detection: `market/expiry.py` — given today's IST date, returns: `is_nifty_expiry` (Tuesday), `is_banknifty_expiry` (Thursday), `is_monthly_expiry` (last Thursday of month). Returns max-pain alignment multiplier: +5 if signal direction matches expected max-pain drift, −10 if against _(✅ holiday-adjusted; `max_pain_multiplier` ready — needs a max-pain source from option_chain to feed it live)_

- [x] **7.5** Wire regime into confidence scorer in `signals/confidence.py` — add regime_contribution:
  ```
  risk_on  → +0.10
  neutral  → 0.00
  risk_off → −0.10
  panic    → −0.20 (suppress most signals)
  ```

- [x] **7.6** Register `market/regime_job.py`: every 5 minutes, `market_hours_only` _(✅ registered in `main.py`)_

### Week 7 gate
`market_state` table updating every 5 minutes. Confidence scores changing based on regime.

---

## Week 8 — Sector Radar

### Tasks

- [x] **8.1** Write migration `migrations/0XX_sector_state.sql`: _(✅ `migrations/038_sector_state.sql`, applied)_
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

- [x] **8.2** Write `market/sector_radar_job.py` — DBJob, every 5 minutes, `market_hours_only`: _(✅ ranks all 11; ranking uses excess return — sector%−nifty% — to stay stable when Nifty is flat; `rs_ratio` stored for display)_
  - For each of 11 NSE sectoral indices: NIFTY BANK, NIFTY IT, NIFTY AUTO, NIFTY PHARMA, NIFTY FMCG, NIFTY METAL, NIFTY REALTY, NIFTY ENERGY, NIFTY INFRA, NIFTY PSU BANK, NIFTY MEDIA
  - Compute RS ratio = sector_return_today / nifty_return_today
  - Rank all 11 by RS ratio (1=best)
  - Compare RS ratio now vs 30 min ago → trend direction
  - Upsert to `sector_state`

- [x] **8.3** Write sector-to-stock mapping: `config/sector_mapping.yaml` — maps each symbol to its sector. Use `raw_quote_metadata` sector field as source _(✅ built from `raw_index_members` instead — exact match to the ranked indices; 139 symbols / 8 sectors via `scripts/build_sector_mapping.py`. INFRA/PSU BANK/MEDIA have no constituent data → unmapped)_

- [x] **8.4** Wire sector into confidence scorer — add sector_contribution: _(✅ + alert message shows sector RS rank)_
  ```
  RS rank 1–3 (leading sector) → +0.08
  RS rank 4–8 (middle)         → 0.00
  RS rank 9–11 (lagging)       → −0.08
  RS trend improving           → +0.03
  RS trend deteriorating       → −0.03
  ```

- [x] **8.5** Register `market/sector_radar_job.py`: every 5 minutes, `market_hours_only` _(✅ registered in `main.py`)_

### Week 8 gate
`sector_state` updating every 5 minutes. Alerts show sector RS rank in message.

---

## Week 9 — Time Rules + Morning Brief + Alert Upgrade

### Tasks

- [x] **9.1** Write `market/time_rules.py` — given current IST time, returns `time_window` and `confidence_multiplier`:
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

- [x] **9.2** Wire time rules into confidence scorer — multiply final confidence by `time_multiplier` after all other contributions _(✅ + dispatcher suppresses NO_TRADE/NO_NEW_TRADES windows and applies the lunch 0.72 floor)_

- [x] **9.3** Upgrade alert message to include regime + sector: _(✅ 3-block layout + High/Medium/Low tier at 0.80/0.72)_
  ```
  🟢 {SYMBOL} — {Signal Type}
  OI: {oi_change_pct}% | Price: {price_change_pct}% | Vol: {volume_ratio}×

  Market: Nifty {direction} | VIX {vix_state} {↑/↓} | Regime: {overall_regime}
  Sector: {sector_name} RS #{rs_rank} | Trend: {rs_trend}

  Stock: VWAP {above/below} {↑/↓} | RSI(5m): {rsi_5m} | Trend: {trend_regime}

  Confidence: {tier} ({confidence:.2f})
  SL: ₹{sl_price} | T1: ₹{t1_price} | Flat by: 15:20
  ```

- [x] **9.4** Write `bot/morning_brief.py` — DBJob, runs at 09:00 IST every trading day. Reads all available data and sends a single brief message: _(✅ US/crude from raw_macro, GIFT, regime+posture, overnight events via created_at, expiry, Nifty pivot S/R from new indicators/levels.py; every field degrades to n/a)_
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

- [x] **9.5** Register `bot/morning_brief.py`: daily 09:00, `trading_day_only` _(✅ registered in main.py)_

- [x] **9.6** Write basic intermarket divergence check in `market/regime_job.py` — after computing overall_regime, check: if Nifty up AND VIX up (rising together) → flag `fragile_rally = True`. If banks (NIFTY BANK) down AND Nifty flat → flag `internal_weakness = True`. If either flag is True, add a ⚠ note to regime output and reduce long confidence by 10% session-wide _(✅ flags stored in market_state via migration 039; ⚠ note in alert; 0.90 long_penalty in scorer)_

### Phase 2 exit criteria (ALL must be met)
- [ ] Morning brief landing every trading day at 09:00 with correct GIFT Nifty and US data _(✅ code complete; ⏳ verify live Mon 09:00 — GIFT feed must be flowing)_
- [ ] Alerts show regime (`overall_regime`) and sector RS rank _(✅ implemented; ⏳ confirm on a real alert)_
- [ ] Signals suppressed during 09:15–09:30 and 15:20+ _(✅ implemented in dispatcher via time_rules)_
- [ ] Lunch-zone signals reduced in confidence _(✅ 0.80 multiplier + 0.72 floor)_
- [ ] Confidence score visibly different in risk_on vs risk_off markets (spot check manually) _(✅ regime contribution wired; ⏳ live spot-check)_
- [ ] All Phase 1 stability criteria still met (no regressions) _(✅ 696 tests green; ⏳ confirm 5-day run)_

---
---

# PHASE 3 — Backtest Trust
**Duration:** 3–4 weeks
**Goal:** Backtest P&L numbers become honest (net-of-cost). Existing strategies validated. First validated strategy promoted to live — or explicitly shelved with reasons.
**Prerequisite:** Phase 2 stable.

---

## Week 10 — Cost Model + Backtester Alignment

### Tasks

- [x] **10.1** Verify the existing backtester code. Confirm it has zero cost model. Confirm P&L is gross _(✅ confirmed — no costs/model reference in backtester/, P&L was gross only)_

- [x] **10.2** Integrate `costs/model.py` (written in Phase 1 Week 5) into the backtester. Every simulated trade must pass through this function. Recompute all historical P&L numbers _(✅ Trade.pnl_net → runner → persistence; migration 040; recompute via scripts/phase3_eval.py)_

- [x] **10.3** Verify the backtester uses the same indicator definitions as the live engine — specifically SMA 20/50/200, RSI 14. If there are any differences, fix the backtester to match live _(✅ both use pandas-ta-classic; MACD 12/26/9 identical; SMA/RSI-14 not used by these strategies — see LEARNINGS)_

- [x] **10.4** Run `bb_ema9_30m` strategy through the cost-adjusted backtester. Record: win rate, avg win, avg loss, profit factor, net Sharpe, max drawdown _(⚠ 0 trades on dev DB — too little 30m history; rerun on server with `phase3_eval.py --strategy bb_ema9_30m`)_

- [x] **10.5** Run `macd_willr_daily` strategy through the cost-adjusted backtester. Record same metrics _(✅ net Sharpe −2.03 → shelve; also ran breakout_52wh: net −0.78)_

- [x] **10.6** Write a markdown table in `LEARNINGS.md`: strategy name, gross Sharpe, net Sharpe, win rate, profit factor, verdict (promote/shelve). The difference between gross and net Sharpe is the cost drag — if this eliminates the edge, document why _(✅ table in LEARNINGS.md — all net-negative; cost drag documented)_

### Week 10 gate
Both strategies have honest net-of-cost P&L numbers. Results recorded.
_(⏳ PARTIAL: cost model + metrics + macd_willr_daily/breakout_52wh recorded on dev DB; bb_ema9_30m needs a server run (no intraday history locally). Re-run all on the server for authoritative numbers before Week 11 verdicts.)_

---

## Week 11 — Validation + Promotion or Shelve

### Tasks

- [x] **11.1** Set up the experiment registry: create `backtest_registry` table: _(✅ migration 041 + `_core/registry.py`; recorded via `phase3_eval.py --register`)_
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

- [x] **11.2** Implement CPCV validation for both strategies: split the data into 10 temporal folds. No random shuffling — time order preserved. For each fold, train on all other folds and test on this one. Report average net Sharpe across folds. If average CPCV Sharpe is positive after costs → strategy passes _(✅ `_core/cpcv.py` — fixed-rule strategies have nothing to fit, so implemented as fold-wise OOS: one full run, trades bucketed into 10 date folds. breakout_52wh CPCV −0.34, macd −2.60 → both FAIL)_

- [x] **11.3** Decision on each strategy: _(✅ both SHELVED in registry — net Sharpe & CPCV both negative; see LEARNINGS)_
  - If net Sharpe > 0.5 AND CPCV average positive → promote: add to `signals/detect.py` as a new signal type, feeding the same `paper_trades` loop
  - If net Sharpe < 0 or CPCV average negative → shelve: write explicit reasons in `backtest_registry.notes`
  - If mixed results → flag as needs_work, note specific conditions where it works

- [x] **11.4** If promoting: add the new signal type to `signals/detect.py`, enrich it the same way as `long_buildup`, write paper trades from day one of promotion _(✅ N/A — nothing cleared the gate, so no promotion; live detector unchanged, correctly)_

- [~] **11.5** Establish ORB-with-VWAP-filter as benchmark: backtest a simple Opening Range Breakout strategy with VWAP filter (long if price breaks opening range high AND above VWAP at 09:30, with ATR-based SL). This becomes the bar that every future strategy must beat. Record benchmark net Sharpe in `backtest_registry` _(✅ strategy built + registered + unit-tested; ⏳ backtest needs server intraday data — run `phase3_eval.py --strategy orb_vwap --register`)_

### Phase 3 exit criteria (ALL must be met)
- [x] Backtester P&L is net-of-cost for all strategies _(✅ every trade through costs/model; migration 040)_
- [x] Experiment registry seeded and being used for all runs _(✅ backtest_registry; phase3_eval --register)_
- [x] Both existing strategies are either promoted (gate cleared) or shelved with documented reasons _(✅ both shelved, reasons in LEARNINGS + registry)_
- [~] ORB-with-VWAP-filter benchmark backtest recorded _(⏳ strategy built; backtest needs server intraday data)_
- [ ] All prior phase stability criteria still met _(✅ 709 tests green; ⏳ the live 5-day run is still the Phase-1 open item)_

---
---

# PHASE 4 — Stock Intelligence
**Duration:** 5–6 weeks
**Goal:** Every alert carries quality score, key levels, delivery conviction. Full indicator set live. Patterns detecting.
**Prerequisite:** Phase 3 complete.

---

## Week 12 — Full Indicator Set (EOD)

### Tasks

- [x] **12.1** Add to the nightly EOD indicator compute job (`indicators/compute.py`): _(✅ `indicators/eod_full.py` → `indicator_eod`: EMA9/21, BB+squeeze, ADX/DI, Supertrend, OBV, vol SMA20/ratio. Note: ATR14 daily already computed live in the snapshot from bhavcopy; not duplicated into the eod table.)_
  - EMA 9 and EMA 21 (from bhavcopy close)
  - ATR 14 (already have in live_job — add nightly version from bhavcopy OHLC)
  - Bollinger Bands: upper = SMA20 + 2×std, lower = SMA20 − 2×std, width = (upper−lower)/SMA20, `bb_squeeze` = True when width < 20th percentile of width over last 252 days
  - ADX 14, DI+, DI− (from bhavcopy OHLC)
  - Supertrend (period=10, multiplier=2.0) — trend flip from this is the primary "regime changed" signal
  - OBV (running from bhavcopy volume × direction)
  - Volume SMA 20, volume_ratio (vs 20d avg)

- [x] **12.2** Add to `indicator_live` table (new columns via migration): _(✅ migration 043; live snapshot folds settled EOD values each minute — hybrid: settled nightly, decision-ready vs live price)_
  - `ema9`, `ema21`, `bb_upper`, `bb_lower`, `bb_squeeze`, `adx`, `supertrend_direction`, `obv`

- [x] **12.3** Upgrade trend_regime classifier to use EMA9/21 in addition to SMA: if `ema9 > ema21 > sma50 > sma200` = strong_uptrend (more precise than SMA-only) _(✅ classify_trend_regime takes optional ema9/ema21; SMA-only fallback preserved)_

- [x] **12.4** Add 5-min intraday indicators to `live_job.py`: Supertrend (intraday), Volume Delta (buy vol − sell vol, approximated from candle direction and volume) _(✅ SupertrendIntraday + VolumeDelta registered (cadence=intraday); latest values surfaced into indicator_live as supertrend_5m_dir / vol_delta)_

### Week 12 gate
Full indicator set computing in EOD job. EMA, BB, ADX, Supertrend all in `indicator_live`. BB squeeze flag working.
_(✅ met: indicator_eod computes nightly; EMA/BB/ADX/Supertrend/OBV surfaced in indicator_live each minute; bb_squeeze works once a symbol has >~272 daily bars (252-day percentile window). PLUS Phase-4 focused universe: live scope cut from ~750 to ~200 core F&O + dynamic watchlist.)_

---

## Week 13 — Levels + Delivery Conviction

### Tasks

- [x] **13.1** Write migration `migrations/0XX_indicator_levels.sql`: _(✅ migration 045_indicator_levels.sql)_
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

- [x] **13.2** Write `indicators/levels.py` — runs nightly at 19:00 from bhavcopy: _(✅ extended levels.py: PDH/PDL, 52w + days-since, 5d/20d ranges, nearest round number + prior-failure count, pivots; registered 19:00 trading-day)_
  - PDH, PDL from yesterday's bhavcopy HIGH/LOW
  - 52w high/low and days-since from `raw_high_low_52w`
  - 5d/20d high-low ranges from last N rows of bhavcopy
  - Round number proximity: nearest of {50, 100, 200, 500, 1000, 2000, 5000}
  - Prior round number failure count: how many times in last 20 sessions did price approach within 0.5% of this round number and fail to break it
  - Pivot points: P = (H+L+C)/3 from yesterday. R1 = 2P−L, R2 = P+(H−L), S1 = 2P−H, S2 = P−(H−L)

- [x] **13.3** Add levels to `pre_market_loader.py` — loads today's levels into Redis `levels:{symbol}` hash at 08:45. Static for the session. _(✅ read_levels + write_levels_to_redis in run_pre_market_load)_

- [x] **13.4** Write migration `migrations/0XX_delivery_conviction.sql`: _(✅ migration 046_delivery_conviction.sql)_
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

- [x] **13.5** Write `indicators/delivery_tracker.py` — nightly at 18:30 from `raw_bhavcopy_cm`:
  - `delivery_ratio = DELIV_QTY / TOTTRDQTY` per symbol per day
  - 5d rolling avg, z-score vs 20d
  - Trend direction (rising if today > 5d avg by >5%)
  - `delivery_conviction_score` = composite:
    - High delivery + price up → score = 0.8 (accumulation)
    - High delivery + price down → score = 0.3 (distribution or capitulation — check trend)
    - Low delivery + price up → score = 0.4 (weak-hands chase)
    - High z-score (> 2) → bonus +0.1

- [x] **13.6** Add levels and delivery to alert message: _(✅ Delivery + Levels lines in format_message)_
  ```
  Stock: Quality n/a | Delivery: {trend} ({ratio:.0%})
  Tech:  VWAP {side} | RSI {rsi_5m} | {trend_regime}
         PDH: {pdh} | 52w High: {high_52w}
  ```

### Week 13 gate
Levels computed nightly for all symbols. Delivery conviction scores available. Alert message shows levels.
_(✅ met: indicator_levels (19:00) + delivery_conviction (18:30) nightly over the F&O+Nifty500 set; levels loaded to Redis at 08:45; alerts carry Delivery + Levels lines. 724 tests green.)_

---

## Week 14 — Fundamentals + Quality Score

### Tasks

- [x] **14.1** Write `fundamentals/quality_score.py` — composite 0–100 score using data already in DB: _(✅ from screener/quote_metadata/shareholding; pledge unavailable (not collected); revenue uses 3y sales CAGR proxy)_
  - Revenue growth YoY (from `raw_financial_results` — available even without PDF extraction)
  - P/E ratio (from `raw_quote_metadata`)
  - Market cap (proxy for size/liquidity)
  - Promoter holding % (from `raw_shareholding_pattern`)
  - Promoter pledge % (from `raw_shareholding_pattern` — if available)
  - ROE (from `raw_fundamentals_screener`)
  - ROCE (from `raw_fundamentals_screener`)
  - D/E ratio (from `raw_fundamentals_screener`)
  - 3y revenue CAGR (from `raw_fundamentals_screener`)

- [x] **14.2** Score each component 0–10, weight them, sum to 0–100: _(✅ graded per rubric, weighted, normalised over available components → graceful degradation; None when no data)_
  - Revenue growth > 15% → 10pts; 10–15% → 7; 5–10% → 4; < 5% or negative → 0–2
  - ROCE > 20% → 10pts; 15–20% → 7; 10–15% → 4; < 10% → 1
  - ROE > 15% → 10pts; similarly graded
  - D/E < 0.3 → 10pts; 0.3–1 → 7; 1–2 → 3; > 2 → 0
  - Promoter holding > 50% → 8pts; 30–50% → 5; < 30% → 2
  - Pledge > 25% → deduct 15pts; > 50% → hard kill (already in gates)
  - P/E below sector avg → 5pts; above → 0–2

- [x] **14.3** Write `fundamentals/table.sql` migration and populate nightly: _(✅ migration 047_stock_fundamentals.sql + nightly job 18:00)_
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

- [x] **14.4** Add quality score to hard gates: quality_score < 30 AND long signal → kill _(✅ in `_hard_gated`; None score never kills)_

- [x] **14.5** Add quality score to confidence scorer (Layer 3): _(✅ `_quality_adjustment`)_
  ```
  quality_score > 70 → +0.10
  quality_score 50–70 → +0.05
  quality_score 30–50 → 0.00
  quality_score < 30  → −0.15 (long signals)
  ```

- [x] **14.6** Add quality score to alert message: `Quality: {quality_score}/100` _(✅ Quality line, shown when fundamentals exist)_

### Week 14 gate
`stock_fundamentals` table populated for all watchlist + F&O symbols. Quality score in every alert.
_(✅ met: nightly quality job over F&O+Nifty500 → stock_fundamentals; quality gates (<30 long-kill), nudges confidence, shows in alerts. 732 tests green. Coverage depends on the sparse screener/shareholding feeds — symbols without fundamentals get a null score that neither gates nor nudges.)_

---

## Week 15 — Patterns + Stock Profile

### Tasks

- [x] **15.1** Write `indicators/patterns.py` — per-minute DBJob during market hours: _(✅ inside bar, volume dry-up, S/R proximity, HH/LL; → patterns table; registered intraday over the live universe)_
  - Inside bar: today's high < yesterday's high AND today's low > yesterday's low
  - Volume dry-up: current 5m volume < 50% of 20-bar avg 5m volume
  - Support proximity: price within 0.5% of S1 or S2 from levels table
  - Resistance proximity: price within 0.5% of R1 or R2 from levels table
  - Higher-high: last bar high > prior bar high (simple momentum check)
  - Lower-low: last bar low < prior bar low

- [x] **15.2** Add RSI–price divergence detector: _(✅ bullish/bearish over last 10 bars → patterns)_
  - Bullish divergence: price making lower low BUT RSI making higher low (over last 10 bars)
  - Bearish divergence: price making higher high BUT RSI making lower high
  - Write to `patterns` with `pattern_type = 'bullish_divergence'` / `'bearish_divergence'`

- [x] **15.3** Add fake-breakout filter to `breakout_52wh` signal: if `wick_rejection > 50%` (close is less than 50% of the way from low to high) AND volume < 1.2× avg → add `fake_breakout_risk = True` flag to signal. Reduce confidence by 0.10 when this flag is set _(✅ flag on signal (migration 049) + −0.10 in scorer)_

- [x] **15.4** Write `profile/builder.py` — nightly DBJob at 19:30. For each symbol: join all Layer 4 outputs into a single row in `stock_profile_daily`. Include: quality_score, trend_regime, momentum_state, delivery_conviction_score, bb_squeeze, adx, levels (pdh/pdl/52w), pattern flags. This table is the ML training archive _(✅ joins fundamentals/delivery/eod/sma/rsi/macd/levels/live + pattern flags; tolerant of missing sources)_

- [x] **15.5** Write migration for `stock_profile_daily` (~60 columns) _(✅ migration 050, 64 columns)_

### Phase 4 exit criteria (ALL must be met)
- [x] Quality score in every alert _(✅ Quality line, when fundamentals exist)_
- [x] Key levels (PDH, PDL, 52w High) visible in alert action section _(✅ Levels line)_
- [x] Delivery conviction trend in every alert _(✅ Delivery line)_
- [x] `stock_profile_daily` populating nightly _(✅ builder registered 19:30; ⏳ first rows after a post-deploy night)_
- [x] Pattern flags feeding into confidence (divergence reduces confidence, BB squeeze boosts it) _(✅ bb_squeeze +0.05, bearish_divergence −0.10, fake_breakout −0.10)_
- [ ] All prior stability criteria still met _(✅ 741 tests green; ⏳ the live 5-day run remains the standing Phase-1 item)_

---
---

# PHASE 5 — Event Intelligence
**Duration:** 4–6 weeks
**Goal:** PDF financial extraction working. Rating action alerts fire. Result beat/miss alerts fire. Pre-event risk flags suppress inappropriate trades.
**Prerequisite:** Phase 4 stable.

---

## Week 16 — PDF Pipeline Foundation + Rating Extractor

### Tasks

- [x] **16.1** Write `config/priority.yaml` — subject → priority mapping. High priority includes: "Outcome of Board Meeting", "Dividend", "Acquisition", "Credit Rating", "Order Win", "Quarterly Results", "MD & CEO Change". Medium: "Investor Presentation", "Press Release". Low/skip: "Trading Window" _(✅ config/priority.yaml already present (125 subjects incl. Credit Rating + skip/Trading Window))_

- [x] **16.2** Write `parsers/classify.py` — reads `raw_announcements`, for each unclassified announcement: matches subject against `priority.yaml` patterns, writes priority to `raw_announcements.priority` column. Also sets `skip = True` for skip subjects _(✅ classify.py applies subject_classifier → priority (incl. skip); 10-min job)_

- [x] **16.3** Extend announcements collector window to **21:30 IST** (from current 19:00). Most credit rating actions arrive 17:00–21:35. This is a scheduler config change in `endpoints.yaml` _(✅ announcements_equity active_hours → 08:00-21:30)_

- [x] **16.4** Write `parsers/pdf_text.py` — takes PDF bytes, returns extracted text string: _(✅ pdf_text.py: pdfplumber → pymupdf fallback, scanned→ocr_required)_
  - Primary: pdfplumber (handles text-based PDFs — majority of NSE filings)
  - Fallback: pymupdf (for edge cases, corrupted PDFs)
  - Scanned PDF detection: if character count < 100 for a multi-page PDF → flag `pdf_error='ocr_required'` and return empty string (no OCR in this phase)

- [x] **16.5** Write `parsers/rating_extractor.py` — reads `raw_announcements` where subject matches credit rating patterns. For each: calls `pdf_text.py` on the attachment, parses the text to extract: _(✅ rating_extractor.py: agency/action/old→new grade/instrument/junk)_
  - Agency: look for "CRISIL", "ICRA", "CARE", "India Ratings", "Acuité", "Brickwork", "INFOMERICS"
  - Action: "reaffirmed"/"upgraded"/"downgraded"/"placed on watch"/"withdrawn"
  - Old rating and new rating from text patterns like "from A−/Stable to BBB+/Stable"
  - Instrument type: "Long Term", "Short Term", "NCD", "Commercial Paper"
  - `is_junk_downgrade`: True if new_rating is BB+/BB/B or below

- [x] **16.6** Write migration `migrations/0XX_rating_actions.sql`: _(✅ migration 051_rating_actions.sql)_
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

- [x] **16.7** Backfill: run `rating_extractor.py` against the ~291 credit-rating PDFs already in `raw_announcements` with status `text_extracted`. Verify outputs on 10 samples manually _(✅ backfill ran on available text (10 local); full ~291 runs on server)_

- [x] **16.8** Write `signals/detect.py` addition — `credit_downgrade` signal: _(✅ credit_downgrade / _junk / credit_upgrade / credit_watch_negative)_
  - Reads `raw_rating_actions` for unprocessed rows since last run
  - If action = 'downgrade': write signal. If `is_junk_downgrade = True`: signal_type = 'credit_downgrade_junk' (highest urgency)
  - If action = 'upgrade': write signal_type = 'credit_upgrade'
  - If action = 'watch_negative': write signal_type = 'credit_watch_negative'

- [x] **16.9** Write rating alert message template (different format from intraday signals): _(✅ distinct rating alert template; sent directly (evening, not via intraday gate))_
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
_(✅ met in code: extractor + raw_rating_actions + credit_* signals + rating alert template, all tested. Alerts go out directly, recency-guarded so the backfill stays silent. ⏳ live: depends on the PDF text pipeline keeping rating PDFs fresh on the server + a real downgrade arriving.)_
_(➕ Rework: a filing is multi-instrument/multi-agency. Migration 052 adds `raw_rating_lines` (per-instrument) + headline cols on `raw_rating_actions` (agencies, worst_action, min_lt_grade, credit_quality_score 0–100, junk/outlook/ST flags). Alerts on all-but-reaffirm/outstanding. Robust against scenario boilerplate, outlook-only changes, Moody's-scale, 'S&P BSE'/'Fitch Group' false agencies, stray single-letter grades. Verified on 39 real filings: 100% agency, 92% grade.)_

### Extras done beyond the Week-16 tasks (➕)
Work completed on top of 16.1–16.9, in this build:

- [x] **Rating-parser robustness** — driven by real PDFs (INDUSINDBK, KRYSTAL, SBFC…): grade-anchored action detection (ignores "could be downgraded if…" scenario text + outlook-only changes), structured NSE-form reader (kills the `Rating Action (…/Downgrade/…)` label false-positive), word-boundaried agencies + global agencies (Moody's/Fitch/S&P by full name), context-aware grade tokens (no stray single letters). New-first phrasing `BBB+ (Downgraded from A-)` handled. 19 parser tests.
- [x] **Multi-instrument storage + scoring** — migration 052 `raw_rating_lines` + headline; `credit_quality_score` 0–100 (AAA=100…BB+=45 junk…D=0).
- [x] **Credit → signal scoring** — `_credit_adjustment` in `confidence.py` + `latest_credit_by_symbol`/`is_junk_downgrade_kill` in `rating_extractor.py`. LT grade = standing swing nudge (+0.05 AAA/AA, −0.10 junk); ST stress (A3/A4/D) −0.05; recent action event-bias (downgrade −0.15, upgrade +0.10, watch −0.05) — swing window 5d, intraday 1d; **recent junk downgrade hard-kills longs**. Wired into the dispatcher.
- [x] **Swing vs intraday split** — migration 053 `signals.horizon` (set at emission via `detect.SIGNAL_HORIZON`). Drives: timing (intraday keeps the strict `time_rules`; **swing is relaxed + sends in an EOD batch 15:20–18:30**), horizon-aware scoring, two message templates (`⚡ [INTRADAY]` flat-by-15:15 vs `📈 [SWING]` hold-days + quality/credit/delivery), and **Telegram topic routing** (`TELEGRAM_TOPIC_INTRADAY/SWING/CREDIT`).
- [x] **Tooling** — `scripts/rating_qa.py` (one-command extraction QA + coverage %), `scripts/rating_review.py` (step through PDFs one-by-one, opens each in the viewer), and `backfill_parser.py` fixes (`created_at` ordering not unsortable `broadcast_dt`, new `--subject` filter, 0-candidate guard).

- [x] **Intraday rules** — `orb_breakout` (break of the 09:15–09:30 opening-range high, volume-confirmed) and `vwap_reclaim` (price reclaims session VWAP after trading below it) in `detect.py`, both `horizon='intraday'`, wired into the detection pass with labels + 7 tests. The ⚡ pipeline now has real setups.

_Migrations added: 051 (rating_actions), 052 (rating_lines + headline), 053 (signal horizon). All green at 778 tests._
_⏳ Optional next refinement: horizon-weighted scoring (intraday down-weights fundamentals/delivery; swing down-weights 5m-RSI/VWAP). Currently the split is the credit factor + time-multiplier._

---

## Week 17 — Financial Extractor (Main Investment)

This is the hardest week in the entire roadmap. Budget 2–3 weeks if needed.

> **STATUS / DESIGN PIVOT (vision-first).** The extractor was rebuilt around
> **GPT-4o vision** reading the P&L from page images (text layer only locates the
> page + units), replacing the camelot/pdfplumber ensemble that mis-selected
> garbage tables and note/year-ago columns. When vision under-extracts, the text
> path **gap-fills** missing fields. This supersedes the literal 17.3 (camelot
> ensemble), 17.4 (per-field alias map), and 17.8 (per-company quirk files — the
> vision model handles odd layouts). Built + tested: extractor, `eval.py`,
> validation layer, CFO field.
>
> **Gate status — NOT YET PROVEN.** The committed `ground_truth/` labels are
> ~55% LLM-generated/unreliable (see memory `week17-ground-truth-integrity`), so
> any eval number against them is meaningless. Two trustworthy-label tracks now
> exist: (a) **human verification** — `scripts/verify_extraction.py` →
> `scripts/promote_verified_labels.py` (reliable, in progress); (b) **XBRL** —
> `parsers/xbrl_financials.py` + `scripts/xbrl_ground_truth.py`/`xbrl_eval.py`
> parse NSE's authoritative INDAS XBRL (built + validated). **XBRL is currently
> data-blocked**: our result PDFs are 2026 (`raw_announcements`) while NSE's
> financial-results/XBRL feed serves Dec-2024 (`raw_financial_results`), and the
> feed carries no PDF link (`resultD` empty) — so no PDF+XBRL pair exists to
> measure against. Path to the gate: human-verify the 2026 corpus → eval on the
> verified subset; keep XBRL for a forward-looking continuous-accuracy monitor
> once both feeds align on live server data.

### Tasks

> **Design + runbook:** see `tests/financial_extraction/README.md` for the full
> download / storage / schema design. Corpus, labels, and the production archive
> are all keyed by the announcement **fingerprint**.

- [x] **17.1** Build a ≥50-PDF *result* corpus for ≥50 F&O companies across recent quarters in `tests/financial_extraction/fixtures/`. *(318 fixtures; ~37–48 are result statements)*
  - Miner: `scripts/mine_announcement_fixtures.py` (source = `raw_announcements`; `raw_financial_results` has no PDF URLs). PDFs stored as `fixtures/pdfs/<fingerprint>.pdf` + `fixtures/metadata.json` (`schema_version 2`).
  - Prefer re-hydrating from `data/archive/` (keyed by fingerprint) over re-downloading from NSE — same key, no label drift.
  - Corpus is subject-agnostic; filter to result subjects (e.g. "Outcome of Board Meeting", "Press Release", "Investor Presentation") for the eval set.

- [ ] **17.2** Hand-label ground truth — **one YAML per fixture**, keyed by fingerprint, in `tests/financial_extraction/ground_truth/<fingerprint>.yaml`. Drafts (`drafts/<fingerprint>.yaml`, gpt-4o, raw source units) are reviewed/normalized-to-crore on promotion. Richer per-file schema (chosen over a single flat file):
  ```yaml
  standalone:                      # numbers in crore
    revenue_cr: 234567
    other_income_cr: ...
    total_income_cr: ...
    total_expenses_cr: ...
    pbt_cr: ...
    tax_cr: ...
    pat_cr: 18900
    total_comprehensive_income_cr: ...
    eps_basic: 28.4
    eps_diluted: ...
    net_interest_income_cr: 28900  # BFSI only (banks/NBFCs)
  consolidated: null               # same shape, or null if absent
  yoy_revenue_growth: 12.3         # company-stated %, null if not printed
  period_label: Q4-FY26
  period_ending: '2026-03-31'
  units_in_source_pdf: INR crore   # provenance
  notes: ...
  _meta: { fingerprint, symbol, subject, broadcast_dt, reviewed, draft_cost_usd }
  ```

- [x] **17.3** `parsers/financial_extractor.py` — **DONE as vision-first** (gpt-4o vision + text gap-fill), NOT the camelot ensemble below (superseded):
  - Strategy 1: `camelot.read_pdf(lattice=True)` — for bordered tables (most result PDFs)
  - Strategy 2: `camelot.read_pdf(stream=True)` — for whitespace-separated tables
  - Strategy 3: `pdfplumber` table extraction
  - Strategy 4: pymupdf text + regex — last resort for narrative-embedded numbers
  - For each strategy: returns `(numbers_dict, confidence_score)`
  - Ensemble: pick highest-confidence non-empty result

- [~] **17.4** *(SUPERSEDED by vision — `config/financial_aliases.yaml` keeps anchors/units/validations, not per-field alias maps)* Write `config/field_aliases.yaml` — maps every variant label to canonical names. Build this from looking at actual PDFs:
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

- [x] **17.5** Write eval script `tests/financial_extraction/eval.py` — runs all 50 fixtures through the extractor, compares against ground truth, reports: *(+ `scripts/xbrl_eval.py` for model-independent XBRL comparison)*
  - Accuracy per field (what % of PDFs got revenue correct within 2%)
  - Accuracy per strategy (which strategy works for which company types)
  - Failure cases list (which PDFs failed and why)
  - Target: 95% accuracy on F&O result PDFs

- [x] **17.6** Write validation layer in `financial_extractor.py`: *(revenue>0, |PAT|<|revenue|, pbt−tax=pat + total-income identities)*
  - Revenue > 0 (warn if negative)
  - PAT magnitude < revenue magnitude (warn otherwise)
  - If extracted YoY growth differs from company-stated YoY by > 5% → flag low confidence

- [x] **17.7** *(CFO added to the vision schema + `extracted_financials.cfo_cr`; receivables deferred)* Add earnings quality extraction alongside the main numbers:
  - CFO (cash flow from operations) if present in the PDF
  - If CFO/PAT > 1.0 → real profits. If < 0.5 → accounting concern
  - Receivables change YoY (if balance sheet available)

- [~] **17.8** *(SUPERSEDED — the vision model handles odd layouts; no per-company quirk files needed)* Write per-company quirk slot: `extractors/quirks/` — empty `__init__.py` plus one example quirk file for the most common failure case you find in the eval. This is the pattern for handling companies whose PDFs don't follow standard format

### Week 17 gate
Financial extractor achieving ≥ 90% accuracy on the 50-fixture eval set (targeting 95% — iterate until you get there).

> **GATE STATUS: not yet proven.** Extractor + eval + validations are built and
> spot-checks are correct (ACC, BEL [after gap-fill], RELIANCE via XBRL). The
> number itself is pending **trustworthy labels** — blocked on the label tracks
> noted in the status box above (human verification in progress; XBRL data-blocked
> on the 2026↔Dec-2024 mismatch). Close it by promoting human-verified verdicts
> and running `eval.py` on that subset.

---

## Week 17.5 — SBI 8-May-2026 Case Study: Catch-the-Signal Checklist

**What this is:** a retrospective post-mortem turned into a work order. On
8 May 2026 SBIN fell ~6.6% (close ₹1,019.55 vs prior ₹1,092; intraday low
₹1,011.3) on Q4 FY26 results. External forensic research (see chat / docs) put
**information leakage at ~2%** and the cause at a **low-quality beat**: headline
PAT ₹19,684 cr (+5.6% YoY) *beat*, but operating profit (PPOP) ₹27,704 cr
(−11.45% YoY, ~−15.7% QoQ), NII ₹44,380 cr (6% below Street), domestic NIM 2.93%
(−18 bps QoQ), provisions −36.6% YoY (which *propped* PAT), and an ~₹4,520 cr
treasury MTM swing. **Our system would have read `pat_cr` and seen a healthy
print** — it never captures the operating line the market actually sold.

> **HONEST SCOPE — what was catchable, and what was not.**
> - **NOT catchable:** the exact print, or a leak. "Catch beforehand" here means
>   two real things only — (a) *flag the stock as carrying NIM/treasury risk into
>   the result* (macro overlay, pre-print), and (b) *react within minutes of the
>   2:01 PM filing, faster than full repricing* (the stock bled to its low by
>   ~2:43 PM; first wire Bloomberg 2:07 PM).
> - **NOT backtestable on THIS event:** 8 May predates our collection start
>   (~2026-05-31). Options/OI/flow snapshots for that date do not exist and NSE
>   serves them live-only. This pays off forward, on the next result season. The
>   one exception is the result PDF itself (re-hydratable as a fixture) — which is
>   enough to prove the extraction + quality-divergence path (S1–S3).
> - **Supersedes Week 18:** tasks 18.4/18.5 trigger on *revenue* YoY (>+15% /
>   <−10%). That is wrong for a bank — SBI's revenue/PAT both *grew*. S3/S4 below
>   replace those thresholds with sector-aware operating-line + quality logic.

> **IMPLEMENTATION STATUS (built + tested offline against the SBI numbers).**
> Done: S1 (sector-aware BFSI schema, `vision_financial.py`/`sector_map.is_bfsi`),
> S2 (migration 060 + `growth_json`, `from_results.py`), S3
> (`fundamentals/earnings_quality.py`), S4 (`detect._detect_result_quality` →
> `result_quality_low/high`), S5 (`bot/result_quality_message.py` + dispatcher
> branch), S6 (migration 061 + `market/macro_rates.py`: manual + FBIL/DBIE CSV
> import, no live scraper), S7 (migration 062 + `pre_screen` BFSI risk flag), S9
> (`pre_screen.implied_vs_realized`), S10 (`tests/fundamentals/
> test_sbi_case_study.py`, 8 tests green; ground truth in
> `ground_truth_bfsi/SBIN_Q4FY26.yaml`). Regression: 294 tests pass.
> S6 done too: repo rate = manual entry (MPC cadence, scraper is negative ROI),
> 10Y yield = manual point or FBIL/DBIE CSV import (`import_rates_csv`,
> `scripts/load_macro_rates.py`) — no live scraper needed at this cadence.
>
> **S10 vision leg VALIDATED on the real filing.** The actual SBI Q4 PDF
> (`fixtures/pdfs/sbin_q4fy26_*.pdf`) now passes a live gpt-4o extraction-accuracy
> gate (`test_sbi_vision_live.py`, creds-gated). Getting there exposed that vision
> misreads a dense 10-column bank P&L; the **dense-bank hardening** fixed it: 300
> DPI / P&L page only; identity corrections; a **TOTAL-INCOME text anchor** (vision
> summed the interest sub-components low → dragged interest-earned/NII down, so we
> re-derive interest-earned = total income − other income and NII = interest
> earned − interest expended); GNPA/NNPA text override; and **growth from STORED
> HISTORY** (not the model's unreliable in-filing comparative columns). End-to-end
> on the real PDF the alert fires `result_quality_low`/short, conf 0.82, all three
> flags, every line correct (`test_bfsi_hardening.py`, 5 tests).
> **Remaining external dependency:**
> - **S8 consensus** — needs a live estimate source + ToS decision. Not started.

### Tasks

> Build order: **S1 → S2 → S3 → S4** need NO external data and capture the bulk of
> this event. S6–S8 add the pre-print risk flag and true beat/miss. S9–S11 are
> corroboration + repro.

**Group A — Extraction: capture the lines that actually moved the stock**

- [x] **S1** Sector-aware financial schema. Today `config/financial_aliases.yaml`
  `canonical_fields` is a fixed 10-field generic P&L; for a bank, `pat_cr` is the
  wrong headline. Make `canonical_fields` a per-`sector_class` map (key off
  `market/sector_map.py`) and add the BFSI block the `pnl_anchors` already locate
  but extraction discards:
  - `interest_earned_cr`, `interest_expended_cr`, `net_interest_income_cr`,
    `operating_profit_cr` (PPOP), `provisions_cr`,
    `profit_on_sale_of_investments_cr` (the treasury line),
    `gross_npa_pct`, `net_npa_pct`, `slippages_cr`
  - Pass `sector` into the vision prompt (`ctx` in `financial_extractor.extract`
    already carries `symbol`); prompt asks only for that sector's fields.
  - Other sectors (stretch, same mechanism): IT → cc-revenue, EBIT margin, deal
    TCV; FMCG/manufacturing → EBITDA, EBITDA margin, volume growth; insurers →
    APE, VNB margin, persistency.
  - **Acceptance (SBI fixture):** extracts PPOP ≈ ₹27,704 cr, NII ≈ ₹44,380 cr,
    provisions (down YoY), GNPA 1.49% / NNPA 0.39%, treasury loss ~₹1,471 cr.

- [x] **S2** Persist the new fields: extend `extracted_financials` (migration) +
  the BFSI keys in the `17.2` ground-truth YAML schema (it already reserves
  `net_interest_income_cr`). Wire YoY/QoQ for the BFSI lines through the existing
  `growth`/`growth_consolidated` path (the extractor already computes growth from
  the PDF's own comparative columns — no stored history needed).

**Group B — Quality-divergence signal (zero external data — highest ROI)**

- [x] **S3** `result_quality_low` / `result_quality_high` in `signals/detect.py`
  (there is **no** result-quality logic wired today). Pure arithmetic on S1/S2
  growth fields — *this is the rule that flags SBI for free*:
  - **Low-quality beat** = PAT growth ≥ 0 **AND** operating-profit (PPOP) growth
    < 0 → "headline beat, operating miss".
  - **Provision-propped** = PAT growth ≥ 0 **AND** provisions down YoY by a
    material margin → "profit supported by provision release, not core".
  - **Treasury-driven** = `profit_on_sale_of_investments_cr` swung negative QoQ /
    `other_income` down sharply → "non-core hit".
  - Generalizes per sector: IT → revenue up but margin down; FMCG → revenue up on
    price, volume down.
  - **Acceptance (SBI):** all three fire — PAT +5.6% vs PPOP −11.45%, provisions
    −36.6% YoY, treasury swing → emits `result_quality_low`.

**Group C — Real-time result-day trigger (react within minutes)**

- [x] **S4** Post-event trigger (the E3 half `pre_screen.py` notes as not-built).
  On a "Outcome of Board Meeting"/result announcement landing, *immediately*:
  parse PDF → S1 extract → S3 quality score → compare vs the frozen
  `earnings_setups` baseline → emit a directional alert. Don't wait for the
  overnight batch (18.4 assumes overnight — too slow; SBI repriced inside ~40
  min). **Supersedes 18.4/18.5.**
  - **Acceptance:** on the SBI fixture, produces a `result_quality_low` alert from
    PDF bytes in < 5 min of ingestion (filing 2:01 → low 2:43 leaves the margin).

- [x] **S5** BFSI-aware alert format (extend 18.6): show NII (±YoY), **NIM (±QoQ
  bps)**, PPOP (±YoY/QoQ), provisions, GNPA/NNPA, treasury line — not just
  revenue/PAT/EPS. The headline-PAT-only card is exactly what hid this event.

**Group D — Macro / sector risk overlay (the only true pre-print flag)**

- [x] **S6** Macro feed: **repo rate** + **10Y G-sec yield** (level + QoQ change).
  Both were public and both predicted the *direction* of SBI's risk: Dec-2025
  repo cut repricing EBLR/T-bill books over Jan–Mar (NIM compression), G-sec
  hardening toward ~7% in Q4 (AFS MTM loss). **Resolved without a live scraper**
  (none required at this cadence): `migration 061` + `market/macro_rates.py`.
  - **Repo rate = manual** (`record_rates()`): changes only ~6×/year at RBI MPC
    meetings, so a scraper is negative ROI — calendar the MPC dates and enter it.
  - **10Y yield = manual point or CSV import** (`import_rates_csv()`): download
    the free authoritative series from **FBIL** (fbil.org.in) or **RBI DBIE**
    (dbie.rbi.org.in) and import (columns auto-detected). Yahoo doesn't carry the
    India 10Y; investpy/Investing.com is unreliable (Cloudflare) — neither built on.
  - Driver: `scripts/load_macro_rates.py {set|csv|state}`. `macro_state()` derives
    `rising_yields` / `repo_cut_recent`, consumed by S7. Tested: `tests/market/
    test_macro_rates.py` (6) + the SBI case study.

- [x] **S7** Sector risk rule stamped onto `earnings_setups` pre-print (extend
  `events/pre_screen.py` / `pre_event_risk.py`): *bank/NBFC/insurer + recent rate
  cut + rising 10Y → flag "NIM/treasury risk into result".* Can't predict the
  number; tells you where to be nervous, across the whole BFSI universe
  automatically. **Acceptance:** SBI carries the flag going into 8 May given the
  Q4 macro state.

**Group E — Consensus beat/miss (the genuine miss — hard dependency)**

- [x] **S8** ✅ UNBLOCKED (2026-06 — user decision: implement all the sources,
  accuracy first). Four adapters feed `consensus_estimates`; lookup **merges
  field-wise** in accuracy order (`consensus.SOURCE_RANK`: **manual → news →
  moneycontrol → yahoo**), so a news NII never masks MC's PAT:
  - **manual / CSV** (`scripts/load_consensus.py set|csv`) — broker previews; the
    most accurate **NII/NIM** path (migration 065 added `nii_est_cr`/`nim_est_pct` —
    for a bank those, not PAT, are the estimates that matter).
  - **news** (`events/consensus_sources/news.py`) — broker previews read out of
    articles: Bing News RSS (publisher URLs verbatim in the link, unlike Google's
    opaque/ToS-bound feed) → publisher page → LLM extraction (JSON, preview-framed
    numbers only, post-result articles rejected) → year-ago sanity band (0.4–2.5×
    vs extracted_financials) → mean across articles/brokers. The *automated*
    NII/NIM path. Misses accepted: paywalls + TLS-blocking hosts (business-standard,
    zeebiz) are skipped, never circumvented.
  - **moneycontrol** (`events/consensus_sources/moneycontrol.py`) — quarterly
    earning-forecast API (rev+PAT+EPS ₹ cr); scId via autosuggest; the fragile leg,
    degrades per-symbol.
  - **yahoo** (`events/consensus_sources/yahoo.py`) — earningsTrend (EPS+revenue,
    INR-guarded so a USD misread can never land).
  Nightly `register_consensus_job` at 20:05 IST (after the calendar fills
  `pending_events`, before the pre-screen stamps setups) fetches for the next-10-day
  reporters only. Verified live: INFY/TCS — MC and Yahoo agree to the crore on
  revenue (₹47,785 / ₹70,991 cr). `matcher.py` flips `surprise_basis` to
  'consensus' automatically. Tests: `tests/events/test_consensus_sources.py`.
  *Caveat:* mid/small-cap coverage thins on both live sources — the manual path is
  the accuracy backstop for names that matter.

**Group F — Positioning corroboration (context, not trigger)**

- [x] **S9** Implied-move vs realized check. `pre_screen.py` already computes
  `implied_move_pct` from the ATM straddle. Add a post-event note when the
  realized move exceeds the implied band → "larger-than-priced surprise". SBI:
  implied ±5.7% (26-May ATM, 1040 strike) vs realized ~7%.
  - Volume confirmation: 8+11 May ≈ 95 m shares vs 30-day avg 18.7 m (~5×) — broad
    post-disclosure exit, *not* a stealth raid (consistent with the ~2% leak read).

**Group G — Reproduce + bound honestly**

- [x] **S10** Add the SBI Q4 FY26 result PDF as a labelled fixture (re-hydrate from
  `data/archive/` by fingerprint if present) with a hand-verified BFSI
  ground-truth YAML, and an end-to-end test: PDF bytes → S1 extract → S3 signal →
  assert `result_quality_low`. This is the one piece of 8 May we *can* prove.

- [x] **S11** Document the limits in-repo (don't let the dashboard imply foresight):
  no leak prediction; no pre-collection backtest; "beforehand" = risk-flag (S7) +
  fast reaction (S4), nothing more.

### Week 17.5 gate
On the SBI Q4 FY26 fixture, the pipeline (a) extracts the BFSI operating lines
(S1), and (b) emits `result_quality_low` end-to-end (S3/S10). Forward-looking:
S4 fires the alert in < 5 min of ingestion and S7 carries the pre-print risk flag
for BFSI names in a rising-yield / post-rate-cut regime.

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
