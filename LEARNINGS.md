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

_None logged yet._

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
