"""Example 01: pin a panel and observe two windows on the credential-free mock.

Run: python examples/01_observe.py

Writes a SQLite store under a temporary directory, records a baseline
window and a drifted window of the same panel, and prints the value-free
window reports. Nothing here needs an API key.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from pollard import SQLiteStore

from epicormic import Drift, MockProvider, Panel, Probe, observe, save_panel


def main() -> None:
    panel = Panel(
        name="example-panel",
        probes=tuple(
            Probe(f"probe-{index}", {"model": "mock", "input": f"Question number {index}"})
            for index in range(8)
        ),
        sampling={"temperature": "0.7"},
        seed_from_attempt=True,
    )
    workdir = Path(tempfile.mkdtemp(prefix="epicormic-example-"))
    save_panel(panel, workdir / "panel.json")
    print(f"panel digest {panel.digest}")
    with SQLiteStore(workdir / "runs.db") as store:
        baseline = observe(
            panel, window_id="baseline", samples=10, fn=MockProvider(seed=1), store=store
        )
        drifted = observe(
            panel,
            window_id="drifted",
            samples=10,
            fn=MockProvider(seed=2, drift=Drift(match_rate=0.5, length_delta=6)),
            store=store,
        )
    for report in (baseline, drifted):
        print(
            f"window {report.window_id}: recorded {report.recorded} refused {report.refused} "
            f"complete {report.complete} header {report.header_id[:16]}"
        )
    print(f"store {workdir / 'runs.db'}")


if __name__ == "__main__":
    main()
