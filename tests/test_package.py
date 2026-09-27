"""Package-level checks: import, version, and pinned dependency floor."""

from __future__ import annotations

import re
from importlib.metadata import requires, version

import pytest
from packaging.requirements import Requirement

import epicormic


def test_package_imports_and_reports_a_semver_version() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", epicormic.__version__), epicormic.__version__
    assert epicormic.__version__ == version("epicormic")


def test_only_runtime_dependency_is_pollard_within_major_one() -> None:
    declared = [Requirement(entry) for entry in requires("epicormic") or []]
    runtime = [req for req in declared if req.marker is None or "extra" not in str(req.marker)]
    assert [req.name for req in runtime] == ["pollard"]
    assert {str(spec) for spec in runtime[0].specifier} == {">=1.6.0", "<2"}


def test_installed_pollard_satisfies_the_floor() -> None:
    major, minor = (int(part) for part in version("pollard").split(".")[:2])
    assert (major, minor) >= (1, 6)
    assert major < 2


def test_pytest_plugin_module_imports_cleanly() -> None:
    import epicormic.pytest_plugin as plugin

    assert plugin.__doc__ is not None
    assert {"pytest_addoption", "pytest_configure", "pytest_pyfunc_call"} <= set(dir(plugin))


def test_lazy_exports_resolve_and_unknown_names_fail() -> None:
    from epicormic.panel import Panel

    assert epicormic.Panel is Panel
    assert "Panel" in dir(epicormic)
    assert set(epicormic.__all__) >= {"Panel", "Probe", "load_panel", "save_panel"}
    with pytest.raises(AttributeError, match="no attribute 'Missing'"):
        _ = epicormic.Missing
