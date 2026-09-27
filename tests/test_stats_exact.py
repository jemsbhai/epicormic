"""Fisher's exact test (docs/PLAN.md, section 8.1)."""

from __future__ import annotations

import math
import random
from typing import Any

import pytest

from epicormic.stats import fisher_exact, indicator_test

try:
    from scipy import stats as scipy_stats
except ImportError:  # pragma: no cover - exercised only without SciPy
    scipy_stats = None


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        (((3, 1), (1, 3)), 0.4857142857),  # Fisher's tea tasting
        (((10, 0), (0, 10)), 1.0825088224e-05),
        (((8, 2), (1, 5)), 0.0349650350),
        (((5, 5), (5, 5)), 1.0),
        (((0, 5), (5, 0)), 0.0079365079),
        (((2, 3), (0, 4)), 0.4444444444),
    ],
)
def test_known_two_sided_p_values(table: Any, expected: float) -> None:
    assert fisher_exact(table).p_value == pytest.approx(expected, abs=1e-9)


def test_effects() -> None:
    result = fisher_exact(((8, 2), (1, 5)))
    assert result.n1 == 10 and result.n2 == 6
    assert result.risk_difference == pytest.approx(1 / 6 - 0.8)
    assert result.log_odds_ratio == pytest.approx(math.log((1.5 * 2.5) / (5.5 * 8.5)))
    assert result.table == ((8, 2), (1, 5))
    assert fisher_exact(((0, 5), (5, 0))).risk_difference == 1.0
    assert math.isfinite(fisher_exact(((0, 5), (5, 0))).log_odds_ratio)


def test_indicator_test_builds_the_table() -> None:
    result = indicator_test([1, 1, 1, 0], [0, 0, 1, 1, 0])
    assert result.table == ((3, 1), (2, 3))
    assert result.p_value == fisher_exact(((3, 1), (2, 3))).p_value
    with pytest.raises(ValueError, match="must be 0 or 1"):
        indicator_test([0.5], [1])
    with pytest.raises(ValueError, match="must be 0 or 1"):
        indicator_test([1], [2])


@pytest.mark.parametrize(
    "table",
    [((1, 2),), ((1, 2, 3), (1, 2)), ((-1, 2), (3, 4)), ((1.5, 2), (3, 4)), ((True, 2), (3, 4))],
)
def test_table_validation(table: Any) -> None:
    with pytest.raises(ValueError):
        fisher_exact(table)


def test_empty_groups_are_rejected() -> None:
    with pytest.raises(ValueError, match="at least one observation"):
        fisher_exact(((0, 0), (3, 4)))


def test_p_value_symmetry_under_row_and_column_swaps() -> None:
    p = fisher_exact(((7, 3), (2, 6))).p_value
    assert fisher_exact(((2, 6), (7, 3))).p_value == pytest.approx(p)
    assert fisher_exact(((3, 7), (6, 2))).p_value == pytest.approx(p)


@pytest.mark.skipif(scipy_stats is None, reason="SciPy not installed")
def test_matches_scipy_over_random_tables() -> None:
    rng = random.Random(11)
    for _ in range(400):
        a, b, c, d = (rng.randrange(0, 16) for _ in range(4))
        if a + b == 0 or c + d == 0:
            continue
        mine = fisher_exact(((a, b), (c, d))).p_value
        theirs = scipy_stats.fisher_exact([[a, b], [c, d]]).pvalue
        assert mine == pytest.approx(theirs, abs=1e-10), (a, b, c, d)


def test_log_choose_guard_and_value() -> None:
    from epicormic.stats.exact import _log_choose

    assert _log_choose(5, 6) == -math.inf
    assert _log_choose(5, -1) == -math.inf
    assert _log_choose(5, 2) == pytest.approx(math.log(10))
