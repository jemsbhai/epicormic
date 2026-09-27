"""Shared fixtures: a small panel and a seeded mock provider."""

from __future__ import annotations

import pytest

from epicormic.mock import MockProvider
from epicormic.panel import Panel, Probe


@pytest.fixture
def panel() -> Panel:
    return Panel(
        name="fixture",
        probes=tuple(
            Probe(f"p{index}", {"model": "mock", "input": f"question {index}"}, family="qa")
            for index in range(3)
        ),
        sampling={"temperature": "0.7", "max_output_tokens": "64"},
        seed_from_attempt=True,
        min_samples=2,
    )


@pytest.fixture
def mock() -> MockProvider:
    return MockProvider(seed=7)
