"""Scorers, references, and score tables (docs/PLAN.md, section 7)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pollard import MemoryStore
from pollard import revalidation as pollard_revalidation

from epicormic.mock import Drift, MockProvider
from epicormic.observation import Observation, find_window_headers, read_window
from epicormic.panel import Panel
from epicormic.scorers import (
    BUILTIN_SCORERS,
    DEFAULT_SCORER_NAMES,
    CostUSD,
    EnergyJoules,
    ExactMatch,
    FinishInfo,
    InputTokens,
    Kind,
    LatencySeconds,
    NormalizedMatch,
    OutputChars,
    OutputTokens,
    Reference,
    Refusal,
    ToolCallCount,
    ToolCallSetJaccard,
    Truncated,
    build_reference,
    build_references,
    finish_info,
    normalized_projection,
    projection_key,
    resolve_scorers,
    score_window,
    tool_call_set,
)
from epicormic.window import observe

# --- result fixtures in the shapes pollard's adapters store ---------------------

USAGE = {"input_tokens": 12, "output_tokens": 5}


def openai_chat(
    text: str = "Hi", finish: str = "stop", refusal: str | None = None, tools: bool = True
) -> dict[str, Any]:
    calls = (
        [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "lookup", "arguments": '{"q": "x"}'},
            }
        ]
        if tools
        else []
    )
    message: dict[str, Any] = {"role": "assistant", "content": text, "refusal": refusal}
    if calls:
        message["tool_calls"] = calls
    result: dict[str, Any] = {
        "id": "chatcmpl-1",
        "model": "gpt-x",
        "choices": [{"index": 0, "finish_reason": finish, "message": message}],
        "usage": {**USAGE, "prompt_tokens": 12, "completion_tokens": 5},
        "text": text,
    }
    if calls:
        result["tool_calls"] = calls
    return result


def openai_responses(
    text: str = "Hi", status: str = "completed", reason: str | None = None, refusal: bool = False
) -> dict[str, Any]:
    content: list[dict[str, Any]] = (
        [{"type": "refusal", "refusal": "no"}]
        if refusal
        else [{"type": "output_text", "text": text}]
    )
    calls = [{"call_id": "c1", "name": "lookup", "arguments": '{"q": "x"}'}]
    result: dict[str, Any] = {
        "id": "resp_1",
        "status": status,
        "incomplete_details": {"reason": reason} if reason else None,
        "output": [
            {"type": "message", "content": content},
            {"type": "function_call", "call_id": "c1", "name": "lookup", "arguments": '{"q": "x"}'},
        ],
        "usage": USAGE,
        "tool_calls": calls,
    }
    if not refusal:
        result["text"] = text
    return result


def anthropic(text: str = "Hi", stop: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_1",
        "stop_reason": stop,
        "content": [
            {"type": "text", "text": text},
            {"type": "tool_use", "id": "tu1", "name": "lookup", "input": {"q": "x"}},
        ],
        "usage": USAGE,
        "text": text,
        "tool_calls": [{"id": "tu1", "name": "lookup", "input": {"q": "x"}}],
    }


def bedrock(text: str = "Hi", stop: str = "end_turn") -> dict[str, Any]:
    return {
        "stopReason": stop,
        "output": {
            "message": {
                "content": [
                    {"text": text},
                    {"toolUse": {"toolUseId": "t1", "name": "lookup", "input": {"q": "x"}}},
                ]
            }
        },
        "usage": {**USAGE, "inputTokens": 12, "outputTokens": 5},
        "text": text,
        "tool_calls": [{"toolUseId": "t1", "name": "lookup", "input": {"q": "x"}}],
    }


def observation(
    result: Any, meta: dict[str, Any] | None = None, digest: str | None = "d"
) -> Observation:
    return Observation(
        node_id="n",
        window_id="w",
        probe_id="p",
        probe_index=0,
        attempt=0,
        payload={"input": "x"},
        result=result,
        result_digest=digest,
        meta=meta if meta is not None else {"duration_s": 0.25, "usage": USAGE},
    )


# --- projection and tool sets -------------------------------------------------


@pytest.mark.parametrize(
    "result", [openai_chat(), openai_responses(), anthropic(), bedrock(), {"a": 1, "usage": {}}]
)
def test_projection_matches_pollard(result: Any) -> None:
    assert normalized_projection(result) == pollard_revalidation._model_semantics(result)


def test_projection_passes_non_dict_results_through() -> None:
    assert normalized_projection("raw") == "raw"
    assert normalized_projection(None) is None


leaf = st.none() | st.booleans() | st.integers() | st.text(max_size=12)
results = st.dictionaries(
    st.sampled_from(["text", "tool_calls", "refusal", "structured_output", "usage", "chunks", "x"]),
    st.recursive(
        leaf,
        lambda c: st.lists(c, max_size=3) | st.dictionaries(st.text(max_size=5), c),
        max_leaves=8,
    ),
    max_size=5,
)


@given(results)
def test_projection_matches_pollard_on_generated_results(result: dict[str, Any]) -> None:
    assert normalized_projection(result) == pollard_revalidation._model_semantics(result)


def test_tool_call_sets_agree_across_adapter_shapes() -> None:
    expected = tool_call_set(openai_chat())
    assert expected is not None and len(expected) == 1
    assert json.loads(next(iter(expected))) == {"name": "lookup", "arguments": {"q": "x"}}
    assert tool_call_set(openai_responses()) == expected
    assert tool_call_set(anthropic()) == expected
    assert tool_call_set(bedrock()) == expected
    assert tool_call_set(openai_chat(tools=False)) is None
    assert tool_call_set("raw") is None
    assert tool_call_set({"tool_calls": ["weird"]}) == frozenset({'"weird"'})


def test_projection_key_is_stable_and_float_safe() -> None:
    first = projection_key(
        {"text": "a", "tool_calls": [{"id": "1", "name": "t", "arguments": '{"p": 1.5}'}]}
    )
    second = projection_key(
        {"tool_calls": [{"id": "2", "name": "t", "arguments": '{"p": 1.5}'}], "text": "a"}
    )
    assert first == second
    assert projection_key({"x": object()}).startswith('{"x":"<object object')


# --- finish detection ---------------------------------------------------------


@pytest.mark.parametrize(
    ("result", "truncated", "refused"),
    [
        (openai_chat(), False, False),
        (openai_chat(finish="length"), True, False),
        (openai_chat(finish="content_filter"), False, True),
        (openai_chat(refusal="no"), False, True),
        (openai_responses(), False, False),
        (openai_responses(status="incomplete", reason="max_output_tokens"), True, False),
        (openai_responses(status="incomplete", reason="content_filter"), False, True),
        (openai_responses(refusal=True), False, True),
        (anthropic(), False, False),
        (anthropic(stop="max_tokens"), True, False),
        (anthropic(stop="refusal"), False, True),
        (bedrock(), False, False),
        (bedrock(stop="max_tokens"), True, False),
        (bedrock(stop="content_filtered"), False, True),
        (bedrock(stop="guardrail_intervened"), False, True),
        ({"finish_reason": "stop", "text": "x"}, False, False),
        ({"finish_reason": "length", "text": "x"}, True, False),
        ({"finish_reason": "content_filter", "refusal": "filtered"}, False, True),
        ({"refusal": None, "text": "x"}, False, False),
    ],
)
def test_finish_info_across_shapes(result: Any, truncated: bool, refused: bool) -> None:
    info = finish_info(result)
    assert info is not None
    assert (info.truncated, info.refused) == (truncated, refused)


@pytest.mark.parametrize("result", ["raw", {"text": "x"}, {"status": "weird"}, None])
def test_finish_info_is_none_when_unrecognizable(result: Any) -> None:
    assert finish_info(result) is None


def test_responses_refusal_detection_tolerates_odd_output_shapes() -> None:
    assert finish_info({"status": "completed", "output": "text"}) == FinishInfo(False, False)
    odd = {"status": "completed", "output": ["x", {"type": "message", "content": "str"}]}
    assert finish_info(odd) == FinishInfo(False, False)


def test_non_json_string_arguments_are_kept_verbatim() -> None:
    result = {"tool_calls": [{"name": "t", "arguments": "not json"}]}
    assert normalized_projection(result) == {"tool_calls": [{"name": "t", "arguments": "not json"}]}
    assert normalized_projection(result) == pollard_revalidation._model_semantics(result)


# --- built-in scorers ---------------------------------------------------------


def test_reference_free_scorers_read_meta_and_result() -> None:
    obs = observation(
        openai_chat(),
        meta={
            "duration_s": 0.5,
            "usage": USAGE,
            "charges": {"usd": 0.001, "tokens": 17},
            "joules": 2.5,
        },
    )
    assert LatencySeconds().score(obs, None) == 0.5
    assert OutputTokens().score(obs, None) == 5
    assert InputTokens().score(obs, None) == 12
    assert CostUSD().score(obs, None) == 0.001
    assert EnergyJoules().score(obs, None) == 2.5
    assert OutputChars().score(obs, None) == 2
    assert ToolCallCount().score(obs, None) == 1
    assert Refusal().score(obs, None) == 0
    assert Truncated().score(obs, None) == 0


def test_reference_free_scorers_return_none_when_undefined() -> None:
    bare = observation({"text": "hello"}, meta={})
    assert LatencySeconds().score(bare, None) is None
    assert OutputTokens().score(bare, None) is None
    assert InputTokens().score(bare, None) is None
    assert CostUSD().score(bare, None) is None
    assert EnergyJoules().score(bare, None) is None
    assert Refusal().score(bare, None) is None
    assert Truncated().score(bare, None) is None
    assert OutputChars().score(bare, None) == 5
    assert ToolCallCount().score(bare, None) == 0
    assert ToolCallCount().score(observation("raw"), None) is None
    assert OutputChars().score(observation(None), None) is None
    assert OutputChars().score(observation({"a": "bc"}), None) == len('{"a":"bc"}')
    usage_in_result = observation({"text": "x", "usage": {"output_tokens": 3}}, meta={})
    assert OutputTokens().score(usage_in_result, None) == 3
    assert (
        OutputTokens().score(
            observation({"text": "x"}, meta={"usage": {"output_tokens": "3"}}), None
        )
        is None
    )
    assert CostUSD().score(observation({"text": "x"}, meta={"charges": "bad"}), None) is None


def test_reference_based_scorers() -> None:
    base = observation(openai_chat(), digest="digest-a")
    reference = build_reference(
        "p", [base, base, observation(openai_chat(text="Other"), digest="digest-b")]
    )
    assert reference.modal_result_digest == "digest-a"
    assert reference.observations == 3
    assert ExactMatch().score(base, reference) == 1
    assert ExactMatch().score(observation(openai_chat(), digest="digest-b"), reference) == 0
    assert ExactMatch().score(observation(openai_chat(), digest=None), reference) is None
    assert ExactMatch().score(base, None) is None
    assert NormalizedMatch().score(observation(openai_chat()), reference) == 1
    assert NormalizedMatch().score(observation(openai_chat(text="Other")), reference) == 0
    # pollard keeps each adapter's tool-call shape after stripping ids, so the
    # same call recorded through another adapter is not a normalized match;
    # tool_call_set is the shape-independent signal.
    assert NormalizedMatch().score(observation(anthropic()), reference) == 0
    assert NormalizedMatch().score(base, None) is None
    assert ToolCallSetJaccard().score(observation(bedrock()), reference) == 1.0
    assert ToolCallSetJaccard().score(observation(openai_chat(tools=False)), reference) == 0.0
    assert ToolCallSetJaccard().score(base, None) is None
    two_tools = {
        "text": "Hi",
        "tool_calls": [
            {"name": "lookup", "arguments": {"q": "x"}},
            {"name": "other", "arguments": {}},
        ],
    }
    assert ToolCallSetJaccard().score(observation(two_tools), reference) == 0.5
    no_tools_reference = build_reference("p", [observation(openai_chat(tools=False))])
    assert no_tools_reference.modal_tool_set is None
    assert (
        ToolCallSetJaccard().score(observation(openai_chat(tools=False)), no_tools_reference)
        is None
    )
    assert ToolCallSetJaccard().score(base, no_tools_reference) == 0.0
    empty = build_reference("p", [])
    assert empty == Reference("p", 0, None, None, None, {})
    assert ExactMatch().score(base, empty) is None
    assert NormalizedMatch().score(base, empty) is None


def test_reference_medians_cover_reference_free_scalars_only() -> None:
    scorers = resolve_scorers()
    observations = [
        observation(
            openai_chat(),
            meta={"duration_s": value, "usage": {"input_tokens": 1, "output_tokens": value * 10}},
        )
        for value in (0.1, 0.3, 0.2)
    ]
    reference = build_reference("p", observations, scorers)
    assert reference.medians == {"output_chars": 2.0, "output_tokens": 2.0, "latency_s": 0.2}


def test_mode_breaks_ties_by_first_occurrence() -> None:
    reference = build_reference(
        "p", [observation(openai_chat(), digest="b"), observation(openai_chat(), digest="a")]
    )
    assert reference.modal_result_digest == "b"


# --- registry -----------------------------------------------------------------


def test_registry_and_resolution() -> None:
    assert set(DEFAULT_SCORER_NAMES) <= set(BUILTIN_SCORERS)
    scorers = resolve_scorers()
    assert tuple(scorer.name for scorer in scorers) == DEFAULT_SCORER_NAMES
    with pytest.raises(KeyError, match="unknown scorer"):
        resolve_scorers(["nope"])
    with pytest.raises(ValueError, match="listed twice"):
        resolve_scorers(["refusal", "refusal"])
    with pytest.raises(ValueError, match="at least one"):
        resolve_scorers([])

    class Custom:
        name = "custom"
        kind: Kind = "scalar"
        reference_based = False

        def score(self, obs: Observation, reference: Reference | None) -> float | None:
            return 42.0

    resolved = resolve_scorers(["custom", "refusal"], extra={"custom": Custom()})
    assert [scorer.name for scorer in resolved] == ["custom", "refusal"]
    shadow = resolve_scorers(["refusal"], extra={"refusal": Custom()})
    assert isinstance(shadow[0], Custom)


# --- score tables over real windows -------------------------------------------


def test_score_window_reflects_injected_drift(panel: Panel) -> None:
    store = MemoryStore()
    observe(panel, window_id="base", samples=10, fn=MockProvider(seed=1), store=store)
    drifted = MockProvider(
        seed=2, drift=Drift(match_rate=0.3, length_delta=12, swap_tool="search", refusal_rate=0.2)
    )
    observe(panel, window_id="cur", samples=10, fn=drifted, store=store)
    base = read_window(store, find_window_headers(store, panel.digest, "base")[0])
    cur = read_window(store, find_window_headers(store, panel.digest, "cur")[0])
    scorers = resolve_scorers()
    references = build_references([base], scorers)
    assert set(references) == {"p0", "p1", "p2"}
    base_table = score_window(base, scorers, references)
    cur_table = score_window(cur, scorers, references)
    assert base_table.window_id == "base"
    assert base_table.scorer_names == DEFAULT_SCORER_NAMES
    assert base_table.probe_ids == ("p0", "p1", "p2")
    for probe_id in base_table.probe_ids:
        assert len(base_table.values["normalized_match"][probe_id]) == 10
        assert base_table.missing("normalized_match", probe_id) == 0
        base_match = sum(base_table.series("normalized_match", probe_id)) / 10
        cur_match = sum(cur_table.series("normalized_match", probe_id)) / 10
        assert base_match >= 0.7 > cur_match
        assert sum(base_table.series("tool_call_set_jaccard", probe_id)) == 10
        assert sum(cur_table.series("tool_call_set_jaccard", probe_id)) == 0
        assert sum(cur_table.series("refusal", probe_id)) >= 1
        assert sum(base_table.series("refusal", probe_id)) == 0
        base_chars = sum(base_table.series("output_chars", probe_id)) / 10
        cur_chars = sum(cur_table.series("output_chars", probe_id)) / 10
        assert cur_chars > base_chars


def test_score_window_without_references_leaves_reference_scorers_undefined(panel: Panel) -> None:
    store = MemoryStore()
    observe(panel, window_id="w", samples=2, fn=MockProvider(seed=1), store=store)
    view = read_window(store, find_window_headers(store, panel.digest, "w")[0])
    table = score_window(view, resolve_scorers())
    assert table.missing("normalized_match", "p0") == 2
    assert table.series("normalized_match", "p0") == ()
    assert table.missing("output_chars", "p0") == 0


def test_build_references_pools_several_windows(panel: Panel) -> None:
    store = MemoryStore()
    observe(panel, window_id="a", samples=2, fn=MockProvider(seed=1), store=store)
    observe(panel, window_id="b", samples=3, fn=MockProvider(seed=1), store=store)
    views = [
        read_window(store, find_window_headers(store, panel.digest, window_id)[0])
        for window_id in ("a", "b")
    ]
    references = build_references(views)
    assert {probe_id: ref.observations for probe_id, ref in references.items()} == {
        "p0": 5,
        "p1": 5,
        "p2": 5,
    }
