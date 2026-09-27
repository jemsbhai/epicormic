"""Example 03: the levers refuse, gate, and contain once a monitor reads drift.

Run: python examples/03_levers.py

An application runtime carries a DriftMeter, a DriftPolicy, and a
ContractGate. Before any verdict the application runs normally; after the
monitor records drift, its model calls are refused with an auditable
refusal node, its side-effectful tool calls need confirmation, and a call
bound to a changed execution fingerprint is refused by the gate.
"""

from __future__ import annotations

from pollard import (
    ActionSpec,
    BudgetExceeded,
    ConfirmationRequired,
    MemoryStore,
    Registry,
    ReplayContract,
    Runtime,
)
from pollard.meters import DepthMeter, StepMeter, TokenMeter, WallClockMeter

from epicormic import (
    AnalysisConfig,
    ContractGate,
    Drift,
    DriftMeter,
    DriftPolicy,
    Ledger,
    MockProvider,
    Monitor,
    Panel,
    Probe,
    analyze,
    observe,
)


def main() -> None:
    panel = Panel(
        name="example-panel",
        probes=tuple(
            Probe(f"probe-{index}", {"model": "mock", "input": f"Question number {index}"})
            for index in range(8)
        ),
        seed_from_attempt=True,
    )
    monitoring = MemoryStore()
    observe(panel, window_id="baseline", samples=10, fn=MockProvider(seed=1), store=monitoring)
    observe(
        panel,
        window_id="current",
        samples=10,
        fn=MockProvider(seed=2, drift=Drift(match_rate=0.3)),
        store=monitoring,
    )
    ledger = Ledger(monitoring)
    monitor = Monitor(
        "weekly",
        panel.digest,
        ("baseline",),
        AnalysisConfig(scorers=("normalized_match",), permutations=300),
    )

    send = ActionSpec(
        "send",
        "1",
        "Send a message.",
        {"type": "object", "properties": {}},
        True,
        handler=lambda args: {"queued": True},
    )
    contract = ReplayContract(provider="mock", model_revision="2026-09")
    application = Runtime(
        MemoryStore(),
        meters=[
            StepMeter(),
            DepthMeter(),
            WallClockMeter(),
            TokenMeter(),
            ContractGate(contract),
            DriftMeter(ledger, "weekly", panel_digest=panel.digest),
        ],
        registry=Registry([send]),
        policies=[DriftPolicy(ledger, "weekly", panel_digest=panel.digest)],
    )

    def model_call(label: str, bound_contract: ReplayContract = contract) -> str:
        with application.run(label) as run:
            try:
                node = run.model_call(
                    bound_contract.bind({"model": "mock", "input": "hello"}),
                    fn=MockProvider(seed=9),
                )
            except BudgetExceeded as refused:
                refusal = application.store.get(refused.refusal_id)
                return f"refused ({refusal.payload['reason']})"
            return node.kind

    print(f"before any verdict: model call {model_call('before')}")
    verdict = analyze(monitoring, monitor, "current")
    print(f"verdict: {verdict.state} ({verdict.rule_fired})")
    print(f"after drift: model call {model_call('after')}")
    with application.run("tools") as run:
        try:
            run.tool_call("send", {})
            print("after drift: tool call ran without confirmation")
        except ConfirmationRequired as pending:
            print("after drift: side-effectful tool call needs confirmation")
            run.confirm(pending.resume_token)
            print("  confirmed by a person, then executed")
    changed = ReplayContract(provider="mock", model_revision="2026-10")
    print(f"changed fingerprint: model call {model_call('fingerprint', changed)}")


if __name__ == "__main__":
    main()
