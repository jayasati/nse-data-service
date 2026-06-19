"""P7 — training-set assembly for the ML probability layer.

There is no stored snapshot history yet (`stock_score` is empty), so we BOOTSTRAP the
training matrix the same way the backtest does: replay every factor engine at a grid
of historical as-of dates and pair each (date, symbol) row with its realised forward
60-day **sector-excess, net-of-cost** return. Features = the engines' cross-sectional
scores (a missing/sparse engine → neutral 50). Target = did that excess beat 0.

Leakage is the whole ballgame here, so:
  * every feature is point-in-time (the engines only read data dated <= as_of);
  * the train/test split is TEMPORAL with an embargo >= the forward horizon, so no
    training row's outcome window overlaps any test row's as_of date.
"""
from __future__ import annotations

import numpy as np

# Factor engines that expose score_universe(conn, syms, as_of_ep, sector_of). Order
# fixes the feature-vector layout (and the persisted model's column meaning).
FEATURE_ENGINES = (
    ("quality", "nse_data.research.quality_engine"),
    ("valuation", "nse_data.research.valuation_engine"),
    ("momentum", "nse_data.research.momentum_engine"),
    ("turnaround", "nse_data.research.turnaround_engine"),
    ("liquidity", "nse_data.research.liquidity_engine"),
    ("surprise", "nse_data.research.surprise_engine"),
)
NEUTRAL = 50.0          # fill for a name an engine didn't score (absence != signal)


def _load_engines():
    import importlib
    return [(name, importlib.import_module(mod)) for name, mod in FEATURE_ENGINES]


def build_dataset(conn, grades=("A core", "B tradeable"), step=20, lookback=600,
                  horizon=60, cost=0.50):
    """Replay engines over a date grid -> (X, y, fwd, dates, symbols, feature_names).

    X: (n,6) engine scores; y: 1 if forward sector-excess net-of-cost > 0; fwd: that
    excess (%, continuous); dates/symbols: per-row as-of date & ticker (for the split
    and leaderboards)."""
    from nse_data.research import edge_stats
    from nse_data.fundamentals.sectors import sector_class_for

    engines = _load_engines()
    feature_names = [n for n, _ in FEATURE_ENGINES]
    grade_of = {r[0]: r[1] for r in conn.execute("SELECT symbol, grade FROM tradeable_universe")}
    syms = [s for s, g in grade_of.items() if g in grades]

    sec_cache = {}
    def sector_of(s):
        if s not in sec_cache:
            sec_cache[s] = sector_class_for(s).value
        return sec_cache[s]

    bench_for, bseries, _ = edge_stats.bench_resolver(conn)
    series_cache = {}
    def series(s):
        if s not in series_cache:
            series_cache[s] = edge_stats.load_series(conn, s)
        return series_cache[s]

    cal = conn.execute(
        "SELECT date(ts,'unixepoch','+05:30') d, MAX(ts) FROM raw_intraday_candles "
        "WHERE symbol='NIFTYBEES' AND interval='day' GROUP BY d ORDER BY d").fetchall()
    end = len(cal) - horizon - 1
    idxs = list(range(max(0, end - lookback), end, step))
    asof = [(cal[i][0], cal[i][1]) for i in idxs]

    rows_X, rows_y, rows_f, rows_d, rows_s = [], [], [], [], []
    for d, ep in asof:
        scored = {name: mod.score_universe(conn, syms, ep, sector_of) for name, mod in engines}
        for sym in syms:
            ss, dates = series(sym)
            if d not in dates:
                continue
            i = dates.index(d)
            if i + horizon >= len(dates):
                continue
            feats = [scored[name].get(sym, {}).get("score", NEUTRAL) for name in feature_names]
            if all(v == NEUTRAL for v in feats):     # no real factor -> uninformative row
                continue
            xd = dates[i + horizon]
            bser, bdates = bseries[bench_for(sector_of(sym))]
            b0 = edge_stats.close_on_or_before(bser, bdates, d)
            b1 = edge_stats.close_on_or_before(bser, bdates, xd)
            bench = (b1 / b0 - 1) * 100 if (b0 and b1) else 0.0
            net = (ss[xd] / ss[d] - 1) * 100 - bench - cost
            rows_X.append(feats)
            rows_y.append(1 if net > 0 else 0)
            rows_f.append(net)
            rows_d.append(d)
            rows_s.append(sym)

    return (np.asarray(rows_X, float), np.asarray(rows_y, float), np.asarray(rows_f, float),
            np.asarray(rows_d), np.asarray(rows_s), feature_names)


def load_store_dataset(conn, horizon=60):
    """Read the matured rows of the `factor_snapshot` feature store directly →
    (X, y, fwd, dates, symbols, feature_names). The preferred source ONCE the store
    has accrued enough labelled dates (no engine replay needed). Same column layout
    as build_dataset so train_ml treats them identically."""
    import numpy as np
    feature_names = [n for n, _ in FEATURE_ENGINES]
    col = f"fwd_excess_{horizon}"
    rows_X, rows_y, rows_f, rows_d, rows_s = [], [], [], [], []
    sql = (f"SELECT snapshot_date, symbol, {','.join(feature_names)}, {col} "
           f"FROM factor_snapshot WHERE {col} IS NOT NULL ORDER BY snapshot_date")
    for r in conn.execute(sql):
        d, sym = r[0], r[1]
        feats = [r[2 + i] if r[2 + i] is not None else NEUTRAL for i in range(len(feature_names))]
        if all(v == NEUTRAL for v in feats):
            continue
        net = r[2 + len(feature_names)]
        rows_X.append(feats)
        rows_y.append(1 if net > 0 else 0)
        rows_f.append(net)
        rows_d.append(d)
        rows_s.append(sym)
    return (np.asarray(rows_X, float), np.asarray(rows_y, float), np.asarray(rows_f, float),
            np.asarray(rows_d), np.asarray(rows_s), feature_names)


def temporal_split(dates, frac=0.70, embargo_dates=3):
    """Boolean (train_mask, test_mask) split by unique as-of date, with an embargo gap
    of `embargo_dates` distinct as-of dates dropped between train and test so a train
    row's forward window cannot reach into the test period."""
    uniq = sorted(set(dates.tolist()))
    cut = int(len(uniq) * frac)
    train_dates = set(uniq[:cut])
    test_dates = set(uniq[cut + embargo_dates:])
    train = np.array([d in train_dates for d in dates])
    test = np.array([d in test_dates for d in dates])
    return train, test
