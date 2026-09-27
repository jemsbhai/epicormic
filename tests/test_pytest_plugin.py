"""The pytest plugin: fixture, marker, options (docs/PLAN.md, section 12.1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pollard import SQLiteStore

from epicormic.mock import Drift, MockProvider
from epicormic.panel import Panel, Probe
from epicormic.verdict import AnalysisConfig, Monitor, analyze
from epicormic.window import observe

pytest_plugins = ["pytester"]

GATED = """
import pytest

@pytest.mark.epicormic_gate
def test_gated(epicormic_state):
    assert epicormic_state is not None

def test_free():
    assert True
"""


def prepare(path: Path, drifted: bool) -> str:
    panel = Panel(
        name="plugin",
        probes=tuple(Probe(f"p{i}", {"model": "mock", "input": f"q{i}"}) for i in range(6)),
        seed_from_attempt=True,
    )
    store_path = path / "runs.db"
    with SQLiteStore(store_path) as store:
        observe(panel, window_id="base", samples=8, fn=MockProvider(seed=1), store=store)
        provider = MockProvider(seed=2, drift=Drift(match_rate=0.2) if drifted else Drift())
        observe(panel, window_id="cur", samples=8, fn=provider, store=store)
        monitor = Monitor(
            "m",
            panel.digest,
            ("base",),
            AnalysisConfig(scorers=("normalized_match",), permutations=200),
        )
        verdict = analyze(store, monitor, "cur")
        assert verdict.state == ("drift" if drifted else "stable")
    return str(store_path)


def run(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    return pytester.runpytest("-p", "epicormic", *args)


def test_gate_fails_on_drift_and_leaves_unmarked_tests_alone(pytester: pytest.Pytester) -> None:
    store = prepare(pytester.path, drifted=True)
    pytester.makepyfile(GATED)
    result = run(pytester, "--epicormic-store", store, "--epicormic-monitor", "m")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(["*epicormic gate: monitor m is in state 'drift'*window cur*"])


def test_gate_passes_on_stable(pytester: pytest.Pytester) -> None:
    store = prepare(pytester.path, drifted=False)
    pytester.makepyfile(GATED)
    result = run(pytester, "--epicormic-store", store, "--epicormic-monitor", "m")
    result.assert_outcomes(passed=2)


def test_fail_on_warning_and_unknown(pytester: pytest.Pytester) -> None:
    store = prepare(pytester.path, drifted=False)
    pytester.makepyfile(GATED)
    result = run(
        pytester,
        "--epicormic-store",
        store,
        "--epicormic-monitor",
        "nothing",
        "--epicormic-fail-on",
        "drift,unknown",
    )
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(["*monitor nothing is in state 'unknown'*"])
    result = run(
        pytester,
        "--epicormic-store",
        store,
        "--epicormic-monitor",
        "m",
        "--epicormic-fail-on",
        "warning",
    )
    result.assert_outcomes(passed=2)


def test_missing_options_and_bad_states_are_usage_errors(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(GATED)
    result = run(pytester)
    assert result.ret != 0
    result.stdout.fnmatch_lines(["*pass --epicormic-store and --epicormic-monitor*"])
    store = prepare(pytester.path, drifted=False)
    result = run(
        pytester,
        "--epicormic-store",
        store,
        "--epicormic-monitor",
        "m",
        "--epicormic-fail-on",
        "weird",
    )
    assert result.ret != 0
    result.stdout.fnmatch_lines(["*unknown states*"])


def test_panel_digest_option_and_marker_registration(pytester: pytest.Pytester) -> None:
    store = prepare(pytester.path, drifted=False)
    pytester.makepyfile(GATED)
    with SQLiteStore(Path(store)) as opened:
        from epicormic.ledger import Ledger

        entry = Ledger(opened).state("m")
        assert entry is not None
        digest = entry.panel_digest
    result = run(
        pytester,
        "--epicormic-store",
        store,
        "--epicormic-monitor",
        "m",
        "--epicormic-panel-digest",
        digest,
    )
    result.assert_outcomes(passed=2)
    markers = run(pytester, "--markers")
    markers.stdout.fnmatch_lines(["*epicormic_gate*"])
