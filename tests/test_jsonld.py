"""JSON-LD interoperability through the [jsonld] extra (docs/PLAN.md, D13 and D18)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pollard import MemoryStore

pytest.importorskip("jsonld_ex")

from epicormic import jsonld
from epicormic.mock import Drift, MockProvider
from epicormic.opinion import Opinion
from epicormic.panel import Panel, Probe
from epicormic.verdict import AnalysisConfig, Monitor, Verdict, analyze
from epicormic.window import observe


def make_panel() -> Panel:
    return Panel(
        name="ld",
        probes=tuple(Probe(f"p{i}", {"model": "mock", "input": f"q{i}"}) for i in range(4)),
        sampling={"temperature": "0.5"},
        seed_from_attempt=True,
    )


@pytest.fixture
def recorded() -> tuple[MemoryStore, Verdict]:
    panel = make_panel()
    store = MemoryStore()
    observe(panel, window_id="b", samples=6, fn=MockProvider(seed=1), store=store)
    drifted = MockProvider(seed=2, drift=Drift(match_rate=0.2))
    observe(panel, window_id="c", samples=6, fn=drifted, store=store)
    config = AnalysisConfig(scorers=("normalized_match",), permutations=100)
    verdict = analyze(store, Monitor("m", panel.digest, ("b",), config), "c")
    return store, verdict


# --- panels -------------------------------------------------------------------------


def test_panel_round_trip_keeps_the_digest(tmp_path: Path) -> None:
    panel = make_panel()
    document = jsonld.panel_to_jsonld(panel)
    assert document["@type"] == "Panel" and document["@id"].endswith(panel.digest)
    assert all(probe["@type"] == "Probe" for probe in document["probes"])
    assert jsonld.panel_from_jsonld(json.loads(json.dumps(document))).digest == panel.digest
    path = tmp_path / "panel.jsonld"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert jsonld.load_panel_jsonld(path).digest == panel.digest


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: "x", "JSON object"),
        (lambda d: {**d, "@type": "Other"}, "@type"),
        (lambda d: {**d, "name": ""}, "invalid"),
        (lambda d: {**d, "probes": []}, "invalid"),
        (lambda d: {**d, "probes": [{"probe_id": "p", "payload": {}}]}, "@type 'Probe'"),
        (lambda d: {**d, "probes": [{"@type": "Probe", "probe_id": "p"}]}, "invalid"),
        (lambda d: {**d, "format": "epicormic/panel/v9"}, "invalid"),
        (lambda d: {**d, "seed_base": "no"}, "invalid"),
    ],
)
def test_panel_document_rejections(mutate: Any, message: str) -> None:
    document = jsonld.panel_to_jsonld(make_panel())
    with pytest.raises(jsonld.JsonLdError, match=message):
        jsonld.panel_from_jsonld(mutate(document))


# --- verdicts -----------------------------------------------------------------------


def test_verdict_export_annotates_state_and_opinion(recorded: tuple[MemoryStore, Verdict]) -> None:
    store, verdict = recorded
    assert verdict.node_id is not None
    created = str(store.get(verdict.node_id).meta["created_at"])
    document = jsonld.verdict_to_jsonld(
        verdict.payload, node_id=verdict.node_id, created_at=created
    )
    assert document["@type"] == "Verdict"
    assert document["@id"] == f"urn:pollard:node:{verdict.node_id}"
    assert document["current_root_node"] == {
        "@id": jsonld.node_urn(verdict.payload["current_root"])
    }
    assert [n["@id"] for n in document["baseline_root_nodes"]] == [
        jsonld.node_urn(r) for r in verdict.payload["baseline_roots"]
    ]
    assert document["@context"] == jsonld.EPICORMIC_CONTEXT
    state = document["state"]
    assert state["@value"] == "drift" and state["@method"] == jsonld.VERDICT_METHOD
    assert state["@humanVerified"] is False
    assert state["@source"].startswith("urn:pypi:epicormic:")
    assert state["@extractedAt"] == created
    assert 0 <= state["@confidence"] <= 1
    assert document["opinion"]["belief"] == verdict.payload["opinion"]["belief"]
    subjective = document["opinion"]["subjective_logic"]
    assert subjective["@type"] == "Opinion"
    assert subjective["belief"] + subjective["disbelief"] + subjective[
        "uncertainty"
    ] == pytest.approx(1)
    assert document["scorers"] == verdict.payload["scorers"]
    assert document["context_integrity"] == jsonld.context_integrity()
    assert document["context_integrity"].startswith("sha256-")
    assert jsonld.validate_verdict_jsonld(document) == []
    assert document["integrity"].startswith("sha256-")
    assert jsonld.verify_verdict_jsonld(document)
    assert jsonld.verify_verdict_jsonld(json.loads(json.dumps(document)))
    assert not jsonld.verify_verdict_jsonld({**document, "monitor_id": "other"})
    assert not jsonld.verify_verdict_jsonld({**document, "integrity": "sha256-nope"})
    assert not jsonld.verify_verdict_jsonld({**document, "integrity": "bad"})
    assert not jsonld.verify_verdict_jsonld({k: v for k, v in document.items() if k != "integrity"})
    bare = jsonld.export_verdict(verdict.payload)
    assert "@id" not in bare and "@extractedAt" not in bare["state"]


def test_verdict_export_rejections(recorded: tuple[MemoryStore, Verdict]) -> None:
    _, verdict = recorded
    with pytest.raises(jsonld.JsonLdError, match="epicormic/verdict/v1"):
        jsonld.verdict_to_jsonld({"format": "other"})
    with pytest.raises(jsonld.JsonLdError, match="opinion"):
        jsonld.verdict_to_jsonld({**verdict.payload, "opinion": None})
    problems = jsonld.validate_verdict_jsonld(
        {"@type": "Verdict", "format": "epicormic/verdict/v1"}
    )
    assert any("monitor_id" in problem for problem in problems)


def test_prov_o_graph(recorded: tuple[MemoryStore, Verdict]) -> None:
    _, verdict = recorded
    document = jsonld.verdict_to_jsonld(verdict.payload, node_id=verdict.node_id)
    graph = jsonld.verdict_to_prov_o(document)
    types = [node.get("@type") for node in graph["@graph"]]
    assert "Verdict" in types
    assert "http://www.w3.org/ns/prov#Entity" in types
    assert "http://www.w3.org/ns/prov#SoftwareAgent" in types
    assert graph["@context"]["prov"] == "http://www.w3.org/ns/prov#"


def test_fusion(recorded: tuple[MemoryStore, Verdict]) -> None:
    _, verdict = recorded
    one = jsonld.fuse_opinions([verdict.opinion])
    assert one.belief == pytest.approx(verdict.opinion.belief, abs=1e-6)
    cumulative = jsonld.fuse_opinions([verdict.opinion, verdict.payload["opinion"]])
    assert isinstance(cumulative, Opinion)
    assert cumulative.uncertainty < verdict.opinion.uncertainty
    averaging = jsonld.fuse_opinions([verdict.opinion, verdict.opinion], method="averaging")
    assert averaging.belief == pytest.approx(verdict.opinion.belief, abs=1e-6)
    with pytest.raises(jsonld.JsonLdError, match="at least one"):
        jsonld.fuse_opinions([])
    with pytest.raises(jsonld.JsonLdError, match="method"):
        jsonld.fuse_opinions([verdict.opinion], method="other")
