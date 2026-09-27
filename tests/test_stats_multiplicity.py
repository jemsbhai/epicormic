"""Benjamini-Hochberg and Holm adjustments (docs/PLAN.md, section 8.1)."""

from __future__ import annotations

from itertools import pairwise
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from epicormic.stats import benjamini_hochberg, holm, rejected


def test_textbook_values() -> None:
    p_values = [0.01, 0.04, 0.03, 0.005]
    assert benjamini_hochberg(p_values) == pytest.approx([0.02, 0.04, 0.04, 0.02])
    assert holm(p_values) == pytest.approx([0.03, 0.06, 0.06, 0.02])


def test_edge_cases() -> None:
    assert benjamini_hochberg([]) == []
    assert holm([]) == []
    assert benjamini_hochberg([0.2]) == [0.2]
    assert holm([0.2]) == [0.2]
    assert benjamini_hochberg([1.0, 1.0]) == [1.0, 1.0]
    assert holm([0.6, 0.7]) == [1.0, 1.0]
    assert benjamini_hochberg([0.0, 0.5]) == [0.0, 0.5]


def test_rejected() -> None:
    assert rejected([0.01, 0.06, 0.05], 0.05) == [True, False, True]
    with pytest.raises(ValueError):
        rejected([0.1], 0)
    with pytest.raises(ValueError):
        rejected([0.1], 1.5)


@pytest.mark.parametrize("values", [[-0.1], [1.5], [float("nan")], ["a"], [True]])
def test_validation(values: Any) -> None:
    with pytest.raises(ValueError):
        benjamini_hochberg(values)
    with pytest.raises(ValueError):
        holm(values)


p_lists = st.lists(st.floats(0, 1, allow_nan=False), min_size=1, max_size=15)


@given(p_lists)
def test_adjusted_values_are_bounded_monotone_and_never_below_raw(values: list[float]) -> None:
    for adjust in (benjamini_hochberg, holm):
        adjusted = adjust(values)
        assert len(adjusted) == len(values)
        for raw, adj in zip(values, adjusted, strict=True):
            assert raw <= adj <= 1.0 + 1e-12
        order = sorted(range(len(values)), key=lambda index: values[index])
        sorted_adjusted = [adjusted[index] for index in order]
        assert all(a <= b + 1e-12 for a, b in pairwise(sorted_adjusted))


@given(p_lists)
def test_holm_is_at_least_as_conservative_as_bh(values: list[float]) -> None:
    for bh, hm in zip(benjamini_hochberg(values), holm(values), strict=True):
        assert bh <= hm + 1e-12
