# LEARNINGS

Running log of what the live system actually taught us — false signals, alert
quality, data-quality surprises, and ops incidents. Created for Phase-1 Week 6
(FEATURE_CHECKLIST task 6.6), kept going thereafter.

**How to use this file**
- **Every alert that fires** gets reviewed (task 6.6): does the message make
  sense, were the numbers right, was the signal genuine or noise?
- Log **false / noise signals** with enough context to recognise the pattern
  again, and a hypothesis for a rule change.
- Log **data-quality** issues (stale feeds, bad ticks, missing ATR) and **ops
  incidents** (collector outages, missed runs, alert failures).
- Date everything (IST). Link to the signal id / paper_trade id where relevant.

---

## Alert review log (task 6.6)

One row per fired alert. "Verdict" = genuine / noise / borderline.

| Date (IST) | Symbol | Signal | Confidence | Numbers OK? | Verdict | Notes |
|------------|--------|--------|-----------|-------------|---------|-------|
| _none yet_ | | | | | | first real alerts pending |

---

## False / noise signals

> Pattern → why it fired → proposed rule change.

_None logged yet._

---

## Data-quality notes

> Stale feeds, bad ticks, missing inputs (e.g. ATR null → no paper trade), etc.

_See Week-6 spot-check results below as they land._

---

## Ops incidents

> Collector outages, missed scheduled runs, backup/health-check failures.

**2026-06-07 — health check was over-broad → false alarms on event-driven feeds.**
First server run of `ops.health_check` flagged 8 feeds (announcements_*,
corporate_actions, board_meetings, insider_trading, large_deals,
financial_results). All are `market_hours_only: False` event-driven feeds that
legitimately go quiet for hours — freshness is not a health signal for them.
**Fix:** `find_failing` now alerts only on `market_hours_only` heartbeat feeds
(indices, gainers, oi_spurts, option_chain, live_equity, price_band, india_vix,
52w high/low) — the ones that genuinely produce a row every interval the session
is open. This also matches task 6.3's "5-minute collector" intent.

---

## Phase 3 — Backtest Trust (Week 10)

### 10.1 — backtester had zero cost model (confirmed)
No reference to `costs/model.py` anywhere in `backtester/` before Phase 3. P&L was
gross only (`pnl_raw` / `pnl_leveraged`). Net-of-cost added in 10.2 (`pnl_net`
through Trade → runner → persistence; migration 040).

### 10.3 — indicator parity (live vs backtest): PASS
Both the live engine and the backtester compute indicators via **pandas-ta-classic**
with identical params, so definitions match by construction:
- **MACD** (the one overlapping family): live `ta.macd(fast=12, slow=26, signal=9)`
  == macd_willr_daily `ta.macd` with the same 12/26/9 defaults.
- **RSI 14 / SMA 20·50·200**: used by the live engine (indicator_live/regime) but
  **not by any of the three backtested strategies** (they use BB+EMA9, MACD+WillR,
  52w-high/volume). So there's no SMA/RSI parity gap to fix for these strategies.

### Metrics basis (agreed)
Net Sharpe = daily-portfolio-returns Sharpe × √252. Sharpe is **capital-invariant**
(capital cancels in mean/std), so the per-trade notional base doesn't affect it.
Max drawdown is reported in **absolute INR** (`max_drawdown_inr`) as the headline —
a % drawdown needs a real account size, which a universe-wide backtest with many
overlapping positions doesn't define.

### 10.4 / 10.5 / 10.6 — strategy results (net of cost)
Cost-adjusted run over the FNO+Nifty500 universe (500 symbols on the dev DB),
full available history. Reproduce with `scripts/phase3_eval.py --strategy <name>`.

| Strategy | Trades | Win% | Profit factor | Gross Sharpe | Net Sharpe | Cost drag (Sharpe) | Max DD (₹) | Verdict |
|---|---|---|---|---|---|---|---|---|
| macd_willr_daily | 16,868 | 50.1 | 0.89 | −0.30 | **−2.03** | 1.73 | −2.49M | **SHELVE** — no edge; costs deepen a gross loss |
| breakout_52wh | 1,142 | 47.9 | 0.93 | −0.15 | **−0.78** | 0.63 | −0.28M | **SHELVE** — net-negative (⚠ this is the *live* Phase-1 strategy) |
| bb_ema9_30m | **0 trades** on dev DB | — | — | — | — | — | — | **CAN'T EVAL LOCALLY** — too little 30m intraday history; run on server |

**Headline:** every strategy that's run so far is **net-negative after costs** — and
two of three are even *gross*-negative on this universe/period, so cost drag isn't
the only problem. `breakout_52wh` being net −0.78 is the important one: **it's the
strategy currently firing live paper alerts.** That needs a hard look in Week 11
(CPCV) before it's trusted, and is itself an argument for the Phase-3 gate.

Caveats: profit factor < 1 on all (gross losses); the dev-DB history is short
(~weeks–months of intraday, longer for daily), so these are provisional until run
on the server's fuller history. **bb_ema9_30m produced 0 trades locally** (intraday
candle history too thin) — must be evaluated on the server before any verdict.
Max-DD in ₹ (capital-independent); net Sharpe is the decision metric (capital
cancels in the ratio).

---

## Phase 3 — Validation & Promotion (Week 11)

### 11.1 / 11.3 — experiment registry (decisions ledger)
`backtest_registry` (migration 041) records every evaluated run + verdict.
Recorded via `scripts/phase3_eval.py --register`.

### 11.2 — CPCV (fold-wise out-of-sample), corrected method
**Method note (important):** these strategies have fixed rules (nothing to fit),
so "CPCV" = fold-wise OOS consistency. The harness runs **one** full backtest
then buckets trades into 10 contiguous date folds by entry date. (An earlier
version re-ran the engine per fold window — that stripped each strategy's lookback
(52w-high needs a year; MACD needs warmup) and produced fake 0-trade folds. Fixed.)

### 11.3 — verdicts (dev DB, ~1yr history)

| Strategy | Full net Sharpe | CPCV avg | Folds positive | Verdict |
|---|---|---|---|---|
| breakout_52wh | −0.78 | −0.34 | 4/10 | **SHELVED** |
| macd_willr_daily | −2.03 | −2.60 | 1/10 | **SHELVED** |
| bb_ema9_30m | — | — | — | not evaluable locally (intraday data) — run on server |
| orb_vwap (benchmark) | — | — | — | strategy built; needs server intraday to backtest |

**Decision: both existing strategies SHELVED.** Neither clears `net Sharpe > 0.5 +
CPCV-positive`. `breakout_52wh` is inconsistent (4/10 folds positive — one or two
good stretches, not a durable edge); `macd_willr_daily` is decisively negative.

### 11.4 — promotion: NONE
Nothing clears the gate → no new signal type added to `signals/detect.py`. The
live detector is unchanged. **`breakout_52wh` is still firing live paper alerts
but backtests/CPCVs net-negative** — flagged for a keep-or-pull call; it should not
graduate from paper to real capital on this evidence.

### 11.5 — ORB+VWAP benchmark
Built `backtester/strategies/orb_vwap` (opening-range breakout + VWAP filter +
ATR stop), registered, unit-tested on synthetic bars. **Can't be backtested
locally** (no intraday history); run on the server to record its benchmark net
Sharpe in the registry:
`PYTHONPATH=src python scripts/phase3_eval.py --strategy orb_vwap --cpcv --register`

### ⚠ Caveat on all Phase-3 numbers
The dev DB holds only ~1 year of daily history and almost no intraday — so these
verdicts are **provisional**. Re-run `phase3_eval.py --strategy all --cpcv
--register` on the **server** (fuller history) before treating any shelve/keep
decision as final, especially the live `breakout_52wh`.

---

## Week-6 spot-check findings (tasks 6.4 / 6.5 / 6.7)

> Manual verification of `signal_outcomes`, `paper_trades`, and pre-market
> seeding against the live DB. Filled in as the spot-checks are run.

Tooling: `scripts/spot_check.py {all,outcomes,trades,premarket}` re-derives the
numbers independently (raw intraday + cost model) and diffs against the stored
values — see task 6.4/6.5/6.7.

**2026-06-07 — must run on the server, not the laptop.** The laptop `data/nse.db`
(5.5 GB) holds raw collector data but the *signals pipeline* tables are empty
there (`signals`, `signal_features`, `signal_outcomes`, `paper_trades` all 0
rows; `indicator_live` had 2 stale rows from 2026-05-27). The detector / paper
tracker / labeler run on the **EC2 host**, so 6.4 and 6.5 must be verified there
(SSM in, or `scripts/transfer_db.sh` a fresh copy first). Running spot_check
locally only confirms the tool works and degrades cleanly on an empty DB.
**TODO:** run `spot_check.py all -n 5` on the server and paste the diffs here.

**2026-06-07 (Sunday) — first server run.** On EC2: `signals`, `signal_outcomes`,
`paper_trades` all 0 rows; `indicator_live` **empty (0 rows)**. Today is a
non-trading day, so empty signals/trades is expected. But `indicator_live` uses
`INSERT OR REPLACE` (never DELETE) — 0 rows means it has *never* been written, so
neither the 08:45 pre-market loader nor the every-minute live job has populated
it on this host yet. Heartbeat market-data feeds *were* fresh (collector is
running), so the likely cause is a freshly-deployed pipeline that hasn't seen a
trading-day 08:45 run. **TODO (Mon 2026-06-08, after 08:45):** run
`spot_check.py premarket` — expect ~755 symbols seeded before 09:15. If still 0,
check `journalctl -u nse-collector@ubuntu | grep -iE "pre_market|live_snapshot"`
for errors in `register_pre_market_loader` / `register_live_job`.
