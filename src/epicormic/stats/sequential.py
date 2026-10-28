"""Layer B: sequential change detection across windows (docs/PLAN.md, section 8.2).

Three detectors. ``cusum`` and ``page_hinkley`` are classical two-sided
schemes on a per-window statistic (the pooled Cliff's delta of a scorer);
they are heuristics whose thresholds are calibrated empirically (EXP-C).
``betting_eprocess`` is the anytime-valid detector: for a stream of
indicator observations ``x_i`` in {0, 1} and a null interval
``[lower, upper]`` for the rate, the wealth of a bettor who bets a fixed
fraction of the maximal stake on "above ``upper``" or "below ``lower``" is a
nonnegative supermartingale with initial value 1 whenever the true rate lies
in the interval, so by Ville's inequality the probability that the two-sided
e-value ever reaches the threshold is bounded, at any stopping time. The
interval is the exact binomial (Clopper-Pearson) confidence interval of the
baseline rate from ``rate_interval`` (D27): it costs part of the false alarm
budget once, at the baseline, and in exchange the plug-in rate of 0.1
(which was clipped away from 0 and 1 and accumulated any difference between
the clipped value and the truth without bound) is gone. A point null is
still available through ``mu0``. The wealth is accumulated in log space;
the reported ``e_value`` may overflow to infinity for overwhelming
evidence, in which case ``log_e_value`` stays finite.

Scalar scorers enter the e-process through ``sign_transform``: each score
becomes ``1`` when it exceeds the probe's baseline median, ties are
excluded, and the null interval is the baseline's own above-median rate
among non-ties.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "CusumState",
    "EProcessState",
    "PageHinkleyState",
    "SignStream",
    "betting_eprocess",
    "clipped_rate",
    "cusum",
    "page_hinkley",
    "rate_interval",
    "sign_transform",
]

DEFAULT_GRID: tuple[float, ...] = (0.1, 0.25, 0.5, 0.75)


@dataclass(frozen=True)
class CusumState:
    s_plus: float
    s_minus: float
    k: float
    h: float
    steps: int
    alarm: bool
    alarm_index: int | None


@dataclass(frozen=True)
class PageHinkleyState:
    up: float
    up_min: float
    down: float
    down_max: float
    delta: float
    threshold: float
    steps: int
    alarm: bool
    alarm_index: int | None

    @property
    def excursion(self) -> float:
        """Largest current one-sided excursion, comparable with ``threshold``."""

        return max(self.up - self.up_min, self.down_max - self.down)


@dataclass(frozen=True)
class EProcessState:
    e_value: float
    log_e_value: float
    e_up: float
    e_down: float
    max_e_value: float
    observations: int
    mu0: float
    alpha: float
    grid: tuple[float, ...]
    alarm: bool
    alarm_index: int | None
    lower: float | None = None
    upper: float | None = None
    confidence: float | None = None

    @property
    def threshold(self) -> float:
        """The e-value at which the alarm fires.

        With a point null the whole budget ``alpha`` goes to the bet and the
        threshold is ``1 / alpha``. With an interval null half the budget is
        spent on the baseline interval (its confidence is ``1 - alpha / 2``)
        and the other half on the bet, so the threshold is ``2 / alpha``;
        the two halves bound the false alarm probability by ``alpha``.
        """

        return 1 / self.alpha if self.confidence is None else 2 / self.alpha


@dataclass(frozen=True)
class SignStream:
    values: tuple[int, ...]
    ties_excluded: int
    median: float


def cusum(values: Sequence[float], *, k: float = 0.1, h: float = 0.4) -> CusumState:
    """Two-sided CUSUM over ``values`` with reference ``k`` and threshold ``h``."""

    _check_values(values)
    if k < 0 or h <= 0:
        raise ValueError("k must be non-negative and h positive")
    s_plus = s_minus = 0.0
    alarm_index: int | None = None
    for index, value in enumerate(values):
        s_plus = max(0.0, s_plus + value - k)
        s_minus = max(0.0, s_minus - value - k)
        if alarm_index is None and max(s_plus, s_minus) >= h:
            alarm_index = index
    return CusumState(
        s_plus=s_plus,
        s_minus=s_minus,
        k=k,
        h=h,
        steps=len(values),
        alarm=alarm_index is not None,
        alarm_index=alarm_index,
    )


def page_hinkley(
    values: Sequence[float], *, delta: float = 0.05, threshold: float = 0.5
) -> PageHinkleyState:
    """Two-sided Page-Hinkley test over ``values``.

    ``up`` accumulates ``x_i - mean_i - delta`` and alarms when it rises
    ``threshold`` above its running minimum; ``down`` accumulates
    ``x_i - mean_i + delta`` and alarms when it falls ``threshold`` below its
    running maximum.
    """

    _check_values(values)
    if delta < 0 or threshold <= 0:
        raise ValueError("delta must be non-negative and threshold positive")
    total = 0.0
    up = down = 0.0
    up_min = down_max = 0.0
    alarm_index: int | None = None
    for index, value in enumerate(values):
        total += value
        mean = total / (index + 1)
        up += value - mean - delta
        down += value - mean + delta
        up_min = min(up_min, up)
        down_max = max(down_max, down)
        if alarm_index is None and (up - up_min >= threshold or down_max - down >= threshold):
            alarm_index = index
    return PageHinkleyState(
        up=up,
        up_min=up_min,
        down=down,
        down_max=down_max,
        delta=delta,
        threshold=threshold,
        steps=len(values),
        alarm=alarm_index is not None,
        alarm_index=alarm_index,
    )


def betting_eprocess(
    values: Sequence[int],
    *,
    mu0: float | None = None,
    lower: float | None = None,
    upper: float | None = None,
    alpha: float = 0.05,
    grid: Sequence[float] = DEFAULT_GRID,
) -> EProcessState:
    """Two-sided betting e-process for a stream of 0/1 values.

    Pass either a point null ``mu0`` or an interval null ``[lower, upper]``
    (D27). For each fraction ``f`` in ``grid`` the upward bettor stakes
    ``lam = f / upper`` on "the rate exceeds ``upper``" and the downward
    bettor ``lam = f / (1 - lower)`` on "the rate is below ``lower``", the
    largest stakes that keep every factor positive. Each factor has
    expectation at most 1 whenever the true rate lies in the interval, so
    each wealth is a nonnegative supermartingale there. Wealth is averaged
    over the grid on each side and the two sides are averaged again; an
    average of supermartingales is a supermartingale. The alarm fires the
    first time the running e-value reaches ``threshold`` (``1 / alpha`` for a
    point null, ``2 / alpha`` for an interval null, see ``EProcessState``)
    and stays recorded afterwards.
    """

    if mu0 is not None:
        if lower is not None or upper is not None:
            raise ValueError("pass either mu0 or lower and upper, not both")
        if not 0 < mu0 < 1:
            raise ValueError("mu0 must lie strictly between 0 and 1")
        lower = upper = mu0
        confidence: float | None = None
    else:
        if lower is None or upper is None:
            raise ValueError("pass mu0, or both lower and upper")
        if not 0 <= lower <= upper <= 1 or upper <= 0 or lower >= 1:
            raise ValueError("need 0 <= lower <= upper <= 1 with upper > 0 and lower < 1")
        confidence = 1 - alpha / 2
    if not 0 < alpha < 1:
        raise ValueError("alpha must lie strictly between 0 and 1")
    fractions = tuple(float(f) for f in grid)
    if not fractions or any(not 0 < f < 1 for f in fractions):
        raise ValueError("grid fractions must lie strictly between 0 and 1")
    for value in values:
        if isinstance(value, bool) or value not in (0, 1):
            raise ValueError(f"e-process values must be 0 or 1, got {value!r}")
    log_up = [0.0] * len(fractions)
    log_down = [0.0] * len(fractions)
    threshold = 1 / alpha if confidence is None else 2 / alpha
    max_e = 1.0
    alarm_index: int | None = None
    for index, value in enumerate(values):
        for position, fraction in enumerate(fractions):
            log_up[position] += math.log1p(fraction / upper * (value - upper))
            log_down[position] += math.log1p(-fraction / (1 - lower) * (value - lower))
        e_value = _combined(log_up, log_down)
        if e_value > max_e:
            max_e = e_value
        if alarm_index is None and e_value >= threshold:
            alarm_index = index
    e_up = _mean_exp(log_up)
    e_down = _mean_exp(log_down)
    e_value = (e_up + e_down) / 2
    return EProcessState(
        e_value=e_value,
        log_e_value=_log_combined(log_up, log_down),
        e_up=e_up,
        e_down=e_down,
        max_e_value=max_e,
        observations=len(values),
        mu0=(lower + upper) / 2 if mu0 is None else mu0,
        alpha=alpha,
        grid=fractions,
        alarm=alarm_index is not None,
        alarm_index=alarm_index,
        lower=None if confidence is None else lower,
        upper=None if confidence is None else upper,
        confidence=confidence,
    )


def sign_transform(values: Sequence[float], median: float) -> SignStream:
    """Turn scalar scores into the indicator ``score > median``, excluding ties."""

    stream: list[int] = []
    ties = 0
    for value in values:
        bad_type = isinstance(value, bool) or not isinstance(value, (int, float))
        if bad_type or not math.isfinite(value):
            raise ValueError(f"scores must be finite numbers, got {value!r}")
        if value == median:
            ties += 1
        else:
            stream.append(int(value > median))
    return SignStream(values=tuple(stream), ties_excluded=ties, median=float(median))


def clipped_rate(ones: int, total: int) -> float:
    """Baseline rate clipped half an observation away from 0 and 1.

    Kept for callers of 0.1; the verdict no longer uses it (D27). A clipped
    point null accumulates the difference between the clipped value and the
    true rate without bound, so a constant baseline alarmed on schedule.
    """

    if total < 1 or not 0 <= ones <= total:
        raise ValueError("need 0 <= ones <= total with total >= 1")
    margin = 1 / (2 * total)
    return min(max(ones / total, margin), 1 - margin)


def rate_interval(ones: int, total: int, confidence: float) -> tuple[float, float]:
    """Two-sided exact binomial (Clopper-Pearson) interval for a rate of ``ones`` in ``total``.

    The lower bound is 0 when ``ones`` is 0 and the upper bound is 1 when
    ``ones`` equals ``total``; otherwise each bound solves a binomial tail
    equation by bisection on the exact binomial distribution.
    """

    if total < 1 or not 0 <= ones <= total:
        raise ValueError("need 0 <= ones <= total with total >= 1")
    if not 0 < confidence < 1:
        raise ValueError("confidence must lie strictly between 0 and 1")
    tail = (1 - confidence) / 2
    lower = 0.0 if ones == 0 else _solve_binomial(tail, total, ones, upper=False)
    upper = 1.0 if ones == total else _solve_binomial(tail, total, ones, upper=True)
    return lower, upper


def _log_choose(n: int, k: int) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def _binomial_pmf(k: int, n: int, p: float) -> float:
    """P(X = k) for X ~ Binomial(n, p) with p strictly inside (0, 1), as the bisection supplies."""

    return math.exp(_log_choose(n, k) + k * math.log(p) + (n - k) * math.log1p(-p))


def _solve_binomial(target: float, n: int, k: int, *, upper: bool) -> float:
    """Bisect for p with P(X <= k) = target (upper bound) or P(X >= k) = target (lower)."""

    low, high = 0.0, 1.0
    for _ in range(80):
        mid = (low + high) / 2
        if upper:
            value = sum(_binomial_pmf(i, n, mid) for i in range(k + 1))
        else:
            value = sum(_binomial_pmf(i, n, mid) for i in range(k, n + 1))
        if (value > target) == upper:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def _mean_exp(logs: Sequence[float]) -> float:
    total = 0.0
    for value in logs:
        try:
            total += math.exp(value)
        except OverflowError:
            return math.inf
    return total / len(logs)


def _combined(log_up: Sequence[float], log_down: Sequence[float]) -> float:
    return (_mean_exp(log_up) + _mean_exp(log_down)) / 2


def _log_combined(log_up: Sequence[float], log_down: Sequence[float]) -> float:
    """log of the two-sided e-value, computed without overflow."""

    logs = [*log_up, *log_down]
    peak = max(logs)
    return peak + math.log(sum(math.exp(value - peak) for value in logs) / len(logs))


def _check_values(values: Sequence[float]) -> None:
    for value in values:
        bad_type = isinstance(value, bool) or not isinstance(value, (int, float))
        if bad_type or not math.isfinite(value):
            raise ValueError(f"values must be finite numbers, got {value!r}")
