"""Honest validation: Deflated Sharpe Ratio, PSR, PBO + a promotion gate (PLAN R9).

The research [R7] is unanimous: a strategy found after many trials can look profitable by
pure luck, so a raw Sharpe / single backtest is "worthless… regardless of reported
performance" (Bailey & López de Prado). This module implements the corrections:

  probabilistic_sharpe_ratio  PSR — P(true Sharpe > benchmark), correcting for sample
                              length AND non-normal returns (skew/kurtosis).
  expected_max_sharpe         E[max Sharpe] under the null from N trials — even zero-skill
                              search produces a high best-Sharpe, so this is the bar.
  deflated_sharpe_ratio       DSR = PSR with the benchmark set to E[max Sharpe] → the
                              probability the edge is real after multiple testing.
  pbo_cscv                    Probability of Backtest Overfitting via Combinatorially
                              Symmetric CV — for parameter/strategy sweeps (config×time).
  promotion_verdict           ties DSR + a sample-size gate into a promote/watch/insufficient
                              call for the P4 paper loop.

Per-trade returns are the input (a trade series); the Sharpe is per-trade, not annualised —
consistent across PSR/DSR. Dependency-free (math + statistics only; inverse-normal via
Acklam's rational approximation).
"""
from __future__ import annotations

import itertools
import math
import statistics as st

_GAMMA = 0.5772156649015329          # Euler-Mascheroni
_E = math.e


# ---- normal CDF / inverse CDF (no scipy) -----------------------------------

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse standard-normal CDF (Acklam's rational approximation)."""
    if p <= 0.0 or p >= 1.0:
        raise ValueError("p must be in (0, 1)")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p <= phigh:
        q = p - 0.5
        r = q*q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
               (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
            ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)


# ---- Sharpe + higher moments -----------------------------------------------

def sharpe(returns: list[float]) -> float | None:
    """Per-trade Sharpe = mean / population-stdev. None if < 2 obs or zero variance."""
    if len(returns) < 2:
        return None
    sd = st.pstdev(returns)
    return (st.fmean(returns) / sd) if sd > 0 else None


def _skew_kurt(returns: list[float]) -> tuple[float, float]:
    """Population skewness and (non-excess) kurtosis; (0, 3) for a normal sample."""
    n = len(returns)
    mean = st.fmean(returns)
    m2 = sum((x - mean) ** 2 for x in returns) / n
    if m2 == 0:
        return 0.0, 3.0
    m3 = sum((x - mean) ** 3 for x in returns) / n
    m4 = sum((x - mean) ** 4 for x in returns) / n
    sd = math.sqrt(m2)
    return m3 / sd**3, m4 / sd**4


def probabilistic_sharpe_ratio(returns: list[float], sr_benchmark: float = 0.0) -> float | None:
    """PSR(sr_benchmark) — probability the true Sharpe exceeds the benchmark."""
    sr = sharpe(returns)
    if sr is None:
        return None
    n = len(returns)
    g3, g4 = _skew_kurt(returns)
    denom = 1 - g3 * sr + ((g4 - 1) / 4) * sr * sr
    if denom <= 0:
        return None
    z = (sr - sr_benchmark) * math.sqrt(n - 1) / math.sqrt(denom)
    return _norm_cdf(z)


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """E[max Sharpe] across `n_trials` independent zero-skill trials (the deflation bar)."""
    if n_trials < 2 or sr_variance <= 0:
        return 0.0
    a = _norm_ppf(1 - 1.0 / n_trials)
    b = _norm_ppf(1 - 1.0 / (n_trials * _E))
    return math.sqrt(sr_variance) * ((1 - _GAMMA) * a + _GAMMA * b)


def deflated_sharpe_ratio(returns: list[float], n_trials: int,
                          sr_variance: float) -> float | None:
    """DSR — PSR against the expected-max-Sharpe bar from `n_trials` trials."""
    sr0 = expected_max_sharpe(n_trials, sr_variance)
    return probabilistic_sharpe_ratio(returns, sr_benchmark=sr0)


# ---- Probability of Backtest Overfitting (CSCV) ----------------------------

def pbo_cscv(matrix: list[list[float]], n_splits: int = 10) -> dict:
    """PBO via Combinatorially Symmetric CV over a configs×time return matrix.

    Splits time into `n_splits` (even) blocks; for every choice of half the blocks as
    in-sample, takes the IS-best config and measures its OUT-of-sample rank. PBO = the
    fraction of splits where the IS-best falls below the OOS median (logit ≤ 0). High PBO
    (→ 0.5+) ⇒ the selection is overfit. Needs ≥ 2 configs and enough columns per block.
    """
    n_cfg = len(matrix)
    if n_cfg < 2:
        raise ValueError("PBO needs ≥ 2 configurations")
    t = len(matrix[0])
    s = n_splits - (n_splits % 2)
    bs = t // s
    if bs < 2:
        raise ValueError("too few observations for the requested n_splits")
    blocks = [list(range(i * bs, (i + 1) * bs)) for i in range(s)]
    n_overfit = total = 0
    logits: list[float] = []
    for is_blocks in itertools.combinations(range(s), s // 2):
        is_cols = [c for b in is_blocks for c in blocks[b]]
        oos_cols = [c for b in range(s) if b not in is_blocks for c in blocks[b]]
        is_sr = [sharpe([matrix[n][c] for c in is_cols]) or -math.inf for n in range(n_cfg)]
        oos_sr = [sharpe([matrix[n][c] for c in oos_cols]) or -math.inf for n in range(n_cfg)]
        best = max(range(n_cfg), key=lambda n: is_sr[n])
        rank = sum(1 for v in oos_sr if v <= oos_sr[best])
        w = min(max(rank / (n_cfg + 1), 1e-6), 1 - 1e-6)
        logit = math.log(w / (1 - w))
        logits.append(logit)
        total += 1
        if logit <= 0:
            n_overfit += 1
    return {"pbo": n_overfit / total, "n_combos": total,
            "median_logit": st.median(logits)}


# ---- promotion gate (P4) ---------------------------------------------------

def promotion_verdict(returns: list[float], *, n_trials: int = 1,
                      sr_variance: float | None = None,
                      min_trades: int = 30, strong_trades: int = 100,
                      dsr_threshold: float = 0.95) -> dict:
    """Promote / watch / reject / insufficient for a strategy's net per-trade returns.

    Gate: need ≥ `min_trades` to say anything and ≥ `strong_trades` to promote (≈100 at a
    ~50% win rate); the edge must clear DSR ≥ `dsr_threshold` net of multiple testing AND
    have positive expectancy. `n_trials` = how many configs/strategies were tried (be
    honest — include shelved ones); `sr_variance` = variance of trial Sharpes (None ⇒ DSR
    can't be deflated, so it can only "watch", never "promote").
    """
    n = len(returns)
    sr = sharpe(returns)
    exp = st.fmean(returns) if n else None
    psr = probabilistic_sharpe_ratio(returns) if n >= 2 else None
    dsr = (deflated_sharpe_ratio(returns, n_trials, sr_variance)
           if (n >= 2 and sr_variance is not None and sr_variance > 0) else None)
    out = {"n": n, "sharpe": (round(sr, 3) if sr is not None else None),
           "expectancy": (round(exp, 3) if exp is not None else None),
           "psr": (round(psr, 3) if psr is not None else None),
           "dsr": (round(dsr, 3) if dsr is not None else None),
           "n_trials": n_trials}
    if n < min_trades:
        out["verdict"] = "insufficient"
    elif n < strong_trades:
        out["verdict"] = "watch"
    elif dsr is not None and dsr >= dsr_threshold and (exp or 0) > 0:
        out["verdict"] = "promote"
    elif (exp or 0) <= 0 or (dsr is not None and dsr < 0.5):
        out["verdict"] = "reject"
    else:
        out["verdict"] = "watch"
    return out
