"""Decimal-string encoding for numbers committed to node identity.

pollard's canonical identity grammar rejects floats, so every statistic
that epicormic commits to a note payload is written as a decimal string
(docs/PLAN.md, D6). The rules:

- finite floats and ``Decimal`` values become strings with at most
  ``SIGNIFICANT`` significant digits, rounded half to even, in plain
  notation (never exponent notation), with trailing zeros removed;
- integers (but not booleans) keep their native type inside structures and
  encode exactly, without rounding, when passed directly;
- non-finite values become ``None`` and their JSON path is reported;
- everything else (strings, booleans, ``None``) passes through unchanged.

The encoding is a pure function of the value, so a verdict recomputed from
the same observations encodes to the same bytes on every platform.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation
from typing import Any

SIGNIFICANT = 6

__all__ = ["SIGNIFICANT", "decimal_str", "encode_evidence", "parse_decimal_str"]

_CONTEXT = Context(prec=SIGNIFICANT, rounding=ROUND_HALF_EVEN)


def decimal_str(value: float | int | Decimal, significant: int = SIGNIFICANT) -> str:
    """Encode one finite number as a decimal string.

    Integers encode exactly. Floats and decimals are rounded half to even to
    ``significant`` digits. Non-finite values raise ``ValueError`` here; the
    structural encoder maps them to ``None`` instead.
    """

    if isinstance(value, bool):
        raise TypeError("booleans are not numbers for evidence encoding")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite value {value!r} cannot be encoded")
        exact = Decimal(value)
    elif isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"non-finite value {value!r} cannot be encoded")
        exact = value
    else:
        raise TypeError(f"cannot encode {type(value).__name__} as a decimal string")
    if significant == SIGNIFICANT:
        context = _CONTEXT
    else:
        context = Context(prec=significant, rounding=ROUND_HALF_EVEN)
    rounded = context.create_decimal(exact)
    if rounded == 0:
        return "0"
    text = format(rounded.normalize(context), "f")
    return text


def parse_decimal_str(text: str) -> Decimal:
    """Parse a decimal string produced by ``decimal_str`` back to ``Decimal``."""

    if not isinstance(text, str) or not text:
        raise TypeError("expected a non-empty decimal string")
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise ValueError(f"not a decimal string: {text!r}") from error
    if not value.is_finite():
        raise ValueError(f"not a finite decimal string: {text!r}")
    return value


def encode_evidence(value: Any, path: str = "$") -> tuple[Any, list[str]]:
    """Encode a nested structure for an identity payload.

    Returns the encoded structure and the list of JSON paths whose values
    were non-finite and therefore replaced by ``None``.
    """

    non_finite: list[str] = []
    encoded = _encode(value, path, non_finite)
    return encoded, non_finite


def _encode(value: Any, path: str, non_finite: list[str]) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            non_finite.append(path)
            return None
        return decimal_str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            non_finite.append(path)
            return None
        return decimal_str(value)
    if isinstance(value, (list, tuple)):
        return [_encode(item, f"{path}[{index}]", non_finite) for index, item in enumerate(value)]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"evidence object keys must be str at {path}")
            result[key] = _encode(item, f"{path}.{key}", non_finite)
        return result
    raise TypeError(f"cannot encode {type(value).__name__} at {path}")
