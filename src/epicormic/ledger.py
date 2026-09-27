"""The ledger: the current drift state of each monitor, read from the store.

The monitor chain is the source of truth. ``record_verdict`` also patches
the monitor root's metadata with a pointer to the latest verdict; the
ledger uses that pointer only after verifying that the node exists, is a
verdict of this monitor, and is the last link of the chain, and otherwise
walks the chain. Levers (docs/PLAN.md, section 11) read their state here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pollard import Node, NodeKind
from pollard.store import Store

from .verdict import MONITOR_FORMAT, MONITOR_LABEL_PREFIX, VERDICT_FORMAT, monitor_label

__all__ = ["Ledger", "LedgerEntry", "LedgerError"]


class LedgerError(ValueError):
    """The ledger cannot resolve a monitor."""


@dataclass(frozen=True)
class LedgerEntry:
    monitor_id: str
    panel_digest: str
    state: str
    verdict_node_id: str
    window_id: str
    sequence_index: int
    computed_at: str | None


class Ledger:
    def __init__(self, store: Store) -> None:
        self.store = store

    def state(self, monitor_id: str, panel_digest: str | None = None) -> LedgerEntry | None:
        """The latest verdict of a monitor, or ``None`` when it has no verdict yet."""

        root_id = self._root(monitor_id, panel_digest)
        if root_id is None:
            return None
        indexed = self._indexed(root_id)
        if indexed is not None:
            return indexed
        history = self._walk(root_id)
        return history[-1] if history else None

    def history(self, monitor_id: str, panel_digest: str | None = None) -> list[LedgerEntry]:
        """Every verdict of a monitor in chain order."""

        root_id = self._root(monitor_id, panel_digest)
        return [] if root_id is None else self._walk(root_id)

    # --- internals -----------------------------------------------------------

    def _root(self, monitor_id: str, panel_digest: str | None) -> str | None:
        if not isinstance(monitor_id, str) or not monitor_id or "/" in monitor_id:
            raise LedgerError("monitor_id must be a non-empty string without '/'")
        if panel_digest is not None:
            root_id = Node.make(
                kind=NodeKind.ROOT,
                parent=None,
                payload={"run": monitor_label(panel_digest, monitor_id)},
            ).id
            return root_id if self.store.exists(root_id) else None
        matches: list[str] = []
        suffix = f"/{monitor_id}"
        for root_id in self.store.roots():
            label = self.store.get(root_id).payload.get("run")
            if (
                isinstance(label, str)
                and label.startswith(MONITOR_LABEL_PREFIX)
                and label.endswith(suffix)
            ):
                matches.append(root_id)
        if len(matches) > 1:
            raise LedgerError(
                f"monitor_id {monitor_id!r} exists for {len(matches)} panels; pass panel_digest"
            )
        return matches[0] if matches else None

    def _indexed(self, root_id: str) -> LedgerEntry | None:
        index = self.store.get(root_id).meta.get("epicormic")
        if not isinstance(index, dict):
            return None
        node_id = index.get("latest_verdict")
        if not isinstance(node_id, str) or not self.store.exists(node_id):
            return None
        node = self.store.get(node_id)
        if node.payload.get("format") != VERDICT_FORMAT or self.store.children(node_id):
            return None
        if not self._descends_from(node_id, root_id):
            return None
        return self._entry(node_id, root_id)

    def _descends_from(self, node_id: str, root_id: str) -> bool:
        cursor = node_id
        while True:
            parent = self.store.get(cursor).parent
            if parent is None:
                return cursor == root_id
            if parent == root_id:
                return True
            cursor = parent

    def _walk(self, root_id: str) -> list[LedgerEntry]:
        headers = [
            child
            for child in self.store.children(root_id)
            if self.store.get(child).payload.get("format") == MONITOR_FORMAT
        ]
        if not headers:
            return []
        entries: list[LedgerEntry] = []
        cursor = headers[0]
        while True:
            verdicts = [
                child
                for child in self.store.children(cursor)
                if self.store.get(child).payload.get("format") == VERDICT_FORMAT
            ]
            if not verdicts:
                return entries
            if len(verdicts) > 1:
                raise LedgerError(f"monitor chain branches at {cursor}")
            cursor = verdicts[0]
            entries.append(self._entry(cursor, root_id))

    def _entry(self, node_id: str, root_id: str) -> LedgerEntry:
        node = self.store.get(node_id)
        payload: dict[str, Any] = dict(node.payload)
        created = node.meta.get("created_at")
        return LedgerEntry(
            monitor_id=str(payload.get("monitor_id", "")),
            panel_digest=str(payload.get("panel_digest", "")),
            state=str(payload.get("state", "unknown")),
            verdict_node_id=node_id,
            window_id=str(payload.get("current_window_id", "")),
            sequence_index=_int(payload.get("sequence_index")),
            computed_at=str(created) if created is not None else None,
        )


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
