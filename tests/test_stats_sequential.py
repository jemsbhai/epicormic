"""Sequential detectors (docs/PLAN.md, section 8.2)."""

from __future__ import annotations

import math
import random
from typing import Any

import pytest

from epicormic.stats import (
    betting_eprocess,
    clipped_rate,
    cusum,
    page_hinkley,
    rate_interval,
    sign_transform,
)
from epicormic.stats.sequential import DEFAULT_GRID

# --- CUSUM and Page-Hinkley ---------------------------------------------------


def test_cusum_is_quiet_on_a_null_stream_and_fires_on_a_step() -> None:
    rng = random.Random(1)
    quiet = cusum([rng.gauss(0, 0.03) for _ in range(200)])
    assert not quiet.alarm and quiet.alarm_index is None and quiet.steps == 200
    stepped = cusum([0.0] * 5 + [0.3] * 4)
    assert stepped.alarm and stepped.alarm_index == 6
    assert stepped.s_plus == pytest.approx(0.8) and stepped.s_minus == 0.0
    dropped = cusum([0.0] * 5 + [-0.3] * 4)
    assert dropped.alarm and dropped.s_minus == pytest.approx(0.8)
    assert cusum([]).steps == 0 and not cusum([]).alarm


def test_cusum_parameters_and_validation() -> None:
    assert cusum([0.5, 0.5], k=0.1, h=0.4).alarm_index == 0
    assert not cusum([0.5, 0.5], k=0.1, h=1.0).alarm
    with pytest.raises(ValueError):
        cusum([0.1], k=-0.1)
    with pytest.raises(ValueError):
        cusum([0.1], h=0)
    with pytest.raises(ValueError):
        cusum([float("nan")])
    with pytest.raises(ValueError):
        cusum([True])


def test_page_hinkley_detects_rises_and_falls() -> None:
    rng = random.Random(2)
    quiet = page_hinkley([rng.gauss(0, 0.03) for _ in range(200)])
    assert not quiet.alarm and quiet.excursion < quiet.threshold
    rise = page_hinkley([0.0] * 6 + [0.4] * 4)
    assert rise.alarm and rise.alarm_index == 7
    assert rise.up - rise.up_min >= rise.threshold
    fall = page_hinkley([0.0] * 6 + [-0.4] * 4)
    assert fall.alarm and fall.down_max - fall.down >= fall.threshold
    assert page_hinkley([]).steps == 0
    with pytest.raises(ValueError):
        page_hinkley([0.1], delta=-1)
    with pytest.raises(ValueError):
        page_hinkley([0.1], threshold=0)


# --- betting e-process ---------------------------------------------------------


def test_empty_stream_is_the_unit_e_value() -> None:
    state = betting_eprocess([], mu0=0.5)
    assert state.e_value == 1.0 and state.log_e_value == 0.0
    assert state.e_up == 1.0 and state.e_down == 1.0
    assert state.max_e_value == 1.0 and not state.alarm and state.alarm_index is None
    assert state.observations == 0 and state.threshold == 20 and state.grid == DEFAULT_GRID


def test_log_space_matches_the_direct_product_on_a_short_stream() -> None:
    values = [1, 0, 1, 1, 0, 1, 1, 1]
    mu0 = 0.6
    state = betting_eprocess(values, mu0=mu0, grid=(0.5,))
    up = 1.0
    down = 1.0
    for value in values:
        up *= 1 + 0.5 / mu0 * (value - mu0)
        down *= 1 - 0.5 / (1 - mu0) * (value - mu0)
    assert state.e_up == pytest.approx(up)
    assert state.e_down == pytest.approx(down)
    assert state.e_value == pytest.approx((up + down) / 2)
    assert state.log_e_value == pytest.approx(math.log((up + down) / 2))


def test_ville_bound_holds_on_null_streams() -> None:
    rng = random.Random(0)
    for mu0 in (0.5, 0.9):
        alarms = 0
        finals = 0.0
        streams = 600
        for _ in range(streams):
            values = [int(rng.random() < mu0) for _ in range(200)]
            state = betting_eprocess(values, mu0=mu0, alpha=0.05)
            alarms += state.alarm
            finals += min(state.e_value, 1e6)
        assert alarms / streams <= 0.05, (mu0, alarms / streams)
        assert finals / streams <= 1.25, (mu0, finals / streams)


def test_shift_is_detected_quickly() -> None:
    rng = random.Random(3)
    delays = []
    for _ in range(200):
        values = [int(rng.random() < 0.5) for _ in range(400)]
        state = betting_eprocess(values, mu0=0.9, alpha=0.05)
        assert state.alarm
        assert state.alarm_index is not None
        delays.append(state.alarm_index + 1)
    delays.sort()
    assert delays[len(delays) // 2] <= 15
    assert delays[int(len(delays) * 0.95)] <= 60


def test_alarm_index_is_the_first_crossing_and_persists() -> None:
    values = [0] * 40 + [1] * 40
    state = betting_eprocess(values, mu0=0.9, alpha=0.05)
    assert state.alarm and state.alarm_index is not None
    assert state.alarm_index < 40
    assert state.max_e_value >= 20
    # the later run of ones pulls the current e-value back down, the alarm stays
    assert state.e_value < state.max_e_value


def test_overwhelming_evidence_overflows_gracefully() -> None:
    state = betting_eprocess([1] * 3000, mu0=0.5, alpha=0.05)
    assert state.e_value == math.inf
    assert math.isfinite(state.log_e_value) and state.log_e_value > 1000
    assert state.alarm and state.alarm_index is not None and state.alarm_index < 20


@pytest.mark.parametrize(
    ("kwargs", "values"),
    [
        ({"mu0": 0.0}, [1]),
        ({"mu0": 1.0}, [1]),
        ({"mu0": 0.5, "alpha": 0}, [1]),
        ({"mu0": 0.5, "alpha": 1}, [1]),
        ({"mu0": 0.5, "grid": ()}, [1]),
        ({"mu0": 0.5, "grid": (1.0,)}, [1]),
        ({"mu0": 0.5, "grid": (0.0,)}, [1]),
        ({"mu0": 0.5}, [2]),
        ({"mu0": 0.5}, [True]),
    ],
)
def test_eprocess_validation(kwargs: dict[str, Any], values: list[Any]) -> None:
    with pytest.raises(ValueError):
        betting_eprocess(values, **kwargs)


# --- helpers -----------------------------------------------------------------


def test_sign_transform_excludes_ties() -> None:
    stream = sign_transform([1.0, 2.0, 3.0, 2.0, 5.0], 2.0)
    assert stream.values == (0, 1, 1) and stream.ties_excluded == 2 and stream.median == 2.0
    assert sign_transform([], 0.0).values == ()
    with pytest.raises(ValueError):
        sign_transform([float("nan")], 0.0)
    with pytest.raises(ValueError):
        sign_transform([True], 0.0)


def test_clipped_rate() -> None:
    assert clipped_rate(10, 10) == 0.95
    assert clipped_rate(0, 10) == 0.05
    assert clipped_rate(5, 10) == 0.5
    assert clipped_rate(1, 1) == 0.5
    with pytest.raises(ValueError):
        clipped_rate(0, 0)
    with pytest.raises(ValueError):
        clipped_rate(3, 2)


# --- interval null (D27) ------------------------------------------------------


def test_rate_interval_reference_values() -> None:
    low, high = rate_interval(10, 200, 0.95)
    assert low == pytest.approx(0.02423, abs=1e-4)
    assert high == pytest.approx(0.09003, abs=1e-4)
    low, high = rate_interval(0, 240, 0.975)
    assert low == 0.0 and high == pytest.approx(0.01809, abs=1e-4)
    low, high = rate_interval(240, 240, 0.975)
    assert high == 1.0 and low == pytest.approx(1 - 0.01809, abs=1e-4)
    low, high = rate_interval(216, 240, 0.975)
    assert low == pytest.approx(0.8481, abs=1e-3) and high == pytest.approx(0.9390, abs=1e-3)
    for ones, total, confidence in ((0, 0, 0.95), (3, 2, 0.95), (1, 2, 1.0), (1, 2, 0.0)):
        with pytest.raises(ValueError):
            rate_interval(ones, total, confidence)


def test_interval_null_arguments_and_threshold() -> None:
    point = betting_eprocess([], mu0=0.5)
    assert point.threshold == 20 and point.lower is None and point.confidence is None
    interval = betting_eprocess([], lower=0.4, upper=0.6, alpha=0.05)
    assert interval.threshold == 40 and interval.confidence == 0.975
    assert interval.lower == 0.4 and interval.upper == 0.6 and interval.mu0 == 0.5
    with pytest.raises(ValueError, match="not both"):
        betting_eprocess([], mu0=0.5, lower=0.4, upper=0.6)
    with pytest.raises(ValueError, match="both lower and upper"):
        betting_eprocess([], lower=0.4)
    for lower, upper in ((0.6, 0.4), (-0.1, 0.5), (0.5, 1.1), (0.0, 0.0), (1.0, 1.0)):
        with pytest.raises(ValueError):
            betting_eprocess([], lower=lower, upper=upper)
    assert betting_eprocess([0, 1], lower=0.0, upper=1.0).e_value <= 1.0


def test_constant_baseline_no_longer_alarms_on_a_constant_stream() -> None:
    # epicormic 0.1.0 clipped a baseline of 0 in 240 to 0.0021 and alarmed on a
    # stream of zeros inside window 13 of an EXP-D run (observations 2880 to
    # 3119); the interval null [0, 0.018] bets downward with lower = 0 and
    # never moves
    lower, upper = rate_interval(0, 240, 0.975)
    stream = [0] * 6720
    state = betting_eprocess(stream, lower=lower, upper=upper, alpha=0.05)
    assert not state.alarm and state.max_e_value <= 1.0 and state.e_value <= 1.0
    old = betting_eprocess(stream, mu0=clipped_rate(0, 240), alpha=0.05)
    assert old.alarm and old.alarm_index is not None and 2880 <= old.alarm_index < 3120
    # the mirror image: a baseline of all ones and a stream of ones
    lower, upper = rate_interval(240, 240, 0.975)
    state = betting_eprocess([1] * 6720, lower=lower, upper=upper, alpha=0.05)
    assert not state.alarm and state.e_value <= 1.0


def test_interval_null_still_detects_a_real_shift() -> None:
    rng = random.Random(4)
    lower, upper = rate_interval(0, 240, 0.975)
    delays = []
    for _ in range(100):
        values = [int(rng.random() < 0.2) for _ in range(400)]
        state = betting_eprocess(values, lower=lower, upper=upper, alpha=0.05)
        assert state.alarm and state.alarm_index is not None
        delays.append(state.alarm_index + 1)
    delays.sort()
    assert delays[len(delays) // 2] <= 20


def test_ville_bound_holds_with_a_baseline_derived_interval() -> None:
    rng = random.Random(5)
    for mu in (0.02, 0.5, 0.9):
        alarms = 0
        streams = 300
        for _ in range(streams):
            ones = sum(int(rng.random() < mu) for _ in range(240))
            lower, upper = rate_interval(ones, 240, 0.975)
            values = [int(rng.random() < mu) for _ in range(1500)]
            alarms += betting_eprocess(values, lower=lower, upper=upper, alpha=0.05).alarm
        assert alarms / streams <= 0.05, (mu, alarms / streams)

