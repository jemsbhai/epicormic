"""Fisher's exact test for indicator scorers (docs/PLAN.md, section 8.1).

The 2 by 2 table is ``((a, b), (c, d))``: group one has ``a`` ones and ``b``
zeros, group two has ``c`` ones and ``d`` zeros. The two-sided p-value is the
sum of the hypergeometric probabilities of every table with the same margins
whose probability does not exceed that of the observed table by more than a
relative tolerance of 1e-7, the convention R's ``fisher.test`` uses.
Probabilities are computed in log space with ``math.lgamma``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["FisherResult", "fisher_exact", "indicator_test"]

RELATIVE_TOLERANCE = 1e-7


@dataclass(frozen=True)
class FisherResult:
    """Outcome of a two-sided Fisher exact test.

    ``risk_difference`` is the rate of ones in group two minus group one;
    ``log_odds_ratio`` uses the Haldane-Anscombe correction (0.5 added to
    every cell) so it is finite for tables with empty cells.
    """

    p_value: float
    risk_difference: float
    log_odds_ratio: float
    table: tuple[tuple[int, int], tuple[int, int]]

    @property
    def n1(self) -> int:
        return self.table[0][0] + self.table[0][1]

    @property
    def n2(self) -> int:
        return self.table[1][0] + self.table[1][1]


def fisher_exact(table: Sequence[Sequence[int]]) -> FisherResult:
    """Two-sided Fisher exact test on a 2 by 2 table of non-negative integers."""

    (a, b), (c, d) = _validate(table)
    n1, n2 = a + b, c + d
    ones = a + c
    total = n1 + n2
    if n1 == 0 or n2 == 0:
        raise ValueError("both groups need at least one observation")
    log_total = _log_choose(total, ones)
    observed = _log_choose(n1, a) + _log_choose(n2, ones - a) - log_total
    threshold = observed + math.log1p(RELATIVE_TOLERANCE)
    low = max(0, ones - n2)
    high = min(n1, ones)
    p_value = 0.0
    for k in range(low, high + 1):
        log_p = _log_choose(n1, k) + _log_choose(n2, ones - k) - log_total
        if log_p <= threshold:
            p_value += math.exp(log_p)
    p_value = min(1.0, p_value)
    risk_difference = c / n2 - a / n1
    log_odds_ratio = math.log(((c + 0.5) * (b + 0.5)) / ((d + 0.5) * (a + 0.5)))
    return FisherResult(
        p_value=p_value,
        risk_difference=risk_difference,
        log_odds_ratio=log_odds_ratio,
        table=((a, b), (c, d)),
    )


def indicator_test(baseline: Sequence[float], current: Sequence[float]) -> FisherResult:
    """Fisher's exact test between two samples of 0/1 indicator values."""

    a = _count_ones(baseline, "baseline")
    c = _count_ones(current, "current")
    return fisher_exact(((a, len(baseline) - a), (c, len(current) - c)))


def _count_ones(values: Sequence[float], name: str) -> int:
    ones = 0
    for value in values:
        if value == 1:
            ones += 1
        elif value != 0:
            raise ValueError(f"{name} values must be 0 or 1, got {value!r}")
    return ones


def _validate(table: Sequence[Sequence[int]]) -> tuple[tuple[int, int], tuple[int, int]]:
    if len(table) != 2 or any(len(row) != 2 for row in table):
        raise ValueError("table must be 2 by 2")
    cells: list[int] = []
    for row in table:
        for cell in row:
            if isinstance(cell, bool) or not isinstance(cell, int) or cell < 0:
                raise ValueError("table cells must be non-negative integers")
            cells.append(cell)
    a, b, c, d = cells
    return (a, b), (c, d)


def _log_choose(n: int, k: int) -> float:
    if k < 0 or k > n:
        return -math.inf
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
