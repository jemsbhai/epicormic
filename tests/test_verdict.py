"""Verdicts: configuration, monitors, state rules, evidence, chains (docs/PLAN.md, 9 and 10)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pollard import MemoryStore, Node, NodeKind, ReplayContract

from epicormic.mock import Drift, MockProvider
from epicormic.observation import Observation, find_window_headers, read_window
from epicormic.panel import Panel, Probe
from epicormic.scorers import Kind, Reference
from epicormic.stats.sequential import CusumState, EProcessState, PageHinkleyState
from epicormic.verdict import (
    MONITOR_FORMAT,
    STATES,
    VERDICT_FORMAT,
    AnalysisConfig,
    Monitor,
    VerdictError,
    _difference_paths,
    _ProbeTest,
    _ScorerAnalysis,
    _state,
    analyze,
    compute_verdict,
    monitor_label,
    read_chain,
    recompute_verdict,
    record_verdict,
)
from epicormic.window import observe

BEHAVIOURAL = (
    "normalized_match",
    "exact_match",
    "tool_call_set_jaccard",
    "refusal",
    "output_chars",
)


def small_panel(probes: int = 8) -> Panel:
    return Panel(
        name="verdict",
        probes=tuple(
            Probe(f"p{i}", {"model": "mock", "input": f"question {i}"}) for i in range(probes)
        ),
        sampling={"temperature": "0.7"},
        seed_from_attempt=True,
        min_samples=5,
    )


def config(**overrides: Any) -> AnalysisConfig:
    values: dict[str, Any] = {"scorers": BEHAVIOURAL, "permutations": 300}
    values.update(overrides)
    return AnalysisConfig(**values)


@pytest.fixture
def scenario() -> tuple[MemoryStore, Panel]:
    panel = small_panel()
    store = MemoryStore()
    observe(panel, window_id="base", samples=10, fn=MockProvider(seed=1), store=store)
    observe(panel, window_id="aa", samples=10, fn=MockProvider(seed=2), store=store)
    drifted = MockProvider(seed=3, drift=Drift(match_rate=0.4, length_delta=6))
    observe(panel, window_id="drift", samples=10, fn=drifted, store=store)
    return store, panel


# --- configuration ---------------------------------------------------------------


def test_config_defaults_round_trip_and_digest() -> None:
    default = AnalysisConfig()
    document = default.to_document()
    assert AnalysisConfig.from_document(document) == default
    assert AnalysisConfig.from_document(json.loads(json.dumps(document))).digest == default.digest
    assert default.alpha_value == 0.05 and default.q_value == 0.05
    assert default.cusum_values == (0.1, 0.4)
    assert default.page_hinkley_values == (0.05, 0.5)
    assert default.eprocess_values == (0.05, (0.1, 0.25, 0.5, 0.75))
    assert config(alpha="0.01").digest != default.digest


@pytest.mark.parametrize(
    "overrides",
    [
        {"scorers": ()},
        {"scorers": ("refusal", "refusal")},
        {"alpha": "0"},
        {"alpha": "1"},
        {"alpha": "x"},
        {"q": "0"},
        {"effect_min": "1.5"},
        {"unknown_fraction": "-0.1"},
        {"permutations": 0},
        {"seed": -1},
        {"n_min": 0},
        {"top_k": -1},
        {"top_k": True},
        {"base_rate": "1"},
        {"prior_weight": "0"},
        {"cusum": {"k": "0.1", "h": "0"}},
        {"cusum": {"k": "0.1"}},
        {"page_hinkley": {"delta": "0.05", "lambda": "0"}},
        {"eprocess": {"alpha": "1", "grid": ["0.5"]}},
        {"eprocess": {"alpha": "0.05", "grid": []}},
        {"eprocess": {"alpha": "0.05", "grid": ["1"]}},
    ],
)
def test_config_validation(overrides: dict[str, Any]) -> None:
    with pytest.raises(VerdictError):
        AnalysisConfig(**overrides)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("x", "must be an object"),
        ({"format": "other"}, "config format"),
        ({"format": "epicormic/config/v1", "extra": 1}, "unknown keys"),
    ],
)
def test_config_document_rejections(document: Any, message: str) -> None:
    with pytest.raises(VerdictError, match=message):
        AnalysisConfig.from_document(document)


# --- monitor -----------------------------------------------------------------------


def test_monitor_identity_and_validation() -> None:
    panel = small_panel(2)
    monitor = Monitor("m1", panel.digest, ("base", "base2"))
    assert (
        monitor.label
        == monitor_label(panel.digest, "m1")
        == f"epicormic-monitor/{panel.digest[:16]}/m1"
    )
    assert len(monitor.root_id) == 64
    header = monitor.header_payload()
    assert header["format"] == MONITOR_FORMAT
    assert header["config_digest"] == monitor.config.digest
    assert header["baseline_roots"] == list(monitor.baseline_roots)
    with pytest.raises(VerdictError):
        Monitor("a/b", panel.digest, ("base",))
    with pytest.raises(VerdictError):
        Monitor("m", panel.digest, ())
    with pytest.raises(VerdictError):
        Monitor("m", panel.digest, ("base", "base"))


# --- end to end ------------------------------------------------------------------


def test_aa_is_stable_and_drift_is_detected_and_chained(
    scenario: tuple[MemoryStore, Panel],
) -> None:
    store, panel = scenario
    monitor = Monitor("m1", panel.digest, ("base",), config())
    assert read_chain(store, monitor) == (None, [])
    first = analyze(store, monitor, "aa")
    assert first.state == "stable" and first.rule_fired == "no rule fired"
    assert first.sequence_index == 1 and first.node_id is not None
    second = analyze(store, monitor, "drift")
    assert second.state == "drift"
    assert second.rule_fired.startswith("pooled test rejected for")
    assert second.sequence_index == 2
    header_id, chain = read_chain(store, monitor)
    assert header_id is not None and [n.id for n in chain] == [first.node_id, second.node_id]
    assert store.get(header_id).payload == monitor.header_payload()
    index = store.get(monitor.root_id).meta["epicormic"]
    assert index == {
        "latest_verdict": second.node_id,
        "state": "drift",
        "window_id": "drift",
        "sequence_index": 2,
    }
    payload = second.payload
    assert payload["format"] == VERDICT_FORMAT and payload["state"] in STATES
    assert payload["monitor_id"] == "m1" and payload["config_digest"] == monitor.config.digest
    assert payload["current_window_id"] == "drift" and payload["sequence_index"] == 2
    assert payload["baseline_roots"] == list(monitor.baseline_roots)
    assert payload["sample_counts"]["baseline"] == 80 and payload["sample_counts"]["current"] == 80
    assert payload["contract_changed"] is False and payload["non_finite_fields"] == []
    match = payload["scorers"]["normalized_match"]
    assert match["kind"] == "indicator" and match["pooled"]["rejected"] is True
    assert match["pooled"]["probes"] == 8 and match["pooled"]["permutations"] == 300
    assert float(match["pooled"]["T"]) < -0.3
    assert all(entry["method"] == "fisher_exact" for entry in match["per_probe"])
    chars = payload["scorers"]["output_chars"]
    assert chars["kind"] == "scalar"
    assert all(entry["method"].startswith("mann_whitney") for entry in chars["per_probe"])
    assert chars["sequential"]["eprocess"]["mu0"] != "0.5"
    assert second.opinion.belief > first.opinion.belief
    assert payload["opinion"]["belief"] == second.payload["opinion"]["belief"]


def test_payload_is_value_free_and_decimal_encoded(scenario: tuple[MemoryStore, Panel]) -> None:
    store, panel = scenario
    monitor = Monitor("m2", panel.digest, ("base",), config())
    verdict = analyze(store, monitor, "drift")
    text = json.dumps(verdict.payload)
    for window_id in ("base", "drift"):
        view = read_window(store, find_window_headers(store, panel.digest, window_id)[0])
        for obs in view.observations:
            assert obs.result["text"] not in text
    assert not any(isinstance(v, float) for v in _flatten(verdict.payload))


def _flatten(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return [item for v in value.values() for item in _flatten(v)]
    if isinstance(value, list):
        return [item for v in value for item in _flatten(v)]
    return [value]


def test_write_false_does_not_record(scenario: tuple[MemoryStore, Panel]) -> None:
    store, panel = scenario
    monitor = Monitor("m3", panel.digest, ("base",), config())
    verdict = analyze(store, monitor, "aa", write=False)
    assert verdict.node_id is None and read_chain(store, monitor) == (None, [])
    assert compute_verdict(store, monitor, "aa").payload == verdict.payload


def test_recompute_matches_and_detects_changed_observations(
    scenario: tuple[MemoryStore, Panel],
) -> None:
    store, panel = scenario
    monitor = Monitor("m4", panel.digest, ("base",), config())
    first = analyze(store, monitor, "aa")
    second = analyze(store, monitor, "drift")
    for node_id in (first.node_id, second.node_id):
        assert node_id is not None
        _, matches = recompute_verdict(store, monitor, node_id)
        assert matches
    observe(panel, window_id="aa", samples=12, fn=MockProvider(seed=2), store=store)
    assert first.node_id is not None
    _, still = recompute_verdict(store, monitor, first.node_id)
    assert not still
    with pytest.raises(VerdictError, match="not a verdict"):
        recompute_verdict(store, monitor, "0" * 64)


def test_stale_verdict_cannot_be_recorded(scenario: tuple[MemoryStore, Panel]) -> None:
    store, panel = scenario
    monitor = Monitor("m5", panel.digest, ("base",), config())
    stale = compute_verdict(store, monitor, "aa")
    analyze(store, monitor, "aa")
    with pytest.raises(VerdictError, match="sequence index"):
        record_verdict(store, stale)


def test_config_change_needs_a_new_monitor(scenario: tuple[MemoryStore, Panel]) -> None:
    store, panel = scenario
    analyze(store, Monitor("m6", panel.digest, ("base",), config()), "aa")
    changed = Monitor("m6", panel.digest, ("base",), config(alpha="0.01"))
    with pytest.raises(VerdictError, match="different configuration"):
        read_chain(store, changed)


def test_two_headers_or_a_branching_chain_are_rejected(scenario: tuple[MemoryStore, Panel]) -> None:
    store, panel = scenario
    monitor = Monitor("m7", panel.digest, ("base",), config())
    verdict = analyze(store, monitor, "aa")
    extra_header = {**monitor.header_payload(), "monitor_id": "other"}
    store.put(Node.make(kind=NodeKind.NOTE, parent=monitor.root_id, payload=extra_header))
    with pytest.raises(VerdictError, match="headers"):
        read_chain(store, monitor)
    store2, panel2 = MemoryStore(), small_panel(2)
    observe(panel2, window_id="base", samples=5, fn=MockProvider(seed=1), store=store2)
    observe(panel2, window_id="aa", samples=5, fn=MockProvider(seed=2), store=store2)
    monitor2 = Monitor("m8", panel2.digest, ("base",), config())
    verdict = analyze(store2, monitor2, "aa")
    header_id, _ = read_chain(store2, monitor2)
    assert header_id is not None and verdict.node_id is not None
    sibling = {**verdict.payload, "monitor_id": "forged"}
    store2.put(Node.make(kind=NodeKind.NOTE, parent=header_id, payload=sibling))
    with pytest.raises(VerdictError, match="branches"):
        read_chain(store2, monitor2)


# --- unknown states ---------------------------------------------------------------


def test_missing_baseline_or_current_window_is_unknown(scenario: tuple[MemoryStore, Panel]) -> None:
    store, panel = scenario
    missing_baseline = analyze(store, Monitor("m9", panel.digest, ("never",), config()), "aa")
    assert missing_baseline.state == "unknown" and "baseline" in missing_baseline.rule_fired
    assert missing_baseline.payload["scorers"] == {} and missing_baseline.opinion.uncertainty == 1.0
    assert missing_baseline.payload["current_header_id"] is not None
    missing_current = analyze(store, Monitor("m10", panel.digest, ("base",), config()), "never")
    assert missing_current.state == "unknown" and "current" in missing_current.rule_fired
    assert missing_current.payload["current_header_id"] is None
    assert missing_current.sequence_index == 1


def test_too_few_samples_is_unknown(scenario: tuple[MemoryStore, Panel]) -> None:
    store, panel = scenario
    observe(panel, window_id="thin", samples=2, fn=MockProvider(seed=9), store=store)
    verdict = analyze(store, Monitor("m11", panel.digest, ("base",), config()), "thin")
    assert verdict.state == "unknown" and verdict.rule_fired == "too many probes below n_min"
    assert verdict.payload["sample_counts"]["probes_below_n_min"] == [f"p{i}" for i in range(8)]
    assert verdict.opinion.uncertainty == 1.0


def test_no_scored_observations_is_unknown(scenario: tuple[MemoryStore, Panel]) -> None:
    store, panel = scenario

    class Silent:
        name = "silent"
        kind: Kind = "scalar"
        reference_based = False

        def score(self, observation: Observation, reference: Reference | None) -> float | None:
            return None

    monitor = Monitor("m12", panel.digest, ("base",), config(scorers=("silent",)))
    verdict = analyze(store, monitor, "aa", extra_scorers={"silent": Silent()})
    assert verdict.state == "unknown" and verdict.rule_fired == "no scored observations"


# --- contract change, truncation, overflow --------------------------------------


def test_contract_change_is_reported_with_paths() -> None:
    panel = small_panel(3)
    store = MemoryStore()
    v1 = ReplayContract(provider="mock", model_revision="v1")
    v2 = ReplayContract(provider="mock", model_revision="v2")
    observe(panel, window_id="base", samples=6, fn=MockProvider(seed=1), store=store, contract=v1)
    observe(panel, window_id="cur", samples=6, fn=MockProvider(seed=2), store=store, contract=v2)
    observe(panel, window_id="bare", samples=6, fn=MockProvider(seed=2), store=store)
    monitor = Monitor("m13", panel.digest, ("base",), config())
    changed = analyze(store, monitor, "cur")
    assert changed.payload["contract_changed"] is True
    assert changed.payload["contract_difference_paths"] == ["/model_revision"]
    bare = analyze(store, monitor, "bare")
    assert bare.payload["contract_changed"] is True
    assert bare.payload["contract_difference_paths"] == ["/"]
    same = analyze(store, Monitor("m14", panel.digest, ("base",), config()), "base")
    assert same.payload["contract_changed"] is False


def test_top_k_truncation_keeps_rejected_entries(scenario: tuple[MemoryStore, Panel]) -> None:
    store, panel = scenario
    monitor = Monitor("m15", panel.digest, ("base",), config(top_k=2))
    quiet = analyze(store, monitor, "aa")
    match = quiet.payload["scorers"]["normalized_match"]
    assert match["per_probe_truncated"] is True
    assert len(match["per_probe"]) == 2
    loud = analyze(store, monitor, "drift")
    match = loud.payload["scorers"]["normalized_match"]
    assert all(e["rejected"] for e in match["per_probe"][2:])
    assert len(match["per_probe"]) > 2


def test_overwhelming_evidence_is_recorded_without_floats() -> None:
    panel = small_panel(8)
    store = MemoryStore()
    observe(
        panel, window_id="base", samples=20, fn=MockProvider(seed=1, match_rate=1.0), store=store
    )
    observe(
        panel,
        window_id="cur",
        samples=20,
        fn=MockProvider(seed=2, drift=Drift(match_rate=0.0)),
        store=store,
    )
    monitor = Monitor("m16", panel.digest, ("base",), config(scorers=("normalized_match",)))
    verdict = analyze(store, monitor, "cur")
    eprocess = verdict.payload["scorers"]["normalized_match"]["sequential"]["eprocess"]
    assert eprocess["e"] is None and eprocess["max_e"] is None
    assert float(eprocess["log_e"]) > 700 and eprocess["alarm"] is True
    assert (
        "$.scorers.normalized_match.sequential.eprocess.e" in verdict.payload["non_finite_fields"]
    )
    assert verdict.state == "drift"
    assert verdict.opinion.disbelief == 0.0 and verdict.payload["opinion"]["disbelief"] == "0"


def test_pooled_baseline_over_two_windows(scenario: tuple[MemoryStore, Panel]) -> None:
    store, panel = scenario
    monitor = Monitor("m17", panel.digest, ("base", "aa"), config())
    verdict = analyze(store, monitor, "drift")
    assert verdict.payload["sample_counts"]["baseline"] == 160
    assert len(verdict.payload["baseline_header_ids"]) == 2
    assert verdict.state == "drift"


# --- state rules in isolation ---------------------------------------------------


def analysis(**overrides: Any) -> _ScorerAnalysis:
    base: dict[str, Any] = {"name": "s", "kind": "indicator", "probes": []}
    base.update(overrides)
    return _ScorerAnalysis(**base)


def probe(p_bh: float | None, effect: float | None) -> _ProbeTest:
    return _ProbeTest("p", 5, 5, "fisher_exact", 0.01, effect, None, p_bh)


def e_state(e_value: float, alarm: bool) -> EProcessState:
    return EProcessState(
        e_value, 0.0, 1.0, 1.0, e_value, 10, 0.5, 0.05, (0.5,), alarm, 0 if alarm else None
    )


def test_state_rules_in_order() -> None:
    cfg = config()
    assert _state([analysis()], cfg) == ("stable", "no rule fired")
    assert _state([analysis(pooled_p_holm=0.04)], cfg)[0] == "drift"
    assert _state([analysis(e_state=e_state(25.0, True))], cfg) == (
        "drift",
        "e-process alarm for s",
    )
    assert (
        _state([analysis(cusum_state=CusumState(0.5, 0.0, 0.1, 0.4, 3, True, 2))], cfg)[0]
        == "drift"
    )
    ph_alarm = PageHinkleyState(0.6, 0.0, 0.0, 0.0, 0.05, 0.5, 3, True, 2)
    assert _state([analysis(ph_state=ph_alarm)], cfg)[0] == "drift"
    assert _state([analysis(probes=[probe(0.04, 0.5)])], cfg) == (
        "warning",
        "per-probe test rejected for s",
    )
    assert _state([analysis(probes=[probe(0.04, 0.1)])], cfg)[0] == "stable"
    assert _state([analysis(probes=[probe(None, 0.5)])], cfg)[0] == "stable"
    half_cusum = CusumState(0.25, 0.0, 0.1, 0.4, 3, False, None)
    assert _state([analysis(cusum_state=half_cusum)], cfg) == (
        "warning",
        "cusum at half threshold for s",
    )
    half_ph = PageHinkleyState(0.3, 0.0, 0.0, 0.0, 0.05, 0.5, 3, False, None)
    assert _state([analysis(ph_state=half_ph)], cfg)[0] == "warning"
    assert _state([analysis(e_state=e_state(12.0, False))], cfg) == (
        "warning",
        "e-process at half threshold for s",
    )
    assert _state([analysis(e_state=e_state(2.0, False))], cfg)[0] == "stable"


def test_difference_paths_cover_lists_and_scalars() -> None:
    assert _difference_paths({"a": [1, 2]}, {"a": [1, 3]}, "") == ["/a/1"]
    assert _difference_paths({"a": [1, 2]}, {"a": [1]}, "") == ["/a"]
    assert _difference_paths(1, 1, "/x") == []
    assert _difference_paths(1, 2, "") == ["/"]


def test_probe_without_baseline_scores_is_skipped_by_the_sign_stream(
    scenario: tuple[MemoryStore, Panel],
) -> None:
    store, panel = scenario

    class Partial:
        name = "partial"
        kind: Kind = "scalar"
        reference_based = False

        def score(self, observation: Observation, reference: Reference | None) -> float | None:
            if observation.window_id == "base" and observation.probe_id == "p0":
                return None
            return float(len(str(observation.result.get("text", ""))))

    monitor = Monitor("m18", panel.digest, ("base",), config(scorers=("partial",)))
    verdict = analyze(store, monitor, "aa", extra_scorers={"partial": Partial()})
    partial = verdict.payload["scorers"]["partial"]
    assert "p0" in partial["probes_below_n_min"]
    assert partial["sequential"]["eprocess"]["observations"] > 0
