"""Permutation tests (docs/PLAN.md, section 8.1).

``permutation_test`` is a two-sample test of a scalar statistic. It
enumerates every assignment when the number of assignments is at most
``ENUMERATION_LIMIT`` and otherwise draws ``permutations`` seeded random
assignments; the Monte Carlo p-value is ``(1 + #{|T*| >= |T|}) / (R + 1)``.

``stratified_permutation_test`` is the primary pooled test: the statistic
is the mean over strata (probes) of Cliff's delta, and labels are permuted
within each stratum only, so probes with different locations never create a
false difference. Each stratum's pooled midranks are computed once; a
permutation shuffles ranks and reads ``U`` off a partial sum, which keeps
the cost linear in the sample size.
"""

from __future__ import annotations

import itertools
import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .rank import midranks

__all__ = [
    "ENUMERATION_LIMIT",
    "PermutationResult",
    "StratifiedResult",
    "mean_difference",
    "permutation_test",
    "stratified_permutation_test",
]

ENUMERATION_LIMIT = 20_000


@dataclass(frozen=True)
class PermutationResult:
    statistic: float
    p_value: float
    permutations: int
    exact: bool
    seed: int | None


@dataclass(frozen=True)
class StratifiedResult:
    statistic: float
    p_value: float
    permutations: int
    seed: int
    strata: int
    deltas: tuple[float, ...]


def mean_difference(baseline: Sequence[float], current: Sequence[float]) -> float:
    return sum(current) / len(current) - sum(baseline) / len(baseline)


def permutation_test(
    baseline: Sequence[float],
    current: Sequence[float],
    *,
    statistic: Callable[[Sequence[float], Sequence[float]], float] = mean_difference,
    permutations: int = 2000,
    seed: int = 0,
) -> PermutationResult:
    """Two-sided two-sample permutation test of ``statistic(baseline, current)``."""

    _check_samples(baseline, current)
    _check_permutations(permutations)
    pooled = [*baseline, *current]
    n_b, n_c = len(baseline), len(current)
    observed = statistic(baseline, current)
    target = abs(observed)
    assignments = math.comb(n_b + n_c, n_c)
    if assignments <= ENUMERATION_LIMIT:
        hits = 0
        for chosen in itertools.combinations(range(n_b + n_c), n_c):
            chosen_set = set(chosen)
            perm_current = [pooled[index] for index in chosen]
            perm_baseline = [pooled[index] for index in range(n_b + n_c) if index not in chosen_set]
            if abs(statistic(perm_baseline, perm_current)) >= target - 1e-12:
                hits += 1
        return PermutationResult(
            statistic=observed,
            p_value=hits / assignments,
            permutations=assignments,
            exact=True,
            seed=None,
        )
    rng = random.Random(seed)
    hits = 0
    values = list(pooled)
    for _ in range(permutations):
        rng.shuffle(values)
        if abs(statistic(values[:n_b], values[n_b:])) >= target - 1e-12:
            hits += 1
    return PermutationResult(
        statistic=observed,
        p_value=(1 + hits) / (permutations + 1),
        permutations=permutations,
        exact=False,
        seed=seed,
    )


def stratified_permutation_test(
    strata: Sequence[tuple[Sequence[float], Sequence[float]]],
    *,
    permutations: int = 2000,
    seed: int = 0,
) -> StratifiedResult:
    """Mean per-stratum Cliff's delta, permuted within strata.

    ``strata`` holds ``(baseline, current)`` pairs, one per probe. Returns the
    observed statistic, the two-sided Monte Carlo p-value, and the observed
    per-stratum deltas in the order given.
    """

    if not strata:
        raise ValueError("at least one stratum is required")
    _check_permutations(permutations)
    prepared: list[tuple[list[float], int, int]] = []
    deltas: list[float] = []
    for baseline, current in strata:
        _check_samples(baseline, current)
        ranks = midranks([*baseline, *current])
        n_b, n_c = len(baseline), len(current)
        deltas.append(_delta_from_ranks(ranks, n_b, n_c))
        prepared.append((ranks, n_b, n_c))
    observed = sum(deltas) / len(deltas)
    target = abs(observed)
    rng = random.Random(seed)
    hits = 0
    for _ in range(permutations):
        total = 0.0
        for ranks, n_b, n_c in prepared:
            rng.shuffle(ranks)
            total += _delta_from_ranks(ranks, n_b, n_c)
        if abs(total / len(prepared)) >= target - 1e-12:
            hits += 1
    return StratifiedResult(
        statistic=observed,
        p_value=(1 + hits) / (permutations + 1),
        permutations=permutations,
        seed=seed,
        strata=len(prepared),
        deltas=tuple(deltas),
    )


def _delta_from_ranks(ranks: Sequence[float], n_b: int, n_c: int) -> float:
    """Cliff's delta of the last ``n_c`` ranks against the first ``n_b``."""

    rank_sum = 0.0
    for rank in ranks[n_b:]:
        rank_sum += rank
    u = rank_sum - n_c * (n_c + 1) / 2
    product = n_b * n_c
    return (2 * u - product) / product


def _check_samples(baseline: Sequence[float], current: Sequence[float]) -> None:
    if not baseline or not current:
        raise ValueError("both samples need at least one value")
    for value in (*baseline, *current):
        bad_type = isinstance(value, bool) or not isinstance(value, (int, float))
        if bad_type or not math.isfinite(value):
            raise ValueError(f"samples must contain finite numbers, got {value!r}")


def _check_permutations(permutations: int) -> None:
    if isinstance(permutations, bool) or not isinstance(permutations, int) or permutations < 1:
        raise ValueError("permutations must be a positive integer")
