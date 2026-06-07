# NSE Data Service — System Architecture & How Signals Are Generated

> Status snapshot: Phases 1–4 (Week 12) built. This document describes how the
> system actually works end-to-end today — what runs, when, and how a raw market
> tick becomes a Telegram alert and a tracked paper trade. For task-level status
> see `FEATURE_CHECKLIST.md`; for empirical findings see `LEARNINGS.md`.

---

## 1. What this system is

An always-on pipeline that ingests NSE market data, computes a full indicator
set, classifies market/sector/time context, detects trade setups, scores their
confidence, alerts the highest-conviction ones to Telegram, and tracks every
signal as a paper trade — so strategies can be validated on honest, cost-adjusted
numbers before any real capital is risked.

It is **not** an auto-trader. It is a decision-support + research system: it
surfaces high-conviction setups with full context, and keeps an auditable record
(`paper_trades`, `signal_outcomes`, `backtest_registry`) to prove or disprove an
edge.

---

## 2. Where it runs

- **Host:** always-on AWS EC2 (`ap-south-1`, burstable t3-class), app at
  `/opt/nse-data-service`, user `ubuntu`. The laptop is a dev box only.
- **Processes (systemd):**
  - `nse-collector@ubuntu` — the data + compute + signal engine (one
    `python -m nse_data.main` process running an APScheduler with all jobs).
  - `nse-bot@ubuntu` — the Telegram dispatcher (separate process, so it keeps
    alerting even if the collector dies).
  - `nse-dashboard@ubuntu` — FastAPI dashboard/API on :8000.
- **System cron:** nightly DB backup (02:00) + ops health check (every 15 min,
  market hours) — independent of the collector so they fire even if it's down.
- **Deploy:** laptop → GitHub → `./scripts/deploy.sh ubuntu` (pull + deps +
  migrations + restart). Migrations also auto-apply on boot.

---

## 3. Data flow (the big picture)

```
 NSE / Yahoo / NSDL feeds
        │  (collectors, every 5m / 1m / daily — config/endpoints.yaml)
        ▼
   raw_* tables           ← raw_bhavcopy_cm, raw_equity_quotes, raw_indices,
        │                    raw_india_vix, raw_oi_spurts, raw_announcements, …
        ├──────────────► nightly EOD indicators ──► indicator_sma/rsi/macd/eod
        │  (off bhavcopy)                            (settled daily series)
        │
        ├──────────────► every-minute intraday compute (market hours)
        │                   ├─ indicator_*_5m  (rsi/macd/vwap/supertrend/vol_delta)
        │                   └─ indicator_live   (per-symbol snapshot — THE table
        │                       the signal engine reads)
        │
        ├──────────────► market context (every 5m) ──► market_state, sector_state
        │
        ▼
   signal detector (every minute) ──► signals + signal_features
        │
        ▼
   dispatcher (every minute) ──► Telegram alert (if confidence clears the gate)
        │
        ▼
   paper_tracker (every minute) ──► paper_trades  ──► (nightly) signal_outcomes
```

Everything below the collectors is gated on market hours except the nightly EOD
compute, the 19:30 outcome labeler, the 02:00 backup, and the watchlist refresh.

---

## 4. The indicator layer (hybrid: settled nightly, decided live)

Two timeframes, one principle: **daily-bar indicators settle once a day; the
live snapshot folds today's live price against them every minute.**

- **Nightly EOD** (off `raw_bhavcopy_cm`, `cadence="eod"`):
  - `indicator_sma` — SMA 20/50/200
  - `indicator_rsi`, `indicator_macd` — RSI 14, MACD 12/26/9
  - `indicator_eod` (Week 12) — EMA 9/21, Bollinger (+`bb_squeeze`), ADX/DI,
    Supertrend (10, 2.0), OBV, volume SMA20 / volume_ratio
- **Intraday 5-min** (off `raw_intraday_candles` + live feed, `cadence="intraday"`,
  recomputed every minute during the session):
  - `indicator_rsi_5m`, `indicator_macd_5m`, `indicator_vwap_5m`
  - `indicator_supertrend_5m`, `indicator_volume_delta_5m` (Week 12)
- **`indicator_live`** — one row per symbol, rebuilt every minute by
  `live_snapshot.build_snapshot`. It reads the latest settled daily values +
  the latest 5-min values + the live price, and writes the "current view":
  VWAP/slope, ATR, RSI(5m), trend_regime, momentum_state, EMA9/21, BB bands +
  squeeze, ADX, daily & 5-min Supertrend direction, OBV, volume delta.

> **Why hybrid, not full-recompute-every-minute:** a daily indicator is built
> from daily bars, which don't change intraday — only today's forming bar does.
> So the minute job reads the settled value and compares it to the live price.
> This is the same pattern SMA→trend_regime has always used; it keeps the 1-min
> loop cheap on a burstable instance while still being decision-ready every
> minute. Live intraday flips are the 5-min Supertrend's job.

`trend_regime` (Week 12 upgrade): `ema9 > ema21 > sma50 > sma200` →
`strong_uptrend` (precise fast→slow stack); falls back to SMA-only when EMAs
aren't available.

---

## 5. The live universe (focused — Phase 4)

The intraday jobs don't sweep the whole market. The **live universe** =

- **Core (~200):** top F&O names by latest-session traded value
  (`universe.top_fno_by_value`), **plus**
- **Dynamic watchlist:** names that earned attention via a trigger
  (`signals/watchlist.py`, refreshed every 15 min, 5-trading-day TTL):
  - `rating` — announcement subject mentions a rating change
  - `news` — high-priority (or sentiment-flagged) announcement
  - `oi_spurt` — unusual derivatives activity (`raw_oi_spurts`)
  - `breakout_52wh` — fresh 52-week high (`raw_high_low_52w`)

`universe.live_universe()` = core ∪ active watchlist, and drives `live_job`,
`detect`, and `pre_market_loader`. This cut the per-minute scope from ~750 to
~200 + a handful — the headroom that lets the full indicator set run live.

---

## 6. Market context (Phase 2)

Computed every 5 minutes during market hours; every alert is enriched with it.

- **`market_state`** (`market/regime_job.py`): NIFTY direction, INDIA VIX level/
  state/direction, GIFT-Nifty lean, advance/decline ratio, % above VWAP,
  partial FII flow → `overall_regime` ∈ {risk_on, neutral, risk_off, panic},
  plus intermarket-divergence flags (`fragile_rally`, `internal_weakness`).
- **`sector_state`** (`market/sector_radar_job.py`): all 11 NSE sector indices
  ranked 1–11 by relative strength vs NIFTY 50 (ranked on *excess return* so it's
  stable when Nifty is flat), with an `rs_trend` (improving/flat/deteriorating).
- **Time rules** (`market/time_rules.py`): maps IST time → window + multiplier
  (NO_TRADE 09:15–09:30, PRIME 1.0, LUNCH 0.80 w/ 0.72 floor, … NO_NEW_TRADES
  after 15:20).
- **Expiry** (`market/expiry.py`): nifty/banknifty/monthly expiry detection +
  max-pain alignment multiplier.

---

## 7. How a signal is generated (the core flow)

This is the heart of the system. Every minute during market hours:

### 7a. Detection — `signals/detect.py` (`register_signal_job`)
For each symbol in `live_universe()`:
1. **Hard gates** (`_hard_gated`): skip if blacklisted, outside its price band,
   or too recently listed.
2. **Rules** — currently two live `signal_type`s:
   - `long_buildup` — rising price + rising OI + volume confirmation
   - `breakout_52wh` — fresh 52-week-high breakout
3. **Dedup** (Redis, 30-min TTL) so the same symbol+type doesn't re-fire every
   minute.
4. On a fresh fire → write a row to **`signals`** AND a full **`signal_features`**
   snapshot (every live indicator value at fire time — the ML training archive;
   it can't be reconstructed later, so it's captured from day one).

A signal in `signals` is a *candidate*, not yet an alert.

### 7b. Confidence scoring — `signals/confidence.py`
The dispatcher scores each undispatched signal. It's a transparent rule-stack
(not a model yet — that's Phase 8), starting at **0.50** and adding:

| Factor | Contribution |
|---|---|
| VWAP alignment (above & rising / below) | +0.10 / −0.10 |
| RSI(5m) zone (50–65 / >75 / >80) | +0.10 / −0.10 / −0.20 |
| Trend regime (strong_up / up / down / strong_down) | +0.10 / +0.05 / −0.10 / −0.20 |
| Volume ratio (>3× / <1×) | +0.05 / −0.10 |
| **Market regime** (risk_on / risk_off / panic) | +0.10 / −0.10 / −0.20 |
| **Sector RS rank** (1–3 leading / 9–11 lagging) | +0.08 / −0.08 |
| **Sector RS trend** (improving / deteriorating) | +0.03 / −0.03 |

The summed score is clamped to [0,1], then scaled by two final multipliers:
- **Time multiplier** (×0.75–1.00) from the time-of-day window
- **Divergence penalty** (×0.90) when `fragile_rally`/`internal_weakness` is set

### 7c. Dispatch — `bot/dispatcher.py` (the `nse-bot` process)
Every minute it polls undispatched signals and, for each:
1. Re-applies the hard gates (a symbol may have been blacklisted since it fired).
2. Reads live context + `market_state` + the signal's sector rank + time window.
3. Scores confidence (above).
4. **Gate:** if the time window is *suppressed* (09:15–09:30 or after 15:20) →
   hold. Otherwise send if `confidence > threshold`, where threshold is **0.65**
   (raised to **0.72** during the lunch window).
5. If it sends → mark `dispatched=1` and post the **alert message**:

```
🟢 TATASTEEL — Long Buildup
OI: 5.00% | Price: 2.10% | Vol: 2.50×

Market: Nifty up | VIX normal ↓ | Regime: risk_on
Sector: METAL RS #3 | Trend: improving

Stock: VWAP above ↑ | RSI(5m): 58.00 | Trend: uptrend

Confidence: High (0.83)
SL: ₹145.50 | T1: ₹154.50 | Flat by: 15:20
```

A low-confidence-but-recent signal is left undispatched so it can re-score next
minute as its indicators evolve; once it ages past 15 min it's given up on.

### 7d. Paper trade — `signals/paper_tracker.py`
Independently of the alert gate, **every** signal opens a paper trade from day
one (so the dataset is complete):
- Entry = detection price; ATR bracket: SL = entry − 1.5×ATR, T1 = entry + 1.5×ATR.
- Managed every minute: close on T1/SL (priced at the level), force-flat at 15:20.
- P&L runs through the **full cost model** (`costs/model.py`) — net of brokerage,
  STT, exchange/SEBI charges, stamp duty, GST, slippage.

### 7e. Outcome labeling — `signals/outcome_labeler.py` (nightly 19:30)
Fills `signal_outcomes` with forward returns (T+30m, T+2h, EOD, T+1d, T+3d) +
MAE/MFE + ATR-bracket hits. This is the *label* side of the ML dataset whose
*features* `signal_features` captured at fire time.

---

## 8. Daily timeline (a trading day)

| IST | What happens |
|---|---|
| 02:00 | Nightly SQLite backup (cron) |
| ~overnight | Nightly EOD indicator compute settles the daily series |
| 08:45 | Pre-market loader seeds `indicator_live`, publishes blacklist |
| 09:00 | **Morning brief** to Telegram (GIFT, US/crude, regime, expiry, S/R) |
| 09:15 | Market opens (signals suppressed until 09:30) |
| every 1m | live indicators → `indicator_live`; signal detector; paper tracker; dispatcher |
| every 5m | data collectors; market regime; sector radar |
| every 15m | watchlist refresh; ops health check |
| 11:30–13:30 | lunch window — multiplier 0.80, only >0.72 confidence passes |
| 15:20 | no new trades; open paper trades force-flat |
| 19:30 | outcome labeler fills `signal_outcomes` |

---

## 9. Backtesting & validation (Phase 3)

Offline — does **not** run during market hours. It's the gate that decides
whether a strategy is allowed to go live.

- **Cost-adjusted backtester** (`backtester/`): every simulated trade passes
  through the same `costs/model.py` → `pnl_net`. Run via `scripts/phase3_eval.py`.
- **Metrics** (`_core/metrics.py`): net Sharpe on daily portfolio returns ×√252,
  profit factor, win rate, max drawdown (₹). Gross vs net = the cost drag.
- **CPCV** (`_core/cpcv.py`): one full run, trades bucketed into 10 temporal
  folds; passes if average net Sharpe across folds is positive (fold-wise
  out-of-sample — these strategies have fixed rules, nothing to fit).
- **Registry** (`backtest_registry` + `_core/registry.py`): every run + verdict
  (promoted/shelved/needs_work) recorded.
- **Promotion path:** clears `net Sharpe > 0.5 AND CPCV-positive` → added to
  `signals/detect.py` as a new live signal_type feeding the same pipeline.
  Nothing has cleared the gate yet — `bb_ema9_30m`, `macd_willr_daily`, and the
  live `breakout_52wh` all backtest net-negative on dev data (provisional;
  re-run on server history pending). The ORB+VWAP strategy is the **benchmark**
  every future strategy must beat.

---

## 10. Ops, dashboard, durability

- **Dashboard** (`/` health, `/stocks`, `/trades`, `/backtest`, `/market`) +
  JSON API (`/api/health`, `/api/market/regime`, `/api/market/sectors`, …).
- **Health check** (`ops/health_check.py`, cron): 🔴 Telegram alert when a
  market-hours heartbeat feed goes stale; ✅ on recovery; de-duped.
- **Backup** (`scripts/backup_db.sh`, cron): consistent `sqlite3 .backup` →
  gzip → integrity check → retention prune.

---

## 11. Key tables (quick reference)

| Table | Role |
|---|---|
| `raw_*` | ingested market data (collectors) |
| `indicator_sma/rsi/macd/eod` | settled daily indicators (nightly) |
| `indicator_*_5m` | intraday 5-min indicators (every minute) |
| `indicator_live` | per-symbol current snapshot — what the signal engine reads |
| `live_watchlist` | dynamic adds to the live universe |
| `market_state` / `sector_state` | regime + sector RS context |
| `signals` / `signal_features` | fired setups + their feature snapshot |
| `paper_trades` / `signal_outcomes` | simulated book + forward-return labels |
| `backtest_runs/_trades/_registry` | backtest results + promote/shelve ledger |

---

## 12. What's live vs pending (as of Week 12)

- **Live:** collectors, full indicator set (nightly + intraday), focused universe
  + watchlist, market/sector/time context, two signal types, confidence scoring,
  Telegram dispatch, paper trades, outcome labeling, morning brief, ops/backup.
- **Pending verification (needs server/live data):** the 5-day clean run, ORB
  benchmark + `bb_ema9_30m` backtests (need server intraday history), the
  keep/pull decision on the live `breakout_52wh`.
- **Not yet built (later phases):** learned confidence model (Phase 8), quality
  score / delivery conviction / pattern detection (rest of Phase 4), and beyond.
