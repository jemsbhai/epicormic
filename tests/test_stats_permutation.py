"""Two-sample and stratified permutation tests (docs/PLAN.md, section 8.1)."""

from __future__ import annotations

import math
import random
from typing import Any

import pytest

from epicormic.stats import (
    cliffs_delta,
    permutation_test,
    stratified_permutation_test,
)
from epicormic.stats.permutation import ENUMERATION_LIMIT, mean_difference


def test_small_samples_are_enumerated_exactly() -> None:
    result = permutation_test([1, 2, 3, 4, 5], [6, 7, 8, 9, 10.0])
    assert result.exact and result.seed is None
    assert result.permutations == math.comb(10, 5)
    assert result.p_value == pytest.approx(2 / math.comb(10, 5))
    assert result.statistic == 5.0


def test_monte_carlo_path_is_seeded_and_reproducible() -> None:
    rng = random.Random(5)
    baseline = [rng.gauss(0, 1) for _ in range(30)]
    current = [rng.gauss(0, 1) for _ in range(30)]
    assert math.comb(60, 30) > ENUMERATION_LIMIT
    first = permutation_test(baseline, current, permutations=300, seed=9)
    second = permutation_test(baseline, current, permutations=300, seed=9)
    assert first == second
    assert not first.exact and first.seed == 9 and first.permutations == 300
    assert 1 / 301 <= first.p_value <= 1
    third = permutation_test(baseline, current, permutations=300, seed=10)
    assert third.p_value != first.p_value or third == first


def test_custom_statistic_and_validation() -> None:
    def median_difference(baseline: Any, current: Any) -> float:
        return float(sorted(current)[len(current) // 2] - sorted(baseline)[len(baseline) // 2])

    result = permutation_test([1, 2, 3], [7, 8, 9.0], statistic=median_difference)
    assert result.statistic == 6.0
    assert result.p_value == pytest.approx(4 / 20)
    with pytest.raises(ValueError, match="permutations must be"):
        permutation_test([1, 2], [3, 4], permutations=0)
    with pytest.raises(ValueError, match="at least one value"):
        permutation_test([], [1])
    with pytest.raises(ValueError, match="finite numbers"):
        permutation_test([1, float("nan")], [1])
    assert mean_difference([1, 3], [4, 8]) == 4.0


def test_stratified_observed_statistic_is_the_mean_delta() -> None:
    strata = [([1, 2, 3, 4], [5, 6, 7, 8]), ([101, 102, 103, 104], [105, 106, 107, 108])]
    result = stratified_permutation_test(strata, permutations=499, seed=1)
    assert result.statistic == 1.0
    assert result.deltas == (1.0, 1.0)
    assert result.strata == 2 and result.seed == 1 and result.permutations == 499
    assert result.p_value == pytest.approx(1 / 500, abs=0.01)
    assert result.deltas == tuple(cliffs_delta(b, c) for b, c in strata)


def test_stratification_ignores_between_probe_location_differences() -> None:
    # within every stratum both groups hold the same values; the strata are
    # far apart, so a pooled comparison would be misled while the
    # stratified statistic is exactly zero and the p-value is exactly one.
    strata = [
        ([0.0, 1.0, 2.0], [2.0, 0.0, 1.0]),
        ([100.0, 101.0, 102.0], [101.0, 102.0, 100.0]),
        ([-50.0, -49.0], [-49.0, -50.0]),
    ]
    result = stratified_permutation_test(strata, permutations=200, seed=0)
    assert result.statistic == 0.0
    assert result.p_value == 1.0
    pooled_baseline = [v for b, _ in strata for v in b]
    pooled_current = [v for _, c in strata for v in c]
    assert cliffs_delta(pooled_baseline, pooled_current) == 0.0


def test_stratification_with_exchangeable_groups_across_shifted_strata() -> None:
    rng = random.Random(21)
    strata = []
    for location in (0.0, 1000.0, -1000.0, 50.0):
        values = [location + rng.gauss(0, 1) for _ in range(16)]
        rng.shuffle(values)
        strata.append((values[:8], values[8:]))
    result = stratified_permutation_test(strata, permutations=400, seed=2)
    assert result.p_value > 0.02


def test_null_rejection_rate_is_calibrated() -> None:
    rng = random.Random(100)
    alpha = 0.05
    rejections = 0
    replications = 400
    for _ in range(replications):
        strata = []
        for _ in range(2):
            values = [rng.gauss(0, 1) for _ in range(16)]
            strata.append((values[:8], values[8:]))
        result = stratified_permutation_test(strata, permutations=199, seed=rng.randrange(1 << 30))
        rejections += result.p_value <= alpha
    rate = rejections / replications
    assert 0.02 <= rate <= 0.08, rate


def test_power_at_a_large_effect() -> None:
    rng = random.Random(7)
    rejections = 0
    replications = 20
    for _ in range(replications):
        strata = []
        for _ in range(12):
            baseline = [rng.gauss(0, 1) for _ in range(10)]
            current = [rng.gauss(1.0, 1) for _ in range(10)]
            strata.append((baseline, current))
        result = stratified_permutation_test(strata, permutations=199, seed=rng.randrange(1 << 30))
        rejections += result.p_value <= 0.05
    assert rejections / replications >= 0.9


def test_stratified_validation() -> None:
    with pytest.raises(ValueError, match="at least one stratum"):
        stratified_permutation_test([])
    with pytest.raises(ValueError, match="at least one value"):
        stratified_permutation_test([([], [1.0])])
    with pytest.raises(ValueError, match="permutations must be"):
        stratified_permutation_test([([1.0], [2.0])], permutations=-1)
