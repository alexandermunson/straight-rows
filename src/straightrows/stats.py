"""ME 457 statistics (Ch. 2-3), pure numpy/stdlib so the pinned dependency set stays unchanged.

Implements docs/ME457_METHODS.md items 1 (Pearson r + r_crit), 2 (Chauvenet) and 6 (t confidence interval).
All cutoffs come from theory (n, alpha), not fitted constants. See the cautions in that doc: 1 Hz samples are
autocorrelated, so callers must pass block means (`block_means`) and not raw samples to the interval/correlation tools.
"""
from __future__ import annotations

import math
import warnings
from statistics import NormalDist

import numpy as np

_ND = NormalDist()


def z_quantile(p: float) -> float:
    return _ND.inv_cdf(p)


# ---------------------------------------------------------------- Student t (no scipy)
def _betacf(a, b, x, itmax=300, eps=3e-14):
    """Continued fraction for the incomplete beta function (Numerical Recipes, modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d; d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c; c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d; d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c; c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _betainc(a, b, x):
    """Regularised incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    bt = math.exp(lbt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: float) -> float:
    x = df / (df + t * t)
    tail = 0.5 * _betainc(df / 2.0, 0.5, x)       # P(T > |t|)
    return 1.0 - tail if t >= 0 else tail


def t_quantile(p: float, df: float) -> float:
    """Inverse CDF of Student t by bisection (df may be non-integer, e.g. Welch-Satterthwaite)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    if df > 1e6:
        return z_quantile(p)
    if p == 0.5:
        return 0.0
    if p < 0.5:
        return -t_quantile(1.0 - p, df)
    lo, hi = 0.0, 1.0
    while t_cdf(hi, df) < p:
        hi *= 2.0
        if hi > 1e12:
            break
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12 * max(1.0, hi):
            break
    return 0.5 * (lo + hi)


# ---------------------------------------------------------------- Ch. 3: Pearson r and its significance threshold
def pearson_r(x, y):
    """(r, n) over pairs where both are finite. r is nan when n < 3 or either series is constant."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    n = int(ok.sum())
    if n < 3:
        return float("nan"), n
    xs, ys = x[ok] - x[ok].mean(), y[ok] - y[ok].mean()
    den = math.sqrt(float((xs * xs).sum() * (ys * ys).sum()))
    if den == 0.0:
        return float("nan"), n
    return float(np.clip((xs * ys).sum() / den, -1.0, 1.0)), n


def r_crit(n: int, alpha: float = 0.05) -> float:
    """Smallest |r| that is significant for n pairs, two-sided at `alpha` (the course's 'min r' table).
    From t = r*sqrt((n-2)/(1-r^2)): r_crit = t / sqrt(n - 2 + t^2) with t the (1 - alpha/2) quantile, df = n - 2."""
    if n < 3:
        return 1.0
    t = t_quantile(1.0 - alpha / 2.0, n - 2)
    return t / math.sqrt(n - 2 + t * t)


def correlate(x, y, alpha: float = 0.05) -> dict:
    """Pearson r with its significance verdict at the sample count actually available."""
    r, n = pearson_r(x, y)
    rc = r_crit(n, alpha)
    return {"r": r, "n": n, "r_crit": rc, "significant": bool(np.isfinite(r) and abs(r) >= rc)}


def block_means(x, block: int) -> np.ndarray:
    """Means of consecutive blocks of `block` samples (nan-aware; short tail dropped). Use before CI/correlation so
    autocorrelated 10 s bins are not counted as independent observations."""
    x = np.asarray(x, float)
    k = len(x) // max(block, 1)
    if k == 0:
        return np.array([])
    m = x[: k * block].reshape(k, block)
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)          # all-nan blocks (machine parked or absent) are dropped below
        out = np.nanmean(np.where(np.isfinite(m), m, np.nan), axis=1)
    return out[np.isfinite(out)]


# ---------------------------------------------------------------- Ch. 3: Chauvenet's criterion
def chauvenet_z(n: int) -> float:
    """Two-sided z0 such that P(|z| > z0) = 1/(2n); samples beyond it are rejected."""
    if n < 2:
        return float("inf")
    return z_quantile(1.0 - 1.0 / (4.0 * n))


def chauvenet_keep(x, max_iter: int = 10) -> np.ndarray:
    """Boolean keep-mask over finite values of x (False for nan and rejected). Iterates: reject, recompute mean and
    std on the survivors, re-test with the new n, until nothing more is rejected."""
    x = np.asarray(x, float)
    keep = np.isfinite(x)
    for _ in range(max_iter):
        v = x[keep]
        n = len(v)
        if n < 3:
            break
        sd = v.std(ddof=1)
        if sd == 0.0:
            break
        bad = keep & (np.abs(x - v.mean()) / sd > chauvenet_z(n))
        if not bad.any():
            break
        keep &= ~bad
    return keep


# ---------------------------------------------------------------- Ch. 2: confidence interval of a mean (t)
def mean_ci(x, conf: float = 0.95) -> dict:
    """Two-sided t interval of the mean of finite values: {mean, lo, hi, half, n, se}. n < 2 gives nan bounds."""
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n == 0:
        return {"mean": float("nan"), "lo": float("nan"), "hi": float("nan"), "half": float("nan"), "n": 0, "se": float("nan")}
    m = float(x.mean())
    if n < 2:
        return {"mean": m, "lo": float("nan"), "hi": float("nan"), "half": float("nan"), "n": n, "se": float("nan")}
    se = float(x.std(ddof=1) / math.sqrt(n))
    half = t_quantile(0.5 + conf / 2.0, n - 1) * se
    return {"mean": m, "lo": m - half, "hi": m + half, "half": half, "n": n, "se": se}


# ---------------------------------------------------------------- Ch. 3: least squares, S_yx, standardized residuals
def linfit(x, y, alpha: float = 0.05) -> dict:
    """Least-squares line y = a + b x over finite pairs. Returns slope, intercept, r2, syx (standard error of the
    estimate, n-2 dof), slope CI half-width at 1-alpha, and whether the slope CI excludes 0. nan fields when n < 3."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    n = int(ok.sum())
    nan = float("nan")
    res = {"n": n, "slope": nan, "intercept": nan, "r2": nan, "syx": nan, "slope_half": nan, "slope_signif": False}
    if n < 3:
        return res
    xs, ys = x[ok], y[ok]
    sxx = float(((xs - xs.mean()) ** 2).sum())
    if sxx == 0.0:
        return res
    b = float(((xs - xs.mean()) * (ys - ys.mean())).sum() / sxx)
    a = float(ys.mean() - b * xs.mean())
    sse = float(((ys - (a + b * xs)) ** 2).sum())
    sst = float(((ys - ys.mean()) ** 2).sum())
    syx = math.sqrt(sse / (n - 2))
    half = t_quantile(1.0 - alpha / 2.0, n - 2) * syx / math.sqrt(sxx)
    res.update(slope=b, intercept=a, r2=(1.0 - sse / sst) if sst > 0 else nan, syx=syx, slope_half=half,
               slope_signif=bool(abs(b) > half))
    return res


def std_residuals(x, y, fit: dict, floor: float = 0.0) -> np.ndarray:
    """Standardized residuals e / S_yx of (x, y) against a fit from `linfit` (S_yx floored at `floor`)."""
    s = max(fit["syx"], floor) if np.isfinite(fit["syx"]) else float("nan")
    return (np.asarray(y, float) - (fit["intercept"] + fit["slope"] * np.asarray(x, float))) / s
