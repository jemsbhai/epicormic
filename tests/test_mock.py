"""The seeded mock provider used by tests, examples, and calibration."""

from __future__ import annotations

from epicormic.mock import Drift, MockProvider


def test_same_request_and_seed_field_give_the_same_result() -> None:
    first = MockProvider(seed=1)({"model": "m", "input": "hello", "seed": 3})
    second = MockProvider(seed=1)({"model": "m", "input": "hello", "seed": 3})
    assert first == second
    assert first["text"].startswith("answer:")
    assert first["usage"]["total_tokens"] == (
        first["usage"]["input_tokens"] + first["usage"]["output_tokens"]
    )


def test_without_seed_field_call_order_drives_variation() -> None:
    provider = MockProvider(seed=1, match_rate=0.0)
    first = provider({"model": "m", "input": "hello"})
    second = provider({"model": "m", "input": "hello"})
    assert first["text"] != second["text"]
    assert provider.calls == 2
    assert provider.requests == [{"model": "m", "input": "hello"}] * 2


def test_canonical_answer_ignores_sampling_fields_and_floats_are_accepted() -> None:
    provider = MockProvider()
    base = provider.canonical_answer({"model": "m", "input": "hello"})
    assert provider.canonical_answer({"model": "m", "input": "hello", "temperature": 0.7}) == base
    assert provider.canonical_answer({"model": "m", "input": "hello", "seed": 4}) == base
    assert provider.canonical_answer({"model": "m", "input": "other"}) != base
    assert provider({"model": "m", "input": "hello", "extra": 1.5})["finish_reason"] == "stop"


def test_match_rate_controls_the_variant_fraction() -> None:
    always = MockProvider(seed=2, match_rate=1.0)
    never = MockProvider(seed=2, match_rate=0.0)
    request = {"model": "m", "input": "hello"}
    assert all(always(request)["text"] == always.canonical_answer(request) for _ in range(20))
    assert all("variant" in never(request)["text"] for _ in range(20))


def test_drift_modes_change_the_observable_result() -> None:
    request = {"model": "m", "input": "hello", "seed": 0}
    baseline = MockProvider(seed=3)(request)
    longer = MockProvider(seed=3, drift=Drift(length_delta=7))(request)
    assert len(longer["text"]) == len(baseline["text"]) + 7
    swapped = MockProvider(seed=3, drift=Drift(swap_tool="search"))(request)
    assert swapped["tool_calls"][0]["name"] == "search"
    assert baseline["tool_calls"][0]["name"] == "lookup"
    refused = MockProvider(seed=3, drift=Drift(refusal_rate=1.0))(request)
    assert (refused["text"], refused["finish_reason"], refused["refusal"]) == (
        "",
        "content_filter",
        "filtered",
    )
    truncated = MockProvider(seed=3, drift=Drift(truncation_rate=1.0))(request)
    assert truncated["finish_reason"] == "length"
    dropped = MockProvider(seed=3, drift=Drift(match_rate=0.0))(request)
    assert "variant" in dropped["text"]


def test_latency_drift_sleeps() -> None:
    import time

    provider = MockProvider(seed=3, drift=Drift(latency_s=0.02))
    started = time.perf_counter()
    provider({"model": "m", "input": "hello"})
    assert time.perf_counter() - started >= 0.02


def test_identity_safe_handles_lists_tuples_and_foreign_types() -> None:
    from decimal import Decimal

    from epicormic.mock import _identity_safe

    assert _identity_safe({"a": [1, (2, 3.5)], "b": Decimal("1.5")}) == {
        "a": [1, [2, "3.5"]],
        "b": "Decimal('1.5')",
    }
    provider = MockProvider()
    assert provider({"input": ["x", "y"], "extra": Decimal("2")})["finish_reason"] == "stop"
