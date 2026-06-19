"""Unit tests for the ML probability layer (P7) — deterministic, no DB.

Pins the invariants the training script relies on: the logistic scorer actually
learns a separable signal, probabilities are well-formed, persistence round-trips,
and the OOS metrics (AUC / IC / decile lift) compute the textbook values.
"""
from __future__ import annotations

import numpy as np

from nse_data.ml import eval as mleval
from nse_data.ml.scorer import LogisticScorer, RidgeRanker, make_model, make_ranker


def _separable(n=600, seed=0):
    """Two factors where higher values => higher P(label=1), plus noise."""
    rng = np.random.default_rng(seed)
    X = rng.normal(50, 15, size=(n, 2))
    logit = 0.06 * (X[:, 0] - 50) + 0.04 * (X[:, 1] - 50)
    p = 1 / (1 + np.exp(-logit))
    y = (rng.random(n) < p).astype(float)
    return X, y


def test_logistic_learns_separable_signal():
    X, y = _separable()
    m = make_model("logistic", feature_names=["a", "b"]).fit(X, y)
    proba = m.predict_proba(X)
    assert proba.min() >= 0.0 and proba.max() <= 1.0
    assert mleval.auc(y, proba) > 0.65          # clearly better than chance
    # both factors point the right way (positive weight on a higher-is-better signal)
    imp = m.importances()
    assert imp["a"] > 0 and imp["b"] > 0
    assert abs(imp["a"]) >= abs(imp["b"])        # 'a' is the stronger driver


def test_save_load_roundtrip(tmp_path):
    X, y = _separable()
    m = make_model("logistic", feature_names=["a", "b"]).fit(X, y)
    p = tmp_path / "model.json"
    m.save(str(p))
    m2 = LogisticScorer.load(str(p))
    assert np.allclose(m.predict_proba(X), m2.predict_proba(X))


def test_auc_perfect_and_inverted():
    y = np.array([0, 0, 1, 1.0])
    assert mleval.auc(y, np.array([0.1, 0.2, 0.8, 0.9])) == 1.0
    assert mleval.auc(y, np.array([0.9, 0.8, 0.2, 0.1])) == 0.0
    assert mleval.auc(y, np.array([0.5, 0.5, 0.5, 0.5])) == 0.5   # all ties -> chance


def test_decile_lift_orders_low_to_high():
    score = np.arange(100, dtype=float)
    fwd = np.arange(100, dtype=float)              # forward return tracks score exactly
    lifts = mleval.decile_lift(score, fwd, n=10)
    assert all(lifts[i] <= lifts[i + 1] for i in range(9))
    assert lifts[-1] > lifts[0]


def test_ic_sign():
    score = np.arange(50, dtype=float)
    assert mleval.spearman_ic(score, score) > 0.99
    assert mleval.spearman_ic(score, score[::-1]) < -0.99


def test_unknown_model_kind_raises():
    import pytest
    with pytest.raises(ValueError):
        make_model("transformer")
    with pytest.raises(ValueError):
        make_ranker("transformer")


# ---- ranking model (continuous excess target) ----

def _excess(n=600, seed=1):
    """Forward excess driven mostly by factor a, weakly by b, plus noise."""
    rng = np.random.default_rng(seed)
    X = rng.normal(50, 15, size=(n, 2))
    y = 0.4 * (X[:, 0] - 50) + 0.1 * (X[:, 1] - 50) + rng.normal(0, 5, n)
    return X, y


def test_ridge_ranker_recovers_ranking():
    X, y = _excess()
    m = make_ranker("ridge", feature_names=["a", "b"]).fit(X, y)
    pred = m.predict(X)
    assert mleval.spearman_ic(pred, y) > 0.4        # ranks the excess
    imp = m.importances()
    assert imp["a"] > 0 and abs(imp["a"]) > abs(imp["b"])   # 'a' is the stronger driver


def test_ridge_ranker_roundtrip(tmp_path):
    X, y = _excess()
    m = make_ranker("ridge", feature_names=["a", "b"]).fit(X, y)
    p = tmp_path / "rank.json"
    m.save(str(p))
    assert np.allclose(m.predict(X), RidgeRanker.load(str(p)).predict(X))


def test_rank_summary_shape():
    X, y = _excess()
    m = make_ranker("ridge").fit(X, y)
    s = mleval.rank_summary(m.predict(X), y)
    assert s["rank_ic"] > 0.4 and "decile_lift" in s and len(s["decile_lift"]) == 10
