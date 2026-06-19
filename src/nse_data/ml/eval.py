"""P7 — out-of-sample metrics for the ML probability layer. Framework-free (numpy).

Everything here judges a probability/score against a forward outcome the model did
NOT see in training (walk-forward test fold). The honest bar, in order of what we
trust: AUC (does a higher P rank winners above losers?), IC (rank-corr of P vs the
continuous forward excess), and decile lift (does the top-decile-by-P actually earn
more excess than the bottom?). A model only earns trust when all three agree OOS.
"""
from __future__ import annotations

import numpy as np


def auc(y_true, score) -> float:
    """Rank-based ROC AUC = P(score(pos) > score(neg)). 0.5 = no skill."""
    y = np.asarray(y_true, float)
    s = np.asarray(score, float)
    pos, neg = s[y == 1], s[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s), float)
    ranks[order] = np.arange(1, len(s) + 1)
    # average ranks for ties
    _, inv, cnt = np.unique(s, return_inverse=True, return_counts=True)
    sums = np.zeros(len(cnt))
    np.add.at(sums, inv, ranks)
    ranks = (sums / cnt)[inv]
    r_pos = ranks[y == 1].sum()
    n_p, n_n = len(pos), len(neg)
    return float((r_pos - n_p * (n_p + 1) / 2) / (n_p * n_n))


def spearman_ic(score, fwd) -> float:
    """Information coefficient: Spearman rank-corr of model score vs forward excess."""
    s = np.asarray(score, float)
    f = np.asarray(fwd, float)
    if len(s) < 3:
        return float("nan")
    rs = _rankdata(s)
    rf = _rankdata(f)
    rs -= rs.mean()
    rf -= rf.mean()
    den = np.sqrt((rs**2).sum() * (rf**2).sum())
    return float((rs * rf).sum() / den) if den else float("nan")


def _rankdata(a):
    a = np.asarray(a, float)
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(len(a), float)
    ranks[order] = np.arange(1, len(a) + 1)
    _, inv, cnt = np.unique(a, return_inverse=True, return_counts=True)
    sums = np.zeros(len(cnt))
    np.add.at(sums, inv, ranks)
    return (sums / cnt)[inv]


def decile_lift(score, fwd, n: int = 10) -> list:
    """Mean forward excess per score n-tile (low→high). Monotone-increasing + a
    positive top-minus-bottom spread = the model's score carries return signal."""
    s = np.asarray(score, float)
    f = np.asarray(fwd, float)
    order = np.argsort(s, kind="mergesort")
    f = f[order]
    out = []
    m = len(f)
    for b in range(n):
        chunk = f[b * m // n:(b + 1) * m // n]
        out.append(float(chunk.mean()) if len(chunk) else float("nan"))
    return out


def rank_summary(pred, fwd) -> dict:
    """OOS metrics for a RANKING model (continuous prediction vs forward excess):
    Rank IC is the headline, plus decile lift on the realised excess."""
    lifts = decile_lift(pred, fwd)
    valid = [x for x in lifts if x == x]
    return {
        "n": int(len(fwd)),
        "rank_ic": spearman_ic(pred, fwd),
        "decile_lift": lifts,
        "top_minus_bottom": (valid[-1] - valid[0]) if len(valid) >= 2 else float("nan"),
        "monotone": all(valid[i] <= valid[i + 1] for i in range(len(valid) - 1)) if valid else False,
    }


def summary(y_true, proba, fwd) -> dict:
    lifts = decile_lift(proba, fwd)
    valid = [x for x in lifts if x == x]
    return {
        "n": int(len(y_true)),
        "base_rate": float(np.mean(y_true)),
        "auc": auc(y_true, proba),
        "ic": spearman_ic(proba, fwd),
        "decile_lift": lifts,
        "top_minus_bottom": (valid[-1] - valid[0]) if len(valid) >= 2 else float("nan"),
        "monotone": all(valid[i] <= valid[i + 1] for i in range(len(valid) - 1)) if valid else False,
    }
