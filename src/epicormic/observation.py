"""Readers over the window layout: turn a pollard tree back into observations.

``read_window`` walks one window header (docs/PLAN.md, section 6) and
returns every recorded sample as an ``Observation`` together with the
refusals, per probe, in probe order. ``find_window_headers`` locates the
headers of a window from the panel digest and window id alone, so callers
never need to keep node ids.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pollard import Node, NodeKind
from pollard.store import Store

from .window import PROBE_HEADER_FORMAT, WINDOW_FORMAT, window_label

__all__ = [
    "Observation",
    "ProbeObservations",
    "WindowView",
    "find_window_headers",
    "find_window_root",
    "read_window",
]


@dataclass(frozen=True)
class Observation:
    """One recorded sample of one probe."""

    node_id: str
    window_id: str
    probe_id: str
    probe_index: int
    attempt: int
    payload: dict[str, Any]
    result: Any
    result_digest: str | None
    meta: dict[str, Any]


@dataclass(frozen=True)
class ProbeObservations:
    probe_id: str
    probe_digest: str
    probe_index: int
    header_id: str
    observations: tuple[Observation, ...]
    refusal_ids: tuple[str, ...]


@dataclass(frozen=True)
class WindowView:
    root_id: str
    header_id: str
    window_id: str
    panel_digest: str
    panel_name: str
    contract: dict[str, Any] | None
    header: dict[str, Any]
    header_meta: dict[str, Any]
    probes: tuple[ProbeObservations, ...]

    @property
    def observations(self) -> tuple[Observation, ...]:
        return tuple(obs for probe in self.probes for obs in probe.observations)

    def probe(self, probe_id: str) -> ProbeObservations:
        for probe in self.probes:
            if probe.probe_id == probe_id:
                return probe
        raise KeyError(probe_id)


def find_window_root(panel_digest: str, window_id: str) -> str:
    """The root node id of a window, computed from the label alone."""

    label = window_label(panel_digest, window_id)
    return Node.make(kind=NodeKind.ROOT, parent=None, payload={"run": label}).id


def find_window_headers(store: Store, panel_digest: str, window_id: str) -> list[str]:
    """Header note ids of a window, oldest first; several exist only if the contract changed."""

    root_id = find_window_root(panel_digest, window_id)
    if not store.exists(root_id):
        return []
    headers = [
        node
        for node in (store.get(child) for child in store.children(root_id))
        if node.kind == NodeKind.NOTE.value and node.payload.get("format") == WINDOW_FORMAT
    ]
    headers.sort(key=lambda node: str(node.meta.get("created_at", "")))
    return [node.id for node in headers]


def read_window(store: Store, header_id: str) -> WindowView:
    """Read every observation and refusal under one window header."""

    header = store.get(header_id)
    if header.kind != NodeKind.NOTE.value or header.payload.get("format") != WINDOW_FORMAT:
        raise ValueError(f"{header_id} is not an epicormic window header")
    window_id = str(header.payload["window_id"])
    probes: list[ProbeObservations] = []
    for anchor_id in store.children(header_id):
        anchor = store.get(anchor_id)
        if not _is_branch_anchor(anchor):
            continue
        for probe_header_id in store.children(anchor_id):
            probe_header = store.get(probe_header_id)
            if probe_header.payload.get("format") != PROBE_HEADER_FORMAT:
                continue
            probes.append(_read_probe(store, probe_header, window_id))
    probes.sort(key=lambda probe: probe.probe_index)
    return WindowView(
        root_id=header.parent or "",
        header_id=header.id,
        window_id=window_id,
        panel_digest=str(header.payload["panel_digest"]),
        panel_name=str(header.payload["panel_name"]),
        contract=_as_dict(header.payload.get("contract")),
        header=dict(header.payload),
        header_meta=dict(header.meta),
        probes=tuple(probes),
    )


def _read_probe(store: Store, probe_header: Node, window_id: str) -> ProbeObservations:
    probe_id = str(probe_header.payload["probe_id"])
    index_value = probe_header.payload["index"]
    if isinstance(index_value, bool) or not isinstance(index_value, int):
        raise ValueError(f"probe header {probe_header.id} has a non-integer index")
    probe_index = index_value
    observations: list[Observation] = []
    refusals: list[str] = []
    for anchor_id in store.children(probe_header.id):
        anchor = store.get(anchor_id)
        if not _is_branch_anchor(anchor):
            continue
        for leaf_id in store.children(anchor_id):
            leaf = store.get(leaf_id)
            if leaf.kind == NodeKind.MODEL_CALL.value:
                observations.append(
                    Observation(
                        node_id=leaf.id,
                        window_id=window_id,
                        probe_id=probe_id,
                        probe_index=probe_index,
                        attempt=leaf.attempt,
                        payload=dict(leaf.payload),
                        result=leaf.result,
                        result_digest=leaf.result_digest,
                        meta=dict(leaf.meta),
                    )
                )
            elif leaf.kind == NodeKind.REFUSAL.value:
                refusals.append(leaf.id)
    observations.sort(key=lambda obs: obs.attempt)
    return ProbeObservations(
        probe_id=probe_id,
        probe_digest=str(probe_header.payload["probe_digest"]),
        probe_index=probe_index,
        header_id=probe_header.id,
        observations=tuple(observations),
        refusal_ids=tuple(refusals),
    )


def _is_branch_anchor(node: Node) -> bool:
    return node.kind == NodeKind.NOTE.value and node.payload == {"branch": True}


def _as_dict(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, dict) else None
