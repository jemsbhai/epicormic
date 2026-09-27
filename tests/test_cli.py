"""Command line interface (docs/PLAN.md, section 12)."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from epicormic.cli import EXIT_CODES, main, resolve_callable
from epicormic.panel import Panel, Probe, save_panel

CONFIG = {
    "format": "epicormic/config/v1",
    "scorers": ["normalized_match", "exact_match", "output_chars"],
    "permutations": 200,
}
CONTRACT = {"format": "pollard/replay-contract/v1", "provider": "mock", "model_revision": "v1"}


def run(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(list(args), out=out, err=err)
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    panel = Panel(
        name="cli",
        probes=tuple(Probe(f"p{i}", {"model": "mock", "input": f"q{i}"}) for i in range(6)),
        sampling={"temperature": "0.5"},
    )
    save_panel(panel, tmp_path / "panel.json")
    (tmp_path / "config.json").write_text(json.dumps(CONFIG), encoding="utf-8")
    (tmp_path / "contract.json").write_text(json.dumps(CONTRACT), encoding="utf-8")
    (tmp_path / "drifted.py").write_text(
        "from epicormic.mock import Drift, MockProvider\n"
        "step = MockProvider(seed=9, drift=Drift(match_rate=0.3))\n"
        "not_callable = 3\n",
        encoding="utf-8",
    )
    (tmp_path / "calib.py").write_text(
        "from epicormic.mock import MockProvider\nstep = MockProvider(seed=4)\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return {"panel": "panel.json", "store": "runs.db", "digest": panel.digest}


def observe(workspace: dict[str, str], window: str, fn: str, *extra: str) -> tuple[int, str, str]:
    return run(
        "observe",
        "--panel",
        workspace["panel"],
        "--store",
        workspace["store"],
        "--window",
        window,
        "--samples",
        "8",
        "--fn",
        fn,
        *extra,
    )


# --- panel, observe, verdict, status, report -----------------------------------


def test_panel_digest(workspace: dict[str, str]) -> None:
    code, out, _ = run("panel", "digest", workspace["panel"])
    assert code == 0 and out.strip() == workspace["digest"]


def test_observe_verdict_status_report_flow(workspace: dict[str, str]) -> None:
    code, out, _ = observe(workspace, "base", "epicormic.mock:step", "--contract", "contract.json")
    assert code == 0
    assert "recorded 48 dispatched 48 served 0 refused 0 not_attempted 0 complete yes" in out
    code, out, _ = observe(workspace, "cur", "drifted:step", "--contract", "contract.json")
    assert code == 0
    code, out, _ = run(
        "verdict",
        "--store",
        workspace["store"],
        "--monitor",
        "m",
        "--panel",
        workspace["panel"],
        "--baseline",
        "base",
        "--current",
        "cur",
        "--config",
        "config.json",
    )
    assert code == EXIT_CODES["drift"]
    assert out.startswith("drift (pooled test rejected for normalized_match) window cur sequence 1")
    assert "verdict " in out and "opinion belief" in out
    code, out, _ = run(
        "verdict",
        "--store",
        workspace["store"],
        "--monitor",
        "m",
        "--panel",
        workspace["panel"],
        "--baseline",
        "base",
        "--config",
        "config.json",
        "--recompute",
    )
    assert code == 0 and "recompute matches" in out
    code, out, _ = run("status", "--store", workspace["store"], "--monitor", "m")
    assert code == EXIT_CODES["drift"] and out.startswith("drift monitor m window cur sequence 1")
    code, out, _ = run(
        "status", "--store", workspace["store"], "--monitor", "m", "--panel", workspace["panel"]
    )
    assert code == EXIT_CODES["drift"]
    code, out, _ = run(
        "report", "--store", workspace["store"], "--monitor", "m", "--out", "report.json"
    )
    assert code == EXIT_CODES["drift"] and "wrote report.json" in out
    document = json.loads(Path("report.json").read_text(encoding="utf-8"))
    assert document["format"] == "epicormic/report/v1"
    assert document["latest"]["state"] == "drift"
    assert [entry["window_id"] for entry in document["history"]] == ["cur"]
    assert document["latest_created_at"] is not None


def test_status_and_report_without_verdicts(workspace: dict[str, str]) -> None:
    observe(workspace, "base", "epicormic.mock:step")
    code, out, _ = run("status", "--store", workspace["store"], "--monitor", "nothing")
    assert code == EXIT_CODES["unknown"] and "has no verdict" in out
    code, _, err = run(
        "report", "--store", workspace["store"], "--monitor", "nothing", "--out", "r.json"
    )
    assert code == 1 and "has no verdict" in err
    code, _, err = run(
        "verdict",
        "--store",
        workspace["store"],
        "--monitor",
        "nothing",
        "--panel",
        workspace["panel"],
        "--baseline",
        "base",
        "--recompute",
    )
    assert code == 1 and "no verdict to recompute" in err


def test_unknown_verdict_when_windows_are_missing(workspace: dict[str, str]) -> None:
    observe(workspace, "base", "epicormic.mock:step")
    code, out, _ = run(
        "verdict",
        "--store",
        workspace["store"],
        "--monitor",
        "m",
        "--panel",
        workspace["panel"],
        "--baseline",
        "base",
        "--current",
        "never",
    )
    assert code == EXIT_CODES["unknown"] and out.startswith("unknown (current window missing)")


def test_contract_change_is_shown(workspace: dict[str, str]) -> None:
    observe(workspace, "base", "epicormic.mock:step", "--contract", "contract.json")
    other = {**CONTRACT, "model_revision": "v2"}
    Path("contract2.json").write_text(json.dumps(other), encoding="utf-8")
    observe(workspace, "cur", "epicormic.mock:step", "--contract", "contract2.json")
    code, out, _ = run(
        "verdict",
        "--store",
        workspace["store"],
        "--monitor",
        "m",
        "--panel",
        workspace["panel"],
        "--baseline",
        "base",
        "--current",
        "cur",
        "--config",
        "config.json",
    )
    assert "contract changed at /model_revision" in out
    assert code in EXIT_CODES.values()


def test_budget_flags_and_incomplete_window(workspace: dict[str, str]) -> None:
    code, out, _ = observe(
        workspace,
        "tight",
        "epicormic.mock:step",
        "--budget-steps",
        "5",
        "--budget-tokens",
        "100000",
        "--budget-usd",
        "1",
    )
    assert code == 1
    assert "recorded 5 dispatched 5 served 0 refused 6 not_attempted 37 complete no" in out


# --- calibrate --------------------------------------------------------------------


def test_calibrate_runs_windows_and_reports_clean(workspace: dict[str, str]) -> None:
    # a fixed-seed provider in its own module keeps this a deterministic
    # regression test of the command, independent of test order
    code, out, _ = run(
        "calibrate",
        "--panel",
        workspace["panel"],
        "--store",
        workspace["store"],
        "--fn",
        "calib:step",
        "--samples",
        "8",
        "--windows",
        "3",
        "--config",
        "config.json",
    )
    assert code == 0
    assert out.count("complete yes") == 3
    assert "window aa-2 sequence 1" in out and "window aa-3 sequence 2" in out
    assert out.strip().endswith("calibration clean")
    code, _, err = run(
        "calibrate",
        "--panel",
        workspace["panel"],
        "--store",
        workspace["store"],
        "--fn",
        "epicormic.mock:step",
        "--samples",
        "8",
        "--windows",
        "1",
    )
    assert code == 1 and "at least 2" in err
    code, _, err = run(
        "calibrate",
        "--panel",
        workspace["panel"],
        "--store",
        workspace["store"],
        "--fn",
        "epicormic.mock:step",
        "--samples",
        "8",
        "--windows",
        "2",
        "--prefix",
        "short",
        "--monitor",
        "a/b",
    )
    assert code == 1 and "monitor_id" in err
    (Path("tiny.json")).write_text(json.dumps({**CONFIG, "n_min": 50}), encoding="utf-8")
    code, out, _ = run(
        "calibrate",
        "--panel",
        workspace["panel"],
        "--store",
        workspace["store"],
        "--fn",
        "epicormic.mock:step",
        "--samples",
        "8",
        "--windows",
        "2",
        "--prefix",
        "thin",
        "--monitor",
        "cal3",
        "--config",
        "tiny.json",
    )
    assert code == EXIT_CODES["unknown"] and "calibration NOT clean" in out


# --- errors and resolution ---------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ("nomodule", "module:attribute"),
        (":step", "module:attribute"),
        ("epicormic.mock:", "module:attribute"),
        ("no.such.module:step", "cannot import"),
        ("epicormic.mock:missing", "no attribute"),
        ("drifted:not_callable", "not callable"),
    ],
)
def test_resolve_callable_errors(workspace: dict[str, str], spec: str, message: str) -> None:
    code, _, err = observe(workspace, "w", spec)
    assert code == 1 and message in err
    with pytest.raises(Exception, match=message):
        resolve_callable(spec)


def test_dotted_attribute_resolution() -> None:
    assert resolve_callable("epicormic.mock:step.__call__") is not None


def test_file_errors(workspace: dict[str, str]) -> None:
    code, _, err = run("panel", "digest", "missing.json")
    assert code == 1 and "panel file not found" in err
    Path("bad.json").write_text("{", encoding="utf-8")
    code, _, err = run("panel", "digest", "bad.json")
    assert code == 1 and "invalid panel" in err
    Path("wrong.json").write_text(json.dumps({"format": "x"}), encoding="utf-8")
    code, _, err = run("panel", "digest", "wrong.json")
    assert code == 1 and "invalid panel" in err
    code, _, err = observe(workspace, "w", "epicormic.mock:step", "--contract", "missing.json")
    assert code == 1 and "contract file not found" in err
    Path("badc.json").write_text("{", encoding="utf-8")
    code, _, err = observe(workspace, "w", "epicormic.mock:step", "--contract", "badc.json")
    assert code == 1 and "not valid JSON" in err
    Path("fmt.json").write_text(json.dumps({"format": "x", "provider": "p"}), encoding="utf-8")
    code, _, err = observe(workspace, "w", "epicormic.mock:step", "--contract", "fmt.json")
    assert code == 1 and "contract format" in err
    Path("unk.json").write_text(json.dumps({**CONTRACT, "extra": 1}), encoding="utf-8")
    code, _, err = observe(workspace, "w", "epicormic.mock:step", "--contract", "unk.json")
    assert code == 1 and "unknown keys" in err
    Path("empty.json").write_text(json.dumps({**CONTRACT, "provider": ""}), encoding="utf-8")
    code, _, err = observe(workspace, "w", "epicormic.mock:step", "--contract", "empty.json")
    assert code == 1 and "invalid contract" in err
    Path("cfg.json").write_text(json.dumps({"format": "epicormic/config/v1", "alpha": "2"}))
    code, _, err = run(
        "verdict",
        "--store",
        workspace["store"],
        "--monitor",
        "m",
        "--panel",
        workspace["panel"],
        "--baseline",
        "base",
        "--current",
        "cur",
        "--config",
        "cfg.json",
    )
    assert code == 1 and "invalid config" in err


def test_window_and_verdict_argument_errors(workspace: dict[str, str]) -> None:
    code, _, err = observe(workspace, "a/b", "epicormic.mock:step")
    assert code == 1 and "window_id" in err
    with pytest.raises(SystemExit) as stop:
        run(
            "verdict",
            "--store",
            workspace["store"],
            "--monitor",
            "m",
            "--panel",
            workspace["panel"],
            "--baseline",
            "base",
        )
    assert stop.value.code == 2
    code, _, err = run(
        "verdict",
        "--store",
        workspace["store"],
        "--monitor",
        "a/b",
        "--panel",
        workspace["panel"],
        "--baseline",
        "base",
        "--current",
        "cur",
    )
    assert code == 1 and "monitor_id" in err
    code, _, err = run("status", "--store", workspace["store"], "--monitor", "a/b")
    assert code == 1 and "monitor_id" in err
    code, _, err = run("report", "--store", workspace["store"], "--monitor", "a/b", "--out", "r")
    assert code == 1 and "monitor_id" in err


def test_jsonld_export_is_deferred(workspace: dict[str, str]) -> None:
    code, _, err = run(
        "report",
        "--store",
        workspace["store"],
        "--monitor",
        "m",
        "--out",
        "r.json",
        "--format",
        "jsonld",
    )
    assert code == 1 and "[jsonld] extra" in err
    code, _, err = run(
        "report", "--store", workspace["store"], "--monitor", "m", "--out", "r.json", "--prov-o"
    )
    assert code == 1 and "[jsonld] extra" in err


def test_module_entry_point_and_version() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "epicormic", "--version"], capture_output=True, text=True
    )
    assert completed.returncode == 0
    assert completed.stdout.strip().startswith("epicormic ")
    with pytest.raises(SystemExit) as stop:
        run("--version")
    assert stop.value.code == 0


def test_main_uses_real_streams_by_default(workspace: dict[str, str], capsys: Any) -> None:
    assert main(["panel", "digest", workspace["panel"]]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == workspace["digest"]
