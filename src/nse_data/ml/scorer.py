"""P7 — ML probability layer. Outputs P(stock outperforms its sector over the next
60 trading days) from the validated factor scores.

The arch doc names XGBoost/LightGBM as the eventual target, but the discipline is
"do NOT replace explainable scoring — ride on top of it". So the DEFAULT model here
is a dependency-free, numpy-only standardised **logistic regression**: it is itself
explainable (a signed weight per factor = the data-driven weights the composite has
been hand-holding), it cannot overfit the still-thin history, and it is exactly the
baseline you must beat before a gradient-booster is justified.

The model is PLUGGABLE: if scikit-learn / xgboost / lightgbm are installed,
`make_model('hgb'|'xgb'|'lgbm')` returns a GBM wrapper with the same fit/predict_proba
interface — no caller change. Until then `make_model('logistic')` (the default) runs
everywhere with only numpy. Models persist to / load from plain JSON (logistic) so a
trained scorer is inspectable and diffable.
"""
from __future__ import annotations

import json

import numpy as np


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


class LogisticScorer:
    """Standardised L2-regularised logistic regression, full-batch gradient descent.
    Explainable: `.w` is one signed coefficient per feature (on standardised inputs,
    so directly comparable in magnitude)."""

    kind = "logistic"

    def __init__(self, l2: float = 1.0, lr: float = 0.5, epochs: int = 400, feature_names=None):
        self.l2 = l2
        self.lr = lr
        self.epochs = epochs
        self.feature_names = list(feature_names) if feature_names else None
        self.mu = None          # feature means (standardisation)
        self.sd = None          # feature stds
        self.w = None           # weights on standardised features
        self.b = 0.0            # bias

    def fit(self, X, y):
        X = np.asarray(X, float)
        y = np.asarray(y, float)
        self.mu = X.mean(axis=0)
        self.sd = X.std(axis=0)
        self.sd[self.sd == 0] = 1.0
        Xs = (X - self.mu) / self.sd
        n, d = Xs.shape
        self.w = np.zeros(d)
        self.b = 0.0
        for _ in range(self.epochs):
            p = _sigmoid(Xs @ self.w + self.b)
            err = p - y
            gw = Xs.T @ err / n + self.l2 * self.w / n
            gb = err.mean()
            self.w -= self.lr * gw
            self.b -= self.lr * gb
        return self

    def predict_proba(self, X):
        X = np.asarray(X, float)
        Xs = (X - self.mu) / self.sd
        return _sigmoid(Xs @ self.w + self.b)

    def importances(self) -> dict:
        """{feature: signed standardised weight}, largest |weight| first."""
        names = self.feature_names or [f"f{i}" for i in range(len(self.w))]
        pairs = sorted(zip(names, self.w.tolist()), key=lambda kv: -abs(kv[1]))
        return dict(pairs)

    # --- persistence (plain JSON; the model IS its weights) ---
    def to_dict(self) -> dict:
        return {"kind": self.kind, "l2": self.l2, "feature_names": self.feature_names,
                "mu": self.mu.tolist(), "sd": self.sd.tolist(),
                "w": self.w.tolist(), "b": float(self.b)}

    @classmethod
    def from_dict(cls, d: dict) -> "LogisticScorer":
        m = cls(l2=d.get("l2", 1.0), feature_names=d.get("feature_names"))
        m.mu = np.asarray(d["mu"], float)
        m.sd = np.asarray(d["sd"], float)
        m.w = np.asarray(d["w"], float)
        m.b = float(d["b"])
        return m

    def save(self, path: str):
        with open(path, "w") as fh:
            json.dump(self.to_dict(), fh, indent=2)

    @classmethod
    def load(cls, path: str) -> "LogisticScorer":
        with open(path) as fh:
            return cls.from_dict(json.load(fh))


class RidgeRanker:
    """Standardised ridge regression that predicts the CONTINUOUS forward excess
    return (the institutional "learn the ranking, not BUY/SELL" target). Explainable:
    `.w` is a signed coefficient per factor. Closed-form (exact), numpy-only.
    `.predict(X)` returns a score whose ORDER is the ranking (units = excess %)."""

    kind = "ridge"

    def __init__(self, l2: float = 1.0, feature_names=None):
        self.l2 = l2
        self.feature_names = list(feature_names) if feature_names else None
        self.mu = self.sd = self.w = None
        self.b = 0.0

    def fit(self, X, y):
        X = np.asarray(X, float)
        y = np.asarray(y, float)
        self.mu = X.mean(axis=0)
        self.sd = X.std(axis=0)
        self.sd[self.sd == 0] = 1.0
        Xs = (X - self.mu) / self.sd
        self.b = float(y.mean())
        d = Xs.shape[1]
        A = Xs.T @ Xs + self.l2 * np.eye(d)
        self.w = np.linalg.solve(A, Xs.T @ (y - self.b))
        return self

    def predict(self, X):
        X = np.asarray(X, float)
        Xs = (X - self.mu) / self.sd
        return Xs @ self.w + self.b

    def importances(self) -> dict:
        names = self.feature_names or [f"f{i}" for i in range(len(self.w))]
        return dict(sorted(zip(names, self.w.tolist()), key=lambda kv: -abs(kv[1])))

    def to_dict(self) -> dict:
        return {"kind": self.kind, "l2": self.l2, "feature_names": self.feature_names,
                "mu": self.mu.tolist(), "sd": self.sd.tolist(),
                "w": self.w.tolist(), "b": float(self.b)}

    @classmethod
    def from_dict(cls, d: dict) -> "RidgeRanker":
        m = cls(l2=d.get("l2", 1.0), feature_names=d.get("feature_names"))
        m.mu = np.asarray(d["mu"], float)
        m.sd = np.asarray(d["sd"], float)
        m.w = np.asarray(d["w"], float)
        m.b = float(d["b"])
        return m

    def save(self, path: str):
        with open(path, "w") as fh:
            json.dump(self.to_dict(), fh, indent=2)

    @classmethod
    def load(cls, path: str) -> "RidgeRanker":
        with open(path) as fh:
            return cls.from_dict(json.load(fh))


class _SklearnRegressor:
    """Wrapper giving a sklearn-style REGRESSOR the RidgeRanker interface (.predict /
    .importances / .save) — the GBM ranking path (HistGradientBoosting / XGB / LGBM)."""

    def __init__(self, est, kind, feature_names=None):
        self.est = est
        self.kind = kind
        self.feature_names = list(feature_names) if feature_names else None

    def fit(self, X, y):
        self.est.fit(np.asarray(X, float), np.asarray(y, float))
        return self

    def predict(self, X):
        return self.est.predict(np.asarray(X, float))

    def importances(self) -> dict:
        imp = getattr(self.est, "feature_importances_", None)
        if imp is None:
            return {}
        names = self.feature_names or [f"f{i}" for i in range(len(imp))]
        return dict(sorted(zip(names, imp.tolist()), key=lambda kv: -kv[1]))

    def save(self, path: str):
        import pickle
        p = path[:-5] + ".pkl" if path.endswith(".json") else path + ".pkl"
        with open(p, "wb") as fh:
            pickle.dump({"est": self.est, "kind": self.kind,
                         "feature_names": self.feature_names}, fh)


def make_ranker(kind: str = "ridge", feature_names=None, **kw):
    """Factory for the RANKING (continuous-excess) target. 'ridge' (default, numpy-only)
    or a GBM regressor ('hgb'=sklearn HistGradientBoostingRegressor, 'xgb', 'lgbm') when
    installed — same .predict/.importances/.save interface as RidgeRanker."""
    kind = kind.lower()
    if kind == "ridge":
        return RidgeRanker(feature_names=feature_names, **kw)
    if kind == "hgb":
        from sklearn.ensemble import HistGradientBoostingRegressor  # type: ignore
        return _SklearnRegressor(HistGradientBoostingRegressor(**kw), "hgb", feature_names)
    if kind == "xgb":
        from xgboost import XGBRegressor  # type: ignore
        return _SklearnRegressor(XGBRegressor(**kw), "xgb", feature_names)
    if kind == "lgbm":
        from lightgbm import LGBMRegressor  # type: ignore
        return _SklearnRegressor(LGBMRegressor(**kw), "lgbm", feature_names)
    raise ValueError(f"unknown ranker kind {kind!r} (use ridge|hgb|xgb|lgbm)")


class _SklearnScorer:
    """Thin wrapper giving any sklearn-style classifier the same interface. Used for
    the GBM upgrade path (HistGradientBoosting / XGBoost / LightGBM) once installed."""

    def __init__(self, est, kind, feature_names=None):
        self.est = est
        self.kind = kind
        self.feature_names = list(feature_names) if feature_names else None

    def fit(self, X, y):
        self.est.fit(np.asarray(X, float), np.asarray(y, float))
        return self

    def predict_proba(self, X):
        return self.est.predict_proba(np.asarray(X, float))[:, 1]

    def importances(self) -> dict:
        imp = getattr(self.est, "feature_importances_", None)
        if imp is None:
            return {}
        names = self.feature_names or [f"f{i}" for i in range(len(imp))]
        return dict(sorted(zip(names, imp.tolist()), key=lambda kv: -kv[1]))

    def save(self, path: str):
        """GBMs aren't JSON-expressible — pickle alongside, swapping the .json suffix."""
        import pickle
        p = path[:-5] + ".pkl" if path.endswith(".json") else path + ".pkl"
        with open(p, "wb") as fh:
            pickle.dump({"est": self.est, "kind": self.kind,
                         "feature_names": self.feature_names}, fh)


def make_model(kind: str = "logistic", feature_names=None, **kw):
    """Factory. 'logistic' (default, numpy-only, always available) or a GBM
    ('hgb'=sklearn HistGradientBoosting, 'xgb'=XGBoost, 'lgbm'=LightGBM) when the
    library is installed — else raises with a clear hint."""
    kind = kind.lower()
    if kind == "logistic":
        return LogisticScorer(feature_names=feature_names, **kw)
    if kind == "hgb":
        from sklearn.ensemble import HistGradientBoostingClassifier  # type: ignore
        return _SklearnScorer(HistGradientBoostingClassifier(**kw), "hgb", feature_names)
    if kind == "xgb":
        from xgboost import XGBClassifier  # type: ignore
        return _SklearnScorer(XGBClassifier(eval_metric="logloss", **kw), "xgb", feature_names)
    if kind == "lgbm":
        from lightgbm import LGBMClassifier  # type: ignore
        return _SklearnScorer(LGBMClassifier(**kw), "lgbm", feature_names)
    raise ValueError(f"unknown model kind {kind!r} (use logistic|hgb|xgb|lgbm)")
