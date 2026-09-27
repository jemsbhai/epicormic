"""Example 04: the command line loop, driven from Python for a self-contained demo.

Run: python examples/04_cli.py

The same commands work from a shell; this script calls the CLI's ``main``
so the example can run anywhere without a console script on the path.
Exit codes are the states: 0 stable, 2 warning, 3 drift, 4 unknown.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from epicormic import Panel, Probe, save_panel
from epicormic.cli import main

CONFIG = {
    "format": "epicormic/config/v1",
    "scorers": ["normalized_match", "exact_match", "output_chars"],
    "permutations": 500,
}
DRIFTED_MODULE = (
    "from epicormic.mock import Drift, MockProvider\n"
    "step = MockProvider(seed=7, drift=Drift(match_rate=0.4))\n"
)


def run(*args: str) -> int:
    print(f"$ epicormic {' '.join(args)}")
    code = main(list(args))
    print(f"exit {code}\n")
    return code


def demo() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="epicormic-cli-"))
    panel = Panel(
        name="example-panel",
        probes=tuple(
            Probe(f"probe-{index}", {"model": "mock", "input": f"Question number {index}"})
            for index in range(8)
        ),
    )
    save_panel(panel, workdir / "panel.json")
    (workdir / "config.json").write_text(json.dumps(CONFIG), encoding="utf-8")
    (workdir / "drifted_provider.py").write_text(DRIFTED_MODULE, encoding="utf-8")
    sys.path.insert(0, str(workdir))
    panel_path, store = str(workdir / "panel.json"), str(workdir / "runs.db")
    run("panel", "digest", panel_path)
    run(
        "observe",
        "--panel",
        panel_path,
        "--store",
        store,
        "--window",
        "baseline",
        "--samples",
        "10",
        "--fn",
        "epicormic.mock:step",
    )
    run(
        "observe",
        "--panel",
        panel_path,
        "--store",
        store,
        "--window",
        "current",
        "--samples",
        "10",
        "--fn",
        "drifted_provider:step",
    )
    run(
        "verdict",
        "--store",
        store,
        "--monitor",
        "weekly",
        "--panel",
        panel_path,
        "--baseline",
        "baseline",
        "--current",
        "current",
        "--config",
        str(workdir / "config.json"),
    )
    run("status", "--store", store, "--monitor", "weekly")
    run("report", "--store", store, "--monitor", "weekly", "--out", str(workdir / "report.json"))


if __name__ == "__main__":
    demo()
