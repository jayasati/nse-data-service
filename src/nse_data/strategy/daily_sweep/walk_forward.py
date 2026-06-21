"""Step 12 — walk-forward across market regimes + a parameter sweep.

Scan each symbol ONCE (full history), then bucket the resolved trades into regime windows and
report metrics per regime / per symbol / pooled. A small parameter grid (swing k × RR) surfaces
the combo that maximises Profit Factor while keeping drawdown low. Runs on the server, where the
full multi-year candle history lives.
"""
from __future__ import annotations

from dataclasses import replace

from .backtest import _metrics, run_backtest
from .config import DailySweepConfig

REGIMES = {
    "covid_2020": ("2020-02-01", "2020-06-30"),
    "bull_2021": ("2021-01-01", "2021-12-31"),
    "sideways_2022": ("2022-01-01", "2022-12-31"),
    "bull_2023_24": ("2023-01-01", "2024-12-31"),
    "current_2025+": ("2025-01-01", "2030-12-31"),
}


def _bucket(trades, start: str, end: str):
    return [t for t in trades if start <= t.setup.entry_time.date().isoformat() <= end]


def walk_forward(conn, symbols, config: DailySweepConfig, regimes=REGIMES) -> dict:
    """{symbol: {'all': metrics, 'by_regime': {name: metrics}}} + a pooled regime view."""
    per_symbol, all_trades = {}, []
    for sym in symbols:
        try:
            rep = run_backtest(conn, sym, config)
        except Exception as e:  # noqa: BLE001 — one bad symbol shouldn't sink the sweep
            per_symbol[sym] = {"error": str(e)[:60]}
            continue
        trades = rep["trades"]
        all_trades += trades
        per_symbol[sym] = {"all": rep["metrics"],
                           "by_regime": {n: _metrics(_bucket(trades, s, e))
                                         for n, (s, e) in regimes.items()}}
    pooled = {n: _metrics(_bucket(all_trades, s, e)) for n, (s, e) in regimes.items()}
    pooled["__all__"] = _metrics(all_trades)
    return {"per_symbol": per_symbol, "pooled": pooled, "n_trades": len(all_trades)}


def param_sweep(conn, symbols, base: DailySweepConfig,
                ks=(3, 5), rrs=(2.0, 3.0)) -> list[dict]:
    """Grid over m5 swing-k × RR target → pooled PF / win / maxDD / net per combo (Step 12 opt)."""
    out = []
    for k in ks:
        for rr in rrs:
            cfg = replace(base, m5_swing_k=k, rr_target=rr)
            trades = []
            for sym in symbols:
                try:
                    trades += run_backtest(conn, sym, cfg)["trades"]
                except Exception:  # noqa: BLE001
                    continue
            m = _metrics(trades)
            out.append({"m5_swing_k": k, "rr": rr, "n": m["n"], "win_rate": m["win_rate"],
                        "profit_factor": m["profit_factor"], "expectancy": m.get("expectancy"),
                        "max_dd": m.get("max_drawdown_pct"), "net": m.get("total_net_pct")})
    return out
