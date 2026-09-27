"""Scorers: value-free signals over observations (docs/PLAN.md, section 7).

A scorer reduces one ``Observation`` to one number (or ``None`` when the
signal is undefined for that observation). Indicator scorers return 0 or 1;
scalar scorers return a measurement. Reference-based scorers compare the
observation with a ``Reference`` built from the baseline observations of the
same probe: the modal result digest, the modal normalized projection, and
the modal tool-call set.

The normalized projection reproduces pollard's ``NormalizedModelComparator``
semantics (documented in pollard docs/revalidation.md): ``text``,
``tool_calls`` with provider-generated ids and indexes removed and JSON
string arguments parsed, ``refusal``, and ``structured_output``; when none
of those names is present, every field except ``usage``, ``provider_usage``,
and ``chunks``. A conformance test compares it with pollard's private
implementation.

Finish-reason detection covers the result shapes pollard's adapters store:
OpenAI Chat Completions (``choices[0].finish_reason``,
``choices[0].message.refusal``), OpenAI Responses (``status``,
``incomplete_details.reason``, refusal content blocks), Anthropic Messages
(``stop_reason``), Amazon Bedrock Converse (``stopReason``), and the plain
``finish_reason`` field that the mock provider and LiteLLM-style results use.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from .observation import Observation, WindowView

Kind = Literal["indicator", "scalar"]

__all__ = [
    "BUILTIN_SCORERS",
    "DEFAULT_SCORER_NAMES",
    "CostUSD",
    "EnergyJoules",
    "ExactMatch",
    "FinishInfo",
    "InputTokens",
    "Kind",
    "LatencySeconds",
    "NormalizedMatch",
    "OutputChars",
    "OutputTokens",
    "Reference",
    "Refusal",
    "ScoreTable",
    "Scorer",
    "ToolCallCount",
    "ToolCallSetJaccard",
    "Truncated",
    "build_reference",
    "build_references",
    "finish_info",
    "normalized_projection",
    "projection_key",
    "resolve_scorers",
    "score_window",
    "tool_call_set",
]


class Scorer(Protocol):
    """One value-free signal over an observation.

    ``name`` identifies the scorer in configurations and verdicts, ``kind``
    is ``"indicator"`` (values 0 or 1) or ``"scalar"``, and
    ``reference_based`` says whether ``score`` needs the probe's ``Reference``.
    Class attributes or read-only properties both satisfy the protocol.
    """

    @property
    def name(self) -> str: ...

    @property
    def kind(self) -> Kind: ...

    @property
    def reference_based(self) -> bool: ...

    def score(self, observation: Observation, reference: Reference | None) -> float | None: ...


@dataclass(frozen=True)
class Reference:
    """Baseline summary of one probe, built from its baseline observations."""

    probe_id: str
    observations: int
    modal_result_digest: str | None
    modal_projection_key: str | None
    modal_tool_set: frozenset[str] | None
    medians: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class FinishInfo:
    """How a result ended, as far as the stored shape reveals."""

    truncated: bool
    refused: bool


# --- result helpers ----------------------------------------------------------


def normalized_projection(result: Any) -> Any:
    """pollard's normalized model semantics of a stored result."""

    if not isinstance(result, dict):
        return result
    names = ("text", "tool_calls", "refusal", "structured_output")
    if any(name in result for name in names):
        projected: dict[str, Any] = {}
        for name in names:
            if name not in result:
                continue
            value = result[name]
            projected[name] = _normalize_tool_calls(value) if name == "tool_calls" else value
        return projected
    ignored = {"usage", "provider_usage", "chunks"}
    return {key: value for key, value in result.items() if key not in ignored}


def projection_key(result: Any) -> str:
    """Stable text form of the normalized projection, used for modal matching."""

    return json.dumps(
        normalized_projection(result),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=repr,
    )


def tool_call_set(result: Any) -> frozenset[str] | None:
    """The set of (name, arguments) keys of the result's tool calls; ``None`` without any."""

    if not isinstance(result, dict):
        return None
    calls = result.get("tool_calls")
    if not isinstance(calls, list) or not calls:
        return None
    keys: set[str] = set()
    for call in _normalize_tool_calls(calls):
        if not isinstance(call, dict):
            keys.add(json.dumps(call, sort_keys=True, default=repr))
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else None
        name = call.get("name", function.get("name") if function else None)
        arguments = call.get("arguments", call.get("input"))
        if arguments is None and function is not None:
            arguments = function.get("arguments")
        keys.add(
            json.dumps(
                {"name": name, "arguments": arguments},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                default=repr,
            )
        )
    return frozenset(keys)


def finish_info(result: Any) -> FinishInfo | None:
    """Detect truncation and refusal across adapter shapes; ``None`` when unrecognizable."""

    if not isinstance(result, dict):
        return None
    truncated = False
    refused = bool(result.get("refusal"))
    known = "refusal" in result

    reason = result.get("finish_reason")
    if isinstance(reason, str):
        known = True
        truncated |= reason == "length"
        refused |= reason == "content_filter"

    choices = result.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        first = choices[0]
        known = True
        reason = first.get("finish_reason")
        truncated |= reason == "length"
        refused |= reason == "content_filter"
        message = first.get("message")
        if isinstance(message, dict):
            refused |= bool(message.get("refusal"))

    status = result.get("status")
    details = result.get("incomplete_details")
    if isinstance(status, str) and status in {"completed", "incomplete", "failed"}:
        known = True
        if isinstance(details, dict):
            truncated |= details.get("reason") == "max_output_tokens"
            refused |= details.get("reason") == "content_filter"
        refused |= _responses_has_refusal_block(result.get("output"))

    stop = result.get("stop_reason")
    if isinstance(stop, str):
        known = True
        truncated |= stop == "max_tokens"
        refused |= stop == "refusal"

    stop = result.get("stopReason")
    if isinstance(stop, str):
        known = True
        truncated |= stop == "max_tokens"
        refused |= stop in {"content_filtered", "guardrail_intervened"}

    return FinishInfo(truncated=truncated, refused=refused) if known else None


def _responses_has_refusal_block(output: Any) -> bool:
    if not isinstance(output, list):
        return False
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "refusal":
                return True
    return False


def _normalize_tool_calls(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    return [_normalize_tool_call(item) for item in value]


def _normalize_tool_call(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    normalized: dict[Any, Any] = {}
    for key, item in value.items():
        if key in {"id", "call_id", "toolUseId", "index"}:
            continue
        if key == "function" and isinstance(item, dict):
            function = dict(item)
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                function["arguments"] = _parse_json(arguments)
            normalized[key] = function
        elif key in {"arguments", "input_json"} and isinstance(item, str):
            normalized[key] = _parse_json(item)
        else:
            normalized[key] = item
    return normalized


def _parse_json(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _usage(observation: Observation) -> Mapping[str, Any] | None:
    usage = observation.meta.get("usage")
    if isinstance(usage, dict):
        return usage
    if isinstance(observation.result, dict):
        result_usage = observation.result.get("usage")
        if isinstance(result_usage, dict):
            return result_usage
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


# --- built-in scorers ----------------------------------------------------------


@dataclass(frozen=True)
class ExactMatch:
    name: str = "exact_match"
    kind: Kind = "indicator"
    reference_based: bool = True

    def score(self, observation: Observation, reference: Reference | None) -> float | None:
        if reference is None or reference.modal_result_digest is None:
            return None
        if observation.result_digest is None:
            return None
        return float(observation.result_digest == reference.modal_result_digest)


@dataclass(frozen=True)
class NormalizedMatch:
    name: str = "normalized_match"
    kind: Kind = "indicator"
    reference_based: bool = True

    def score(self, observation: Observation, reference: Reference | None) -> float | None:
        if reference is None or reference.modal_projection_key is None:
            return None
        return float(projection_key(observation.result) == reference.modal_projection_key)


@dataclass(frozen=True)
class ToolCallSetJaccard:
    name: str = "tool_call_set_jaccard"
    kind: Kind = "scalar"
    reference_based: bool = True

    def score(self, observation: Observation, reference: Reference | None) -> float | None:
        if reference is None:
            return None
        mine = tool_call_set(observation.result)
        theirs = reference.modal_tool_set
        if mine is None and theirs is None:
            return None
        if not mine or not theirs:
            return 0.0
        return len(mine & theirs) / len(mine | theirs)


@dataclass(frozen=True)
class ToolCallCount:
    name: str = "tool_call_count"
    kind: Kind = "scalar"
    reference_based: bool = False

    def score(self, observation: Observation, reference: Reference | None) -> float | None:
        if not isinstance(observation.result, dict):
            return None
        calls = observation.result.get("tool_calls")
        return float(len(calls)) if isinstance(calls, list) else 0.0


@dataclass(frozen=True)
class Refusal:
    name: str = "refusal"
    kind: Kind = "indicator"
    reference_based: bool = False

    def score(self, observation: Observation, reference: Reference | None) -> float | None:
        info = finish_info(observation.result)
        return None if info is None else float(info.refused)


@dataclass(frozen=True)
class Truncated:
    name: str = "truncated"
    kind: Kind = "indicator"
    reference_based: bool = False

    def score(self, observation: Observation, reference: Reference | None) -> float | None:
        info = finish_info(observation.result)
        return None if info is None else float(info.truncated)


@dataclass(frozen=True)
class OutputChars:
    name: str = "output_chars"
    kind: Kind = "scalar"
    reference_based: bool = False

    def score(self, observation: Observation, reference: Reference | None) -> float | None:
        result = observation.result
        if isinstance(result, dict) and isinstance(result.get("text"), str):
            return float(len(result["text"]))
        if result is None:
            return None
        return float(len(projection_key(result)))


@dataclass(frozen=True)
class OutputTokens:
    name: str = "output_tokens"
    kind: Kind = "scalar"
    reference_based: bool = False

    def score(self, observation: Observation, reference: Reference | None) -> float | None:
        usage = _usage(observation)
        return None if usage is None else _number(usage.get("output_tokens"))


@dataclass(frozen=True)
class InputTokens:
    name: str = "input_tokens"
    kind: Kind = "scalar"
    reference_based: bool = False

    def score(self, observation: Observation, reference: Reference | None) -> float | None:
        usage = _usage(observation)
        return None if usage is None else _number(usage.get("input_tokens"))


@dataclass(frozen=True)
class LatencySeconds:
    name: str = "latency_s"
    kind: Kind = "scalar"
    reference_based: bool = False

    def score(self, observation: Observation, reference: Reference | None) -> float | None:
        return _number(observation.meta.get("duration_s"))


@dataclass(frozen=True)
class CostUSD:
    name: str = "cost_usd"
    kind: Kind = "scalar"
    reference_based: bool = False

    def score(self, observation: Observation, reference: Reference | None) -> float | None:
        charges = observation.meta.get("charges")
        if not isinstance(charges, dict):
            return None
        return _number(charges.get("usd"))


@dataclass(frozen=True)
class EnergyJoules:
    name: str = "energy_j"
    kind: Kind = "scalar"
    reference_based: bool = False

    def score(self, observation: Observation, reference: Reference | None) -> float | None:
        return _number(observation.meta.get("joules"))


_BUILTINS: tuple[Scorer, ...] = (
    ExactMatch(),
    NormalizedMatch(),
    ToolCallSetJaccard(),
    ToolCallCount(),
    Refusal(),
    Truncated(),
    OutputChars(),
    OutputTokens(),
    InputTokens(),
    LatencySeconds(),
    CostUSD(),
    EnergyJoules(),
)
BUILTIN_SCORERS: Mapping[str, Scorer] = {scorer.name: scorer for scorer in _BUILTINS}

DEFAULT_SCORER_NAMES: tuple[str, ...] = (
    "normalized_match",
    "exact_match",
    "tool_call_set_jaccard",
    "refusal",
    "truncated",
    "output_chars",
    "output_tokens",
    "latency_s",
)


def resolve_scorers(
    names: Iterable[str] = DEFAULT_SCORER_NAMES,
    extra: Mapping[str, Scorer] | None = None,
) -> tuple[Scorer, ...]:
    """Map scorer names to instances; caller scorers in ``extra`` may shadow built-ins."""

    registry: dict[str, Scorer] = dict(BUILTIN_SCORERS)
    if extra:
        registry.update(extra)
    scorers: list[Scorer] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise ValueError(f"scorer {name!r} listed twice")
        if name not in registry:
            raise KeyError(f"unknown scorer {name!r}; known: {sorted(registry)}")
        seen.add(name)
        scorers.append(registry[name])
    if not scorers:
        raise ValueError("at least one scorer is required")
    return tuple(scorers)


# --- references and score tables ---------------------------------------------


def build_reference(
    probe_id: str,
    observations: Sequence[Observation],
    scorers: Sequence[Scorer] = (),
) -> Reference:
    """Summarize the baseline observations of one probe.

    The modal digest, projection, and tool set are the most common values,
    ties broken by first occurrence. ``medians`` holds the median score of
    every reference-free scalar scorer over these observations.
    """

    digests = Counter(obs.result_digest for obs in observations if obs.result_digest is not None)
    projections = Counter(projection_key(obs.result) for obs in observations)
    tool_sets = Counter(
        tool_set for tool_set in (tool_call_set(obs.result) for obs in observations)
        if tool_set is not None
    )
    medians: dict[str, float] = {}
    for scorer in scorers:
        if scorer.reference_based or scorer.kind != "scalar":
            continue
        values = [
            value
            for value in (scorer.score(obs, None) for obs in observations)
            if value is not None
        ]
        if values:
            medians[scorer.name] = float(statistics.median(values))
    return Reference(
        probe_id=probe_id,
        observations=len(observations),
        modal_result_digest=_mode(digests),
        modal_projection_key=_mode(projections),
        modal_tool_set=_mode(tool_sets),
        medians=medians,
    )


def build_references(
    views: Sequence[WindowView], scorers: Sequence[Scorer] = ()
) -> dict[str, Reference]:
    """References for every probe across one or more baseline windows, pooled."""

    grouped: dict[str, list[Observation]] = {}
    for view in views:
        for probe in view.probes:
            grouped.setdefault(probe.probe_id, []).extend(probe.observations)
    return {
        probe_id: build_reference(probe_id, observations, scorers)
        for probe_id, observations in grouped.items()
    }


def _mode(counter: Counter[Any]) -> Any:
    if not counter:
        return None
    best = max(counter.values())
    for value, count in counter.items():
        if count == best:
            return value
    return None  # pragma: no cover - unreachable, counter is non-empty


@dataclass(frozen=True)
class ScoreTable:
    """Scores of one window: scorer name -> probe id -> per-attempt values.

    ``None`` marks a signal that is undefined for that observation.
    """

    window_id: str
    scorer_names: tuple[str, ...]
    probe_ids: tuple[str, ...]
    values: Mapping[str, Mapping[str, tuple[float | None, ...]]]

    def series(self, scorer: str, probe_id: str) -> tuple[float, ...]:
        """Defined values of one scorer for one probe, in attempt order."""

        return tuple(value for value in self.values[scorer][probe_id] if value is not None)

    def missing(self, scorer: str, probe_id: str) -> int:
        return sum(1 for value in self.values[scorer][probe_id] if value is None)


def score_window(
    view: WindowView,
    scorers: Sequence[Scorer],
    references: Mapping[str, Reference] | None = None,
) -> ScoreTable:
    """Score every observation of a window; reference-based scorers need ``references``."""

    values: dict[str, dict[str, tuple[float | None, ...]]] = {
        scorer.name: {} for scorer in scorers
    }
    for probe in view.probes:
        reference = references.get(probe.probe_id) if references else None
        for scorer in scorers:
            values[scorer.name][probe.probe_id] = tuple(
                scorer.score(obs, reference) for obs in probe.observations
            )
    return ScoreTable(
        window_id=view.window_id,
        scorer_names=tuple(scorer.name for scorer in scorers),
        probe_ids=tuple(probe.probe_id for probe in view.probes),
        values=values,
    )
