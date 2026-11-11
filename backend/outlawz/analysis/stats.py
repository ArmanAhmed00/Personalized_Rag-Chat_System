"""Small statistics, written out so there's no scipy dependency.

Distributions come from two special functions computed with standard continued fractions:

* the regularised incomplete beta function, for Student's t and F;
* the regularised incomplete gamma function, for chi-square.

Each test returns plain numbers; ``tests/test_stats.py`` checks them against reference values.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

_EPS = 3e-16
_TINY = 1e-300
_MAX_ITER = 500


# ---------------------------------------------------------------- special functions


def _betacf(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > _TINY else _TINY)
    h = d
    for m in range(1, _MAX_ITER + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > _TINY else _TINY)
        c = 1.0 + aa / c
        c = c if abs(c) > _TINY else _TINY
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > _TINY else _TINY)
        c = 1.0 + aa / c
        c = c if abs(c) > _TINY else _TINY
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_front = (
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    )
    front = math.exp(log_front)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def gammainc_upper(a: float, x: float) -> float:
    """Regularised upper incomplete gamma function Q(a, x)."""
    if x <= 0.0:
        return 1.0
    log_front = -x + a * math.log(x) - math.lgamma(a)
    if x < a + 1.0:
        term = total = 1.0 / a
        ap = a
        for _ in range(_MAX_ITER * 4):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * _EPS:
                break
        return max(0.0, 1.0 - total * math.exp(log_front))
    b = x + 1.0 - a
    c = 1.0 / _TINY
    d = 1.0 / b
    h = d
    for i in range(1, _MAX_ITER * 4):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = 1.0 / (d if abs(d) > _TINY else _TINY)
        c = b + an / c
        c = c if abs(c) > _TINY else _TINY
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return math.exp(log_front) * h


# ---------------------------------------------------------------- distributions


def t_sf(t: float, df: float) -> float:
    """P(T > t) for Student's t with ``df`` degrees of freedom."""
    if math.isinf(t):
        return 0.0 if t > 0 else 1.0
    tail = 0.5 * betainc(df / 2.0, 0.5, df / (df + t * t))
    return tail if t >= 0 else 1.0 - tail


def t_two_sided(t: float, df: float) -> float:
    return min(1.0, 2.0 * t_sf(abs(t), df))


def f_sf(f: float, df1: float, df2: float) -> float:
    """P(F > f) for the F distribution."""
    if f <= 0:
        return 1.0
    return betainc(df2 / 2.0, df1 / 2.0, df2 / (df2 + df1 * f))


def chi2_sf(x: float, df: float) -> float:
    """P(X > x) for chi-square with ``df`` degrees of freedom."""
    return gammainc_upper(df / 2.0, x / 2.0)


def normal_two_sided(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2.0))


# ---------------------------------------------------------------- tests


@dataclass
class TTest:
    t: float
    df: float
    p: float
    mean_a: float
    mean_b: float
    n_a: int
    n_b: int
    difference: float


def welch_t(a: Sequence[float], b: Sequence[float]) -> TTest:
    """Welch's two-sample t-test (doesn't assume equal variances)."""
    x, y = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    n1, n2 = len(x), len(y)
    if n1 < 2 or n2 < 2:
        raise ValueError("Each group needs at least two values.")
    m1, m2 = float(x.mean()), float(y.mean())
    v1, v2 = float(x.var(ddof=1)), float(y.var(ddof=1))
    se2 = v1 / n1 + v2 / n2
    if se2 == 0:
        raise ValueError("Both groups have no variation, so they can't be compared this way.")
    t = (m1 - m2) / math.sqrt(se2)
    df = se2**2 / ((v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1))
    return TTest(t, df, t_two_sided(t, df), m1, m2, n1, n2, m1 - m2)


def rank(values: Sequence[float] | np.ndarray) -> np.ndarray:
    """Average ranks (1-based), ties share the mean of their positions."""
    data = np.asarray(values, dtype=float)
    order = np.argsort(data, kind="mergesort")
    ranks = np.empty(len(data), dtype=float)
    sorted_values = data[order]
    i = 0
    while i < len(data):
        j = i
        while j + 1 < len(data) and sorted_values[j + 1] == sorted_values[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


@dataclass
class RankTest:
    u: float
    z: float
    p: float


def mann_whitney(a: Sequence[float], b: Sequence[float]) -> RankTest:
    """Mann–Whitney U (the rank check): normal approximation with tie and continuity
    corrections."""
    n1, n2 = len(a), len(b)
    if n1 < 1 or n2 < 1:
        raise ValueError("Each group needs at least one value.")
    ranks = rank([*a, *b])
    u1 = float(ranks[:n1].sum() - n1 * (n1 + 1) / 2.0)
    n = n1 + n2
    _, counts = np.unique(np.asarray([*a, *b], dtype=float), return_counts=True)
    tie = float(((counts**3) - counts).sum())
    mean = n1 * n2 / 2.0
    var = n1 * n2 / 12.0 * ((n + 1) - tie / (n * (n - 1))) if n > 1 else 0.0
    if var <= 0:
        return RankTest(u1, 0.0, 1.0)
    diff = u1 - mean
    z = (diff - math.copysign(0.5, diff) if diff else 0.0) / math.sqrt(var)
    return RankTest(u1, z, normal_two_sided(z))


@dataclass
class Anova:
    f: float
    df_between: int
    df_within: int
    p: float
    eta_squared: float


def one_way_anova(groups: Sequence[Sequence[float]]) -> Anova:
    arrays = [np.asarray(g, dtype=float) for g in groups if len(g)]
    k = len(arrays)
    n = sum(len(g) for g in arrays)
    if k < 2 or n - k < 1:
        raise ValueError("ANOVA needs at least two groups and more values than groups.")
    grand = float(np.concatenate(arrays).mean())
    ss_between = float(sum(len(g) * (g.mean() - grand) ** 2 for g in arrays))
    ss_within = float(sum(((g - g.mean()) ** 2).sum() for g in arrays))
    df_b, df_w = k - 1, n - k
    if ss_within == 0:
        raise ValueError("Every group has no variation, so ANOVA can't be computed.")
    f = (ss_between / df_b) / (ss_within / df_w)
    total = ss_between + ss_within
    return Anova(f, df_b, df_w, f_sf(f, df_b, df_w), ss_between / total if total else 0.0)


@dataclass
class ChiSquare:
    chi2: float
    df: int
    p: float
    cramers_v: float
    expected_min: float


def chi_square_independence(table: np.ndarray) -> ChiSquare:
    """Pearson's chi-square test of independence (no continuity correction)."""
    observed = np.asarray(table, dtype=float)
    if observed.ndim != 2 or min(observed.shape) < 2:
        raise ValueError("Chi-square needs at least two categories on each side.")
    total = observed.sum()
    if total == 0:
        raise ValueError("The table is empty.")
    expected = observed.sum(axis=1, keepdims=True) * observed.sum(axis=0, keepdims=True) / total
    if (expected == 0).any():
        raise ValueError("A whole row or column is empty, so chi-square can't be computed.")
    chi2 = float(((observed - expected) ** 2 / expected).sum())
    df = (observed.shape[0] - 1) * (observed.shape[1] - 1)
    v = math.sqrt(chi2 / (total * (min(observed.shape) - 1)))
    return ChiSquare(chi2, df, chi2_sf(chi2, df), v, float(expected.min()))


@dataclass
class Fit:
    n: int
    r: float
    r_squared: float
    p: float
    slope: float
    intercept: float


def pearson(x: Sequence[float] | np.ndarray, y: Sequence[float] | np.ndarray) -> Fit:
    """Pearson's r with its p-value, plus the least-squares line y = slope·x + intercept."""
    a, b = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    n = len(a)
    if n < 3:
        raise ValueError("At least three pairs of values are needed.")
    dx, dy = a - a.mean(), b - b.mean()
    sxx, syy, sxy = float((dx * dx).sum()), float((dy * dy).sum()), float((dx * dy).sum())
    if sxx == 0 or syy == 0:
        raise ValueError(
            "One of the columns has no variation, so there's no relationship to measure."
        )
    r = max(-1.0, min(1.0, sxy / math.sqrt(sxx * syy)))
    slope = sxy / sxx
    intercept = float(b.mean() - slope * a.mean())
    if abs(r) >= 1.0:
        p = 0.0
    else:
        t = r * math.sqrt((n - 2) / (1 - r * r))
        p = t_two_sided(t, n - 2)
    return Fit(n, r, r * r, p, slope, intercept)


def spearman(x: Sequence[float] | np.ndarray, y: Sequence[float] | np.ndarray) -> Fit:
    return pearson(rank(x), rank(y))


def describe_p(p: float) -> str:
    """Plain words for a p-value."""
    if p < 0.001:
        return "very unlikely to be chance (p < 0.001)"
    if p < 0.05:
        return f"unlikely to be chance (p = {p:.3f})"
    return f"plausibly just chance (p = {p:.2f})"
