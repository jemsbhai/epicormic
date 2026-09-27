"""Levers on pollard's fail-closed hooks (docs/PLAN.md, section 11)."""

from __future__ import annotations

from typing import Any

import pytest
from pollard import (
    ActionSpec,
    BudgetExceeded,
    ConfirmationRequired,
    Decision,
    MemoryStore,
    NodeKind,
    PolicyViolation,
    Registry,
    ReplayContract,
    Runtime,
)
from pollard.meters import DepthMeter, MeterPrecheckRefusal, StepMeter, TokenMeter, WallClockMeter
from pollard.policy import PolicyContext

from epicormic.ledger import Ledger
from epicormic.levers import ContractGate, DriftMeter, DriftPolicy, LeverError
from epicormic.mock import Drift, MockProvider
from epicormic.panel import Panel, Probe
from epicormic.verdict import AnalysisConfig, Monitor, analyze, contract_digest
from epicormic.window import observe


def make_panel(name: str = "levers") -> Panel:
    return Panel(
        name=name,
        probes=tuple(Probe(f"p{i}", {"model": "mock", "input": f"{name} {i}"}) for i in range(6)),
        seed_from_attempt=True,
    )


@pytest.fixture
def monitored() -> tuple[MemoryStore, Panel, Monitor]:
    panel = make_panel()
    store = MemoryStore()
    observe(panel, window_id="base", samples=8, fn=MockProvider(seed=1), store=store)
    observe(panel, window_id="aa", samples=8, fn=MockProvider(seed=2), store=store)
    drifted = MockProvider(seed=3, drift=Drift(match_rate=0.2))
    observe(panel, window_id="drift", samples=8, fn=drifted, store=store)
    monitor = Monitor(
        "m",
        panel.digest,
        ("base",),
        AnalysisConfig(scorers=("normalized_match",), permutations=200),
    )
    return store, panel, monitor


def app_call(runtime: Runtime) -> Any:
    with runtime.run("app") as run:
        return run.model_call({"model": "mock", "input": "hello"}, fn=MockProvider(seed=5))


def runtime_with(meter: Any) -> Runtime:
    return Runtime(
        MemoryStore(),
        meters=[StepMeter(), DepthMeter(), WallClockMeter(), TokenMeter(), meter],
    )


# --- DriftMeter -------------------------------------------------------------------


def test_meter_allows_before_any_verdict_and_refuses_on_drift(
    monitored: tuple[MemoryStore, Panel, Monitor],
) -> None:
    store, panel, monitor = monitored
    meter = DriftMeter(Ledger(store), "m", panel_digest=panel.digest)
    assert meter.name == "epicormic" and meter.monitor_id == "m"
    assert meter.current_state() == "unknown"
    runtime = runtime_with(meter)
    assert app_call(runtime).kind == NodeKind.MODEL_CALL.value
    analyze(store, monitor, "aa")
    assert meter.current_state() == "stable"
    assert app_call(runtime).kind == NodeKind.MODEL_CALL.value
    verdict = analyze(store, monitor, "drift")
    assert meter.current_state() == "drift"
    with pytest.raises(BudgetExceeded) as refused:
        app_call(runtime)
    node = runtime.store.get(refused.value.refusal_id)
    assert node.kind == NodeKind.REFUSAL.value
    assert node.payload["meter"] == "epicormic"
    assert node.payload["reason"] == "epicormic_drift"
    assert node.meta["monitor_id"] == "m" and node.meta["state"] == "drift"
    assert node.meta["verdict_node_id"] == verdict.node_id
    assert node.meta["window_id"] == "drift" and node.meta["sequence_index"] == 2
    assert node.meta["panel_digest"] == panel.digest
    assert meter.charge("model_call", {}, None, {}) == 0


def test_meter_refuse_on_unknown_and_kinds(monitored: tuple[MemoryStore, Panel, Monitor]) -> None:
    store, panel, _monitor = monitored
    strict = DriftMeter(
        Ledger(store), "m", panel_digest=panel.digest, refuse_on=("unknown", "drift")
    )
    with pytest.raises(BudgetExceeded):
        app_call(runtime_with(strict))
    assert strict.precheck_estimate("note", {}) is None
    assert strict.precheck_estimate("tool_call", {}) is None
    tools_only = DriftMeter(
        Ledger(store), "m", panel_digest=panel.digest, refuse_on=("unknown",), kinds=("tool_call",)
    )
    assert app_call(runtime_with(tools_only)).kind == NodeKind.MODEL_CALL.value
    with pytest.raises(MeterPrecheckRefusal, match="state 'unknown'"):
        tools_only.precheck_estimate("tool_call", {"name": "x"})


def test_meter_finds_the_monitor_by_id_alone(monitored: tuple[MemoryStore, Panel, Monitor]) -> None:
    store, _panel, monitor = monitored
    analyze(store, monitor, "drift")
    meter = DriftMeter(Ledger(store), "m")
    assert meter.current_state() == "drift"
    assert meter.precheck_estimate("note", {}) is None
    with pytest.raises(BudgetExceeded):
        app_call(runtime_with(meter))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"refuse_on": ("weird",)},
        {"kinds": ()},
        {"kinds": ("",)},
        {"name": ""},
    ],
)
def test_meter_validation(
    monitored: tuple[MemoryStore, Panel, Monitor], kwargs: dict[str, Any]
) -> None:
    store, _panel, _monitor = monitored
    with pytest.raises(LeverError):
        DriftMeter(Ledger(store), "m", **kwargs)
    with pytest.raises(LeverError):
        DriftMeter("not a ledger", "m")  # type: ignore[arg-type]
    with pytest.raises(LeverError):
        DriftMeter(Ledger(store), "")


# --- DriftPolicy ------------------------------------------------------------------


def specs() -> tuple[ActionSpec, ActionSpec]:
    send = ActionSpec(
        "send",
        "1",
        "Send.",
        {"type": "object", "properties": {}},
        True,
        handler=lambda a: {"queued": True},
    )
    read = ActionSpec(
        "read",
        "1",
        "Read.",
        {"type": "object", "properties": {}},
        False,
        handler=lambda a: {"ok": True},
    )
    return send, read


def test_policy_confirms_side_effects_during_drift_and_allows_reads(
    monitored: tuple[MemoryStore, Panel, Monitor],
) -> None:
    store, panel, monitor = monitored
    send, read = specs()
    policy = DriftPolicy(Ledger(store), "m", panel_digest=panel.digest)
    assert policy.monitor_id == "m"
    runtime = Runtime(MemoryStore(), registry=Registry([send, read]), policies=[policy])
    with runtime.run("tools") as run:
        assert run.tool_call("send", {}).result == {"queued": True}  # unknown -> allow
    analyze(store, monitor, "drift")
    with runtime.run("tools-2") as run:
        assert run.tool_call("read", {}).result == {"ok": True}
        with pytest.raises(ConfirmationRequired) as pending:
            run.tool_call("send", {})
        assert run.confirm(pending.value.resume_token).result == {"queued": True}


def test_policy_denies_when_configured_and_covers_every_state(
    monitored: tuple[MemoryStore, Panel, Monitor],
) -> None:
    store, panel, monitor = monitored
    send, read = specs()
    analyze(store, monitor, "drift")
    deny = DriftPolicy(Ledger(store), "m", panel_digest=panel.digest, on_drift=Decision.DENY)
    runtime = Runtime(MemoryStore(), registry=Registry([send]), policies=[deny])
    with runtime.run("tools") as run, pytest.raises(PolicyViolation) as refused:
        run.tool_call("send", {})
    assert runtime.store.get(refused.value.refusal_id).payload["reason"] == "policy"
    everything = DriftPolicy(
        Ledger(store),
        "m",
        panel_digest=panel.digest,
        on_drift=Decision.DENY,
        on_warning=Decision.CONFIRM,
        on_unknown=Decision.DENY,
        on_stable=Decision.ALLOW,
        side_effects_only=False,
    )
    ctx = PolicyContext(spec=read, args={}, cursor_id="0" * 64, run_label="x", counters={})
    assert everything.decide(ctx) == Decision.DENY
    assert everything.decisions == {
        "drift": Decision.DENY,
        "warning": Decision.CONFIRM,
        "unknown": Decision.DENY,
        "stable": Decision.ALLOW,
    }
    with pytest.raises(LeverError):
        DriftPolicy(Ledger(store), "m", on_drift="deny")  # type: ignore[arg-type]


# --- ContractGate -----------------------------------------------------------------


def test_contract_gate_passes_expected_and_unbound_and_refuses_changed() -> None:
    v1 = ReplayContract(provider="mock", model_revision="v1")
    v2 = ReplayContract(provider="mock", model_revision="v2", sdk="s", sdk_version="1")
    gate = ContractGate(v1)
    assert gate.name == "epicormic-contract"
    assert gate.expected_digest == contract_digest(v1.to_dict())
    runtime = Runtime(MemoryStore(), meters=[StepMeter(), gate])
    with runtime.run("gate") as run:
        assert run.model_call(v1.bind({"input": "x"}), fn=MockProvider(seed=1)).kind == "model_call"
        assert run.model_call({"input": "y"}, fn=MockProvider(seed=1)).kind == "model_call"
        with pytest.raises(BudgetExceeded) as refused:
            run.model_call(v2.bind({"input": "z"}), fn=MockProvider(seed=1))
    node = runtime.store.get(refused.value.refusal_id)
    assert node.payload["reason"] == "contract_changed"
    assert node.payload["meter"] == "epicormic-contract"
    assert node.meta["expected_digest"] == gate.expected_digest
    assert node.meta["live_digest"] == contract_digest(v2.to_dict())
    assert node.meta["difference_paths"] == ["/model_revision", "/sdk", "/sdk_version"]
    assert gate.charge("model_call", {}, None, {}) == 0
    assert gate.precheck_estimate("tool_call", v2.bind({"input": "z"})) is None


def test_contract_gate_acknowledgement_and_validation() -> None:
    v1 = ReplayContract(provider="mock", model_revision="v1")
    v2 = ReplayContract(provider="mock", model_revision="v2")
    acknowledged = ContractGate(v1, acknowledged={contract_digest(v2.to_dict())})
    runtime = Runtime(MemoryStore(), meters=[StepMeter(), acknowledged])
    with runtime.run("gate") as run:
        assert run.model_call(v2.bind({"input": "z"}), fn=MockProvider(seed=1)).kind == "model_call"
    with pytest.raises(LeverError):
        ContractGate("v1")  # type: ignore[arg-type]
    with pytest.raises(LeverError):
        ContractGate(v1, acknowledged={""})
    with pytest.raises(LeverError):
        ContractGate(v1, kinds=())
    with pytest.raises(LeverError):
        ContractGate(v1, name="")
