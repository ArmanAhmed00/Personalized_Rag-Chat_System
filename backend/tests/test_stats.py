"""The hand-written statistics against reference values (computed once with SciPy, which is not
a dependency: the numbers are written out here)."""

from __future__ import annotations

import numpy as np
import pytest

from outlawz.analysis import stats

APPROX = {"rel": 1e-6, "abs": 1e-9}


@pytest.mark.parametrize(
    ("fn", "args", "expected"),
    [
        (stats.t_sf, (2.0, 10), 0.03669401738537018),
        (stats.t_two_sided, (2.228, 10), 0.05001177181711132),
        (stats.t_sf, (-1.5, 4.5), 0.8998904571719233),
        (stats.f_sf, (4.0, 2, 10), 0.052922149401344654),
        (stats.f_sf, (1.7, 3, 27.5), 0.19015278036086705),
        (stats.chi2_sf, (3.841, 1), 0.050013683763956804),
        (stats.chi2_sf, (10, 4), 0.04042768199451279),
        (stats.chi2_sf, (150, 120), 0.03307348091130466),
        (stats.chi2_sf, (0.5, 7), 0.9994464813904249),
        (stats.betainc, (2.5, 3.5, 0.4), 0.4869041915261176),
        (stats.gammainc_upper, (3.0, 2.5), 0.5438131158833297),
    ],
)
def test_distributions(fn, args, expected) -> None:  # type: ignore[no-untyped-def]
    assert fn(*args) == pytest.approx(expected, **APPROX)


def test_edges() -> None:
    assert stats.betainc(2, 3, 0) == 0 and stats.betainc(2, 3, 1) == 1
    assert stats.chi2_sf(0, 3) == 1.0
    assert stats.f_sf(0, 2, 5) == 1.0
    assert stats.t_sf(float("inf"), 5) == 0.0


A = [12.1, 14.3, 11.8, 15.2, 13.7, 12.9, 14.8, 13.1]
B = [10.4, 11.9, 12.2, 10.8, 11.5, 13.0, 10.1]


def test_welch_t() -> None:
    result = stats.welch_t(A, B)
    assert result.t == pytest.approx(3.530876671743739, **APPROX)
    assert result.df == pytest.approx(12.992401855834045, **APPROX)
    assert result.p == pytest.approx(0.0036921792947527134, rel=1e-5)
    with pytest.raises(ValueError):
        stats.welch_t([1.0], [2.0, 3.0])
    with pytest.raises(ValueError):
        stats.welch_t([1.0, 1.0], [2.0, 2.0])


def test_mann_whitney() -> None:
    result = stats.mann_whitney(A, B)
    assert result.u == 50.0
    assert result.p == pytest.approx(0.012841262337219548, rel=1e-6)
    ties = stats.mann_whitney([1, 2, 2, 3, 3, 3, 4], [2, 3, 3, 4, 5, 5])
    assert ties.u == 10.5
    assert ties.p == pytest.approx(0.13820766697402573, rel=1e-6)


def test_rank_averages_ties() -> None:
    assert list(stats.rank([10, 20, 20, 5, 30, 20])) == [2.0, 4.0, 4.0, 1.0, 6.0, 4.0]


def test_anova() -> None:
    result = stats.one_way_anova(
        [[4.2, 4.8, 5.1, 4.6, 5.0], [5.9, 6.1, 5.5, 6.4], [4.9, 5.2, 5.6, 5.3, 5.0, 5.4]]
    )
    assert result.f == pytest.approx(16.029592772030902, **APPROX)
    assert result.p == pytest.approx(0.0004081959466054222, rel=1e-6)
    assert (result.df_between, result.df_within) == (2, 12)
    assert 0 < result.eta_squared < 1


def test_chi_square() -> None:
    result = stats.chi_square_independence(np.array([[20, 15, 25], [30, 10, 20]]))
    assert result.chi2 == pytest.approx(3.5555555555555554, **APPROX)
    assert result.p == pytest.approx(0.16901331540606607, rel=1e-6)
    assert result.df == 2
    with pytest.raises(ValueError):
        stats.chi_square_independence(np.array([[1, 2, 3]]))


def test_pearson_and_line() -> None:
    fit = stats.pearson([1, 2, 3, 4, 5, 6, 7, 8], [2.1, 3.9, 6.2, 7.8, 10.1, 12.2, 13.8, 16.1])
    assert fit.r == pytest.approx(0.999419474795813, **APPROX)
    assert fit.p == pytest.approx(4.888933612558555e-10, rel=1e-4)
    assert fit.slope == pytest.approx(1.9976190476190478, **APPROX)
    assert fit.intercept == pytest.approx(0.0357142857142847, abs=1e-9)
    weak = stats.pearson([3, 1, 4, 1, 5, 9, 2, 6], [2, 7, 1, 8, 2, 8, 1, 8])
    assert weak.r == pytest.approx(0.2096553190730121, **APPROX)
    assert weak.p == pytest.approx(0.6182637176162882, rel=1e-6)
    with pytest.raises(ValueError):
        stats.pearson([1, 1, 1], [1, 2, 3])


def test_spearman() -> None:
    fit = stats.spearman([3, 1, 4, 1, 5, 9, 2, 6], [2, 7, 1, 8, 2, 8, 1, 8])
    assert fit.r == pytest.approx(0.19885368120992467, **APPROX)


def test_plain_words_for_p() -> None:
    assert stats.describe_p(0.0001).startswith("very unlikely")
    assert stats.describe_p(0.02) == "unlikely to be chance (p = 0.020)"
    assert stats.describe_p(0.4).startswith("plausibly just chance")
