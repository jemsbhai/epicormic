"""Levers: driving pollard's fail-closed hooks from the ledger (docs/PLAN.md, section 11).

``DriftMeter`` implements pollard's ``Meter`` protocol and refuses a step
before dispatch, through ``MeterPrecheckRefusal``, when the monitored
drift state is one of ``refuse_on``. ``DriftPolicy`` implements pollard's
``Policy`` protocol and maps the drift state to an ALLOW, CONFIRM, or DENY
decision for registered tool calls, by default only for side-effectful
ones. ``ContractGate`` is a meter that refuses a model call whose bound
replay contract differs from the expected execution fingerprint unless the
live fingerprint's digest has been acknowledged.

All three are constructed by the application and passed into
``pollard.Runtime(meters=[...], policies=[...])``; a ``DriftMeter`` is
appended to pollard's default meters, never substituted for them. A ledger
lookup that fails (an ambiguous monitor id, a branching chain) raises
normally, because that is a configuration or storage error, not a
governance decision; a monitor with no verdict yet has state ``unknown``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pollard import Decision, ReplayContract
from pollard.meters import MeterPrecheckRefusal
from pollard.policy import PolicyContext
from pollard.revalidation import extract_replay_contract

from .ledger import Ledger, LedgerEntry
from .verdict import STATES, contract_digest, difference_paths

__all__ = ["ContractGate", "DriftMeter", "DriftPolicy", "LeverError"]


class LeverError(ValueError):
    """A lever is misconfigured."""


def _check_states(name: str, states: Iterable[str]) -> tuple[str, ...]:
    values = tuple(states)
    unknown = [state for state in values if state not in STATES]
    if unknown:
        raise LeverError(f"{name} contains unknown states {unknown}; choose from {STATES}")
    return values


def _check_kinds(kinds: Iterable[str]) -> tuple[str, ...]:
    values = tuple(kinds)
    if not values or not all(isinstance(kind, str) and kind for kind in values):
        raise LeverError("kinds must be a non-empty tuple of node kind names")
    return values


def _check_decision(name: str, decision: Decision) -> Decision:
    if not isinstance(decision, Decision):
        raise LeverError(f"{name} must be a pollard Decision")
    return decision


class _LedgerReader:
    def __init__(self, ledger: Ledger, monitor_id: str, panel_digest: str | None) -> None:
        if not isinstance(ledger, Ledger):
            raise LeverError("ledger must be an epicormic Ledger")
        if not isinstance(monitor_id, str) or not monitor_id:
            raise LeverError("monitor_id must be a non-empty string")
        self.ledger = ledger
        self.monitor_id = monitor_id
        self.panel_digest = panel_digest

    def entry(self) -> LedgerEntry | None:
        return self.ledger.state(self.monitor_id, self.panel_digest)

    def audit(self, entry: LedgerEntry | None, state: str) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "monitor_id": self.monitor_id,
            "panel_digest": self.panel_digest if entry is None else entry.panel_digest,
            "state": state,
        }
        if entry is not None:
            meta.update(
                {
                    "verdict_node_id": entry.verdict_node_id,
                    "window_id": entry.window_id,
                    "sequence_index": entry.sequence_index,
                }
            )
        return meta


class DriftMeter:
    """A pollard meter that refuses dispatch while a monitor is in a listed state."""

    def __init__(
        self,
        ledger: Ledger,
        monitor_id: str,
        *,
        panel_digest: str | None = None,
        refuse_on: Iterable[str] = ("drift",),
        kinds: Iterable[str] = ("model_call",),
        name: str = "epicormic",
    ) -> None:
        self._reader = _LedgerReader(ledger, monitor_id, panel_digest)
        self.refuse_on = _check_states("refuse_on", refuse_on)
        self.kinds = _check_kinds(kinds)
        if not isinstance(name, str) or not name:
            raise LeverError("name must be a non-empty string")
        self.name = name

    @property
    def monitor_id(self) -> str:
        return self._reader.monitor_id

    def current_state(self) -> str:
        """The monitor's state as the meter sees it (``unknown`` before any verdict)."""

        entry = self._reader.entry()
        return "unknown" if entry is None else entry.state

    def precheck_estimate(self, node_kind: str, payload: Mapping[str, Any]) -> int | None:
        """Refuse through ``MeterPrecheckRefusal`` or return ``None`` (no estimate)."""

        del payload
        if node_kind not in self.kinds:
            return None
        entry = self._reader.entry()
        state = "unknown" if entry is None else entry.state
        if state in self.refuse_on:
            raise MeterPrecheckRefusal(
                "epicormic_drift",
                f"monitor {self.monitor_id!r} is in state {state!r}",
                audit_meta=self._reader.audit(entry, state),
            )
        return None

    def charge(
        self, node_kind: str, payload: Mapping[str, Any], result: Any, meta: Mapping[str, Any]
    ) -> int:
        del node_kind, payload, result, meta
        return 0


class DriftPolicy:
    """A pollard policy that maps the monitor's state to a decision on tool calls."""

    def __init__(
        self,
        ledger: Ledger,
        monitor_id: str,
        *,
        panel_digest: str | None = None,
        on_drift: Decision = Decision.CONFIRM,
        on_warning: Decision = Decision.ALLOW,
        on_unknown: Decision = Decision.ALLOW,
        on_stable: Decision = Decision.ALLOW,
        side_effects_only: bool = True,
    ) -> None:
        self._reader = _LedgerReader(ledger, monitor_id, panel_digest)
        self.decisions = {
            "drift": _check_decision("on_drift", on_drift),
            "warning": _check_decision("on_warning", on_warning),
            "unknown": _check_decision("on_unknown", on_unknown),
            "stable": _check_decision("on_stable", on_stable),
        }
        self.side_effects_only = bool(side_effects_only)

    @property
    def monitor_id(self) -> str:
        return self._reader.monitor_id

    def decide(self, ctx: PolicyContext) -> Decision:
        if self.side_effects_only and not ctx.spec.side_effects:
            return Decision.ALLOW
        entry = self._reader.entry()
        state = "unknown" if entry is None else entry.state
        return self.decisions.get(state, Decision.ALLOW)


class ContractGate:
    """A pollard meter that refuses model calls bound to an unexpected replay contract."""

    def __init__(
        self,
        expected: ReplayContract,
        *,
        acknowledged: Iterable[str] = (),
        kinds: Iterable[str] = ("model_call",),
        name: str = "epicormic-contract",
    ) -> None:
        if not isinstance(expected, ReplayContract):
            raise LeverError("expected must be a pollard ReplayContract")
        self.expected = expected.to_dict()
        self.expected_digest = contract_digest(self.expected)
        self.acknowledged = frozenset(acknowledged)
        if not all(isinstance(item, str) and item for item in self.acknowledged):
            raise LeverError("acknowledged must contain contract digests")
        self.kinds = _check_kinds(kinds)
        if not isinstance(name, str) or not name:
            raise LeverError("name must be a non-empty string")
        self.name = name

    def precheck_estimate(self, node_kind: str, payload: Mapping[str, Any]) -> int | None:
        """Refuse through ``MeterPrecheckRefusal`` or return ``None`` (no estimate)."""

        if node_kind not in self.kinds:
            return None
        bound = extract_replay_contract(dict(payload))
        if bound is None:
            return None
        live_digest = contract_digest(bound)
        if live_digest == self.expected_digest or live_digest in self.acknowledged:
            return None
        raise MeterPrecheckRefusal(
            "contract_changed",
            "bound replay contract differs from the expected execution fingerprint",
            audit_meta={
                "expected_digest": self.expected_digest,
                "live_digest": live_digest,
                "difference_paths": difference_paths(self.expected, bound),
            },
        )

    def charge(
        self, node_kind: str, payload: Mapping[str, Any], result: Any, meta: Mapping[str, Any]
    ) -> int:
        del node_kind, payload, result, meta
        return 0
