# Daily Sweep — multi-timeframe ICT strategy

> ## ⛔ SHELVED — 2026-06-21 (no net-of-cost edge)
> Net-of-cost backtest across 29 NSE names (2023–2026 — the only candle history in the DB; 2020–22
> doesn't exist). After fixing two real defects and adding the missing Step-2 filter, **no parameter
> set is profitable**: every config is PF < 1.0; the best (1H retracement + deep 0.5–0.79 fib) is
> **PF 0.98, net −₹2,769, and not regime-robust** (+₹7k in 2025, −₹9.8k in 2023–24). The live
> forward-test job is **disabled** (`main.py`). Kept in-tree as a reference implementation.
>
> **The honest trail:**
> 1. First run looked "+₹170k / PF 1.07" — **misleading**: a per-trade %-sum, and propped up by a
>    backtest **bug** — a short-session trade with no 15:25 bar exited at the whole-series' last
>    candle (a future price), fabricating a +72R / ₹75k BHARTIARTL "win" (~₹1M with uncapped sizing).
>    User eyeballed the candles and caught it. Fixed (exit at the entry day's last bar) + regression test.
> 2. **Position sizing** risked ₹10k but built ₹2.8M avg notionals → costs ate **93%** of the gross
>    edge. Capped to ₹40k margin × 5 (₹200k notional). Clean baseline then: **PF 0.89, −₹78,596**.
> 3. **Step 2 (1H retracement) was never wired in** — `scan_setups` took the 1H frame but ignored it.
>    Adding it (point-in-time, no look-ahead) cut trades ~70% and improved net 96% — but only to
>    breakeven-negative. Still missing/never-needed: Step 9 event-day block, Step 7 target models B/C.
>
> **Lesson:** unit-green ≠ validated. Eyeball real trades, measure rupees (not per-trade %), and a
> backtest "edge" that rests on one outlier trade is a bug until proven otherwise.

Rule-based, no subjective interpretation. **Daily trend → 1H retracement → 5m liquidity sweep
→ market-structure shift (BOS) → Fair Value Gap → entry.** Two delivery modes:
1. **Backtest** (walk-forward across regimes; metrics + per-trade report) — built first.
2. **Live** (intraday scan during sessions → its OWN ntfy/Telegram channel) — SHELVED (see above).

## Reuse map (no redundant code)
| Need | Reuses |
|---|---|
| Swing highs/lows + HH-HL/LH-LL trend + BOS | `indicators/trend/market_structure._structure_frame` (parameterized `k`) |
| ATR (sweep threshold, trailing) | `indicators/volatility/atr.atr_latest` |
| Candles (Daily / 1H / 5m), indices + stocks | `raw_intraday_candles` via `data.py` (native or resampled from `minute`) |
| Backtest metrics + trade types | `backtester/_core/types` (Signal/Trade/RunReport) |
| Net-of-cost P&L | `costs/model.compute_costs` |
| Session windows | `market/time_rules` |
| Live alert delivery | `bot/notify` (new channel) |

## Files / steps
| File | Spec steps | Status |
|---|---|---|
| `config.py` | all parameters | ✅ |
| `data.py` | candle loading D/1H/5m (indices resample from `minute`) | ✅ |
| `structure.py` | **1** daily trend · **2** retracement zone · **4** BOS | ✅ |
| `sweep.py` | **3** 5m liquidity sweep (size > 0.1%/0.25·ATR OR-floor, vol-gate skipped for index spot) | ✅ |
| `fvg.py` | **5** Fair Value Gap (3-candle) | ✅ |
| `setup.py` | **6** entry · **7** SL/target (A 1:3 ✅; B/C next) · **8** session · **9** gap ✅ (event-day hook) | ✅ |
| `backtest.py` | **10** metrics ✅ · **11** trade report ✅ · **12** walk-forward (next) | ✅ |
| `live.py` | intraday scan (5min, market hours) → dedicated 'sweep' channel | ✅ |

## Data note
Indices (NIFTY/BANKNIFTY/FINNIFTY) store only `minute` bars → Daily/5m/1h are **resampled**;
stocks have native `day` + `5minute`. `data.py` hides this — both work uniformly. The laptop DB
is partial; full multi-year candles (for Step 12 walk-forward) live on the EC2 server.

## Build order
S1 daily trend ✅ → S3 sweep + S5 FVG (independent, pure functions) → S2 retracement → S4 BOS →
S6 entry/SL/target wiring → S10/11 backtest → S12 walk-forward → live scan + channel.
Each step is a pure, unit-tested function before it's wired into the backtest/live runners.
