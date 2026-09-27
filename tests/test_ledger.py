"""The ledger (docs/PLAN.md, section 11)."""

from __future__ import annotations

import pytest
from pollard import MemoryStore, Node, NodeKind

from epicormic.ledger import Ledger, LedgerError
from epicormic.mock import Drift, MockProvider
from epicormic.panel import Panel, Probe
from epicormic.verdict import AnalysisConfig, Monitor, analyze, read_chain
from epicormic.window import observe

SCORERS = ("normalized_match", "output_chars")


def panel_named(name: str, probes: int = 4) -> Panel:
    return Panel(
        name=name,
        probes=tuple(
            Probe(f"p{i}", {"model": "mock", "input": f"{name} {i}"}) for i in range(probes)
        ),
        seed_from_attempt=True,
    )


@pytest.fixture
def recorded() -> tuple[MemoryStore, Panel, Monitor, list[str]]:
    panel = panel_named("ledger")
    store = MemoryStore()
    observe(panel, window_id="base", samples=6, fn=MockProvider(seed=1), store=store)
    observe(panel, window_id="w1", samples=6, fn=MockProvider(seed=2), store=store)
    drifted = MockProvider(seed=3, drift=Drift(match_rate=0.2))
    observe(panel, window_id="w2", samples=6, fn=drifted, store=store)
    monitor = Monitor(
        "m", panel.digest, ("base",), AnalysisConfig(scorers=SCORERS, permutations=200)
    )
    ids = [analyze(store, monitor, window_id).node_id or "" for window_id in ("w1", "w2")]
    return store, panel, monitor, ids


def test_state_and_history_before_and_after_verdicts() -> None:
    panel = panel_named("empty")
    store = MemoryStore()
    ledger = Ledger(store)
    assert ledger.state("m", panel.digest) is None
    assert ledger.state("m") is None
    assert ledger.history("m", panel.digest) == []
    observe(panel, window_id="base", samples=6, fn=MockProvider(seed=1), store=store)
    monitor = Monitor("m", panel.digest, ("base",), AnalysisConfig(scorers=SCORERS))
    analyze(store, monitor, "missing")
    entry = ledger.state("m", panel.digest)
    assert entry is not None and entry.state == "unknown" and entry.sequence_index == 1


def test_indexed_lookup_and_id_only_lookup(
    recorded: tuple[MemoryStore, Panel, Monitor, list[str]],
) -> None:
    store, panel, _monitor, ids = recorded
    ledger = Ledger(store)
    entry = ledger.state("m", panel.digest)
    assert entry is not None
    assert entry.verdict_node_id == ids[-1] and entry.window_id == "w2"
    assert entry.state == "drift" and entry.sequence_index == 2
    assert entry.monitor_id == "m" and entry.panel_digest == panel.digest
    assert entry.computed_at is not None
    assert ledger.state("m") == entry
    history = ledger.history("m")
    assert [e.verdict_node_id for e in history] == ids
    assert [e.window_id for e in history] == ["w1", "w2"]


def test_index_is_verified_and_the_chain_is_the_fallback(
    recorded: tuple[MemoryStore, Panel, Monitor, list[str]],
) -> None:
    store, panel, monitor, ids = recorded
    ledger = Ledger(store)
    root = monitor.root_id
    store.update_meta(root, {"epicormic": {"latest_verdict": "0" * 64}})
    assert ledger.state("m", panel.digest).verdict_node_id == ids[-1]  # type: ignore[union-attr]
    store.update_meta(root, {"epicormic": {"latest_verdict": ids[0]}})  # stale: not the last link
    assert ledger.state("m", panel.digest).verdict_node_id == ids[-1]  # type: ignore[union-attr]
    header_id, _ = read_chain(store, monitor)
    store.update_meta(root, {"epicormic": {"latest_verdict": header_id}})  # not a verdict
    assert ledger.state("m", panel.digest).verdict_node_id == ids[-1]  # type: ignore[union-attr]
    store.update_meta(root, {"epicormic": "bad"})
    assert ledger.state("m", panel.digest).verdict_node_id == ids[-1]  # type: ignore[union-attr]
    # a verdict-shaped leaf that does not descend from this monitor's root
    other_root = Node.make(kind=NodeKind.ROOT, parent=None, payload={"run": "other"})
    store.put(other_root)
    foreign = Node.make(
        kind=NodeKind.NOTE, parent=other_root.id, payload=dict(store.get(ids[-1]).payload)
    )
    store.put(foreign)
    store.update_meta(root, {"epicormic": {"latest_verdict": foreign.id}})
    assert ledger.state("m", panel.digest).verdict_node_id == ids[-1]  # type: ignore[union-attr]


def test_ambiguous_monitor_id_needs_the_panel_digest(
    recorded: tuple[MemoryStore, Panel, Monitor, list[str]],
) -> None:
    store, panel, _monitor, _ids = recorded
    other = panel_named("other")
    observe(other, window_id="base", samples=6, fn=MockProvider(seed=1), store=store)
    analyze(store, Monitor("m", other.digest, ("base",), AnalysisConfig(scorers=SCORERS)), "base")
    ledger = Ledger(store)
    with pytest.raises(LedgerError, match="pass panel_digest"):
        ledger.state("m")
    assert ledger.state("m", panel.digest).window_id == "w2"  # type: ignore[union-attr]
    assert ledger.state("m", other.digest).window_id == "base"  # type: ignore[union-attr]


def test_branching_chain_and_bad_ids_are_rejected(
    recorded: tuple[MemoryStore, Panel, Monitor, list[str]],
) -> None:
    store, panel, monitor, ids = recorded
    ledger = Ledger(store)
    with pytest.raises(LedgerError, match="monitor_id"):
        ledger.state("a/b")
    header_id, _ = read_chain(store, monitor)
    assert header_id is not None
    forged = {**store.get(ids[0]).payload, "monitor_id": "forged"}
    store.put(Node.make(kind=NodeKind.NOTE, parent=header_id, payload=forged))
    store.update_meta(monitor.root_id, {"epicormic": {"latest_verdict": "0" * 64}})
    with pytest.raises(LedgerError, match="branches"):
        ledger.history("m", panel.digest)


def test_root_without_header_has_no_history() -> None:
    panel = panel_named("bare")
    store = MemoryStore()
    monitor = Monitor("m", panel.digest, ("base",))
    store.put(Node.make(kind=NodeKind.ROOT, parent=None, payload={"run": monitor.label}))
    assert Ledger(store).history("m", panel.digest) == []
    assert Ledger(store).state("m", panel.digest) is None
