"""Layer B: sequential change detection across windows (docs/PLAN.md, section 8.2).

Three detectors. ``cusum`` and ``page_hinkley`` are classical two-sided
schemes on a per-window statistic (the pooled Cliff's delta of a scorer);
they are heuristics whose thresholds are calibrated empirically (EXP-C).
``betting_eprocess`` is the anytime-valid detector: for a stream of
indicator observations ``x_i`` in {0, 1} and a null rate ``mu0``, the wealth
of a bettor who bets a fixed fraction of the maximal stake on "above
``mu0``" or "below ``mu0``" is a nonnegative supermartingale with initial
value 1 under the null, so by Ville's inequality the probability that the
two-sided e-value ever reaches ``1 / alpha`` is at most ``alpha``, at any
stopping time. The wealth is accumulated in log space; the reported
``e_value`` may overflow to infinity for overwhelming evidence, in which
case ``log_e_value`` stays finite.

Scalar scorers enter the e-process through ``sign_transform``: each score
becomes ``1`` when it exceeds the probe's baseline median, ties are
excluded, and the null rate is one half.
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

    @property
    def threshold(self) -> float:
        return 1 / self.alpha


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
    mu0: float,
    alpha: float = 0.05,
    grid: Sequence[float] = DEFAULT_GRID,
) -> EProcessState:
    """Two-sided betting e-process for a stream of 0/1 values with null mean ``mu0``.

    For each fraction ``f`` in ``grid`` the upward bettor stakes
    ``lam = f / mu0`` and the downward bettor ``lam = f / (1 - mu0)``, the
    largest stakes that keep every factor positive. Wealth is averaged over
    the grid on each side and the two sides are averaged again; an average
    of e-processes is an e-process. The alarm fires the first time the
    running e-value reaches ``1 / alpha`` and stays recorded afterwards.
    """

    if not 0 < mu0 < 1:
        raise ValueError("mu0 must lie strictly between 0 and 1; clip a degenerate rate first")
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
    threshold = 1 / alpha
    max_e = 1.0
    alarm_index: int | None = None
    for index, value in enumerate(values):
        centred = value - mu0
        for position, fraction in enumerate(fractions):
            log_up[position] += math.log1p(fraction / mu0 * centred)
            log_down[position] += math.log1p(-fraction / (1 - mu0) * centred)
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
        mu0=mu0,
        alpha=alpha,
        grid=fractions,
        alarm=alarm_index is not None,
        alarm_index=alarm_index,
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
    """Baseline rate clipped half an observation away from 0 and 1."""

    if total < 1 or not 0 <= ones <= total:
        raise ValueError("need 0 <= ones <= total with total >= 1")
    margin = 1 / (2 * total)
    return min(max(ones / total, margin), 1 - margin)


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
