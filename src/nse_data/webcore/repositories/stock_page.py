"""Per-stock page reads — every SQL behind the /stocks symbol tabs.

One repository for the whole stock cockpit (overview strip + results / events /
filings / activity / flow tabs). Defensive by design: the dashboard must render
against any DB vintage, so every read tolerates a missing table (fresh deploy,
dev laptop) by returning empty — the UI shows "no data", never a 500.
"""
from __future__ import annotations

import sqlite3

from ..introspect import table_exists


class StockPageRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    # ---- helpers -----------------------------------------------------------
    def _rows(self, table: str, sql: str, args: tuple = ()) -> list[dict]:
        if not table_exists(self.conn, table):
            return []
        try:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]
        except sqlite3.OperationalError:
            return []   # column drift on an old DB — degrade, don't 500

    def _one(self, table: str, sql: str, args: tuple = ()) -> dict | None:
        rows = self._rows(table, sql + " LIMIT 1", args)
        return rows[0] if rows else None

    # ---- overview ----------------------------------------------------------
    def profile_latest(self, symbol: str) -> dict | None:
        return self._one(
            "stock_profile_daily",
            "SELECT session_date, quality_score, trend_regime, momentum_state, "
            "delivery_conviction_score, rsi_14, sma_50, sma_200, high_52w, low_52w "
            "FROM stock_profile_daily WHERE symbol=? ORDER BY session_date DESC",
            (symbol,),
        )

    def surveillance(self, symbol: str) -> list[dict]:
        out = []
        for table, label in (("raw_surveillance_asm_st", "ASM-ST"),
                             ("raw_surveillance_asm_lt", "ASM-LT"),
                             ("raw_surveillance_gsm", "GSM")):
            row = self._one(
                table,
                f"SELECT stage, as_on FROM {table} WHERE symbol=? ORDER BY as_on DESC",
                (symbol,),
            )
            if row:
                out.append({"list": label, "stage": row["stage"], "as_on": row["as_on"]})
        return out

    def price_band(self, symbol: str) -> dict | None:
        return self._one(
            "raw_price_bands",
            "SELECT band, remarks FROM raw_price_bands WHERE symbol=?",
            (symbol,),
        )

    def sector_state_latest(self, sector_name: str) -> dict | None:
        return self._one(
            "sector_state",
            "SELECT sector_name, rs_rank, rs_trend, sector_return_pct, as_of "
            "FROM sector_state WHERE sector_name=? ORDER BY as_of DESC",
            (sector_name,),
        )

    def next_pending_event(self, symbol: str) -> dict | None:
        return self._one(
            "pending_events",
            "SELECT event_type, expected_date, confidence, status FROM pending_events "
            "WHERE symbol=? AND status='upcoming' AND expected_date >= date('now') "
            "ORDER BY expected_date",
            (symbol,),
        )

    # ---- results -----------------------------------------------------------
    def financials(self, symbol: str, limit: int = 12) -> list[dict]:
        return self._rows(
            "extracted_financials",
            "SELECT period_ending, scope, revenue_cr, pat_cr, pbt_cr, other_income_cr, "
            "net_interest_income_cr, operating_profit_cr, provisions_cr, "
            "gross_npa_pct, net_npa_pct, eps_basic, growth_json, narrative_json, "
            "extract_confidence, strategy, broadcast_dt, "
            "profit_on_sale_of_investments_cr, "
            "total_income_cr, finance_cost_cr, depreciation_cr, tax_cr, "
            "cost_of_materials_cr, purchases_of_stock_cr, change_in_inventory_cr, "
            "employee_cost_cr, other_expenses_cr, exceptional_items_cr, "
            "pbt_before_exceptional_cr, "
            "current_tax_cr, deferred_tax_cr, share_of_associates_cr, "
            "other_comprehensive_income_cr, operating_expenses_cr, "
            "gross_npa_cr, net_npa_cr, cet1_ratio, return_on_assets, "
            "interest_earned_cr, interest_expended_cr "
            "FROM extracted_financials WHERE symbol=? "
            "ORDER BY period_ending DESC, scope LIMIT ?",
            (symbol, limit),
        )

    def estimates(self, symbol: str, limit: int = 20) -> list[dict]:
        return self._rows(
            "consensus_estimates",
            "SELECT period_ending, source, rev_est_cr, pat_est_cr, eps_est, "
            "nii_est_cr, nim_est_pct, as_of FROM consensus_estimates "
            "WHERE symbol=? ORDER BY period_ending DESC, source LIMIT ?",
            (symbol, limit),
        )

    def ratings(self, symbol: str, limit: int = 8) -> list[dict]:
        return self._rows(
            "raw_rating_actions",
            "SELECT broadcast_dt, agency, action, old_rating, new_rating, "
            "instrument_type, is_junk_downgrade FROM raw_rating_actions "
            "WHERE symbol=? ORDER BY broadcast_dt DESC LIMIT ?",
            (symbol, limit),
        )

    # ---- events ------------------------------------------------------------
    def pending_events(self, symbol: str, limit: int = 10) -> list[dict]:
        return self._rows(
            "pending_events",
            "SELECT event_type, expected_date, source, confidence, status, purpose "
            "FROM pending_events WHERE symbol=? ORDER BY expected_date DESC LIMIT ?",
            (symbol, limit),
        )

    def board_meetings(self, symbol: str, limit: int = 8) -> list[dict]:
        return self._rows(
            "raw_board_meetings",
            "SELECT meeting_date, purpose, details FROM raw_board_meetings "
            "WHERE symbol=? ORDER BY meeting_date DESC LIMIT ?",
            (symbol, limit),
        )

    def corporate_actions(self, symbol: str, limit: int = 10) -> list[dict]:
        return self._rows(
            "raw_corporate_actions",
            "SELECT subject, ex_date, record_date, face_value FROM raw_corporate_actions "
            "WHERE symbol=? ORDER BY ex_date DESC LIMIT ?",
            (symbol, limit),
        )

    def earnings_setup_latest(self, symbol: str) -> dict | None:
        return self._one(
            "earnings_setups",
            "SELECT event_date, run_up_5d, run_up_10d, run_up_class, implied_move_pct, "
            "iv_atm, pcr, oi_buildup_class, sector_rank, fundamental_class, "
            "expectation_proxy_score, bfsi_macro_risk FROM earnings_setups "
            "WHERE symbol=? ORDER BY event_date DESC",
            (symbol,),
        )

    # ---- filings -----------------------------------------------------------
    def announcements(self, symbol: str, limit: int = 25) -> list[dict]:
        return self._rows(
            "raw_announcements",
            "SELECT broadcast_dt, subject, priority, sentiment, pdf_status, "
            "attachment_url, pdf_type, extraction_strategy "
            "FROM raw_announcements WHERE symbol=? AND deleted_at IS NULL "
            "ORDER BY created_at DESC LIMIT ?",
            (symbol, limit),
        )

    # ---- activity ----------------------------------------------------------
    def signals(self, symbol: str, limit: int = 25) -> list[dict]:
        return self._rows(
            "signals",
            "SELECT s.id, s.signal_type, s.detected_at, s.price, s.confidence, "
            "s.direction, s.dispatched, o.ret_1d, o.ret_eod, o.hit_t1, o.hit_sl "
            "FROM signals s LEFT JOIN signal_outcomes o ON o.signal_id = s.id "
            "WHERE s.symbol=? ORDER BY s.detected_at DESC LIMIT ?",
            (symbol, limit),
        )

    def paper_trades(self, symbol: str, limit: int = 25) -> list[dict]:
        return self._rows(
            "paper_trades",
            "SELECT signal_type, direction, entry_time, entry_price, exit_time, "
            "exit_price, exit_reason, net_pnl, status FROM paper_trades "
            "WHERE symbol=? ORDER BY entry_time DESC LIMIT ?",
            (symbol, limit),
        )

    def backtest_summary(self, symbol: str) -> dict | None:
        return self._one(
            "backtest_trades",
            "SELECT COUNT(*) AS trades, SUM(pnl_net) AS pnl_net, "
            "SUM(CASE WHEN pnl_net > 0 THEN 1 ELSE 0 END) AS wins "
            "FROM backtest_trades WHERE symbol=?",
            (symbol,),
        )

    # ---- flow --------------------------------------------------------------
    def large_deals(self, symbol: str, limit: int = 12) -> list[dict]:
        return self._rows(
            "raw_large_deals",
            "SELECT deal_date, deal_type, client_name, buy_sell, quantity, "
            "weighted_avg_price FROM raw_large_deals WHERE symbol=? "
            "ORDER BY deal_date DESC LIMIT ?",
            (symbol, limit),
        )

    def insider_trades(self, symbol: str, limit: int = 12) -> list[dict]:
        return self._rows(
            "raw_insider_trading",
            "SELECT intimation_date, acquirer_name, acquirer_category, "
            "transaction_type, no_of_securities, value_in_rupees, mode_of_acquisition "
            "FROM raw_insider_trading WHERE symbol=? "
            "ORDER BY intimation_date DESC LIMIT ?",
            (symbol, limit),
        )

    def shareholding(self, symbol: str, limit: int = 5) -> list[dict]:
        return self._rows(
            "raw_shareholding_pattern",
            "SELECT qe_date, promoter_pct, public_pct FROM raw_shareholding_pattern "
            "WHERE symbol=? ORDER BY qe_date DESC LIMIT ?",
            (symbol, limit),
        )

    def delivery_trend(self, symbol: str, limit: int = 20) -> list[dict]:
        rows = self._rows(
            "delivery_conviction",
            "SELECT session_date, delivery_ratio, delivery_ratio_5d_avg, "
            "delivery_trend, delivery_conviction_score FROM delivery_conviction "
            "WHERE symbol=? ORDER BY session_date DESC LIMIT ?",
            (symbol, limit),
        )
        if rows:
            return rows
        return self._rows(   # fallback: raw bhavcopy delivery %
            "raw_bhavcopy_cm",
            "SELECT date AS session_date, delivery_pct AS delivery_ratio "
            "FROM raw_bhavcopy_cm WHERE symbol=? AND series='EQ' "
            "ORDER BY date DESC LIMIT ?",
            (symbol, limit),
        )

    def oi_latest(self, symbol: str) -> dict | None:
        return self._one(
            "raw_oi_spurts",
            "SELECT as_of, latest_oi, prev_oi, change_in_oi, avg_oi_pct, volume "
            "FROM raw_oi_spurts WHERE symbol=? ORDER BY as_of DESC",
            (symbol,),
        )

    def volatility_latest(self, symbol: str) -> dict | None:
        return self._one(
            "raw_volatility",
            "SELECT date, daily_volatility, annualised_volatility FROM raw_volatility "
            "WHERE symbol=? ORDER BY date DESC",
            (symbol,),
        )

    # ---- intraday moves (research view) ------------------------------------
    def intraday_moves(self, symbol: str, limit: int = 200) -> list[dict]:
        """Significant single-day intraday moves from the open (gap excluded),
        constant/sustained ones first (sort by consistency)."""
        join = ("LEFT JOIN move_causes c ON c.symbol=e.symbol AND c.date=e.date"
                if table_exists(self.conn, "move_causes") else "")
        cause_cols = (", c.category AS cause_category, c.cause_summary, c.source_url AS cause_url, "
                      "c.source_date AS cause_date, c.confidence AS cause_confidence, "
                      "c.regime AS cause_regime" if join else "")
        return self._rows(
            "intraday_move_events",
            f"SELECT e.date, e.direction, e.move_pct, e.up_move_pct, e.down_move_pct, "
            f"e.net_pct, e.gap_pct, e.move_start, e.move_end, e.leg_minutes, e.consistency, "
            f"e.max_retrace_pct, e.pattern{cause_cols} "
            f"FROM intraday_move_events e {join} WHERE e.symbol=? "
            f"ORDER BY e.consistency DESC, ABS(e.move_pct) DESC LIMIT ?",
            (symbol, limit),
        )

    def universe_grade(self, symbol: str) -> str | None:
        row = self._one(
            "tradeable_universe",
            "SELECT grade FROM tradeable_universe WHERE symbol=?", (symbol,))
        return row["grade"] if row else None

    def intraday_move_candidates(self, symbol: str, limit: int = 3000) -> list[dict]:
        """All candidate causes (every source) for the symbol's moves — the audit
        trail behind each move, for the UI expand."""
        return self._rows(
            "move_cause_candidates",
            "SELECT date, source, cause_type, event_date, summary, url "
            "FROM move_cause_candidates WHERE symbol=? "
            "ORDER BY date DESC, weight DESC, event_date DESC LIMIT ?",
            (symbol, limit),
        )
