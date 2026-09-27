"""Command line interface (docs/PLAN.md, section 12).

Every command works on a SQLite store path; remote stores are used through
the Python API. The CLI never constructs provider clients: ``observe`` and
``calibrate`` take ``--fn module:attribute``, which must resolve to a
pollard step callable ``(request) -> result``. Output is value-free: it
names states, ids, digests, counts, and statistics, never a result.

Exit codes: 0 stable, 2 warning, 3 drift, 4 unknown, 1 error (or an
incomplete observation), so CI can gate on the verdict.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pollard import Budget, ReplayContract, SQLiteStore
from pollard.revalidation import REPLAY_CONTRACT_FORMAT

from . import __version__
from .ledger import Ledger, LedgerEntry, LedgerError
from .panel import Panel, PanelError, load_panel
from .verdict import (
    AnalysisConfig,
    Monitor,
    Verdict,
    VerdictError,
    analyze,
    read_chain,
    recompute_verdict,
)
from .window import WindowError, WindowReport, observe

__all__ = ["EXIT_CODES", "main", "resolve_callable"]

EXIT_CODES = {"stable": 0, "warning": 2, "drift": 3, "unknown": 4}
EXIT_ERROR = 1


class CliError(Exception):
    """A user-facing error; printed to stderr with exit code 1."""


# --- helpers ---------------------------------------------------------------------


def resolve_callable(spec: str) -> Callable[[dict[str, Any]], Any]:
    """Import ``module:attribute`` and return it; the attribute may be dotted."""

    if ":" not in spec:
        raise CliError(f"--fn must be module:attribute, got {spec!r}")
    module_name, _, attribute = spec.partition(":")
    if not module_name or not attribute:
        raise CliError(f"--fn must be module:attribute, got {spec!r}")
    try:
        target: Any = importlib.import_module(module_name)
    except ImportError as error:
        raise CliError(f"cannot import {module_name!r}: {error}") from error
    for part in attribute.split("."):
        try:
            target = getattr(target, part)
        except AttributeError as error:
            raise CliError(f"{module_name!r} has no attribute {attribute!r}") from error
    if not callable(target):
        raise CliError(f"{spec!r} is not callable")
    return target  # type: ignore[no-any-return]


def _load_json(path: str, what: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CliError(f"{what} file not found: {path}") from error
    except json.JSONDecodeError as error:
        raise CliError(f"{what} file is not valid JSON: {path} ({error})") from error


def _load_panel(path: str) -> Panel:
    try:
        return load_panel(path)
    except FileNotFoundError as error:
        raise CliError(f"panel file not found: {path}") from error
    except PanelError as error:
        raise CliError(f"invalid panel: {error}") from error


def _load_contract(path: str | None) -> ReplayContract | None:
    if path is None:
        return None
    document = _load_json(path, "contract")
    if not isinstance(document, dict) or document.get("format") != REPLAY_CONTRACT_FORMAT:
        raise CliError(f"contract format must be {REPLAY_CONTRACT_FORMAT!r}")
    fields = {
        "provider",
        "model_revision",
        "api_version",
        "adapter",
        "adapter_version",
        "sdk",
        "sdk_version",
        "application_revision",
        "environment",
    }
    unknown = set(document) - fields - {"format"}
    if unknown:
        raise CliError(f"contract has unknown keys: {sorted(unknown)}")
    try:
        return ReplayContract(**{key: value for key, value in document.items() if key in fields})
    except (TypeError, ValueError) as error:
        raise CliError(f"invalid contract: {error}") from error


def _load_config(path: str | None) -> AnalysisConfig:
    if path is None:
        return AnalysisConfig()
    try:
        return AnalysisConfig.from_document(_load_json(path, "config"))
    except VerdictError as error:
        raise CliError(f"invalid config: {error}") from error


def _budget(args: argparse.Namespace) -> Budget | None:
    if args.budget_tokens is None and args.budget_usd is None and args.budget_steps is None:
        return None
    return Budget(tokens=args.budget_tokens, usd=args.budget_usd, steps=args.budget_steps)


def _print_report(report: WindowReport, out: Any) -> None:
    print(f"window {report.window_id} panel {report.panel_digest[:16]}", file=out)
    print(f"root {report.root_id}", file=out)
    print(f"header {report.header_id}", file=out)
    print(
        f"recorded {report.recorded} dispatched {report.dispatched} served {report.served} "
        f"refused {report.refused} not_attempted {report.not_attempted} "
        f"complete {'yes' if report.complete else 'no'}",
        file=out,
    )


def _print_verdict(verdict: Verdict, out: Any) -> None:
    opinion = verdict.payload["opinion"]
    print(
        f"{verdict.state} ({verdict.rule_fired}) window {verdict.current_window_id} "
        f"sequence {verdict.sequence_index}",
        file=out,
    )
    if verdict.node_id is not None:
        print(f"verdict {verdict.node_id}", file=out)
    print(
        f"opinion belief {opinion['belief']} disbelief {opinion['disbelief']} "
        f"uncertainty {opinion['uncertainty']} observations {opinion['observations']}",
        file=out,
    )
    if verdict.payload.get("contract_changed"):
        paths = ",".join(verdict.payload.get("contract_difference_paths", []))
        print(f"contract changed at {paths}", file=out)


def _print_entry(entry: LedgerEntry, out: Any) -> None:
    print(
        f"{entry.state} monitor {entry.monitor_id} window {entry.window_id} "
        f"sequence {entry.sequence_index} verdict {entry.verdict_node_id}",
        file=out,
    )


def _exit_for(state: str) -> int:
    return EXIT_CODES.get(state, EXIT_ERROR)


# --- commands --------------------------------------------------------------------


def cmd_panel(args: argparse.Namespace, out: Any) -> int:
    panel = _load_panel(args.panel)
    print(panel.digest, file=out)
    return 0


def cmd_observe(args: argparse.Namespace, out: Any) -> int:
    panel = _load_panel(args.panel)
    fn = resolve_callable(args.fn)
    contract = _load_contract(args.contract)
    try:
        with SQLiteStore(Path(args.store)) as store:
            report = observe(
                panel,
                window_id=args.window,
                samples=args.samples,
                fn=fn,
                store=store,
                contract=contract,
                budget=_budget(args),
            )
    except WindowError as error:
        raise CliError(str(error)) from error
    _print_report(report, out)
    return 0 if report.complete else EXIT_ERROR


def cmd_verdict(args: argparse.Namespace, out: Any) -> int:
    panel = _load_panel(args.panel)
    config = _load_config(args.config)
    baselines = tuple(item for item in args.baseline.split(",") if item)
    try:
        monitor = Monitor(args.monitor, panel.digest, baselines, config)
        with SQLiteStore(Path(args.store)) as store:
            if args.recompute:
                _, chain = read_chain(store, monitor)
                if not chain:
                    raise CliError(f"monitor {args.monitor!r} has no verdict to recompute")
                verdict, matches = recompute_verdict(store, monitor, chain[-1].id)
                _print_verdict(verdict, out)
                print(
                    f"recompute {'matches' if matches else 'DIFFERS FROM'} {chain[-1].id}", file=out
                )
                return 0 if matches else EXIT_ERROR
            verdict = analyze(store, monitor, args.current)
    except VerdictError as error:
        raise CliError(str(error)) from error
    _print_verdict(verdict, out)
    return _exit_for(verdict.state)


def cmd_calibrate(args: argparse.Namespace, out: Any) -> int:
    panel = _load_panel(args.panel)
    fn = resolve_callable(args.fn)
    config = _load_config(args.config)
    if args.windows < 2:
        raise CliError("--windows must be at least 2")
    window_ids = [f"{args.prefix}-{index}" for index in range(1, args.windows + 1)]
    worst = 0
    try:
        with SQLiteStore(Path(args.store)) as store:
            for window_id in window_ids:
                report = observe(
                    panel, window_id=window_id, samples=args.samples, fn=fn, store=store
                )
                _print_report(report, out)
            monitor = Monitor(args.monitor, panel.digest, (window_ids[0],), config)
            for window_id in window_ids[1:]:
                verdict = analyze(store, monitor, window_id)
                _print_verdict(verdict, out)
                worst = max(worst, _exit_for(verdict.state))
    except (WindowError, VerdictError) as error:
        raise CliError(str(error)) from error
    print(f"calibration {'clean' if worst == 0 else 'NOT clean'}", file=out)
    return worst


def cmd_status(args: argparse.Namespace, out: Any) -> int:
    panel_digest = _load_panel(args.panel).digest if args.panel else None
    try:
        with SQLiteStore(Path(args.store)) as store:
            entry = Ledger(store).state(args.monitor, panel_digest)
    except LedgerError as error:
        raise CliError(str(error)) from error
    if entry is None:
        print(f"unknown monitor {args.monitor} has no verdict", file=out)
        return EXIT_CODES["unknown"]
    _print_entry(entry, out)
    return _exit_for(entry.state)


def cmd_report(args: argparse.Namespace, out: Any) -> int:
    panel_digest = _load_panel(args.panel).digest if args.panel else None
    if args.prov_o and args.format != "jsonld":
        raise CliError("--prov-o requires --format jsonld")
    try:
        with SQLiteStore(Path(args.store)) as store:
            ledger = Ledger(store)
            history = ledger.history(args.monitor, panel_digest)
            if not history:
                raise CliError(f"monitor {args.monitor!r} has no verdict")
            latest = store.get(history[-1].verdict_node_id)
            created_at = latest.meta.get("created_at")
            document: dict[str, Any] = {
                "format": "epicormic/report/v1",
                "monitor_id": args.monitor,
                "latest": dict(latest.payload),
                "latest_created_at": created_at,
                "history": [
                    {
                        "sequence_index": entry.sequence_index,
                        "window_id": entry.window_id,
                        "state": entry.state,
                        "verdict_node_id": entry.verdict_node_id,
                        "computed_at": entry.computed_at,
                    }
                    for entry in history
                ],
            }
    except LedgerError as error:
        raise CliError(str(error)) from error
    if args.format == "jsonld":
        document = _jsonld_report(document, latest.id, created_at, prov_o=args.prov_o)
    Path(args.out).write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {args.out}", file=out)
    _print_entry(history[-1], out)
    return _exit_for(history[-1].state)


def _jsonld_report(
    document: dict[str, Any], node_id: str, created_at: Any, *, prov_o: bool
) -> dict[str, Any]:
    try:
        from . import jsonld
    except ImportError as error:
        raise CliError(str(error)) from error
    verdict = jsonld.verdict_to_jsonld(
        document["latest"],
        node_id=node_id,
        created_at=str(created_at) if created_at is not None else None,
    )
    problems = jsonld.validate_verdict_jsonld(verdict)
    if problems:  # pragma: no cover - the exporter and the shape agree by construction
        raise CliError(f"verdict document failed shape validation: {problems}")
    if prov_o:
        return jsonld.verdict_to_prov_o(verdict)
    return {
        "@context": dict(jsonld.EPICORMIC_CONTEXT),
        "@type": "Report",
        "format": "epicormic/report/v1",
        "monitor_id": document["monitor_id"],
        "latest": verdict,
        "history": document["history"],
        "context_integrity": jsonld.context_integrity(),
    }


# --- parser -----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="epicormic",
        description="Provider drift detection and containment for pollard-governed AI systems.",
    )
    parser.add_argument("--version", action="version", version=f"epicormic {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    panel = commands.add_parser("panel", help="panel utilities")
    panel_commands = panel.add_subparsers(dest="panel_command", required=True)
    digest = panel_commands.add_parser("digest", help="print a panel's digest")
    digest.add_argument("panel")
    digest.set_defaults(func=cmd_panel)

    obs = commands.add_parser("observe", help="record one window of a panel")
    obs.add_argument("--panel", required=True)
    obs.add_argument("--store", required=True, help="SQLite store path")
    obs.add_argument("--window", required=True, help="window id")
    obs.add_argument("--samples", required=True, type=int)
    obs.add_argument("--fn", required=True, help="module:attribute of the step callable")
    obs.add_argument("--contract", help="replay contract JSON document")
    obs.add_argument("--budget-tokens", type=int)
    obs.add_argument("--budget-usd", help="USD budget as a decimal string")
    obs.add_argument("--budget-steps", type=int)
    obs.set_defaults(func=cmd_observe)

    ver = commands.add_parser("verdict", help="compare a window with the baseline")
    ver.add_argument("--store", required=True)
    ver.add_argument("--monitor", required=True)
    ver.add_argument("--panel", required=True)
    ver.add_argument("--baseline", required=True, help="baseline window ids, comma separated")
    ver.add_argument("--current", help="current window id (not needed with --recompute)")
    ver.add_argument("--config", help="analysis config JSON document")
    ver.add_argument("--recompute", action="store_true", help="recompute the latest verdict")
    ver.set_defaults(func=cmd_verdict)

    cal = commands.add_parser("calibrate", help="A/A calibration: several windows, one provider")
    cal.add_argument("--panel", required=True)
    cal.add_argument("--store", required=True)
    cal.add_argument("--fn", required=True)
    cal.add_argument("--samples", required=True, type=int)
    cal.add_argument("--windows", type=int, default=2)
    cal.add_argument("--prefix", default="aa")
    cal.add_argument("--monitor", default="calibrate")
    cal.add_argument("--config")
    cal.set_defaults(func=cmd_calibrate)

    stat = commands.add_parser("status", help="print a monitor's ledger state")
    stat.add_argument("--store", required=True)
    stat.add_argument("--monitor", required=True)
    stat.add_argument("--panel", help="panel file, needed when the monitor id is ambiguous")
    stat.set_defaults(func=cmd_status)

    rep = commands.add_parser("report", help="write the latest verdict and history to a file")
    rep.add_argument("--store", required=True)
    rep.add_argument("--monitor", required=True)
    rep.add_argument("--out", required=True)
    rep.add_argument("--panel")
    rep.add_argument("--format", choices=("json", "jsonld"), default="json")
    rep.add_argument("--prov-o", action="store_true", dest="prov_o")
    rep.set_defaults(func=cmd_report)
    return parser


def main(argv: Sequence[str] | None = None, *, out: Any = None, err: Any = None) -> int:
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "verdict" and not args.recompute and not args.current:
        parser.error("verdict requires --current unless --recompute is given")
    try:
        code = args.func(args, out)
    except CliError as error:
        print(f"epicormic: {error}", file=err)
        return EXIT_ERROR
    return int(code)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
