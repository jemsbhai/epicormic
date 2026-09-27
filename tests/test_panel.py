"""Probe and Panel contract (docs/PLAN.md, sections 5.1 and 5.2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from epicormic.panel import (
    PANEL_FORMAT,
    Panel,
    PanelError,
    Probe,
    load_panel,
    save_panel,
)

LOCKED_PANEL_DIGEST = "56fb11b1ab372ac85493e05a86a02eebaed9118693fa01e00e6b27f415129bd8"
LOCKED_PROBE_DIGEST = "c4f165413353aa231cb4af49d4b9990f433b19174a9928f16cd60a353d07e4f3"


def demo_panel() -> Panel:
    return Panel(
        name="demo",
        probes=(
            Probe("p1", {"model": "x", "input": "hi"}, ("a",)),
            Probe("p2", {"model": "x", "input": "yo"}),
        ),
        sampling={"temperature": "0.7", "max_output_tokens": "64"},
        seed_from_attempt=True,
    )


def test_digests_are_locked_across_platforms() -> None:
    panel = demo_panel()
    assert panel.digest == LOCKED_PANEL_DIGEST
    assert panel.probes[0].digest == LOCKED_PROBE_DIGEST


def test_probe_order_is_significant() -> None:
    panel = demo_panel()
    reordered = Panel(
        name=panel.name,
        probes=tuple(reversed(panel.probes)),
        sampling=panel.sampling,
        seed_from_attempt=panel.seed_from_attempt,
    )
    assert reordered.digest != panel.digest


def test_document_round_trip_preserves_digest() -> None:
    panel = demo_panel()
    document = panel.to_document()
    assert document["format"] == PANEL_FORMAT
    assert Panel.from_document(document) == panel
    assert Panel.from_document(document).digest == panel.digest
    assert Probe.from_document(panel.probes[0].to_document()) == panel.probes[0]


def test_file_round_trip_preserves_digest(tmp_path: Path) -> None:
    panel = demo_panel()
    path = tmp_path / "panel.json"
    save_panel(panel, path)
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert json.loads(text) == panel.to_document()
    loaded = load_panel(path)
    assert loaded == panel
    assert loaded.digest == LOCKED_PANEL_DIGEST


def test_non_ascii_survives_the_file(tmp_path: Path) -> None:
    probe = Probe("jp", {"input": "\u65e5\u672c\u8a9e"})
    panel = Panel(name="\u00e9", probes=(probe,))
    path = tmp_path / "panel.json"
    save_panel(panel, path)
    assert "\u65e5\u672c\u8a9e" in path.read_text(encoding="utf-8")
    assert load_panel(path).digest == panel.digest


def test_load_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(PanelError, match="not valid JSON"):
        load_panel(path)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("nope", "must be an object"),
        ({"format": "other/v1", "name": "n", "probes": []}, "panel format must be"),
        ({"format": PANEL_FORMAT, "name": "n", "probes": [], "extra": 1}, "unknown keys"),
        ({"format": PANEL_FORMAT, "name": "n", "probes": "x"}, "probes must be a list"),
        ({"format": PANEL_FORMAT, "name": "n", "probes": []}, "at least one probe"),
    ],
)
def test_from_document_rejections(document: Any, message: str) -> None:
    with pytest.raises(PanelError, match=message):
        Panel.from_document(document)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("nope", "must be an object"),
        ({"probe_id": "p"}, "needs probe_id and payload"),
        ({"probe_id": "p", "payload": {"a": 1}, "extra": 1}, "unknown keys"),
        ({"probe_id": "p", "payload": {"a": 1}, "tags": "x"}, "tags must be a list"),
    ],
)
def test_probe_from_document_rejections(document: Any, message: str) -> None:
    with pytest.raises(PanelError, match=message):
        Probe.from_document(document)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"probe_id": "bad id", "payload": {"a": 1}}, "probe_id must match"),
        ({"probe_id": "p", "payload": {}}, "non-empty object"),
        ({"probe_id": "p", "payload": [1]}, "non-empty object"),
        ({"probe_id": "p", "payload": {"t": 0.7}}, r"floats are not allowed .* at \$\.payload\.t"),
        ({"probe_id": "p", "payload": {"a": 1}, "tags": ["x"]}, "tuple of strings"),
        ({"probe_id": "p", "payload": {"a": 1}, "tags": (1,)}, "tuple of strings"),
        ({"probe_id": "p", "payload": {"a": 1}, "family": 3}, "family must be"),
    ],
)
def test_probe_validation(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(PanelError, match=message):
        Probe(**kwargs)


def test_probe_payload_is_copied_not_shared() -> None:
    payload: dict[str, Any] = {"input": ["a"]}
    probe = Probe("p", payload)
    payload["input"].append("b")
    assert probe.payload == {"input": ["a"]}
    document = probe.to_document()
    assert isinstance(document["payload"], dict)
    document["payload"]["input"] = None
    assert probe.payload == {"input": ["a"]}


def one_probe() -> tuple[Probe, ...]:
    return (Probe("p", {"input": "x"}),)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"name": "", "probes": one_probe()}, "non-empty string"),
        ({"name": "n", "probes": ()}, "at least one probe"),
        ({"name": "n", "probes": [Probe("p", {"a": 1})]}, "at least one probe"),
        ({"name": "n", "probes": ("x",)}, "Probe instances"),
        ({"name": "n", "probes": (Probe("p", {"a": 1}), Probe("p", {"b": 2}))}, "duplicate"),
        ({"name": "n", "probes": one_probe(), "sampling": [1]}, "sampling must be an object"),
        ({"name": "n", "probes": one_probe(), "sampling": {"": "1"}}, "non-empty strings"),
        ({"name": "n", "probes": one_probe(), "sampling": {"t": 0.7}}, "must be a decimal string"),
        ({"name": "n", "probes": one_probe(), "sampling": {"t": "abc"}}, r"sampling\['t'\]"),
        ({"name": "n", "probes": one_probe(), "seed_from_attempt": 1}, "must be a boolean"),
        ({"name": "n", "probes": one_probe(), "seed_base": True}, "seed_base must be an integer"),
        ({"name": "n", "probes": one_probe(), "seed_field": ""}, "seed_field must be"),
        ({"name": "n", "probes": one_probe(), "min_samples": 0}, "at least 1"),
        ({"name": "n", "probes": one_probe(), "min_samples": True}, "at least 1"),
        ({"name": "n", "probes": one_probe(), "sampling": {"input": "1"}}, "reserved"),
        (
            {"name": "n", "probes": (Probe("p", {"seed": 1}),), "seed_from_attempt": True},
            "reserved",
        ),
    ],
)
def test_panel_validation(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(PanelError, match=message):
        Panel(**kwargs)


def test_seed_field_may_appear_in_payload_when_seed_from_attempt_is_off() -> None:
    panel = Panel(name="n", probes=(Probe("p", {"seed": 1}),))
    assert panel.request_for(panel.probes[0], 4) == {"seed": 1}


def test_decoded_sampling_types() -> None:
    panel = Panel(
        name="n",
        probes=one_probe(),
        sampling={"temperature": "0.7", "top_k": "40", "top_p": "1.0", "big": "1e3"},
    )
    decoded = panel.decoded_sampling()
    assert decoded == {"temperature": 0.7, "top_k": 40, "top_p": 1.0, "big": 1000.0}
    assert isinstance(decoded["top_k"], int)
    assert isinstance(decoded["top_p"], float)
    assert isinstance(decoded["big"], float)


def test_request_for_merges_without_touching_the_probe() -> None:
    panel = demo_panel()
    probe = panel.probes[1]
    request = panel.request_for(probe, 3)
    assert request == {
        "model": "x",
        "input": "yo",
        "temperature": 0.7,
        "max_output_tokens": 64,
        "seed": 3,
    }
    request["input"] = "changed"
    assert probe.payload["input"] == "yo"
    assert panel.request_for(probe, 0)["seed"] == 0
    plain = Panel(name="n", probes=(probe,))
    assert plain.request_for(probe, 9) == probe.payload


@pytest.mark.parametrize("attempt", [-1, True, 1.0, "0"])
def test_request_for_rejects_bad_attempts(attempt: Any) -> None:
    panel = demo_panel()
    with pytest.raises(ValueError):
        panel.request_for(panel.probes[0], attempt)


def test_probe_lookup() -> None:
    panel = demo_panel()
    assert panel.probe("p2") is panel.probes[1]
    with pytest.raises(KeyError):
        panel.probe("missing")


probe_ids = st.from_regex(r"[A-Za-z0-9_.-]{1,12}", fullmatch=True)
payload_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=20),
    lambda children: st.lists(children, max_size=3)
    | st.dictionaries(st.text(max_size=8), children, max_size=3),
    max_leaves=10,
)
payloads = st.dictionaries(
    st.text(min_size=1, max_size=8).filter(lambda key: key not in {"temperature", "seed"}),
    payload_values,
    min_size=1,
    max_size=4,
)


probe_lists = st.lists(
    st.tuples(probe_ids, payloads), min_size=1, max_size=5, unique_by=lambda item: item[0]
)


@given(probe_lists, st.booleans())
def test_generated_panels_round_trip(
    probes: list[tuple[str, dict[str, Any]]], seeded: bool
) -> None:
    panel = Panel(
        name="generated",
        probes=tuple(Probe(probe_id, payload) for probe_id, payload in probes),
        sampling={"temperature": "0.2"},
        seed_from_attempt=seeded,
    )
    document = json.loads(json.dumps(panel.to_document(), ensure_ascii=False))
    restored = Panel.from_document(document)
    assert restored == panel
    assert restored.digest == panel.digest
    request = panel.request_for(panel.probes[0], 2)
    assert request["temperature"] == 0.2
    assert ("seed" in request) is seeded
