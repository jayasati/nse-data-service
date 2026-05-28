# High-Level Design — NSE Trading System

**Purpose of this document.** Anchor the architecture so future work has one place to align against. When something feels like drift, this file is the arbiter. The full to-do inventory is in `FEATURE_CHECKLIST.md`; this is *how the pieces fit together*, not *what's left*.

**Last revised:** 2026-05-28. Edit when an architectural decision changes — not when a task is added.

---

## 1. What this system does

A single sentence: **collect NSE market data → compute features → fire signals during the session → deliver to Telegram.**

Three concrete outputs:

1. **Live Telegram alerts** during market hours when a strategy's conditions confluence-fire on a tradeable symbol.
2. **A queryable data service** (FastAPI) over normalised market data — used by the alert bot, the dashboard, and any future subscribers.
3. **A research workbench** — backfilled indicator + signal history that powers backtesting, drift monitoring, and model retraining.

The bot itself (decision engine + delivery) is a **separate process/repo** that reads this service over HTTP. This service does not place orders, does not size positions, does not own Telegram credentials. It produces the *information* that the bot acts on.

---

## 2. Principles (do not violate)

These are the rules that, when broken, cause drift. Every PR should be checkable against them.

1. **Server-side compute is the source of truth.** Dashboard JS exists to *display* what the server computed. Any indicator/feature/signal the bot acts on must live in a SQLite table. Client-side recomputation is fallback for in-progress UI, never authoritative.

2. **Point-in-time correctness, always.** A feature value at bar T must be computable using only data available at or before T. `pd.shift(1)` on every rolling stat that touches the current bar. Closed bars only when the consumer is a lower cadence. No future-leakage in features, in labels, or in joins.

3. **One file per atomic concern.** One indicator → one file. One feature → one file. One strategy → one file. The registry pattern (`indicators/registry.py`, future `features/registry.py`, `strategies/registry.py`) lists every concrete instance — adding a new one is a one-file PR plus one line in the registry.

4. **Fact-shape feature store, not wide tables.** Adding a feature must not require a migration. `signal_features(symbol, bar_key, cadence, feature, value)` is the schema. Pivot at query time when a strategy needs a row.

5. **Cadence is a first-class concept.** Three values: `eod`, `intraday`, `session`. The `Indicator`/`Feature`/`Strategy` ABCs all declare cadence and the scheduler routes by it. No code path reads "is it daily or intraday" via string-matching a table name.

6. **Registry-driven dashboard.** When a new indicator/feature/strategy ships, the dashboard surfaces it automatically based on metadata (`pane`, `cadence`). Frontend never hardcodes indicator names.

7. **No mega-models, no DSLs (yet).** ML, when it arrives, is one LightGBM per signal type — not a single model voting on all setups. Strategies are Python expressions over features, not a custom rule language. We get DSLs wrong; lean code we can review.

8. **Universe filtering is layered.** Full universe (~2,700 EQ) → tradable (FNO ∪ Nifty 500 ~500) → liquid (`liquidity_quality_score` gate) → not-blacklisted (GSM/ASM/Unsolicited/T2T) → not-in-event-window. Each layer is a join filter, not a hardcoded list.

9. **Stage gates protect production.** Paper for 60+ days at Sharpe ≥ 1.0 net of costs before ML meta-labeling. Live only after ML reduces trade count 30–40% while holding Sharpe. Costs modelled to the rupee.

10. **Migrations are append-only.** Never edit an applied migration. New state = new file. Drop-and-recreate is a separate migration, not a rewrite of the original. (The DBJob archetype is what will make hot-application robust; until then `scripts/migrate.py` is the manual path.)

---

## 3. Layer map

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  LAYER 1 — Session                                                           │
│  SessionManager (single NSE network boundary) + 3-hop warm-up + RL + CB     │
│  Status: ✅ built                                                            │
└──────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  LAYER 2 — Collectors  (32 live, ~25 more planned)                          │
│  Five archetypes: Snapshot / Event / CSV / Reference / Fanout               │
│  Writes raw_* tables. SQLite, WAL, idempotent upserts.                      │
│  • Equity live: indices, gainers/losers, pre-open, call-auction             │
│  • Derivatives: oi_spurts, option_chain, most_active_fno                    │
│  • Filings: announcements (equity/SME/debt/MF), board_meetings,             │
│    corporate_actions, integrated_filings, shareholding_pattern              │
│  • Surveillance: GSM / ASM-LT / ASM-ST / Unsolicited → blacklist view       │
│  • Reference: fno_list, index_members, quote_metadata, price_bands          │
│  • EOD: bhavcopy_cm, bhavcopy_fo, volatility_report                         │
│  • External: macro (Yahoo), screener.in, GIFT Nifty, India VIX, NSDL FPI    │
│  Status: ✅ 32 done. 📋 ~25 todo (MWPL, F&O ban, MF disclosures, AMFI, …)   │
└──────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  LAYER 3 — Parsers & Retention  (active work)                                │
│  PDF download → text/table extract → financial numbers + sentiment           │
│  Three-tier archive (hot/warm/cold), nightly cleanup, status state machine   │
│  Status: 📋 all todo. ~6 weeks per Layer 3 plan.                            │
└──────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  LAYER 4 — Indicators / Patterns / Levels / Fundamentals / Events            │
│                                                                              │
│  Indicators (compute over raw_bhavcopy_cm / raw_intraday_candles + live)    │
│    EOD daily ✅ SMA / RSI / MACD                                             │
│              📋 EMA, ATR, BB, ADX, Supertrend, Stoch RSI, ParabolicSAR,     │
│                 Donchian, Keltner, OBV, CMF, Ichimoku, Volume SMA, …        │
│    Intraday ✅ RSI_5m / MACD_5m                                              │
│              📋 Supertrend_5m, Donchian_5m, VWAP, Volume Delta, CVD          │
│    Session  📋 Pivot Points, Volume Profile (POC/VAH/VAL)                    │
│                                                                              │
│  Patterns + Levels + Regimes + Relative Strength + Options Greeks            │
│    Status: all 📋. Order matters: indicators first, then patterns/regimes.  │
│                                                                              │
│  Fundamentals (composes from screener + announcements + parsed results)      │
│    Status: 📋 — depends on Layer 3 extractor for fresh data.                │
└──────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  LAYER 5 — Stock Profile                                                     │
│  profile/builder.py — nightly composer reading every Layer 4 output.         │
│  stock_profile_daily: ~60 columns × ~3,000 symbols × ~250 sessions/yr.       │
│  Indexed by (as_of_date, quality_score), (as_of_date, trend_regime).         │
│  Status: 📋. Becomes possible once Layer 4 indicators are ≥80% complete.    │
└──────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  LAYER 6 — Signal Engine                                                     │
│                                                                              │
│  ┌─ Features  (one file each, registry-driven)                              │
│  │   feat_rsi_oversold_cross_daily      feat_vix_calm                       │
│  │   feat_macd_bull_cross_5m            feat_no_news_window                 │
│  │   feat_above_sma200                  feat_volume_surge                   │
│  │   feat_delivery_ratio_strong         feat_nifty_uptrend                  │
│  │   feat_oi_buildup_long               feat_pcr_extreme_low                │
│  │   feat_liquidity_quality_score      feat_blacklist                       │
│  │   ...                                                                    │
│  │   Writes signal_features(symbol, bar_key, cadence, feature, value)      │
│  │   Runs at each cadence (EOD nightly + intraday every-minute).            │
│  │                                                                          │
│  ├─ Strategies  (one file each, registry-driven)                            │
│  │   strategy_oversold_bounce = AND(features...)                            │
│  │   strategy_breakout_long   = AND(features...)                            │
│  │   strategy_result_beat     = AND(features...)                            │
│  │   ...                                                                    │
│  │   Writes signals(rule, symbol, bar_key, payload, dispatched_at,         │
│  │                   UNIQUE(rule, symbol, bar_key))                         │
│  │                                                                          │
│  ├─ Outcome labeling  (offline, nightly — feeds ML training)               │
│  │   signals/outcome_labeler.py — triple-barrier with dynamic σ.            │
│  │   paper_trades table — every alert logged as if traded.                  │
│  │                                                                          │
│  └─ ML (added after rules ship — meta-labeling, not replacement)            │
│      LightGBM per signal type → P(profitable | features), >0.55 → trade.   │
│      CPCV validation, Deflated Sharpe, PSI/KS drift, isotonic calibration.  │
└──────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  LAYER 7 — API surface  (FastAPI, what the bot + dashboard read)            │
│  GET /signals?since=&types=&limit=    GET /profile/{symbol}                  │
│  GET /announcements                   GET /option-chain/{symbol}             │
│  GET /blacklist  /universe  /quote/{symbol}  /events/pending                 │
│  POST /webhooks  /admin/replay  GET /admin/endpoint-health                   │
│  Status: 📋 — dashboard already has /api/stocks/* (partial Layer 7).        │
└──────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼  (HTTP over the API)
┌──────────────────────────────────────────────────────────────────────────────┐
│  BOT  (separate process/repo)                                                │
│                                                                              │
│  Decision engine — 6-layer filter chain:                                     │
│    1. Hard filters (blacklist, pledge>25%, loss-making + long)              │
│    2. Quality gate (quality_score < 30 + long → reject)                     │
│    3. Regime alignment (penalty/bonus matrix by regime × signal)             │
│    4. Quality boost (high quality → +0.05 confidence)                       │
│    5. Model probability blend (0.6 × rule + 0.4 × ML)                       │
│    6. Final threshold + dedup + throttle                                     │
│                                                                              │
│  Risk:                                                                       │
│    Structure-based SL (primary) + ATR fallback + chandelier trail            │
│    Quarter-to-half Kelly sizing, capped 2% per trade                         │
│    Vol-targeted gross 12% annualized, cut 40–50% when VIX > 22              │
│    Time-of-day rules, daily / weekly kill switches                          │
│                                                                              │
│  Delivery:                                                                   │
│    Telegram primary, email fallback, webhook subscribers                    │
│    Explainability card per alert (top reasons, confidence, analog)          │
│    Quiet hours / throttle                                                    │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Cadence model

Three time bases, three scheduling profiles, three retention policies. Every piece of code in Layers 4–6 declares one.

| Cadence | Source(s) | Schedule | Retention | Use |
|---|---|---|---|---|
| **eod** | `raw_bhavcopy_cm`, daily reference tables | nightly after bhavcopy load (~18:00 IST) | forever (small) | swing setups, daily indicators, daily features, regime classifiers, fundamentals |
| **intraday** | `raw_intraday_candles` + `raw_equity_quotes` live | every 1 minute, 09:15–15:30 IST, `is_market_open()` gate | 30 days rolling | live signals, in-session indicators, VWAP, intraday RSI/MACD, live volume profiles |
| **session** | `raw_intraday_candles` aggregated per session | one-shot at session open (pivots) / close (POC) | 30+ days | pivot points, volume profile (POC/VAH/VAL), session VWAP final |

**Hard rule:** a feature's cadence determines which signal_features cadence value it writes. A strategy can join across cadences in queries, but it itself declares one cadence — the cadence on which it fires.

---

## 5. Universe filtering

Funnel applied at multiple stages, not a single hardcoded list:

```
   ALL EQ (~2,700)        full bhavcopy universe — EOD indicator compute
       │
       ▼  filter: FNO ∪ NIFTY500 membership
   TRADABLE (~500)        intraday compute, feature compute
       │
       ▼  filter: liquidity_quality_score > threshold
   LIQUID (~300, estimate) signal-firing universe
       │
       ▼  filter: NOT IN blacklist view (GSM, ASM, Unsolicited)
   NOT BLACKLISTED        candidate for an alert
       │
       ▼  filter: not within N days of earnings / corporate action
   NOT IN EVENT WINDOW    fires
       │
       ▼  bot Layer 1-2-3 filters (quality_score, regime, pledge, etc.)
   ALERTED                Telegram message goes out
```

Every filter is a SQL join, not a Python loop. Adding a new exclusion = adding a join, never a hardcoded list.

---

## 6. The signal engine — concrete shape

This is the next thing being built. The contract:

### 6a. `signal_features` (the fact-shape feature store)

```sql
CREATE TABLE signal_features (
    symbol      TEXT    NOT NULL,
    bar_key     TEXT    NOT NULL,   -- date string for eod, epoch_str for intraday
    cadence     TEXT    NOT NULL,   -- "eod" | "intraday" | "session"
    feature     TEXT    NOT NULL,   -- "rsi_oversold_cross_daily", ...
    value       REAL,                -- 1.0/0.0 for boolean; raw number for thresholds
    computed_at INTEGER NOT NULL,    -- audit
    PRIMARY KEY (symbol, bar_key, cadence, feature)
);
```

One row per (symbol × bar × feature). No wide columns. Adding a feature = adding a row source, never a migration.

### 6b. `Feature` ABC

```python
class Feature(ABC):
    name: str
    cadence: Cadence            # eod | intraday | session
    description: str            # one-line, surfaces in dashboards/explainability
    @abstractmethod
    def compute(self, conn) -> pd.DataFrame:
        """Return (symbol, bar_key, value) for every symbol/bar this feature
        evaluates true (or has a numerical value). Writer joins on PK."""
```

Each Feature reads what it needs from `indicator_*`, `raw_*`, `raw_india_vix`, `raw_indices`, `raw_announcements`, `raw_corporate_actions`, etc. Pure compute; no I/O beyond reads.

### 6c. `Strategy` ABC

```python
class Strategy(ABC):
    name: str
    cadence: Cadence
    requires: list[str]              # feature names this strategy reads
    @abstractmethod
    def fire_for_bar(self, conn, bar_key) -> Iterable[SignalCandidate]:
        """Inner-join signal_features on `requires` at bar_key; for each
        symbol where every required feature is truthy, yield a candidate
        carrying the feature snapshot as payload."""
```

Defaults to AND-of-features. Override `fire_for_bar` for OR / scoring / weighted-vote semantics. Most strategies will be the default.

### 6d. `signals` (what the bot reads)

```sql
CREATE TABLE signals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    rule          TEXT    NOT NULL,
    symbol        TEXT    NOT NULL,
    bar_key       TEXT    NOT NULL,
    cadence       TEXT    NOT NULL,
    fired_at      INTEGER NOT NULL,
    payload       TEXT    NOT NULL,   -- JSON snapshot of all features at fire time
    dispatched_at INTEGER,             -- NULL until Telegram (or other) confirms
    UNIQUE (rule, symbol, bar_key)    -- dedup primitive
);
```

`UNIQUE(rule, symbol, bar_key)` + `INSERT OR IGNORE` is the entire dedup story. The same setup at the same bar can never fire twice.

### 6e. Cadence-aware scheduling

```
After indicators_intraday tick (every minute):
  → compute every Feature where cadence="intraday"
  → write signal_features rows
  → run every Strategy where cadence="intraday"
  → write new signals rows
  → Telegram dispatcher picks up signals where dispatched_at IS NULL

After EOD bhavcopy load:
  → compute every Feature where cadence="eod"
  → write signal_features rows
  → run every Strategy where cadence="eod"
  → write new signals rows
  → Telegram dispatcher picks up (rate-limited to "quiet hours" config)
```

Three jobs, all already-existing scheduler integration points.

---

## 7. The bot pipeline (out of scope for this repo)

The alert bot is a *separate process*. It HTTP-reads this service. Its responsibilities:

1. **Filter the firehose** via the 6-layer decision chain.
2. **Size the position** (Kelly, vol-targeted, correlation-aware).
3. **Compose the message** (explainability card, top reasons, analog).
4. **Deliver** via Telegram / email / webhook.
5. **Throttle** (max N alerts/hour, quiet hours).
6. **Track** outcomes back to this service via webhook (so the labeler closes the loop).

This separation lets us reload bot config without touching the data service, and lets multiple bots subscribe (e.g. one paper, one live, one experimental).

---

## 8. The ML/research loop

Two cycles, both downstream of the signal engine:

```
                    ┌───────────────────────────────────────┐
                    │  signals (every alert ever fired)     │
                    └──────────────┬────────────────────────┘
                                   │
                                   ▼
                    ┌───────────────────────────────────────┐
                    │  signals/outcome_labeler.py (nightly) │
                    │  triple-barrier labels per signal:    │
                    │  hit-target, hit-stop, time-flat      │
                    │  → paper_trades                       │
                    └──────────────┬────────────────────────┘
                                   │
                ┌──────────────────┴────────────────────┐
                ▼                                       ▼
   ┌──────────────────────────┐         ┌──────────────────────────┐
   │  ML training (weekly)    │         │  Drift monitoring        │
   │  one LightGBM per signal │         │  PSI/KS per feature      │
   │  CPCV + Deflated Sharpe  │         │  rolling Sharpe vs train │
   │  isotonic calibration    │         │  → strategy_decay_score  │
   │  → ml_models registry    │         │  → auto-disable strategy │
   └──────────────────────────┘         └──────────────────────────┘
```

**Meta-labeling**, not replacement: rules produce the signal; ML decides *size*, not *side*. Calibrated P > 0.55 → take trade.

---

## 9. Backtest + cost realism

The single highest-leverage piece outside the signal engine itself:

- `signal_features` + `signals` history → vectorbt sweeps for parameter sensitivity
- `paper_trades` + 4-segment Indian fee model (brokerage / STT / exchange / SEBI / stamp / GST) → realistic Sharpe net of costs
- `nautilus_trader` for production-grade fill simulation when a strategy graduates
- ORB-with-VWAP-filter as the public benchmark (~Sharpe 1.16 to beat)
- Stage gates: paper 60d ≥ 1.0 Sharpe → ML meta-labeling cuts frequency 30–40% without dropping Sharpe → live (expect 30% Sharpe shrinkage live-vs-paper)

---

## 10. Anti-patterns (do not do these)

Concrete examples of "drift" that this doc is here to prevent.

| Anti-pattern | Why it's wrong | What to do instead |
|---|---|---|
| Adding a wide column to `signal_features` to hold a new feature | Schema churns; migrations balloon; can't add features at runtime. | Add a new row source. Fact-shape stays. |
| Computing indicators in dashboard JS for the bot to consume | Bot can't subscribe to a browser. Math forks. | Indicators are server-side. Dashboard is display. |
| One mega-LightGBM that votes on every signal type | Confounds signal categories; SHAP loses interpretability; sector residuals leak. | One model per signal type. Sector-conditional only if data demands. |
| Writing a strategy DSL ("if RSI < 30 and …") | Solos-DSLs become a maintenance tarpit. Custom syntax = custom parser bugs. | Plain Python. ABC + AND/OR helpers. |
| Hardcoding "skip stock X" in a strategy | Reasoning gets buried. Reviewer can't find why. | Add a `Feature` that returns false. Strategy reads features only. |
| Reaching into `raw_*` from a strategy | Couples strategies to feed schemas. Refeed format change cascades. | Strategies read `signal_features` only. Raw tables are read by Features. |
| Recomputing yesterday's indicators every night | Wasteful; risk of changing historical values. | Incremental compute — only since the last watermark. |
| Editing a past migration to "fix" something | Breaks every DB already at that version. | New migration. Drop / recreate / data-fix is a follow-up file. |
| Adding a delivery channel inside the signal engine | Couples production to ops concerns. | Engine writes `signals`. Dispatcher reads and delivers. |

---

## 11. Build sequence (from now)

Locked-in order. Boxes are tasks; bars below them are dependencies that must land first.

```
                              [Indicator stack — daily + intraday RSI/MACD] ✅
                                              │
                  ┌───────────────────────────┼───────────────────────────┐
                  ▼                           ▼                           ▼
        [More indicators        [Feature layer + registry]      [Signal engine schema +
        EMA, ATR, BB, ADX,       (Layer 4 still adding)           Strategy ABC]  ← NEXT
        Supertrend, VWAP, …]     +5 baseline features:                │
                  │              rsi_oversold_cross,                  ▼
                  ▼              macd_bull_cross,            [First strategy:
        [Patterns + Levels +     above_sma200,                oversold_bounce
         Regimes + Relative      vix_calm, no_news_window]    end-to-end]
         Strength + Options Δ]            │                          │
                  │                       ▼                          ▼
                  ▼              [More features + strategies]   [Telegram dispatcher]
        [Layer 5: stock profile               │                       │
         composer]                            ▼                       ▼
                                     [Outcome labeling +      [ALERTS LIVE]
                                      paper_trades]                   │
                                              │                       │
                                              ▼                       ▼
                                     [ML meta-labeling]      [Drift monitoring +
                                      LightGBM per signal     strategy decay]
                                              │
                                              ▼
                                     [Bot decision engine
                                      = separate repo]
```

The **critical path** to "first live Telegram alert" runs:

1. Signal engine schema (`signal_features`, `signals`) + Feature/Strategy ABCs  ← next
2. ~5 baseline features (RSI cross, MACD cross, SMA filter, VIX calm, news window)
3. One strategy (`oversold_bounce`) wiring those features
4. Telegram dispatcher (reads `signals` where `dispatched_at IS NULL`, posts, marks dispatched)

Everything else (more indicators, profile composer, ML, bot decision engine, backtest infra) is **parallel work** that adds quality but isn't on the critical path.

---

## 12. What this doc deliberately leaves to others

- **`FEATURE_CHECKLIST.md`** owns the inventory of what's done / todo per layer.
- **`FINAL_ARCHITECTURE.md`** (where it exists) owns deeper internals — collector framework, session manager, write strategies.
- **`LEARNINGS.md`** owns NSE-specific quirks, fixture gotchas, dead-end design choices.
- **`docs/adr/`** owns one-off decisions with rationale. New architectural calls land as ADRs and may update this doc.

When you find yourself drifting, re-read §2 (principles) and §10 (anti-patterns) first.
