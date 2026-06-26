"""
Process entrypoint. Starts the scheduler with the announcements collector
running on a 5m cron from 08:00-19:00 IST.

    python -m nse_data.main

Logs to stdout as JSON (structlog). systemd captures and rotates.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env via python-dotenv BEFORE any project import reads os.environ.
# systemd's EnvironmentFile parses .env differently from python-dotenv — it
# mis-read a malformed ANGEL_TOTP_SECRET line (trailing junk → non-base32 chars)
# and broke the Angel TOTP login, while dotenv parses just the valid secret.
# override=True supersedes the systemd-provided (mangled) values.
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=True)

import structlog

from .fundamentals.quality_score import register_quality_job
from .fundamentals.strength_scores import register_strength_job
from .indicators.delivery_tracker import register_delivery_job
from .indicators.levels import register_levels_job
from .indicators.live_job import register_live_job
from .indicators.patterns import register_patterns_job
from .parsers.classify import register_classify_job
from .parsers.rating_extractor import register_rating_job
from .profile.builder import register_profile_builder
from .indicators.pre_market_loader import register_pre_market_loader
from .bot.morning_brief import register_morning_brief
from .bot.paper_digest import register_paper_digest_job
from .bot.eod_summary import register_eod_summary
from .events.calendar import register_calendar_job
from .events.consensus_job import register_consensus_job
from .events.pre_event_risk import register_pre_event_risk_job
from .events.pre_screen import register_pre_screen_job
from .parsers.analyst_ratings import register_analyst_ratings_job
from .psychology.state_classifier import register_state_classifier_job
from .psychology.exhaustion_detector import register_exhaustion_detector_job
from .psychology.stop_hunt_detector import register_stop_hunt_detector_job
from .collectors.angel_live_equity import register_angel_live_job
from .fundamentals.from_results import (
    register_extract_runner,
    register_fast_result_lane,
    register_intraday_extract_runner,
)
from .market.regime_job import register_regime_job
from .research.snapshot_job import register_factor_snapshot_job
from .research.paper_trade import register_paper_trade_job
from .market.sector_radar_job import register_sector_radar_job
from .options.job import register_options_job
from .smart_money.score import register_smart_money_job
from .smart_money.short_squeeze import register_short_squeeze_job
# Daily Sweep live job SHELVED 2026-06-21 (no edge — see strategy/daily_sweep/README.md):
# from .strategy.daily_sweep.live import register_daily_sweep_job
from .research.weekly_positional import register_weekly_positional_job
from .collectors.global_markets import register_global_markets_job
from .research.basket_rotation import register_basket_job
from .research.large_deal_signals import register_large_deal_signals_job
from .research.promoter_signals import register_promoter_signals_job
from .research.smallcap_momentum import register_smallcap_job
from .research.news_engine import register_news_score_job
from .research.news_store import register_move_news_job
from .research.conviction import register_conviction_job
from .collectors.bhavcopy_candles import register_bhavcopy_candle_job
from .signals.detect import register_signal_job
from .signals.watchlist import register_watchlist_job
from .signals.outcome_labeler import register_outcome_labeler
from .signals.paper_tracker import register_paper_tracker
from .scheduler.catchup import run_due
from .scheduler.jobs import register_jobs
from .scheduler.runner import make_runner, make_scheduler
from .session.manager import SessionManager
from .settings import load_endpoints
from .storage.cache import MemoryDedupCache, RedisDedupCache
from .storage.db import apply_migrations, open_db


log = structlog.get_logger()


def _build_dedup_cache():
    """Prefer Redis; fall back to in-process memory cache if unavailable."""
    try:
        import redis  # type: ignore
        r = redis.Redis(decode_responses=True)
        r.ping()
        log.info("dedup_cache_redis_ok")
        return RedisDedupCache(r, namespace="announcements_equity")
    except Exception as e:
        log.warning("dedup_cache_redis_unavailable", error=str(e))
        return MemoryDedupCache()


def main() -> int:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )

    endpoints = load_endpoints("config/endpoints.yaml")

    # One-time migration pass at startup. Use a dedicated connection that's
    # closed immediately — the long-lived `db` of before is gone, because
    # APScheduler runs jobs in worker threads and SQLite connections aren't
    # thread-portable. See _make_runner: each job opens its own connection.
    db_path = "data/nse.db"
    mig_conn = open_db(db_path)
    newly = apply_migrations(mig_conn)
    mig_conn.close()
    if newly:
        log.info("migrations_applied", files=newly)

    session = SessionManager()
    dedup_cache = _build_dedup_cache()

    # Catch-up pass: this host isn't always-on, so a daily/weekly run scheduled
    # while the laptop was asleep never fired. Before starting the live loop,
    # run any daily/weekly collector whose data lags its last expected run.
    # (Recovers a missed schedule, not lost history — see scheduler/catchup.py.)
    cu_conn = open_db(db_path)
    try:
        run_due(session, db_path, endpoints, dedup_cache, cu_conn)
    except Exception:
        log.exception("catchup_failed")  # never let catch-up block the scheduler
    finally:
        cu_conn.close()

    scheduler = make_scheduler()
    registered = register_jobs(
        scheduler, endpoints, make_runner(session, db_path, dedup_cache)
    )
    # Live intraday-indicator compute: every minute during market hours, sweeps
    # the FNO + Nifty 500 universe, writes to indicator_*_5m. Gated internally
    # on is_market_open() so off-hours ticks are cheap no-ops.
    registered.append(register_live_job(scheduler, db_path))
    # Pre-market loader: 08:45 IST on trading days, seeds indicator_live with the
    # previous session's values and publishes the blacklist + quality flags to
    # Redis before the 09:15 open.
    registered.append(register_pre_market_loader(scheduler, db_path))
    # DISABLED 2026-06-18: the intraday TA/event signal system (detector +
    # paper-trade tracker + outcome labeler) is retired — its rules were shelved
    # net-negative, and it's superseded by the validated Q+V+Momentum daily
    # swing strategy (scripts/paper_trade.py → paper_book). Re-enable by
    # uncommenting if the intraday research dataset is needed again.
    # registered.append(register_signal_job(scheduler, db_path))
    # registered.append(register_paper_tracker(scheduler, db_path))
    # registered.append(register_outcome_labeler(scheduler, db_path))
    # Market regime classifier: every 5 minutes during market hours, snapshots
    # VIX/Nifty/breadth/GIFT into market_state with an overall_regime tag the
    # confidence scorer reads (Phase 2, Week 7). Internally gated on market hours.
    registered.append(register_regime_job(scheduler, db_path))
    # Sector radar: every 5 minutes during market hours, ranks the 11 NSE
    # sectoral indices by relative strength vs NIFTY 50 into sector_state; the
    # confidence scorer reads a signal's sector rank/trend (Phase 2, Week 8).
    registered.append(register_sector_radar_job(scheduler, db_path))
    # Options analytics (every 5 min, market hours) → options_metrics + index GEX onto
    # market_state: GEX (mean-revert vs trending tape), max-pain, PCR over the nearest
    # expiry, plus PCR-extreme / max-pain-drift signals (Week 23; gated dispatch).
    registered.append(register_options_job(scheduler, db_path))
    # Smart-money composite (daily 20:00): weighted FII cash + FII derivatives + DII +
    # tiered block-deal flow → smart_money_daily (Week 22; gated dispatch). The
    # participant_oi collector (19:00, endpoints.yaml) feeds the derivatives leg.
    registered.append(register_smart_money_job(scheduler, db_path))
    # Short-squeeze detector (daily 18:40): deep 15d fall + rising delivery + price turning
    # up → short_squeeze candidates (Week 24; SLB confirmer deferred). Gated dispatch.
    registered.append(register_short_squeeze_job(scheduler, db_path))
    # Daily Sweep strategy — SHELVED 2026-06-21. Net-of-cost backtest (data-bug + ₹40k×5 sizing +
    # Step-2 1H-retracement fix) shows NO edge: every parameter set is PF < 1 (best 0.98, net
    # −₹2.8k, not regime-robust). Live job disabled — see strategy/daily_sweep/README.md postmortem.
    # registered.append(register_daily_sweep_job(scheduler, db_path))
    # Weekly positional book — Monday 09:00 IST: rank (validated lean + slow-data overlay) →
    # snapshot to weekly_ranking (UI history) → ATR-size + paper-rebalance the basket (forward edge).
    registered.append(register_weekly_positional_job(scheduler, db_path))
    # Global / cross-asset context (US, VIX, DXY, crude, gold, Asia + fresh Indian indices via
    # Yahoo) — 08:15 & 17:15 IST. Phase-1 inputs the NSE feeds don't carry.
    registered.append(register_global_markets_job(scheduler, db_path))
    # News-impact scores (news_engine) persisted nightly 17:00 IST → news_daily (Phase-2 signal
    # that was computed on-demand but never stored).
    registered.append(register_news_score_job(scheduler, db_path))
    # Task 5B — retroactively fetch news for the day's >5% movers (every 15 min, mkt hours).
    registered.append(register_move_news_job(scheduler, db_path))
    # Task 2 — macro-theme / basket-rotation detector (every 15 min, mkt hours).
    registered.append(register_basket_job(scheduler, db_path))
    # Task 3 — isolated small-cap EOD momentum track (paper-only), 19:35 after bhavcopy.
    registered.append(register_smallcap_job(scheduler, db_path))
    # P2 — entity-classified bulk/block deal signals (16:30 after deal collector).
    registered.append(register_large_deal_signals_job(scheduler, db_path))
    # P3 — promoter/insider signal classification (22:15 after insider collector).
    registered.append(register_promoter_signals_job(scheduler, db_path))
    # Intraday high-conviction synthesis engine — 09:10 IST (post pre-open) → conviction_daily.
    registered.append(register_conviction_job(scheduler, db_path))
    # Keep the daily-candle table fresh from the official bhavcopy (19:30 IST, post-EOD).
    registered.append(register_bhavcopy_candle_job(scheduler, db_path))
    # Morning brief: 09:00 IST on trading days — one Telegram message with global
    # cues, GIFT-implied open, regime + posture, overnight events, expiry and
    # Nifty pivot S/R (Phase 2, Week 9).
    registered.append(register_morning_brief(scheduler, db_path))
    # EOD summary: 18:00 IST on trading days — close, sectors, today's signals + paper
    # activity, tomorrow's events. Sends directly (ungated), the bookend to the brief (W27).
    registered.append(register_eod_summary(scheduler, db_path))
    # Weekly paper-book digest to Telegram (Mon 08:30 IST) — pushes the P4 forward
    # track record (open positions / expectancy / R9 verdict / progress) so the loop
    # can be monitored without SSHing in (PROFITABILITY_PLAN P4).
    registered.append(register_paper_digest_job(scheduler, db_path))
    # Live watchlist: every 15 min, refreshes the dynamic watchlist (rating/news/
    # OI-spurt/52wh triggers) that the live universe adds to the F&O core (Phase 4).
    registered.append(register_watchlist_job(scheduler, db_path))
    # Nightly per-symbol levels (19:00) + delivery conviction (18:30) off
    # bhavcopy — reference levels loaded at 08:45 and shown in alerts (Week 13).
    registered.append(register_levels_job(scheduler, db_path))
    registered.append(register_delivery_job(scheduler, db_path))
    # Nightly fundamentals quality score (18:00) — gates + nudges signals (Week 14).
    registered.append(register_quality_job(scheduler, db_path))
    # Nightly financial-strength screens (18:05) → stock_strength: Piotroski F-score
    # + balance-sheet score (interest coverage / current ratio / D-E / debt trend) +
    # distress flags, for the pre-buy conviction card (PROFITABILITY_PLAN R7+R8).
    registered.append(register_strength_job(scheduler, db_path))
    # Daily 18:45 factor snapshot → factor_snapshot feature store: every ranking
    # engine's score + composite + sector rank + regime, point-in-time, plus a
    # top-up of matured forward-return labels (P8 — the ML/factor-research matrix).
    registered.append(register_factor_snapshot_job(scheduler, db_path))
    # Daily 19:15 paper-trade pass (after candles + the 18:45 snapshot): drives each
    # ranking strategy's positions (lean / qvm / buyscore_adaptive) through the
    # state machine into paper_book, so the forward, out-of-sample track record
    # advances every session with no human in the loop (P4 — accumulate a quarter,
    # then promote). Writes paper_book only; dispatches nothing live (G12 stays shut).
    registered.append(register_paper_trade_job(scheduler, db_path))
    # Per-minute intraday pattern scan (inside bar, divergence, S/R proximity…)
    # → patterns table, feeding the confidence scorer (Week 15).
    registered.append(register_patterns_job(scheduler, db_path))
    # Nightly 19:30 stock-profile roll-up → stock_profile_daily (ML archive, Wk 15).
    registered.append(register_profile_builder(scheduler, db_path))
    # Announcement priority classification (10 min) + credit-rating extractor →
    # raw_rating_actions + credit_* alerts (20 min) — Phase 5, Week 16.
    registered.append(register_classify_job(scheduler, db_path))
    registered.append(register_rating_job(scheduler, db_path))
    # Results calendar (20:00) → pending_events from board meetings + cadence, and
    # pre-event screen (20:15) → earnings_setups snapshot + "buy-rumor priced in"
    # Telegram flags for results due in the next few days — Phase 5, E2.
    registered.append(register_calendar_job(scheduler, db_path))
    # Consensus estimates (20:05) — between the calendar (which decides who is
    # about to report) and the pre-screen (which stamps estimates onto setups):
    # Moneycontrol + Yahoo for the upcoming reporters; manual CSV outranks both.
    registered.append(register_consensus_job(scheduler, db_path))
    registered.append(register_pre_screen_job(scheduler, db_path))
    # Pre-event run-up risk (20:20) → pre_event_run_5d/10d + days_to_event +
    # the BUY_RUMOR_IN_PLAY/.../SELL_RUMOR_IN_PLAY class on indicator_live,
    # feeding the dispatcher's buy-rumor long gate — Week 18.2/18.3.
    registered.append(register_pre_event_risk_job(scheduler, db_path))
    # Psychological state classifier: every 5 min during market hours, tags each
    # symbol FOMO_EUPHORIA/BUY_RUMOR/CAPITULATION/... on indicator_live + Redis;
    # the confidence scorer (Layer 7) and alert line read it — Week 19.
    registered.append(register_state_classifier_job(scheduler, db_path))
    # Psychology alerts on top of the classifier (Week 20/21): FOMO-warning +
    # capitulation-watch (every minute, 2h dedup) and the stop-hunt / liquidity-grab
    # detector (every 5 min on 5-min closes). Compute + log; gated dispatch (G12).
    registered.append(register_exhaustion_detector_job(scheduler, db_path))
    registered.append(register_stop_hunt_detector_job(scheduler, db_path))
    # Analyst broker recommendations: every 30 min (trading days, 08:30–15:35),
    # Moneycontrol broker-recos feed → raw_analyst_ratings + tier-1
    # upgrade/downgrade alerts — Week 18.7/18.8.
    registered.append(register_analyst_ratings_job(scheduler, db_path))
    # Angel-sourced live quotes for the ~337 top-1000 names NSE's free feed
    # doesn't serve (ETFs + sub-index stocks) → raw_equity_quotes under
    # LIVE_INDEX, so their intraday 1-min candles build live like the rest.
    registered.append(register_angel_live_job(scheduler, db_path))
    # Financial extraction, three latency tiers → extracted_financials:
    #  - fast lane (every 1 min, market hours): full pipeline on just-filed result
    #    PDFs so a mid-session reaction is scored with the real surprise
    #  - intraday safety net (every 5 min, market hours): financial-extract any
    #    result the general parser text-extracted that the fast lane missed
    #  - nightly sweep (21:00): after-close filings + backlog
    registered.append(register_fast_result_lane(scheduler, db_path, session))
    registered.append(register_intraday_extract_runner(scheduler, db_path))
    registered.append(register_extract_runner(scheduler, db_path))
    log.info("scheduler_starting", jobs=registered)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("scheduler_stopping")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())