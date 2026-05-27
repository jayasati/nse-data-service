# nse-data-service

Inspect the database:

```
sqlitebrowser data/nse.db
```

## Backfill intraday candles (Groww)

NSE's free feeds don't publish minute history, so historical intraday candles are
backfilled from a broker API. Groww is the default broker — set credentials in `.env`:

```
GROWW_API_KEY=...            # from Groww → Trading APIs
GROWW_TOTP_SECRET=...        # TOTP secret for that API key
# (or a daily GROWW_ACCESS_TOKEN instead of key+secret)
```

Verify credentials, then backfill 1-minute candles (the dashboard derives 5m/15m/etc.
from minute data on the fly):

```
# verify credentials
python scripts/backfill_intraday.py groww-check

# full universe: ~6 months of 1-min candles for the top 1000 symbols by turnover.
# Groww caps minute requests at ~7 days, so each symbol paginates ~26 windows —
# the whole run takes a few hours. Run it detached and watch progress separately.
# Use the venv's python explicitly so a detached run doesn't lose it.
nohup .venv/bin/python scripts/backfill_intraday.py run --top 1000 --interval minute --days 170 \
    > backfill.log 2>&1 &

# smaller runs: top N, or specific symbols
python scripts/backfill_intraday.py run --top 50 --interval minute --days 30
python scripts/backfill_intraday.py run --symbols RELIANCE,TCS --interval minute --days 60
```

Watch progress while it runs:

```
# live, scrolling [N/1000] log lines (Ctrl-C stops the tail, not the backfill)
tail -f backfill.log

# skip the "skip (have …)" lines and only show symbols being fetched
tail -f backfill.log | grep -v skip

# one-line summary: symbols done / candle count / DB span
python scripts/backfill_intraday.py progress --interval minute --top 1000
```

Re-runs are idempotent and resume where they left off, so re-running the same command
after an interruption continues from where it stopped (use `--no-resume` to re-fetch).
A symbol counts as "done" once its stored history reaches the target start (within
~7 days), so keep `--days` consistent across runs — picking a window that matches your
existing data lets resume skip finished symbols instead of re-fetching them to extend
them further back.


  Manual tool too:
  python scripts/run_collectors.py --due --dry-run   # list overdue
  python scripts/run_collectors.py --due             # run them
  python scripts/run_collectors.py fii_dii           # specific feeds