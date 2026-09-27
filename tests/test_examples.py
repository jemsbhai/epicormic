"""Every example runs to completion on the mock and prints what its docstring promises."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
EXPECTED = {
    "01_observe.py": ["panel digest", "window baseline", "window drifted", "complete True"],
    "02_verdict.py": [
        "same-provider: stable",
        "changed-provider: drift",
        "recompute matches: True",
    ],
    "03_levers.py": [
        "before any verdict: model call model_call",
        "verdict: drift",
        "after drift: model call refused (epicormic_drift)",
        "side-effectful tool call needs confirmation",
        "changed fingerprint: model call refused (contract_changed)",
    ],
    "04_cli.py": ["$ epicormic panel digest", "exit 0", "drift (", "exit 3", "wrote "],
}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_example_runs(name: str, tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(EXAMPLES / name)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=600,
    )
    assert completed.returncode == 0, completed.stderr
    for marker in EXPECTED[name]:
        assert marker in completed.stdout, (marker, completed.stdout)


def test_every_example_is_covered() -> None:
    assert sorted(p.name for p in EXAMPLES.glob("0*.py")) == sorted(EXPECTED)
