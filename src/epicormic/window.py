"""Observation windows: recording N samples of every probe in a pollard tree.

One window is one pollard run. Its layout (docs/PLAN.md, section 6, D5):

    ROOT {"run": "epicormic/<panel16>/<window_id>"}
    `-- NOTE window header
        |-- NOTE {"branch": true} attempt=<probe index>
        |   `-- NOTE probe header
        |       |-- NOTE {"branch": true} attempt=<sample>  `-- MODEL_CALL attempt=<sample>
        |       `-- ...
        `-- ...

The header identity commits to the panel digest, the window id, the
declared replay contract, and the sampling configuration, and to nothing
that may legitimately change between reruns: the requested sample count
and the epicormic version live in the header's metadata. Observation runs
in pollard's hybrid mode, so a rerun serves every sample that already
exists and dispatches only the missing ones.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from pollard import Budget, BudgetExceeded, ReplayContract, Runtime
from pollard.replay import ReplayMode
from pollard.store import Store

from . import __version__
from ._canon import IdentityValue
from .panel import Panel, Probe

WINDOW_FORMAT = "epicormic/window/v1"
PROBE_HEADER_FORMAT = "epicormic/probe/v1"
LABEL_PREFIX = "epicormic/"

StepResult = dict[str, Any] | Iterator[dict[str, Any]]
StepFn = Callable[[dict[str, Any]], StepResult]
SampleCallback = Callable[["SampleEvent"], None]

__all__ = [
    "LABEL_PREFIX",
    "PROBE_HEADER_FORMAT",
    "WINDOW_FORMAT",
    "ProbeReport",
    "SampleEvent",
    "WindowError",
    "WindowReport",
    "header_payload",
    "observe",
    "window_label",
]


class WindowError(ValueError):
    """The observation request cannot be carried out as asked."""


@dataclass(frozen=True)
class SampleEvent:
    """What happened to one sample: ``served`` from the store, ``dispatched``, or ``refused``."""

    probe_id: str
    probe_index: int
    attempt: int
    outcome: str
    node_id: str


@dataclass(frozen=True)
class ProbeReport:
    probe_id: str
    probe_digest: str
    probe_index: int
    header_id: str
    sample_ids: tuple[str, ...]
    refusal_ids: tuple[str, ...]
    served: int
    dispatched: int
    not_attempted: int

    @property
    def refused(self) -> int:
        return len(self.refusal_ids)

    @property
    def recorded(self) -> int:
        return len(self.sample_ids)


@dataclass(frozen=True)
class WindowReport:
    root_id: str
    header_id: str
    label: str
    panel_digest: str
    window_id: str
    samples_requested: int
    probes: tuple[ProbeReport, ...]

    @property
    def recorded(self) -> int:
        return sum(probe.recorded for probe in self.probes)

    @property
    def dispatched(self) -> int:
        return sum(probe.dispatched for probe in self.probes)

    @property
    def served(self) -> int:
        return sum(probe.served for probe in self.probes)

    @property
    def refused(self) -> int:
        return sum(probe.refused for probe in self.probes)

    @property
    def not_attempted(self) -> int:
        return sum(probe.not_attempted for probe in self.probes)

    @property
    def complete(self) -> bool:
        return all(probe.recorded == self.samples_requested for probe in self.probes)


def window_label(panel_digest: str, window_id: str) -> str:
    """The pollard run label of a window; distinct windows never share a root."""

    _check_window_id(window_id)
    return f"{LABEL_PREFIX}{panel_digest[:16]}/{window_id}"


def header_payload(
    panel: Panel, window_id: str, contract: ReplayContract | None
) -> dict[str, IdentityValue]:
    """The identity payload of the window header note."""

    _check_window_id(window_id)
    return {
        "format": WINDOW_FORMAT,
        "panel_digest": panel.digest,
        "panel_name": panel.name,
        "window_id": window_id,
        "contract": contract.to_dict() if contract is not None else None,
        "sampling": dict(panel.sampling),
        "seed_from_attempt": panel.seed_from_attempt,
        "seed_base": panel.seed_base,
        "seed_field": panel.seed_field,
    }


def observe(
    panel: Panel,
    *,
    window_id: str,
    samples: int,
    fn: StepFn,
    store: Store | str | None = None,
    runtime: Runtime | None = None,
    contract: ReplayContract | None = None,
    budget: Budget | None = None,
    probe_budget: Budget | None = None,
    keep_chunks: bool = False,
    on_sample: SampleCallback | None = None,
) -> WindowReport:
    """Record ``samples`` observations of every probe of ``panel`` in one window.

    Pass either ``store`` (a pollard store or a SQLite path; a hybrid-mode
    ``Runtime`` with the default meters is built around it) or ``runtime``
    (which must be in hybrid mode; use this to attach meters and policies).
    ``fn`` is the caller's step callable; it receives the request built by
    ``Panel.request_for`` and never the bare identity payload.

    A sample refused by a budget or meter leaves a refusal node in the tree
    and is counted; the remaining samples of that probe are not attempted,
    and observation continues with the next probe. Rerunning with the same
    arguments after raising the budget dispatches only what is missing.
    """

    if isinstance(samples, bool) or not isinstance(samples, int) or samples < 1:
        raise WindowError("samples must be an integer of at least 1")
    if (runtime is None) == (store is None):
        raise WindowError("pass exactly one of store or runtime")
    if runtime is None:
        runtime = Runtime(store, mode=ReplayMode.HYBRID)
    elif runtime.mode != ReplayMode.HYBRID:
        raise WindowError(
            f"observation requires a hybrid-mode Runtime, got mode={runtime.mode.value!r}"
        )
    label = window_label(panel.digest, window_id)
    payload = header_payload(panel, window_id, contract)
    reports: list[ProbeReport] = []

    with runtime.run(label, budget=budget) as run:
        header = run.note(payload)
        runtime.store.update_meta(
            header.id,
            {"epicormic": {"samples_requested": samples, "epicormic_version": __version__}},
        )
        for index, probe in enumerate(panel.probes):
            with run.branch(attempt=index, budget=probe_budget) as probe_run:
                probe_header = probe_run.note(
                    {
                        "format": PROBE_HEADER_FORMAT,
                        "probe_id": probe.probe_id,
                        "probe_digest": probe.digest,
                        "index": index,
                    }
                )
                sample_ids: list[str] = []
                refusal_ids: list[str] = []
                attempted = 0
                dispatched = 0
                for attempt in range(samples):
                    attempted += 1
                    step = _Step(fn, panel.request_for(probe, attempt))
                    with probe_run.branch(attempt=attempt) as sample_run:
                        try:
                            node = sample_run.model_call(
                                probe.payload, fn=step, attempt=attempt, keep_chunks=keep_chunks
                            )
                        except BudgetExceeded as refused:
                            refusal_ids.append(refused.refusal_id)
                            _emit(on_sample, probe, index, attempt, "refused", refused.refusal_id)
                            break
                    sample_ids.append(node.id)
                    dispatched += int(step.fired)
                    _emit(
                        on_sample,
                        probe,
                        index,
                        attempt,
                        "dispatched" if step.fired else "served",
                        node.id,
                    )
                reports.append(
                    ProbeReport(
                        probe_id=probe.probe_id,
                        probe_digest=probe.digest,
                        probe_index=index,
                        header_id=probe_header.id,
                        sample_ids=tuple(sample_ids),
                        refusal_ids=tuple(refusal_ids),
                        served=len(sample_ids) - dispatched,
                        dispatched=dispatched,
                        not_attempted=samples - attempted,
                    )
                )
        root_id = run.root_id

    return WindowReport(
        root_id=root_id,
        header_id=header.id,
        label=label,
        panel_digest=panel.digest,
        window_id=window_id,
        samples_requested=samples,
        probes=tuple(reports),
    )


class _Step:
    """The step callable handed to pollard for one sample.

    It ignores the identity payload pollard passes and dispatches the full
    request built by ``Panel.request_for``; ``fired`` records whether pollard
    invoked it, which distinguishes a dispatched sample from one served from
    the store in hybrid mode.
    """

    def __init__(self, fn: StepFn, request: dict[str, Any]) -> None:
        self.fn = fn
        self.request = request
        self.fired = False

    def __call__(self, _payload: dict[str, Any]) -> StepResult:
        self.fired = True
        return self.fn(dict(self.request))


def _emit(
    callback: SampleCallback | None,
    probe: Probe,
    index: int,
    attempt: int,
    outcome: str,
    node_id: str,
) -> None:
    if callback is not None:
        callback(SampleEvent(probe.probe_id, index, attempt, outcome, node_id))


def _check_window_id(window_id: str) -> None:
    if not isinstance(window_id, str) or not window_id or "/" in window_id:
        raise WindowError("window_id must be a non-empty string without '/'")
