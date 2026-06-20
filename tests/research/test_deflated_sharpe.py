"""Tests for the R9 honest-validation stats (Deflated/Probabilistic Sharpe, PBO, gate)."""
from __future__ import annotations

import math

from nse_data.research import deflated_sharpe as ds


def test_norm_cdf_ppf_roundtrip():
    assert abs(ds._norm_cdf(0.0) - 0.5) < 1e-12
    assert abs(ds._norm_ppf(0.975) - 1.959964) < 1e-4        # the classic z
    for p in (0.01, 0.25, 0.5, 0.75, 0.99):
        assert abs(ds._norm_cdf(ds._norm_ppf(p)) - p) < 1e-6


def test_sharpe():
    assert ds.sharpe([1, 2, 1, 2]) is not None
    assert ds.sharpe([5]) is None                            # < 2 obs
    assert ds.sharpe([3, 3, 3]) is None                      # zero variance


def test_psr_half_at_zero_sharpe():
    # symmetric zero-mean series → Sharpe 0 → PSR(0) = 0.5
    psr = ds.probabilistic_sharpe_ratio([1, -1] * 50)
    assert abs(psr - 0.5) < 1e-9


def test_psr_high_for_strong_consistent_edge():
    psr = ds.probabilistic_sharpe_ratio([1.0, 2.0] * 60)     # Sharpe ~3, n=120
    assert psr > 0.99


def test_expected_max_sharpe_grows_with_trials():
    assert ds.expected_max_sharpe(1, 1.0) == 0.0             # a single trial → no inflation
    assert ds.expected_max_sharpe(100, 0.0) == 0.0          # no dispersion → no bar
    e10 = ds.expected_max_sharpe(10, 1.0)
    e100 = ds.expected_max_sharpe(100, 1.0)
    assert 0 < e10 < e100                                    # more trials ⇒ higher luck bar


def test_dsr_below_psr_under_multiple_testing():
    r = [1.0, 2.0] * 60
    psr = ds.probabilistic_sharpe_ratio(r, 0.0)
    dsr = ds.deflated_sharpe_ratio(r, n_trials=50, sr_variance=1.0)
    assert dsr < psr                                         # deflation lowers the probability


def test_pbo_zero_for_a_clear_persistent_winner():
    # config 0 has a high positive mean every period; others hover at zero
    t = 20
    cfg0 = [1.1 if i % 2 == 0 else 0.9 for i in range(t)]    # mean 1.0, low vol
    cfg1 = [0.1 if i % 2 == 0 else -0.1 for i in range(t)]   # mean 0
    cfg2 = [-0.1 if i % 2 == 0 else 0.1 for i in range(t)]   # mean 0
    out = ds.pbo_cscv([cfg0, cfg1, cfg2], n_splits=10)
    assert out["pbo"] == 0.0                                 # IS-best is always OOS-best
    assert out["n_combos"] == math.comb(10, 5)


def test_promotion_gate_insufficient_watch_promote_reject():
    # < 30 trades → insufficient
    assert ds.promotion_verdict([1.0, -0.5] * 5, n_trials=3, sr_variance=0.1)["verdict"] == "insufficient"
    # 30..100 → watch
    assert ds.promotion_verdict([1.0, 0.5] * 25, n_trials=3, sr_variance=0.1)["verdict"] == "watch"
    # ≥100, strong edge, deflated → promote
    promote = ds.promotion_verdict([1.0, 0.5] * 60, n_trials=3, sr_variance=0.1)
    assert promote["verdict"] == "promote" and promote["dsr"] >= 0.95
    # ≥100 but negative expectancy → reject
    assert ds.promotion_verdict([-0.5, 0.2] * 60, n_trials=3, sr_variance=0.1)["verdict"] == "reject"


def test_promotion_cannot_promote_without_sr_variance():
    # no trial-Sharpe dispersion → DSR can't be deflated → never "promote"
    v = ds.promotion_verdict([1.0, 0.5] * 60, n_trials=3, sr_variance=None)
    assert v["dsr"] is None and v["verdict"] == "watch"
