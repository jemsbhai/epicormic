"""Window observation: tree layout, identity, resumability, budgets (docs/PLAN.md, section 6)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pollard import Budget, MemoryStore, NodeKind, ReplayContract, Runtime, SQLiteStore

from epicormic.mock import MockProvider
from epicormic.observation import find_window_headers, read_window
from epicormic.panel import Panel, Probe
from epicormic.window import (
    PROBE_HEADER_FORMAT,
    WINDOW_FORMAT,
    SampleEvent,
    WindowError,
    header_index_of,
    header_payload,
    observe,
    window_label,
)


def kinds_by_depth(store: MemoryStore, root_id: str) -> dict[int, set[str]]:
    found: dict[int, set[str]] = {}

    def walk(node_id: str, depth: int) -> None:
        node = store.get(node_id)
        found.setdefault(depth, set()).add(node.kind)
        for child in store.children(node_id):
            walk(child, depth + 1)

    walk(root_id, 0)
    return found


def test_window_label_and_header_payload(panel: Panel) -> None:
    label = window_label(panel.digest, "w1")
    assert label == f"epicormic/{panel.digest[:16]}/w1"
    contract = ReplayContract(provider="mock", model_revision="v1")
    payload = header_payload(panel, "w1", contract)
    assert payload["format"] == WINDOW_FORMAT
    assert payload["panel_digest"] == panel.digest
    assert payload["contract"] == contract.to_dict()
    assert payload["sampling"] == panel.sampling
    assert "samples" not in payload
    assert header_payload(panel, "w1", None)["contract"] is None


@pytest.mark.parametrize("window_id", ["", "a/b", 3, None])
def test_window_id_validation(panel: Panel, window_id: Any) -> None:
    with pytest.raises(WindowError, match="window_id"):
        window_label(panel.digest, window_id)


def test_layout_identity_and_dispatch_requests(panel: Panel, mock: MockProvider) -> None:
    store = MemoryStore()
    contract = ReplayContract(provider="mock", model_revision="v1")
    events: list[SampleEvent] = []
    report = observe(
        panel,
        window_id="w1",
        samples=4,
        fn=mock,
        store=store,
        contract=contract,
        on_sample=events.append,
    )
    assert report.complete
    assert (report.recorded, report.dispatched, report.served, report.refused) == (12, 12, 0, 0)
    assert report.not_attempted == 0
    assert mock.calls == 12
    assert kinds_by_depth(store, report.root_id) == {
        0: {"root"},
        1: {"note"},
        2: {"note"},
        3: {"note"},
        4: {"note"},
        5: {"model_call"},
    }
    header = store.get(report.header_id)
    assert header.payload == header_payload(panel, "w1", contract)
    assert header.meta["epicormic"]["samples_requested"] == 4
    assert [event.outcome for event in events] == ["dispatched"] * 12
    assert [(event.probe_index, event.attempt) for event in events[:5]] == [
        (0, 0),
        (0, 1),
        (0, 2),
        (0, 3),
        (1, 0),
    ]
    for probe_report, probe in zip(report.probes, panel.probes, strict=True):
        probe_header = store.get(probe_report.header_id)
        assert probe_header.payload == {
            "format": PROBE_HEADER_FORMAT,
            "probe_id": probe.probe_id,
            "probe_digest": probe.digest,
            "index": probe_report.probe_index,
        }
        anchor = store.get(probe_header.parent or "")
        assert anchor.payload == {"branch": True}
        assert anchor.attempt == probe_report.probe_index
        for attempt, node_id in enumerate(probe_report.sample_ids):
            node = store.get(node_id)
            assert node.kind == NodeKind.MODEL_CALL.value
            assert node.attempt == attempt
            assert node.payload == probe.payload
            assert store.get(node.parent or "").attempt == attempt
            assert node.meta["usage"]["total_tokens"] > 0
    requests = mock.requests
    assert requests[5] == {
        "model": "mock",
        "input": "question 1",
        "temperature": 0.7,
        "max_output_tokens": 64,
        "seed": 1,
    }
    assert {request["seed"] for request in requests[:4]} == {0, 1, 2, 3}


def test_rerun_serves_and_raising_samples_dispatches_only_the_gap(
    panel: Panel, mock: MockProvider
) -> None:
    store = MemoryStore()
    first = observe(panel, window_id="w1", samples=4, fn=mock, store=store)
    events: list[SampleEvent] = []
    second = observe(
        panel, window_id="w1", samples=4, fn=mock, store=store, on_sample=events.append
    )
    assert (second.dispatched, second.served) == (0, 12)
    assert {event.outcome for event in events} == {"served"}
    assert mock.calls == 12
    third = observe(panel, window_id="w1", samples=6, fn=mock, store=store)
    assert (third.dispatched, third.served, third.recorded) == (6, 12, 18)
    assert third.header_id == first.header_id
    assert third.root_id == first.root_id
    assert store.get(first.header_id).meta["epicormic"]["samples_requested"] == 6
    assert mock.calls == 18


def test_budget_refusals_are_recorded_and_resumable(panel: Panel, mock: MockProvider) -> None:
    store = MemoryStore()
    events: list[SampleEvent] = []
    report = observe(
        panel,
        window_id="w2",
        samples=4,
        fn=mock,
        store=store,
        budget=Budget(steps=5),
        on_sample=events.append,
    )
    assert not report.complete
    assert (report.recorded, report.refused, report.not_attempted) == (5, 2, 5)
    assert [(probe.recorded, probe.refused, probe.not_attempted) for probe in report.probes] == [
        (4, 0, 0),
        (1, 1, 2),
        (0, 1, 3),
    ]
    refusal = store.get(report.probes[1].refusal_ids[0])
    assert refusal.kind == NodeKind.REFUSAL.value
    assert refusal.payload["meter"] == "steps"
    assert [event.outcome for event in events].count("refused") == 2
    resumed = observe(
        panel, window_id="w2", samples=4, fn=mock, store=store, budget=Budget(steps=50)
    )
    assert resumed.complete
    assert (resumed.dispatched, resumed.served, resumed.refused) == (7, 5, 0)
    view = read_window(store, find_window_headers(store, panel.digest, "w2")[0])
    assert [len(probe.observations) for probe in view.probes] == [4, 4, 4]
    assert [len(probe.refusal_ids) for probe in view.probes] == [0, 1, 1]


def test_probe_budget_scopes_each_probe(panel: Panel, mock: MockProvider) -> None:
    store = MemoryStore()
    report = observe(
        panel, window_id="w3", samples=4, fn=mock, store=store, probe_budget=Budget(steps=2)
    )
    assert [(probe.recorded, probe.refused, probe.not_attempted) for probe in report.probes] == [
        (2, 1, 1)
    ] * 3


def test_contract_change_creates_a_second_header_under_the_same_root(
    panel: Panel, mock: MockProvider
) -> None:
    store = MemoryStore()
    first = observe(
        panel,
        window_id="w4",
        samples=2,
        fn=mock,
        store=store,
        contract=ReplayContract(provider="mock", model_revision="v1"),
    )
    second = observe(
        panel,
        window_id="w4",
        samples=2,
        fn=mock,
        store=store,
        contract=ReplayContract(provider="mock", model_revision="v2"),
    )
    assert first.root_id == second.root_id
    assert first.header_id != second.header_id
    assert second.dispatched == 6
    headers = find_window_headers(store, panel.digest, "w4")
    assert headers == [first.header_id, second.header_id]
    assert [header_index_of(store, h) for h in headers] == [0, 1]


def test_header_order_survives_identical_timestamps(
    panel: Panel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stores list children by kind and id, so the header ordinal must carry creation order."""

    import pollard.runtime

    monkeypatch.setattr(pollard.runtime, "_now_utc", lambda: "2026-09-27T00:00:00.000000Z")
    for trial in range(4):
        store = MemoryStore()
        created = [
            observe(
                panel,
                window_id=f"tie{trial}",
                samples=1,
                fn=MockProvider(seed=1),
                store=store,
                contract=ReplayContract(provider="mock", model_revision=revision),
            ).header_id
            for revision in ("v1", "v2", "v3", "v4")
        ]
        assert find_window_headers(store, panel.digest, f"tie{trial}") == created
        assert [header_index_of(store, h) for h in created] == [0, 1, 2, 3]
        # a rerun under an earlier contract serves that header and keeps its ordinal
        again = observe(
            panel,
            window_id=f"tie{trial}",
            samples=2,
            fn=MockProvider(seed=1),
            store=store,
            contract=ReplayContract(provider="mock", model_revision="v2"),
        )
        assert again.header_id == created[1]
        assert header_index_of(store, created[1]) == 1
        assert store.get(created[1]).meta["epicormic"]["samples_requested"] == 2
        assert find_window_headers(store, panel.digest, f"tie{trial}") == created


def test_headers_without_an_ordinal_sort_last_by_created_at_then_id(
    panel: Panel, mock: MockProvider
) -> None:
    store = MemoryStore()
    first = observe(
        panel,
        window_id="legacy",
        samples=1,
        fn=mock,
        store=store,
        contract=ReplayContract(provider="mock", model_revision="v1"),
    ).header_id
    second = observe(
        panel,
        window_id="legacy",
        samples=1,
        fn=mock,
        store=store,
        contract=ReplayContract(provider="mock", model_revision="v2"),
    ).header_id
    third = observe(
        panel,
        window_id="legacy",
        samples=1,
        fn=mock,
        store=store,
        contract=ReplayContract(provider="mock", model_revision="v3"),
    ).header_id
    # strip the ordinal from two headers and give them explicit, reversed timestamps
    store.update_meta(first, {"created_at": "2026-09-27T00:00:02Z", "epicormic": {}})
    store.update_meta(second, {"created_at": "2026-09-27T00:00:01Z", "epicormic": {}})
    assert header_index_of(store, first) is None
    assert find_window_headers(store, panel.digest, "legacy") == [third, second, first]
    # identical timestamps fall back to the id
    store.update_meta(first, {"created_at": "2026-09-27T00:00:01Z"})
    assert find_window_headers(store, panel.digest, "legacy") == [third, *sorted([first, second])]
    # a rerun of a header without an ordinal assigns the next one
    store.update_meta(third, {"epicormic": {}})
    again = observe(
        panel,
        window_id="legacy",
        samples=1,
        fn=mock,
        store=store,
        contract=ReplayContract(provider="mock", model_revision="v1"),
    )
    assert again.header_id == first and header_index_of(store, first) == 2
    assert find_window_headers(store, panel.digest, "legacy")[0] == first


def test_runtime_argument_must_be_hybrid_and_exclusive(panel: Panel, mock: MockProvider) -> None:
    with pytest.raises(WindowError, match="exactly one of store or runtime"):
        observe(panel, window_id="w", samples=1, fn=mock)
    with pytest.raises(WindowError, match="exactly one of store or runtime"):
        observe(
            panel,
            window_id="w",
            samples=1,
            fn=mock,
            store=MemoryStore(),
            runtime=Runtime(mode="hybrid"),
        )
    with pytest.raises(WindowError, match="hybrid-mode Runtime"):
        observe(panel, window_id="w", samples=1, fn=mock, runtime=Runtime(mode="record"))
    runtime = Runtime(MemoryStore(), mode="hybrid")
    report = observe(panel, window_id="w", samples=1, fn=mock, runtime=runtime)
    assert report.complete


@pytest.mark.parametrize("samples", [0, -1, True, 2.0])
def test_samples_validation(panel: Panel, mock: MockProvider, samples: Any) -> None:
    with pytest.raises(WindowError, match="samples"):
        observe(panel, window_id="w", samples=samples, fn=mock, store=MemoryStore())


def test_sqlite_store_by_path(panel: Panel, mock: MockProvider, tmp_path: Path) -> None:
    path = tmp_path / "runs.db"
    report = observe(panel, window_id="w5", samples=2, fn=mock, store=str(path))
    assert report.complete
    reopened = SQLiteStore(str(path))
    view = read_window(reopened, find_window_headers(reopened, panel.digest, "w5")[0])
    assert [len(probe.observations) for probe in view.probes] == [2, 2, 2]


def test_generator_step_results_pass_through(panel: Panel) -> None:
    def streaming(request: dict[str, Any]) -> Any:
        yield {"text": "part-"}
        yield {"text": "one", "usage": {"input_tokens": 1, "output_tokens": 1}}

    store = MemoryStore()
    small = Panel(name="s", probes=(Probe("p", {"input": "x"}),))
    report = observe(small, window_id="w6", samples=1, fn=streaming, store=store)
    node = store.get(report.probes[0].sample_ids[0])
    assert node.result is not None
