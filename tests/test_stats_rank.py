"""Mann-Whitney U, Cliff's delta, Hodges-Lehmann (docs/PLAN.md, section 8.1)."""

from __future__ import annotations

import itertools
import math
import random
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from epicormic.stats import cliffs_delta, hodges_lehmann, mann_whitney, u_statistic
from epicormic.stats.rank import EXACT_LIMIT, midranks

try:
    from scipy import stats as scipy_stats
except ImportError:  # pragma: no cover - exercised only without SciPy
    scipy_stats = None


def test_midranks_share_mean_rank_on_ties() -> None:
    assert midranks([10, 20, 20, 30]) == [1.0, 2.5, 2.5, 4.0]
    assert midranks([5]) == [1.0]
    assert midranks([2, 2, 2]) == [2.0, 2.0, 2.0]
    assert midranks([3, 1, 2]) == [3.0, 1.0, 2.0]


def test_u_delta_and_shift_on_hand_cases() -> None:
    assert u_statistic([1, 2, 3], [4, 5, 6]) == 9
    assert u_statistic([4, 5, 6], [1, 2, 3]) == 0
    assert u_statistic([1, 2, 3], [1, 2, 3]) == 4.5
    assert cliffs_delta([1, 2, 3], [4, 5, 6]) == 1.0
    assert cliffs_delta([4, 5, 6], [1, 2, 3]) == -1.0
    assert cliffs_delta([1, 2, 3], [1, 2, 3]) == 0.0
    assert hodges_lehmann([1, 2, 3], [4, 5, 6]) == 3.0
    assert hodges_lehmann([0, 0], [1, 3]) == 2.0


def test_u_counts_pairs_with_ties_half() -> None:
    baseline = [1.0, 2.0, 2.0, 5.0]
    current = [2.0, 3.0, 6.0]
    pairs = sum((c > b) + 0.5 * (c == b) for c in current for b in baseline)
    assert u_statistic(baseline, current) == pairs


def brute_force_exact_p(baseline: list[float], current: list[float]) -> float:
    pooled = [*baseline, *current]
    n_c = len(current)
    observed = u_statistic(baseline, current)
    product = len(baseline) * n_c
    lower = min(observed, product - observed)
    count = 0
    total = 0
    for chosen in itertools.combinations(range(len(pooled)), n_c):
        chosen_set = set(chosen)
        perm_current = [pooled[i] for i in chosen]
        perm_baseline = [pooled[i] for i in range(len(pooled)) if i not in chosen_set]
        u = u_statistic(perm_baseline, perm_current)
        if min(u, product - u) <= lower + 1e-12:
            count += 1
        total += 1
    return min(1.0, count / total)


@pytest.mark.parametrize("seed", range(12))
def test_exact_p_matches_brute_force_enumeration(seed: int) -> None:
    rng = random.Random(seed)
    n_b, n_c = rng.randrange(1, 6), rng.randrange(1, 6)
    pool = rng.sample(range(100), n_b + n_c)
    baseline = [float(x) for x in pool[:n_b]]
    current = [float(x) for x in pool[n_b:]]
    result = mann_whitney(baseline, current)
    assert result.method == "mann_whitney_exact"
    assert result.p_value == pytest.approx(brute_force_exact_p(baseline, current))


def test_exact_p_is_one_at_the_centre_and_small_at_the_extreme() -> None:
    assert mann_whitney([1, 4], [2, 3]).p_value == 1.0
    extreme = mann_whitney(list(range(10)), list(range(10, 20)))
    assert extreme.p_value == pytest.approx(2 / math.comb(20, 10))
    assert extreme.cliffs_delta == 1.0


def test_ties_or_large_samples_use_the_normal_approximation() -> None:
    tied = mann_whitney([1, 2, 2, 3], [2, 3, 4])
    assert tied.method == "mann_whitney_normal"
    large = mann_whitney(list(range(EXACT_LIMIT + 1)), [100 + i for i in range(3)])
    assert large.method == "mann_whitney_normal"
    assert mann_whitney([1, 1, 1], [1, 1]).p_value == 1.0


def test_result_fields() -> None:
    result = mann_whitney([1, 2, 3], [2, 4, 6, 8])
    assert (result.n_baseline, result.n_current) == (3, 4)
    assert result.u == u_statistic([1, 2, 3], [2, 4, 6, 8])
    assert result.cliffs_delta == pytest.approx((2 * result.u - 12) / 12)
    assert result.hodges_lehmann == hodges_lehmann([1, 2, 3], [2, 4, 6, 8])
    assert 0 < result.p_value <= 1


@pytest.mark.parametrize(
    ("baseline", "current"),
    [
        ([], [1]),
        ([1], []),
        ([float("nan")], [1]),
        ([1], [float("inf")]),
        ([True], [1]),
        (["a"], [1]),
    ],
)
def test_sample_validation(baseline: Any, current: Any) -> None:
    with pytest.raises(ValueError):
        mann_whitney(baseline, current)


@given(
    st.lists(st.floats(-1e6, 1e6, allow_nan=False), min_size=1, max_size=8),
    st.lists(st.floats(-1e6, 1e6, allow_nan=False), min_size=1, max_size=8),
)
@settings(max_examples=100)
def test_delta_is_antisymmetric_and_bounded(baseline: list[float], current: list[float]) -> None:
    delta = cliffs_delta(baseline, current)
    assert -1 <= delta <= 1
    assert cliffs_delta(current, baseline) == pytest.approx(-delta)
    assert 0 < mann_whitney(baseline, current).p_value <= 1


@pytest.mark.skipif(scipy_stats is None, reason="SciPy not installed")
def test_matches_scipy_exact_and_asymptotic() -> None:
    rng = random.Random(3)
    for _ in range(150):
        n_b, n_c = rng.randrange(1, 12), rng.randrange(1, 12)
        pool = rng.sample(range(10_000), n_b + n_c)
        baseline = [float(x) for x in pool[:n_b]]
        current = [float(x) for x in pool[n_b:]]
        mine = mann_whitney(baseline, current)
        theirs = scipy_stats.mannwhitneyu(
            current, baseline, alternative="two-sided", method="exact"
        )
        assert mine.u == pytest.approx(theirs.statistic)
        assert mine.p_value == pytest.approx(theirs.pvalue, abs=1e-12)
    for _ in range(150):
        n_b, n_c = rng.randrange(3, 30), rng.randrange(3, 30)
        baseline = [float(rng.randrange(0, 6)) for _ in range(n_b)]
        current = [float(rng.randrange(0, 7)) for _ in range(n_c)]
        mine = mann_whitney(baseline, current)
        if mine.method != "mann_whitney_normal":
            continue
        theirs = scipy_stats.mannwhitneyu(
            current, baseline, alternative="two-sided", method="asymptotic", use_continuity=True
        )
        assert mine.p_value == pytest.approx(theirs.pvalue, abs=1e-12)
