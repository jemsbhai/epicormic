"""Decimal-string encoding for evidence numbers (docs/PLAN.md, D6)."""

from __future__ import annotations

import math
import re
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from epicormic._decimal import SIGNIFICANT, decimal_str, encode_evidence, parse_decimal_str

PLAIN = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]*[1-9])?$")


def significant_digits(text: str) -> int:
    """Count digits between the first and last non-zero digit, inclusive."""

    return len(text.lstrip("-").replace(".", "").strip("0"))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.734, "1.734"),
        (0.00826446, "0.00826446"),
        (0.5, "0.5"),
        (20.0, "20"),
        (123456789.0, "123457000"),
        (1e-9, "0.000000001"),
        (0.6289939, "0.628994"),
        (1 / 3, "0.333333"),
        (2 / 3, "0.666667"),
        (-0.1234567, "-0.123457"),
        (0.0, "0"),
        (-0.0, "0"),
        (1e21, "1000000000000000000000"),
        (240, "240"),
        (10**30, "1" + "0" * 30),
        (Decimal("0.1234565"), "0.123456"),
        (Decimal("0.1234575"), "0.123458"),
        (Decimal("2.5"), "2.5"),
    ],
)
def test_known_encodings(value: Any, expected: str) -> None:
    assert decimal_str(value) == expected


def test_significant_digits_parameter() -> None:
    assert decimal_str(1 / 3, significant=3) == "0.333"
    assert decimal_str(123456.0, significant=2) == "120000"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), Decimal("NaN")])
def test_non_finite_values_are_rejected(value: Any) -> None:
    with pytest.raises(ValueError):
        decimal_str(value)


@pytest.mark.parametrize("value", [True, "1.5", None, [1.0]])
def test_non_numbers_are_rejected(value: Any) -> None:
    with pytest.raises(TypeError):
        decimal_str(value)


@given(st.floats(allow_nan=False, allow_infinity=False))
def test_encoding_is_plain_bounded_and_round_trips(value: float) -> None:
    text = decimal_str(value)
    assert PLAIN.match(text), text
    assert "e" not in text.lower()
    assert significant_digits(text) <= SIGNIFICANT
    parsed = float(parse_decimal_str(text))
    if value == 0:
        assert parsed == 0
    else:
        assert math.isclose(parsed, value, rel_tol=1e-5), (value, text)


@given(st.integers())
def test_integers_encode_exactly(value: int) -> None:
    assert decimal_str(value) == str(value)
    assert parse_decimal_str(decimal_str(value)) == Decimal(value)


@pytest.mark.parametrize("text", ["", "abc", "nan", "inf", "1e5x"])
def test_parse_rejects_non_decimal_strings(text: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        parse_decimal_str(text)


def test_parse_rejects_non_strings() -> None:
    with pytest.raises(TypeError):
        parse_decimal_str(1.5)  # type: ignore[arg-type]


def test_encode_evidence_structure() -> None:
    value = {
        "count": 240,
        "flag": True,
        "name": "probe-1",
        "nothing": None,
        "stats": [1.0, float("nan"), 0.1, Decimal("2.50"), (3, 4.5)],
        "nested": {"p": float("inf"), "q": 1 / 3},
    }
    encoded, non_finite = encode_evidence(value)
    assert encoded == {
        "count": 240,
        "flag": True,
        "name": "probe-1",
        "nothing": None,
        "stats": ["1", None, "0.1", "2.5", [3, "4.5"]],
        "nested": {"p": None, "q": "0.333333"},
    }
    assert non_finite == ["$.stats[1]", "$.nested.p"]
    assert isinstance(encoded["count"], int)
    assert isinstance(encoded["flag"], bool)


def test_encode_evidence_rejects_non_string_keys_and_unknown_types() -> None:
    with pytest.raises(TypeError, match=r"keys must be str at \$\.a"):
        encode_evidence({"a": {1: 2}})
    with pytest.raises(TypeError, match=r"cannot encode set at \$\.a\[0\]"):
        encode_evidence({"a": [{1, 2}]})


@given(
    st.dictionaries(
        st.text(), st.floats(allow_nan=False, allow_infinity=False) | st.integers(), max_size=8
    )
)
def test_encode_evidence_is_deterministic(value: dict[str, Any]) -> None:
    assert encode_evidence(value) == encode_evidence(value)


def test_encode_evidence_reports_non_finite_decimals_by_path() -> None:
    value = {"e": [Decimal("NaN"), Decimal("-Infinity"), Decimal("1.5")]}
    encoded, non_finite = encode_evidence(value)
    assert encoded == {"e": [None, None, "1.5"]}
    assert non_finite == ["$.e[0]", "$.e[1]"]
