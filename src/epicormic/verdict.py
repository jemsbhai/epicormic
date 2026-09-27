"""Verdicts: comparing a current window with the baseline and recording the evidence.

A ``Monitor`` fixes a panel, a set of baseline windows, and an
``AnalysisConfig``; each ``analyze`` call compares one current window with
the pooled baseline, runs Layer A per probe and pooled, runs Layer B over
the monitor's whole history recomputed from the store, applies the state
rules of docs/PLAN.md section 9, derives the opinion, and appends a
value-free verdict note to the monitor chain (section 6). Every number in
the note is a decimal string (D6); the note names probes, node ids,
digests, scorer and method names, and nothing taken from any result.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pollard import Node, NodeKind, Runtime
from pollard.replay import ReplayMode
from pollard.store import Store

from . import __version__
from ._canon import IdentityValue, canonical_bytes, domain_digest
from ._decimal import encode_evidence, parse_decimal_str
from .observation import WindowView, find_window_headers, find_window_root, read_window
from .opinion import Opinion, opinion_from_log_evidence, vacuous
from .scorers import (
    DEFAULT_SCORER_NAMES,
    Reference,
    Scorer,
    ScoreTable,
    build_references,
    resolve_scorers,
    score_window,
)
from .stats import (
    benjamini_hochberg,
    betting_eprocess,
    clipped_rate,
    cusum,
    holm,
    indicator_test,
    mann_whitney,
    page_hinkley,
    sign_transform,
    stratified_permutation_test,
)

MONITOR_FORMAT = "epicormic/monitor/v1"
VERDICT_FORMAT = "epicormic/verdict/v1"
CONFIG_DOMAIN = b"epicormic/config/v1\n"
MONITOR_LABEL_PREFIX = "epicormic-monitor/"
STATES = ("stable", "warning", "drift", "unknown")

__all__ = [
    "CONFIG_DOMAIN",
    "MONITOR_FORMAT",
    "STATES",
    "VERDICT_FORMAT",
    "AnalysisConfig",
    "Monitor",
    "Verdict",
    "VerdictError",
    "analyze",
    "compute_verdict",
    "monitor_label",
    "read_chain",
    "recompute_verdict",
    "record_verdict",
]


class VerdictError(ValueError):
    """The analysis cannot be carried out as configured."""


def _decimal(name: str, text: str, low: float, high: float) -> float:
    try:
        value = float(parse_decimal_str(text))
    except (TypeError, ValueError) as error:
        raise VerdictError(f"{name} must be a decimal string, got {text!r}") from error
    if not low <= value <= high:
        raise VerdictError(f"{name} must lie in [{low}, {high}], got {text!r}")
    return value


@dataclass(frozen=True)
class AnalysisConfig:
    """Everything a verdict depends on besides the observations.

    Numbers are decimal strings so the configuration document is
    identity-safe and digested exactly as written.
    """

    scorers: tuple[str, ...] = DEFAULT_SCORER_NAMES
    alpha: str = "0.05"
    q: str = "0.05"
    permutations: int = 2000
    seed: int = 0
    n_min: int = 5
    effect_min: str = "0.2"
    unknown_fraction: str = "0.5"
    top_k: int = 50
    base_rate: str = "0.5"
    prior_weight: str = "2"
    cusum: Mapping[str, str] = field(default_factory=lambda: {"k": "0.1", "h": "0.4"})
    page_hinkley: Mapping[str, str] = field(
        default_factory=lambda: {"delta": "0.05", "lambda": "0.5"}
    )
    eprocess: Mapping[str, Any] = field(
        default_factory=lambda: {"alpha": "0.05", "grid": ["0.1", "0.25", "0.5", "0.75"]}
    )

    def __post_init__(self) -> None:
        if not self.scorers or len(set(self.scorers)) != len(self.scorers):
            raise VerdictError("scorers must be a non-empty tuple of distinct names")
        for name, value, low in (
            ("permutations", self.permutations, 1),
            ("seed", self.seed, 0),
            ("n_min", self.n_min, 1),
            ("top_k", self.top_k, 0),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < low:
                raise VerdictError(f"{name} must be an integer of at least {low}")
        self.validate_numbers()
        object.__setattr__(self, "scorers", tuple(self.scorers))
        object.__setattr__(self, "cusum", dict(self.cusum))
        object.__setattr__(self, "page_hinkley", dict(self.page_hinkley))
        object.__setattr__(self, "eprocess", dict(self.eprocess))

    def validate_numbers(self) -> None:
        """Parse every decimal field once; raises ``VerdictError`` on a bad value."""

        _ = (
            self.alpha_value,
            self.q_value,
            self.effect_min_value,
            self.unknown_fraction_value,
            self.base_rate_value,
            self.prior_weight_value,
            self.cusum_values,
            self.page_hinkley_values,
            self.eprocess_values,
        )

    @property
    def alpha_value(self) -> float:
        value = _decimal("alpha", self.alpha, 0, 1)
        if value in (0, 1):
            raise VerdictError("alpha must lie strictly between 0 and 1")
        return value

    @property
    def q_value(self) -> float:
        value = _decimal("q", self.q, 0, 1)
        if value in (0, 1):
            raise VerdictError("q must lie strictly between 0 and 1")
        return value

    @property
    def effect_min_value(self) -> float:
        return _decimal("effect_min", self.effect_min, 0, 1)

    @property
    def unknown_fraction_value(self) -> float:
        return _decimal("unknown_fraction", self.unknown_fraction, 0, 1)

    @property
    def base_rate_value(self) -> float:
        value = _decimal("base_rate", self.base_rate, 0, 1)
        if value in (0, 1):
            raise VerdictError("base_rate must lie strictly between 0 and 1")
        return value

    @property
    def prior_weight_value(self) -> float:
        value = _decimal("prior_weight", self.prior_weight, 0, 1e9)
        if value == 0:
            raise VerdictError("prior_weight must be positive")
        return value

    @property
    def cusum_values(self) -> tuple[float, float]:
        k = _decimal("cusum.k", str(self.cusum.get("k", "")), 0, 10)
        h = _decimal("cusum.h", str(self.cusum.get("h", "")), 0, 10)
        if h == 0:
            raise VerdictError("cusum.h must be positive")
        return k, h

    @property
    def page_hinkley_values(self) -> tuple[float, float]:
        delta = _decimal("page_hinkley.delta", str(self.page_hinkley.get("delta", "")), 0, 10)
        lam = _decimal("page_hinkley.lambda", str(self.page_hinkley.get("lambda", "")), 0, 10)
        if lam == 0:
            raise VerdictError("page_hinkley.lambda must be positive")
        return delta, lam

    @property
    def eprocess_values(self) -> tuple[float, tuple[float, ...]]:
        alpha = _decimal("eprocess.alpha", str(self.eprocess.get("alpha", "")), 0, 1)
        if alpha in (0, 1):
            raise VerdictError("eprocess.alpha must lie strictly between 0 and 1")
        grid_text = self.eprocess.get("grid")
        if not isinstance(grid_text, list) or not grid_text:
            raise VerdictError("eprocess.grid must be a non-empty list of decimal strings")
        grid = tuple(_decimal("eprocess.grid", str(item), 0, 1) for item in grid_text)
        if any(f in (0, 1) for f in grid):
            raise VerdictError("eprocess.grid fractions must lie strictly between 0 and 1")
        return alpha, grid

    def to_document(self) -> dict[str, IdentityValue]:
        return {
            "format": "epicormic/config/v1",
            "scorers": list(self.scorers),
            "alpha": self.alpha,
            "q": self.q,
            "permutations": self.permutations,
            "seed": self.seed,
            "n_min": self.n_min,
            "effect_min": self.effect_min,
            "unknown_fraction": self.unknown_fraction,
            "top_k": self.top_k,
            "base_rate": self.base_rate,
            "prior_weight": self.prior_weight,
            "cusum": dict(self.cusum),
            "page_hinkley": dict(self.page_hinkley),
            "eprocess": {"alpha": self.eprocess["alpha"], "grid": list(self.eprocess["grid"])},
        }

    @property
    def digest(self) -> str:
        return domain_digest(CONFIG_DOMAIN, self.to_document())

    @classmethod
    def from_document(cls, document: Any) -> AnalysisConfig:
        if not isinstance(document, dict):
            raise VerdictError("config document must be an object")
        if document.get("format") != "epicormic/config/v1":
            raise VerdictError("config format must be 'epicormic/config/v1'")
        known = {
            "format",
            "scorers",
            "alpha",
            "q",
            "permutations",
            "seed",
            "n_min",
            "effect_min",
            "unknown_fraction",
            "top_k",
            "base_rate",
            "prior_weight",
            "cusum",
            "page_hinkley",
            "eprocess",
        }
        unknown = set(document) - known
        if unknown:
            raise VerdictError(f"config document has unknown keys: {sorted(unknown)}")
        defaults = cls()
        return cls(
            scorers=tuple(document.get("scorers", defaults.scorers)),
            alpha=document.get("alpha", defaults.alpha),
            q=document.get("q", defaults.q),
            permutations=document.get("permutations", defaults.permutations),
            seed=document.get("seed", defaults.seed),
            n_min=document.get("n_min", defaults.n_min),
            effect_min=document.get("effect_min", defaults.effect_min),
            unknown_fraction=document.get("unknown_fraction", defaults.unknown_fraction),
            top_k=document.get("top_k", defaults.top_k),
            base_rate=document.get("base_rate", defaults.base_rate),
            prior_weight=document.get("prior_weight", defaults.prior_weight),
            cusum=document.get("cusum", defaults.cusum),
            page_hinkley=document.get("page_hinkley", defaults.page_hinkley),
            eprocess=document.get("eprocess", defaults.eprocess),
        )


def monitor_label(panel_digest: str, monitor_id: str) -> str:
    if not isinstance(monitor_id, str) or not monitor_id or "/" in monitor_id:
        raise VerdictError("monitor_id must be a non-empty string without '/'")
    return f"{MONITOR_LABEL_PREFIX}{panel_digest[:16]}/{monitor_id}"


@dataclass(frozen=True)
class Monitor:
    """A named comparison stream: one panel, one pooled baseline, one configuration."""

    monitor_id: str
    panel_digest: str
    baseline_window_ids: tuple[str, ...]
    config: AnalysisConfig = field(default_factory=AnalysisConfig)

    def __post_init__(self) -> None:
        monitor_label(self.panel_digest, self.monitor_id)
        if not self.baseline_window_ids or len(set(self.baseline_window_ids)) != len(
            self.baseline_window_ids
        ):
            raise VerdictError("baseline_window_ids must be a non-empty tuple of distinct ids")
        object.__setattr__(self, "baseline_window_ids", tuple(self.baseline_window_ids))

    @property
    def label(self) -> str:
        return monitor_label(self.panel_digest, self.monitor_id)

    @property
    def root_id(self) -> str:
        return Node.make(kind=NodeKind.ROOT, parent=None, payload={"run": self.label}).id

    @property
    def baseline_roots(self) -> tuple[str, ...]:
        return tuple(
            find_window_root(self.panel_digest, window_id) for window_id in self.baseline_window_ids
        )

    def header_payload(self) -> dict[str, IdentityValue]:
        return {
            "format": MONITOR_FORMAT,
            "panel_digest": self.panel_digest,
            "monitor_id": self.monitor_id,
            "baseline_window_ids": list(self.baseline_window_ids),
            "baseline_roots": list(self.baseline_roots),
            "config": self.config.to_document(),
            "config_digest": self.config.digest,
        }


@dataclass(frozen=True)
class Verdict:
    """One computed verdict; ``payload`` is the value-free identity document."""

    monitor: Monitor
    current_window_id: str
    sequence_index: int
    state: str
    rule_fired: str
    opinion: Opinion
    payload: dict[str, Any]
    node_id: str | None = None


@dataclass
class _ProbeTest:
    probe_id: str
    n_b: int
    n_c: int
    method: str
    p: float | None
    effect: float | None
    shift: float | None
    p_bh: float | None = None


@dataclass
class _ScorerAnalysis:
    name: str
    kind: str
    probes: list[_ProbeTest]
    pooled_T: float | None = None
    pooled_p: float | None = None
    pooled_p_holm: float | None = None
    pooled_probes: int = 0
    fraction_effect: float | None = None
    deltas: tuple[float, ...] = ()
    below_n_min: list[str] = field(default_factory=list)
    cusum_state: Any = None
    ph_state: Any = None
    e_state: Any = None
    e_mu0: float | None = None
    e_ties: int = 0
    e_baseline_n: int = 0


# --- chain reading -------------------------------------------------------------


def read_chain(store: Store, monitor: Monitor) -> tuple[str | None, list[Node]]:
    """The monitor header id (if recorded) and its verdict notes in chain order."""

    root_id = monitor.root_id
    if not store.exists(root_id):
        return None, []
    headers = [
        node
        for node in (store.get(child) for child in store.children(root_id))
        if node.payload.get("format") == MONITOR_FORMAT
    ]
    if not headers:
        return None, []
    if len(headers) > 1:
        raise VerdictError(
            f"monitor {monitor.monitor_id!r} has {len(headers)} headers; a monitor's "
            "configuration cannot change, use a new monitor_id"
        )
    header = headers[0]
    if header.payload.get("config_digest") != monitor.config.digest:
        raise VerdictError(
            f"monitor {monitor.monitor_id!r} was recorded with a different configuration; "
            "use a new monitor_id"
        )
    chain: list[Node] = []
    cursor = header
    while True:
        verdicts = [
            node
            for node in (store.get(child) for child in store.children(cursor.id))
            if node.payload.get("format") == VERDICT_FORMAT
        ]
        if not verdicts:
            return header.id, chain
        if len(verdicts) > 1:
            raise VerdictError(f"monitor chain branches at {cursor.id}")
        cursor = verdicts[0]
        chain.append(cursor)


# --- analysis ------------------------------------------------------------------


def compute_verdict(
    store: Store,
    monitor: Monitor,
    current_window_id: str,
    *,
    extra_scorers: Mapping[str, Scorer] | None = None,
    chain: Sequence[Node] | None = None,
) -> Verdict:
    """Compare ``current_window_id`` with the monitor's baseline without writing anything.

    ``chain`` is the verdict history the sequential detectors run over; it
    defaults to the monitor's recorded chain, and ``recompute_verdict``
    passes the prefix that preceded a recorded verdict.
    """

    config = monitor.config
    scorers = resolve_scorers(config.scorers, extra_scorers)
    if chain is None:
        _, chain = read_chain(store, monitor)
    sequence_index = len(chain) + 1

    baseline_views = _baseline_views(store, monitor)
    current_header_ids = find_window_headers(store, monitor.panel_digest, current_window_id)
    current_view = read_window(store, current_header_ids[-1]) if current_header_ids else None

    if not baseline_views or current_view is None:
        reason = "baseline window missing" if not baseline_views else "current window missing"
        return _unknown_verdict(monitor, current_window_id, sequence_index, reason, current_view)

    references = build_references(baseline_views, scorers)
    baseline_table = _pooled_table(baseline_views, scorers, references)
    current_table = score_window(current_view, scorers, references)

    probe_ids = list(current_table.probe_ids)
    current_counts = {p.probe_id: len(p.observations) for p in current_view.probes}
    baseline_counts = _baseline_counts(baseline_views)
    refused = sum(len(p.refusal_ids) for p in current_view.probes)
    below = [pid for pid in probe_ids if current_counts.get(pid, 0) < config.n_min]
    unknown_reason: str | None = None
    if probe_ids and len(below) / len(probe_ids) > config.unknown_fraction_value:
        unknown_reason = "too many probes below n_min"
    if not any(
        current_table.series(name, pid) for name in current_table.scorer_names for pid in probe_ids
    ):
        unknown_reason = "no scored observations"

    analyses = [
        _analyze_scorer(scorer, baseline_table, current_table, probe_ids, config)
        for scorer in scorers
    ]
    _apply_multiplicity(analyses, config)

    history_views = _history_views(store, monitor, chain)
    for analysis, scorer in zip(analyses, scorers, strict=True):
        _sequential(
            analysis, scorer, baseline_table, references, history_views, current_view, config
        )

    if unknown_reason is not None:
        state, rule = "unknown", unknown_reason
    else:
        state, rule = _state(analyses, config)

    contract_changed, contract_paths = _contract_change(baseline_views, current_view)
    opinion = _opinion(analyses, config, state)
    payload = _payload(
        monitor,
        current_view,
        baseline_views,
        sequence_index,
        state,
        rule,
        contract_changed,
        contract_paths,
        baseline_counts,
        current_counts,
        refused,
        below,
        analyses,
        opinion,
        config,
    )
    return Verdict(
        monitor=monitor,
        current_window_id=current_window_id,
        sequence_index=sequence_index,
        state=state,
        rule_fired=rule,
        opinion=opinion,
        payload=payload,
    )


def record_verdict(store: Store, verdict: Verdict) -> Verdict:
    """Append the verdict note to the monitor chain and update the ledger index."""

    monitor = verdict.monitor
    runtime = Runtime(store, mode=ReplayMode.RECORD)
    with runtime.run(monitor.label) as run:
        header_id, chain = read_chain(store, monitor)
        if header_id is None:
            header = run.note(monitor.header_payload())
            header_id = header.id
        run.cursor_id = chain[-1].id if chain else header_id
        expected_index = len(chain) + 1
        if verdict.sequence_index != expected_index:
            raise VerdictError(
                f"verdict has sequence index {verdict.sequence_index} but the chain expects "
                f"{expected_index}; recompute before recording"
            )
        node = run.note(verdict.payload)
    store.update_meta(
        monitor.root_id,
        {
            "epicormic": {
                "latest_verdict": node.id,
                "state": verdict.state,
                "window_id": verdict.current_window_id,
                "sequence_index": verdict.sequence_index,
            }
        },
    )
    return Verdict(
        monitor=monitor,
        current_window_id=verdict.current_window_id,
        sequence_index=verdict.sequence_index,
        state=verdict.state,
        rule_fired=verdict.rule_fired,
        opinion=verdict.opinion,
        payload=verdict.payload,
        node_id=node.id,
    )


def recompute_verdict(
    store: Store,
    monitor: Monitor,
    node_id: str,
    *,
    extra_scorers: Mapping[str, Scorer] | None = None,
) -> tuple[Verdict, bool]:
    """Recompute a recorded verdict from the store and report whether it matches.

    The recomputation runs over the chain prefix that preceded the verdict,
    so the sequential detectors see exactly the history they saw when the
    verdict was written; a mismatch means the observations, the code, or
    the configuration changed.
    """

    _, chain = read_chain(store, monitor)
    for index, node in enumerate(chain):
        if node.id == node_id:
            recorded = node.payload
            window_id = str(recorded["current_window_id"])
            verdict = compute_verdict(
                store, monitor, window_id, extra_scorers=extra_scorers, chain=chain[:index]
            )
            return verdict, verdict.payload == recorded
    raise VerdictError(f"{node_id} is not a verdict of monitor {monitor.monitor_id!r}")


def analyze(
    store: Store,
    monitor: Monitor,
    current_window_id: str,
    *,
    extra_scorers: Mapping[str, Scorer] | None = None,
    write: bool = True,
) -> Verdict:
    """Compute a verdict and, unless ``write`` is false, record it."""

    verdict = compute_verdict(store, monitor, current_window_id, extra_scorers=extra_scorers)
    return record_verdict(store, verdict) if write else verdict


# --- helpers -------------------------------------------------------------------


def _baseline_views(store: Store, monitor: Monitor) -> list[WindowView]:
    views: list[WindowView] = []
    for window_id in monitor.baseline_window_ids:
        for header_id in find_window_headers(store, monitor.panel_digest, window_id):
            views.append(read_window(store, header_id))
    return views


def _history_views(store: Store, monitor: Monitor, chain: Sequence[Node]) -> list[WindowView]:
    views: list[WindowView] = []
    for node in chain:
        header_id = node.payload.get("current_header_id")
        if isinstance(header_id, str) and store.exists(header_id):
            views.append(read_window(store, header_id))
    return views


def _baseline_counts(views: Sequence[WindowView]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for view in views:
        for probe in view.probes:
            counts[probe.probe_id] = counts.get(probe.probe_id, 0) + len(probe.observations)
    return counts


def _pooled_table(
    views: Sequence[WindowView], scorers: Sequence[Scorer], references: Mapping[str, Reference]
) -> ScoreTable:
    tables = [score_window(view, scorers, references) for view in views]
    values: dict[str, dict[str, tuple[float | None, ...]]] = {s.name: {} for s in scorers}
    probe_ids: list[str] = []
    for table in tables:
        for probe_id in table.probe_ids:
            if probe_id not in probe_ids:
                probe_ids.append(probe_id)
            for name in table.scorer_names:
                merged = values[name].get(probe_id, ())
                values[name][probe_id] = (*merged, *table.values[name][probe_id])
    return ScoreTable(
        window_id="+".join(view.window_id for view in views),
        scorer_names=tuple(s.name for s in scorers),
        probe_ids=tuple(probe_ids),
        values=values,
    )


def _analyze_scorer(
    scorer: Scorer,
    baseline: ScoreTable,
    current: ScoreTable,
    probe_ids: Sequence[str],
    config: AnalysisConfig,
) -> _ScorerAnalysis:
    analysis = _ScorerAnalysis(name=scorer.name, kind=scorer.kind, probes=[])
    strata: list[tuple[tuple[float, ...], tuple[float, ...]]] = []
    for probe_id in probe_ids:
        b = (
            baseline.series(scorer.name, probe_id)
            if probe_id in baseline.values[scorer.name]
            else ()
        )
        c = current.series(scorer.name, probe_id)
        if len(b) < config.n_min or len(c) < config.n_min:
            analysis.below_n_min.append(probe_id)
            analysis.probes.append(
                _ProbeTest(probe_id, len(b), len(c), "insufficient", None, None, None)
            )
            continue
        strata.append((b, c))
        if scorer.kind == "indicator":
            fisher = indicator_test(b, c)
            analysis.probes.append(
                _ProbeTest(
                    probe_id,
                    len(b),
                    len(c),
                    "fisher_exact",
                    fisher.p_value,
                    fisher.risk_difference,
                    None,
                )
            )
        else:
            rank = mann_whitney(b, c)
            analysis.probes.append(
                _ProbeTest(
                    probe_id,
                    len(b),
                    len(c),
                    rank.method,
                    rank.p_value,
                    rank.cliffs_delta,
                    rank.hodges_lehmann,
                )
            )
    if strata:
        pooled = stratified_permutation_test(
            strata, permutations=config.permutations, seed=config.seed
        )
        analysis.pooled_T = pooled.statistic
        analysis.pooled_p = pooled.p_value
        analysis.pooled_probes = pooled.strata
        analysis.deltas = pooled.deltas
        analysis.fraction_effect = sum(
            1 for d in pooled.deltas if abs(d) >= config.effect_min_value
        ) / len(pooled.deltas)
    return analysis


def _apply_multiplicity(analyses: Sequence[_ScorerAnalysis], config: AnalysisConfig) -> None:
    tests = [
        (analysis, index)
        for analysis in analyses
        for index, probe in enumerate(analysis.probes)
        if probe.p is not None
    ]
    adjusted = benjamini_hochberg([analysis.probes[index].p or 0.0 for analysis, index in tests])
    for (analysis, index), p_bh in zip(tests, adjusted, strict=True):
        analysis.probes[index].p_bh = p_bh
    pooled = [analysis for analysis in analyses if analysis.pooled_p is not None]
    for analysis, p_holm in zip(pooled, holm([a.pooled_p or 0.0 for a in pooled]), strict=True):
        analysis.pooled_p_holm = p_holm


def _sequential(
    analysis: _ScorerAnalysis,
    scorer: Scorer,
    baseline: ScoreTable,
    references: Mapping[str, Reference],
    history: Sequence[WindowView],
    current: WindowView,
    config: AnalysisConfig,
) -> None:
    k, h = config.cusum_values
    delta, lam = config.page_hinkley_values
    e_alpha, grid = config.eprocess_values
    history_tables = [score_window(view, (scorer,), references) for view in history]
    window_values: list[float] = []
    for table in history_tables:
        strata = [
            (baseline.series(scorer.name, pid), table.series(scorer.name, pid))
            for pid in table.probe_ids
            if pid in baseline.values[scorer.name]
            and len(baseline.series(scorer.name, pid)) >= config.n_min
            and len(table.series(scorer.name, pid)) >= config.n_min
        ]
        if strata:
            window_values.append(
                stratified_permutation_test(strata, permutations=1, seed=config.seed).statistic
            )
    if analysis.pooled_T is not None:
        window_values.append(analysis.pooled_T)
    analysis.cusum_state = cusum(window_values, k=k, h=h)
    analysis.ph_state = page_hinkley(window_values, delta=delta, threshold=lam)

    baseline_values = [
        value for pid in baseline.probe_ids for value in baseline.series(scorer.name, pid)
    ]
    if not baseline_values:
        return
    stream: list[int] = []
    ties = 0
    current_table = score_window(current, (scorer,), references)
    if scorer.kind == "indicator":
        mu0 = clipped_rate(int(sum(baseline_values)), len(baseline_values))
        for table in (*history_tables, current_table):
            for pid in table.probe_ids:
                stream.extend(int(v) for v in table.series(scorer.name, pid))
    else:
        medians = {
            pid: float(statistics.median(baseline.series(scorer.name, pid)))
            for pid in baseline.probe_ids
            if baseline.series(scorer.name, pid)
        }
        # null rate of the sign stream: the baseline's own above-median rate
        # among non-ties, per probe median, pooled; one half only when the
        # score distribution has no mass at the median (D21)
        above = 0
        non_ties = 0
        for pid, median in medians.items():
            signs = sign_transform(baseline.series(scorer.name, pid), median)
            above += sum(signs.values)
            non_ties += len(signs.values)
        if non_ties == 0:
            return
        mu0 = clipped_rate(above, non_ties)
        for table in (*history_tables, current_table):
            for pid in table.probe_ids:
                if pid not in medians:
                    continue
                signs = sign_transform(table.series(scorer.name, pid), medians[pid])
                stream.extend(signs.values)
                ties += signs.ties_excluded
    analysis.e_state = betting_eprocess(stream, mu0=mu0, alpha=e_alpha, grid=grid)
    analysis.e_mu0 = mu0
    analysis.e_ties = ties
    analysis.e_baseline_n = len(baseline_values)


def _state(analyses: Sequence[_ScorerAnalysis], config: AnalysisConfig) -> tuple[str, str]:
    alpha = config.alpha_value
    q = config.q_value
    effect_min = config.effect_min_value
    for analysis in analyses:
        if analysis.pooled_p_holm is not None and analysis.pooled_p_holm <= alpha:
            return "drift", f"pooled test rejected for {analysis.name}"
    for analysis in analyses:
        if analysis.e_state is not None and analysis.e_state.alarm:
            return "drift", f"e-process alarm for {analysis.name}"
        if analysis.cusum_state is not None and analysis.cusum_state.alarm:
            return "drift", f"cusum alarm for {analysis.name}"
        if analysis.ph_state is not None and analysis.ph_state.alarm:
            return "drift", f"page-hinkley alarm for {analysis.name}"
    for analysis in analyses:
        for probe in analysis.probes:
            rejected = probe.p_bh is not None and probe.p_bh <= q
            if rejected and probe.effect is not None and abs(probe.effect) >= effect_min:
                return "warning", f"per-probe test rejected for {analysis.name}"
    for analysis in analyses:
        cs = analysis.cusum_state
        if cs is not None and max(cs.s_plus, cs.s_minus) >= cs.h / 2:
            return "warning", f"cusum at half threshold for {analysis.name}"
        ph = analysis.ph_state
        if ph is not None and ph.excursion >= ph.threshold / 2:
            return "warning", f"page-hinkley at half threshold for {analysis.name}"
        es = analysis.e_state
        if es is not None and es.e_value >= es.threshold / 2:
            return "warning", f"e-process at half threshold for {analysis.name}"
    return "stable", "no rule fired"


def _opinion(analyses: Sequence[_ScorerAnalysis], config: AnalysisConfig, state: str) -> Opinion:
    """Average the e-values of the scorers whose streams carried observations."""

    base_rate = config.base_rate_value
    states = [a.e_state for a in analyses if a.e_state is not None and a.e_state.observations > 0]
    if state == "unknown" or not states:
        return vacuous(base_rate)
    logs = [s.log_e_value for s in states]
    peak = max(logs)  # always finite: the e-process keeps its log wealth finite
    log_mean = peak + math.log(sum(math.exp(v - peak) for v in logs) / len(logs))
    observations = min(s.observations for s in states)
    return opinion_from_log_evidence(
        log_mean, observations, base_rate=base_rate, prior_weight=config.prior_weight_value
    )


def _contract_change(
    baseline_views: Sequence[WindowView], current: WindowView
) -> tuple[bool, list[str]]:
    current_digest = _contract_digest(current.contract)
    baseline_digests = {_contract_digest(view.contract) for view in baseline_views}
    if current_digest in baseline_digests:
        return False, []
    paths: list[str] = []
    for view in baseline_views:
        for path in _difference_paths(view.contract, current.contract, ""):
            if path not in paths:
                paths.append(path)
    return True, paths


def _contract_digest(contract: Mapping[str, Any] | None) -> str:
    document: IdentityValue = dict(contract) if contract is not None else None
    return domain_digest(b"epicormic/contract/v1\n", document)


def _difference_paths(left: Any, right: Any, prefix: str) -> list[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        paths: list[str] = []
        for key in sorted(set(left) | set(right)):
            paths.extend(_difference_paths(left.get(key), right.get(key), f"{prefix}/{key}"))
        return paths
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        paths = []
        for index, (a, b) in enumerate(zip(left, right, strict=True)):
            paths.extend(_difference_paths(a, b, f"{prefix}/{index}"))
        return paths
    if left == right:
        return []
    return [prefix or "/"]


def _payload(
    monitor: Monitor,
    current: WindowView,
    baseline_views: Sequence[WindowView],
    sequence_index: int,
    state: str,
    rule: str,
    contract_changed: bool,
    contract_paths: Sequence[str],
    baseline_counts: Mapping[str, int],
    current_counts: Mapping[str, int],
    refused: int,
    below: Sequence[str],
    analyses: Sequence[_ScorerAnalysis],
    opinion: Opinion,
    config: AnalysisConfig,
) -> dict[str, Any]:
    scorers: dict[str, Any] = {}
    for analysis in analyses:
        entries: list[dict[str, Any]] = [
            {
                "probe_id": probe.probe_id,
                "n_b": probe.n_b,
                "n_c": probe.n_c,
                "method": probe.method,
                "effect": probe.effect,
                "shift": probe.shift,
                "p": probe.p,
                "p_bh": probe.p_bh,
                "rejected": bool(probe.p_bh is not None and probe.p_bh <= config.q_value),
            }
            for probe in analysis.probes
        ]
        entries.sort(key=_entry_order)
        kept = [e for i, e in enumerate(entries) if i < config.top_k or e["rejected"]]
        p_holm = analysis.pooled_p_holm
        cusum_state = analysis.cusum_state
        ph = analysis.ph_state
        e_state = analysis.e_state
        scorers[analysis.name] = {
            "kind": analysis.kind,
            "pooled": {
                "T": analysis.pooled_T,
                "p": analysis.pooled_p,
                "p_holm": p_holm,
                "permutations": config.permutations,
                "seed": config.seed,
                "probes": analysis.pooled_probes,
                "fraction_effect_ge_min": analysis.fraction_effect,
                "rejected": bool(p_holm is not None and p_holm <= config.alpha_value),
            },
            "per_probe": kept,
            "per_probe_truncated": len(kept) < len(entries),
            "probes_below_n_min": list(analysis.below_n_min),
            "sequential": {
                "cusum": None
                if cusum_state is None
                else {
                    "s_plus": cusum_state.s_plus,
                    "s_minus": cusum_state.s_minus,
                    "k": cusum_state.k,
                    "h": cusum_state.h,
                    "windows": cusum_state.steps,
                    "alarm": cusum_state.alarm,
                },
                "page_hinkley": None
                if ph is None
                else {
                    "excursion": ph.excursion,
                    "delta": ph.delta,
                    "lambda": ph.threshold,
                    "windows": ph.steps,
                    "alarm": ph.alarm,
                },
                "eprocess": None
                if e_state is None
                else {
                    "e": e_state.e_value,
                    "log_e": e_state.log_e_value,
                    "max_e": e_state.max_e_value,
                    "threshold": e_state.threshold,
                    "observations": e_state.observations,
                    "mu0": analysis.e_mu0,
                    "baseline_observations": analysis.e_baseline_n,
                    "ties_excluded": analysis.e_ties,
                    "alarm": e_state.alarm,
                    "alarm_index": e_state.alarm_index,
                },
            },
        }
    e_states = [a.e_state for a in analyses if a.e_state is not None and a.e_state.observations > 0]
    document = {
        "format": VERDICT_FORMAT,
        "event": "epicormic_verdict",
        "monitor_id": monitor.monitor_id,
        "panel_digest": monitor.panel_digest,
        "config_digest": config.digest,
        "epicormic_version": __version__,
        "baseline_roots": list(monitor.baseline_roots),
        "baseline_header_ids": [view.header_id for view in baseline_views],
        "current_root": current.root_id,
        "current_header_id": current.header_id,
        "current_window_id": current.window_id,
        "sequence_index": sequence_index,
        "state": state,
        "rule_fired": rule,
        "contract_changed": contract_changed,
        "contract_difference_paths": list(contract_paths),
        "sample_counts": {
            "baseline": sum(baseline_counts.values()),
            "current": sum(current_counts.values()),
            "current_refused": refused,
            "probes_below_n_min": list(below),
        },
        "scorers": scorers,
        "opinion": {
            "format": "epicormic/opinion/v1",
            "source": "eprocess-average/v1",
            "observations": min((s.observations for s in e_states), default=0),
            "prior_weight": config.prior_weight,
            "base_rate": config.base_rate,
            "belief": opinion.belief,
            "disbelief": opinion.disbelief,
            "uncertainty": opinion.uncertainty,
            "projected_probability": opinion.projected_probability,
        },
    }
    encoded, non_finite = encode_evidence(document)
    encoded["non_finite_fields"] = non_finite
    canonical_bytes(encoded)
    return dict(encoded)


def _entry_order(entry: dict[str, Any]) -> tuple[float, str]:
    effect = entry["effect"]
    return (-(abs(effect)) if effect is not None else 1.0, str(entry["probe_id"]))


def _unknown_verdict(
    monitor: Monitor,
    current_window_id: str,
    sequence_index: int,
    reason: str,
    current: WindowView | None,
) -> Verdict:
    config = monitor.config
    opinion = vacuous(config.base_rate_value)
    document = {
        "format": VERDICT_FORMAT,
        "event": "epicormic_verdict",
        "monitor_id": monitor.monitor_id,
        "panel_digest": monitor.panel_digest,
        "config_digest": config.digest,
        "epicormic_version": __version__,
        "baseline_roots": list(monitor.baseline_roots),
        "baseline_header_ids": [],
        "current_root": current.root_id
        if current
        else find_window_root(monitor.panel_digest, current_window_id),
        "current_header_id": current.header_id if current else None,
        "current_window_id": current_window_id,
        "sequence_index": sequence_index,
        "state": "unknown",
        "rule_fired": reason,
        "contract_changed": False,
        "contract_difference_paths": [],
        "sample_counts": {
            "baseline": 0,
            "current": 0,
            "current_refused": 0,
            "probes_below_n_min": [],
        },
        "scorers": {},
        "opinion": {
            "format": "epicormic/opinion/v1",
            "source": "eprocess-average/v1",
            "observations": 0,
            "prior_weight": config.prior_weight,
            "base_rate": config.base_rate,
            "belief": 0.0,
            "disbelief": 0.0,
            "uncertainty": 1.0,
            "projected_probability": opinion.projected_probability,
        },
        "non_finite_fields": [],
    }
    encoded, _ = encode_evidence(document)
    return Verdict(
        monitor=monitor,
        current_window_id=current_window_id,
        sequence_index=sequence_index,
        state="unknown",
        rule_fired=reason,
        opinion=opinion,
        payload=dict(encoded),
    )
