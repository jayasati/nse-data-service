# Daily Sweep — multi-timeframe ICT strategy

Rule-based, no subjective interpretation. **Daily trend → 1H retracement → 5m liquidity sweep
→ market-structure shift (BOS) → Fair Value Gap → entry.** Two delivery modes:
1. **Backtest** (walk-forward across regimes; metrics + per-trade report) — built first.
2. **Live** (intraday scan during sessions → its OWN ntfy/Telegram channel) — built after backtest.

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
| `live.py` | intraday scan → dedicated alert channel | ⬜ |

## Data note
Indices (NIFTY/BANKNIFTY/FINNIFTY) store only `minute` bars → Daily/5m/1h are **resampled**;
stocks have native `day` + `5minute`. `data.py` hides this — both work uniformly. The laptop DB
is partial; full multi-year candles (for Step 12 walk-forward) live on the EC2 server.

## Build order
S1 daily trend ✅ → S3 sweep + S5 FVG (independent, pure functions) → S2 retracement → S4 BOS →
S6 entry/SL/target wiring → S10/11 backtest → S12 walk-forward → live scan + channel.
Each step is a pure, unit-tested function before it's wired into the backtest/live runners.
