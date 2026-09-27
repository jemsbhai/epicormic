"""Multiple-comparison adjustments (docs/PLAN.md, section 8.1).

``benjamini_hochberg`` controls the false discovery rate across the
per-probe, per-scorer tests of a window; ``holm`` controls the family-wise
error rate across the pooled per-scorer tests. Both return adjusted
p-values in the original order, monotone and capped at 1, so a test is
rejected when its adjusted p-value is at most the chosen level.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["benjamini_hochberg", "holm", "rejected"]


def benjamini_hochberg(p_values: Sequence[float]) -> list[float]:
    """Benjamini-Hochberg adjusted p-values."""

    _check(p_values)
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda index: p_values[index])
    adjusted = [0.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        index = order[rank - 1]
        running = min(running, p_values[index] * m / rank)
        # never below the raw value: guards the one-ulp rounding of p * m / m
        adjusted[index] = max(running, p_values[index])
    return adjusted


def holm(p_values: Sequence[float]) -> list[float]:
    """Holm step-down adjusted p-values."""

    _check(p_values)
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda index: p_values[index])
    adjusted = [0.0] * m
    running = 0.0
    for rank, index in enumerate(order, start=1):
        running = max(running, min(1.0, p_values[index] * (m - rank + 1)))
        adjusted[index] = max(running, p_values[index])
    return adjusted


def rejected(adjusted: Sequence[float], level: float) -> list[bool]:
    """Which adjusted p-values are at or below ``level``."""

    if not 0 < level <= 1:
        raise ValueError("level must be in (0, 1]")
    return [value <= level for value in adjusted]


def _check(p_values: Sequence[float]) -> None:
    for value in p_values:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError(f"p-values must lie in [0, 1], got {value!r}")
