"""pytest plugin: gate a test suite on a monitor's drift state (docs/PLAN.md, section 12.1).

Registered under the ``pytest11`` entry point. It adds three options
(``--epicormic-store``, ``--epicormic-monitor``, ``--epicormic-fail-on``,
plus ``--epicormic-panel-digest`` for ambiguous monitor ids), a
session-scoped ``epicormic_state`` fixture that returns the monitor's
latest ``LedgerEntry`` (or ``None``), and an ``epicormic_gate`` marker:
every marked test fails in its call phase, before its body runs, when the
state is in the fail set. Unmarked tests are untouched, so the gate is
explicit and never fails a suite by surprise. epicormic's own suite
disables the plugin with ``-p no:epicormic``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from .ledger import Ledger, LedgerEntry
from .verdict import STATES

__all__ = ["epicormic_state", "pytest_addoption", "pytest_configure", "pytest_pyfunc_call"]

_OPTION_HELP = "epicormic gate: pass --epicormic-store and --epicormic-monitor"


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("epicormic", "epicormic drift gate")
    group.addoption("--epicormic-store", default=None, help="SQLite store holding the monitor")
    group.addoption("--epicormic-monitor", default=None, help="monitor id to gate on")
    group.addoption(
        "--epicormic-panel-digest", default=None, help="panel digest when the id is ambiguous"
    )
    group.addoption(
        "--epicormic-fail-on",
        default="drift",
        help="comma-separated states that fail gated tests (default: drift)",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "epicormic_gate: fail this test before its body when the monitored provider state "
        "is in --epicormic-fail-on (default: drift)",
    )


def _fail_states(config: pytest.Config) -> tuple[str, ...]:
    raw = str(config.getoption("--epicormic-fail-on") or "")
    states = tuple(item.strip() for item in raw.split(",") if item.strip())
    unknown = [state for state in states if state not in STATES]
    if unknown:
        raise pytest.UsageError(
            f"--epicormic-fail-on has unknown states {unknown}; choose from {STATES}"
        )
    return states


def _lookup(config: pytest.Config) -> LedgerEntry | None:
    store_path = config.getoption("--epicormic-store")
    monitor_id = config.getoption("--epicormic-monitor")
    if not store_path or not monitor_id:
        raise pytest.UsageError(_OPTION_HELP)
    from pollard import SQLiteStore

    panel_digest = config.getoption("--epicormic-panel-digest")
    with SQLiteStore(Path(str(store_path))) as store:
        return Ledger(store).state(str(monitor_id), panel_digest or None)


@pytest.fixture(scope="session")
def epicormic_state(request: pytest.FixtureRequest) -> LedgerEntry | None:
    """The monitor's latest ledger entry, read once per session."""

    return _lookup(request.config)


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    """Fail a gated test in its call phase, before the body runs."""

    if pyfuncitem.get_closest_marker("epicormic_gate") is None:
        return None
    config = pyfuncitem.config
    cache: dict[str, Any] = config.stash.setdefault(_STATE_KEY, {})
    if "entry" not in cache:
        cache["entry"] = _lookup(config)
    entry: LedgerEntry | None = cache["entry"]
    state = "unknown" if entry is None else entry.state
    if state not in _fail_states(config):
        return None
    detail = (
        f"monitor {config.getoption('--epicormic-monitor')} is in state {state!r}"
        if entry is None
        else (
            f"monitor {entry.monitor_id} is in state {state!r} "
            f"(window {entry.window_id}, verdict {entry.verdict_node_id})"
        )
    )
    pytest.fail(f"epicormic gate: {detail}", pytrace=False)


_STATE_KEY: pytest.StashKey[dict[str, Any]] = pytest.StashKey()
