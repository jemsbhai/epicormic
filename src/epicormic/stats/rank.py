"""Rank statistics for scalar scorers (docs/PLAN.md, section 8.1).

``mann_whitney`` computes ``U`` for the current sample against the baseline,
``U = #{(c, b): c > b} + 0.5 * #{c == b}``, with an exact two-sided p-value
when both samples have at most ``EXACT_LIMIT`` values and no ties (through
the classical counting recurrence for the null distribution of U), and
otherwise the normal approximation with continuity and tie corrections.
Cliff's delta is ``(2U - n_b n_c) / (n_b n_c)`` and the Hodges-Lehmann shift
is the median of all pairwise ``c - b`` differences, in the scorer's units.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache

__all__ = [
    "EXACT_LIMIT",
    "RankResult",
    "cliffs_delta",
    "hodges_lehmann",
    "mann_whitney",
    "midranks",
    "u_statistic",
]

EXACT_LIMIT = 20


@dataclass(frozen=True)
class RankResult:
    u: float
    p_value: float
    method: str
    cliffs_delta: float
    hodges_lehmann: float
    n_baseline: int
    n_current: int


def midranks(values: Sequence[float]) -> list[float]:
    """Average ranks (1-based) of ``values``, ties sharing their mean rank."""

    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        mean_rank = (position + end + 2) / 2
        for index in order[position : end + 1]:
            ranks[index] = mean_rank
        position = end + 1
    return ranks


def u_statistic(baseline: Sequence[float], current: Sequence[float]) -> float:
    """``U`` of the current sample: pairs where current exceeds baseline, ties half."""

    _check(baseline, current)
    ranks = midranks([*baseline, *current])
    n_c = len(current)
    rank_sum = sum(ranks[len(baseline) :])
    return rank_sum - n_c * (n_c + 1) / 2


def cliffs_delta(baseline: Sequence[float], current: Sequence[float]) -> float:
    """Cliff's delta of current over baseline, in [-1, 1]."""

    product = len(baseline) * len(current)
    return (2 * u_statistic(baseline, current) - product) / product


def hodges_lehmann(baseline: Sequence[float], current: Sequence[float]) -> float:
    """Median of all pairwise ``current - baseline`` differences."""

    _check(baseline, current)
    return float(statistics.median([c - b for c in current for b in baseline]))


def mann_whitney(baseline: Sequence[float], current: Sequence[float]) -> RankResult:
    """Two-sided Mann-Whitney U test of current against baseline."""

    _check(baseline, current)
    n_b, n_c = len(baseline), len(current)
    product = n_b * n_c
    u = u_statistic(baseline, current)
    pooled = [*baseline, *current]
    tie_counts = [count for count in Counter(pooled).values() if count > 1]
    if not tie_counts and n_b <= EXACT_LIMIT and n_c <= EXACT_LIMIT:
        p_value = _exact_p(n_b, n_c, u)
        method = "mann_whitney_exact"
    else:
        p_value = _normal_p(n_b, n_c, u, tie_counts)
        method = "mann_whitney_normal"
    return RankResult(
        u=u,
        p_value=p_value,
        method=method,
        cliffs_delta=(2 * u - product) / product,
        hodges_lehmann=hodges_lehmann(baseline, current),
        n_baseline=n_b,
        n_current=n_c,
    )


def _exact_p(n_b: int, n_c: int, u: float) -> float:
    counts = _u_counts(n_c, n_b)
    total = math.comb(n_b + n_c, n_c)
    product = n_b * n_c
    lower = int(min(u, product - u))
    tail = sum(counts[value] for value in range(lower + 1))
    if u * 2 == product:
        return 1.0
    return min(1.0, 2 * tail / total)


@cache
def _u_counts(m: int, n: int) -> tuple[int, ...]:
    """Number of rank arrangements of ``m`` current and ``n`` baseline values per U value."""

    if m == 0 or n == 0:
        return (1,)
    with_top_current = _u_counts(m - 1, n)
    with_top_baseline = _u_counts(m, n - 1)
    size = m * n + 1
    counts = [0] * size
    for value, count in enumerate(with_top_current):
        counts[value + n] += count
    for value, count in enumerate(with_top_baseline):
        counts[value] += count
    return tuple(counts)


def _normal_p(n_b: int, n_c: int, u: float, tie_counts: Sequence[int]) -> float:
    total = n_b + n_c
    product = n_b * n_c
    mean = product / 2
    tie_term = sum(t**3 - t for t in tie_counts) / (total * (total - 1)) if total > 1 else 0.0
    variance = product / 12 * ((total + 1) - tie_term)
    if variance <= 0:
        return 1.0
    deviation = max(0.0, abs(u - mean) - 0.5)
    z = deviation / math.sqrt(variance)
    return min(1.0, math.erfc(z / math.sqrt(2)))


def _check(baseline: Sequence[float], current: Sequence[float]) -> None:
    if not baseline or not current:
        raise ValueError("both samples need at least one value")
    for value in (*baseline, *current):
        bad_type = isinstance(value, bool) or not isinstance(value, (int, float))
        if bad_type or not math.isfinite(value):
            raise ValueError(f"samples must contain finite numbers, got {value!r}")
