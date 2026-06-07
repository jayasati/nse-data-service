-- Net-of-cost backtest P&L (FEATURE_CHECKLIST Phase 3, Week 10, task 10.2).
--
-- The backtester recorded only gross P&L (pnl_raw / pnl_leveraged). Phase 3
-- runs every simulated trade through costs/model.py; these columns hold the
-- result. pnl_net = pnl_raw - round-trip costs (brokerage/STT/exchange/SEBI/
-- stamp/GST/slippage) for that trade's qty.

ALTER TABLE backtest_trades ADD COLUMN pnl_net REAL;   -- per-trade net P&L
ALTER TABLE backtest_runs   ADD COLUMN pnl_net REAL;   -- run total net P&L
ALTER TABLE backtest_runs   ADD COLUMN max_dd_net REAL;  -- worst peak-to-trough on net
