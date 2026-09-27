"""Subjective Logic opinion (docs/PLAN.md, section 10.1)."""

from __future__ import annotations

import math
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from epicormic.opinion import Opinion, opinion_from_evidence, opinion_from_log_evidence, vacuous


def test_worked_example_from_the_plan() -> None:
    opinion = opinion_from_evidence(1.734, 240)
    assert opinion.belief == pytest.approx(0.628994, abs=5e-7)
    assert opinion.disbelief == pytest.approx(0.362742, abs=5e-7)
    assert opinion.uncertainty == pytest.approx(0.00826446, abs=5e-9)
    assert opinion.projected_probability == pytest.approx(0.633126, abs=5e-7)
    assert opinion.base_rate == 0.5


def test_unit_evidence_projects_to_the_base_rate() -> None:
    for base_rate in (0.1, 0.5, 0.9):
        opinion = opinion_from_evidence(1.0, 50, base_rate=base_rate)
        assert opinion.belief / (opinion.belief + opinion.disbelief) == pytest.approx(base_rate)


def test_no_observations_is_vacuous_and_infinite_evidence_is_certain() -> None:
    assert opinion_from_evidence(123.0, 0) == vacuous(0.5)
    assert vacuous(0.3) == Opinion(0.0, 0.0, 1.0, 0.3)
    assert vacuous(0.3).projected_probability == 0.3
    certain = opinion_from_evidence(math.inf, 10)
    assert certain.disbelief == 0.0 and certain.belief == pytest.approx(10 / 12)
    assert opinion_from_log_evidence(math.inf, 10) == certain
    faint = opinion_from_evidence(1e-300, 10)
    assert faint.belief == pytest.approx(0.0, abs=1e-12)
    assert faint.disbelief == pytest.approx(10 / 12)


@given(
    st.floats(-50, 50),
    st.floats(-50, 50),
    st.integers(0, 10_000),
    st.floats(0.01, 0.99),
)
def test_properties(log_a: float, log_b: float, observations: int, base_rate: float) -> None:
    first = opinion_from_log_evidence(log_a, observations, base_rate=base_rate)
    assert first.belief + first.disbelief + first.uncertainty == pytest.approx(1.0)
    assert 0 <= first.projected_probability <= 1
    second = opinion_from_log_evidence(log_b, observations, base_rate=base_rate)
    if log_a <= log_b:
        assert first.belief <= second.belief + 1e-12
    more = opinion_from_log_evidence(log_a, observations + 1, base_rate=base_rate)
    assert more.uncertainty <= first.uncertainty


@pytest.mark.parametrize(
    ("args", "kwargs"),
    [
        ((0.0, -1), {}),
        ((0.0, True), {}),
        ((0.0, 5), {"base_rate": 0.0}),
        ((0.0, 5), {"base_rate": 1.0}),
        ((0.0, 5), {"prior_weight": 0.0}),
        ((math.nan, 5), {}),
        ((-math.inf, 5), {}),
    ],
)
def test_log_evidence_validation(args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        opinion_from_log_evidence(*args, **kwargs)


def test_evidence_validation() -> None:
    with pytest.raises(ValueError):
        opinion_from_evidence(0.0, 5)
    with pytest.raises(ValueError):
        opinion_from_evidence(math.nan, 5)


def test_opinion_validation() -> None:
    with pytest.raises(ValueError):
        Opinion(0.5, 0.5, 0.5, 0.5)
    with pytest.raises(ValueError):
        Opinion(1.5, -0.5, 0.0, 0.5)
    with pytest.raises(TypeError):
        Opinion("a", 0.0, 1.0, 0.5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Opinion(True, 0.0, 0.0, 0.5)
