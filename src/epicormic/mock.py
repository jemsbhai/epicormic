"""A seeded, credential-free step callable with injectable drift.

``MockProvider`` stands in for a hosted model in tests, examples, and A/A
calibration. Every request gets a deterministic answer derived from the
request itself and the provider seed, with sample-to-sample variation that
depends on the request's ``seed`` field when present and on the call order
otherwise. Drift is injected explicitly through ``Drift`` so that a test can
state exactly what changed and by how much.
"""

from __future__ import annotations

import hashlib
import random
import time
from dataclasses import dataclass, field
from typing import Any

from ._canon import canonical_bytes

__all__ = ["Drift", "MockProvider"]


@dataclass(frozen=True)
class Drift:
    """What the mock changes relative to its baseline behaviour.

    ``match_rate`` overrides the probability that the answer equals the
    probe's canonical answer; ``length_delta`` adds characters to every
    answer; ``latency_s`` sleeps before answering; ``refusal_rate`` is the
    probability of a content-filter refusal; ``swap_tool`` renames the tool
    the mock calls; ``truncation_rate`` is the probability of a length stop.
    """

    match_rate: float | None = None
    length_delta: int = 0
    latency_s: float = 0.0
    refusal_rate: float = 0.0
    swap_tool: str | None = None
    truncation_rate: float = 0.0


@dataclass
class MockProvider:
    """Deterministic stand-in for a provider; call it like a pollard step callable."""

    seed: int = 0
    match_rate: float = 0.9
    tool_name: str = "lookup"
    drift: Drift = field(default_factory=Drift)
    calls: int = 0
    requests: list[dict[str, Any]] = field(default_factory=list)

    def canonical_answer(self, request: dict[str, Any]) -> str:
        """The answer the mock gives when it matches; a pure function of the probe content."""

        content = {key: value for key, value in request.items() if key not in _NON_CONTENT}
        digest = hashlib.sha256(canonical_bytes(_identity_safe(content))).hexdigest()[:12]
        return f"answer:{digest}"

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        self.requests.append(dict(request))
        rng = self._rng(request)
        if self.drift.latency_s > 0:
            time.sleep(self.drift.latency_s)
        match_rate = self.match_rate if self.drift.match_rate is None else self.drift.match_rate
        answer = self.canonical_answer(request)
        refused = rng.random() < self.drift.refusal_rate
        truncated = not refused and rng.random() < self.drift.truncation_rate
        if not refused and rng.random() >= match_rate:
            answer = f"{answer}:variant-{rng.randrange(1_000_000)}"
        if self.drift.length_delta:
            answer = answer + "x" * self.drift.length_delta
        tool = self.drift.swap_tool or self.tool_name
        text = "" if refused else answer
        output_tokens = max(1, len(text) // 4)
        input_tokens = max(1, len(str(request.get("input", ""))) // 4) + 8
        return {
            "text": text,
            "finish_reason": "content_filter" if refused else ("length" if truncated else "stop"),
            "refusal": "filtered" if refused else None,
            "tool_calls": [{"name": tool, "arguments": {"q": str(request.get("input", ""))[:16]}}],
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
        }

    def _rng(self, request: dict[str, Any]) -> random.Random:
        material: dict[str, Any] = {"seed": self.seed, "request": _identity_safe(request)}
        if "seed" not in request:
            material["call"] = self.calls
        return random.Random(hashlib.sha256(canonical_bytes(material)).hexdigest())


_NON_CONTENT = frozenset({"seed", "temperature", "top_p", "top_k", "max_output_tokens"})


def _identity_safe(value: Any) -> Any:
    """Make a request hashable under the identity grammar (floats become strings)."""

    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return [_identity_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _identity_safe(item) for key, item in value.items()}
    return repr(value)
