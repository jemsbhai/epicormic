"""Readers over the window layout (docs/PLAN.md, section 5.4)."""

from __future__ import annotations

import pytest
from pollard import MemoryStore, Node, NodeKind, ReplayContract

from epicormic.mock import MockProvider
from epicormic.observation import find_window_headers, find_window_root, read_window
from epicormic.panel import Panel
from epicormic.window import observe


def test_find_window_root_matches_the_recorded_root(panel: Panel, mock: MockProvider) -> None:
    store = MemoryStore()
    report = observe(panel, window_id="w1", samples=2, fn=mock, store=store)
    assert find_window_root(panel.digest, "w1") == report.root_id
    assert find_window_headers(store, panel.digest, "w1") == [report.header_id]
    assert find_window_headers(store, panel.digest, "never") == []


def test_read_window_returns_observations_in_probe_and_attempt_order(
    panel: Panel, mock: MockProvider
) -> None:
    store = MemoryStore()
    contract = ReplayContract(provider="mock", model_revision="v1")
    report = observe(panel, window_id="w1", samples=3, fn=mock, store=store, contract=contract)
    view = read_window(store, report.header_id)
    assert view.root_id == report.root_id
    assert view.window_id == "w1"
    assert view.panel_digest == panel.digest
    assert view.panel_name == panel.name
    assert view.contract == contract.to_dict()
    assert view.header_meta["epicormic"]["samples_requested"] == 3
    assert [probe.probe_id for probe in view.probes] == ["p0", "p1", "p2"]
    assert [probe.probe_index for probe in view.probes] == [0, 1, 2]
    for probe, panel_probe in zip(view.probes, panel.probes, strict=True):
        assert probe.probe_digest == panel_probe.digest
        assert [obs.attempt for obs in probe.observations] == [0, 1, 2]
        assert probe.refusal_ids == ()
        for obs in probe.observations:
            assert obs.window_id == "w1"
            assert obs.probe_id == panel_probe.probe_id
            assert obs.payload == panel_probe.payload
            assert obs.result["text"].startswith("answer:")
            assert obs.result_digest is not None
            assert "duration_s" in obs.meta
    assert len(view.observations) == 9
    assert view.probe("p2").probe_index == 2
    with pytest.raises(KeyError):
        view.probe("missing")


def test_read_window_rejects_non_header_nodes(panel: Panel, mock: MockProvider) -> None:
    store = MemoryStore()
    report = observe(panel, window_id="w1", samples=1, fn=mock, store=store)
    with pytest.raises(ValueError, match="not an epicormic window header"):
        read_window(store, report.root_id)
    with pytest.raises(ValueError, match="not an epicormic window header"):
        read_window(store, report.probes[0].header_id)


def test_foreign_nodes_in_the_tree_are_ignored_and_bad_headers_rejected(
    panel: Panel, mock: MockProvider
) -> None:
    store = MemoryStore()
    report = observe(panel, window_id="w1", samples=1, fn=mock, store=store)
    header = store.get(report.header_id)
    # a plain note under the header is not a probe anchor
    store.put(Node.make(kind=NodeKind.NOTE, parent=header.id, payload={"foreign": 1}))
    # an anchor whose child is not a probe header
    anchor = Node.make(kind=NodeKind.NOTE, parent=header.id, payload={"branch": True}, attempt=99)
    store.put(anchor)
    store.put(Node.make(kind=NodeKind.NOTE, parent=anchor.id, payload={"format": "other"}))
    # a plain note under a probe header is not a sample anchor
    probe_header = store.get(report.probes[0].header_id)
    store.put(Node.make(kind=NodeKind.NOTE, parent=probe_header.id, payload={"foreign": 2}))
    view = read_window(store, header.id)
    assert [probe.probe_id for probe in view.probes] == ["p0", "p1", "p2"]
    assert [len(probe.observations) for probe in view.probes] == [1, 1, 1]
    # a probe header with a non-integer index is rejected
    bad_anchor = Node.make(
        kind=NodeKind.NOTE, parent=header.id, payload={"branch": True}, attempt=98
    )
    store.put(bad_anchor)
    store.put(
        Node.make(
            kind=NodeKind.NOTE,
            parent=bad_anchor.id,
            payload={
                "format": "epicormic/probe/v1",
                "probe_id": "bad",
                "probe_digest": "x",
                "index": "0",
            },
        )
    )
    with pytest.raises(ValueError, match="non-integer index"):
        read_window(store, header.id)
