# PLAN — the profit realignment (2026-06-13)

> The previous PLAN.md (2026-05-30) was deleted; this one is written after the
> strategy-validation week and supersedes prior sequencing. The grandplan stays
> the vision; FEATURE_CHECKLIST.md stays the capability inventory; **this file
> decides what gets built next and why.**

## North star (unchanged)

A personal intraday alert system: 500+ NSE stocks watched every minute,
confidence-scored Telegram alerts, one VPS. Core thesis: edge is not being
first to news — it's knowing which stocks are *set up* to move and collapsing
many forces into one verdict.

## What changed: the validation week's evidence (2026-06-12/13)

All measured on 500 symbols × 1 year of verified 1-min data, net of the full
cost model, with anti-lookahead fills and temporal-fold (CPCV) checks:

| Variant | Trades | Gross | Net | Verdict |
|---|---|---|---|---|
| S1 VWAP+RVOL+Breakout (faithful) | 5,006 | +₹108k | −₹591k | shelved |
| S1 + catalyst gate | 2,018 | +₹67k | −₹223k | shelved (CPCV: 7/8 folds negative) |
| S1 + catalyst + weak-tape + BE-at-1R (best) | 1,006 | +₹61k | −₹84k | shelved (CPCV avg Sharpe −3.5, 1/8 folds positive) |
| S2 CPR+VWAP trend (NIFTY/BANKNIFTY) | ~100 ea | ≈ 0 | −₹8–9k ea | shelved pending futures-cost model |
| S4 RS-screen standalone | 17,220 | −₹325k | −₹2.7M | rejected |

Measured side-findings that now steer design:
- **Weak-tape modifier is real**: catalyst breakouts pay when the index is
  flat/weak (idiosyncratic strength), and *lose* when tape-aligned. The
  popular "only trade with the market" filter is backwards for this setup.
- **The binding constraint is costs, not win rate**: every good variant was
  gross-positive. ~₹140 round-trip per ₹1L notional needs ≥0.15% edge per
  trade; 5-min breakout edges measure ~0.03–0.06%.
- Educational strategy documents (strategies.md) describe real patterns but
  are written cost-blind; following them literally loses money.

## The realignment: three decisions

**D1 — Intraday TA strategies are out of the codebase (removed 2026-06-13,
post-validation, on Jay's call).** The three engines built that week
(vwap_rvol_breakout, cpr_vwap_trend, rs_leader) were deleted after their
verdicts were recorded — they were never committed, so the table above and
the technique notes here are the surviving record. The reusable ideas
(consolidation/base detection, time-of-day RVOL baseline, CPR day-typing,
weak-tape filter, breakeven-at-1R / trailing exits) are re-implementable
from the descriptions in this file when P1 needs them as timing modules
inside event trades. Only the pre-existing benchmark engines (orb_vwap etc.)
remain registered.

**D2 — The profit engine is event-driven reaction (grandplan Phase 4/5,
checklist Group C), not generic TA.** Result-day moves run 5–15%; costs become
noise; and the moat is already built: Phase-5 earnings engine (E1–E5,
react-don't-predict), field-wise consensus (manual > news > MC > Yahoo), BFSI
quality signal, sector result playbooks, filings feeds. Nobody running a
strategies.md copy has these inputs.

**D3 — Validation protocol is now law.** Nothing reaches live alerts (even
paper) without: full-universe backtest net of costs → CPCV temporal folds →
explicit promote-or-shelve entry in this file. A negative verdict recorded
cheaply is the system working.

## The build sequence

**P1 — Result-day reaction strategy (S8), backtested. (next)**
Engine: earnings event (filing/board-meeting date) + surprise direction from
the extraction/consensus pipeline → gap-and-hold-VWAP filter → first
consolidation breakout (reuse S1's base detector) → swing exit (trail, allow
overnight hold variants). Backtest over the past 4 quarters of result days.
*Local blockers*: filings coverage on the laptop is partial (973 events);
the full event history lives on EC2 — run the definitive backtest there.

**P2 — Wire the winner to live paper alerts.**
Route through the existing signals/dispatcher/Telegram path with confidence
scoring; every alert logged as a paper trade with outcome labeling from day
one (checklist Phase 1/9 machinery). Promote to real size only after a
quarter of labeled paper results.

**P3 — Futures cost model + index strategy revisit.**
Add a futures cost profile to the backtester cost model; re-run S2 (CPR trend
day) on NIFTY/BANKNIFTY with it and a percentile-based CPR-narrowness filter
(fixed 0.5% fires on 40% of days — not selective). Promote or shelve.

**P4 — Confluence as confidence, not as a strategy.**
S10's seven conditions become scored inputs to the confidence engine on event
trades (the weak-tape finding shows naive condition-stacking can subtract).
OI legs (S3/S10) stay blocked until a real OI feed exists (Angel futures OI —
candidate collector).

## Standing gates

- EC2 is the system of record for events/filings; the laptop validates math.
- Indicators: all server-computed, verified exact (2026-06-12); dashboards and
  bot read the same indicator_* rows. Never recompute client-side.
- Every strategy/config verdict gets a dated row in the table above. No
  retuning a shelved variant without new data or a new hypothesis written
  down first.
