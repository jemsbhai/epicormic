"""Example 02: an A/A window, a drifted window, and the verdicts they earn.

Run: python examples/02_verdict.py

Records a baseline and two current windows on the mock, analyses each
against the baseline with behavioural scorers, prints the states with the
rules that fired, and recomputes the verdicts from the store to show that
a verdict is a pure function of the observations and the configuration.
"""

from __future__ import annotations

from pollard import MemoryStore

from epicormic import (
    AnalysisConfig,
    Drift,
    MockProvider,
    Monitor,
    Panel,
    Probe,
    analyze,
    observe,
    recompute_verdict,
)

SCORERS = ("normalized_match", "exact_match", "tool_call_set_jaccard", "refusal", "output_chars")


def main() -> None:
    panel = Panel(
        name="example-panel",
        probes=tuple(
            Probe(f"probe-{index}", {"model": "mock", "input": f"Question number {index}"})
            for index in range(12)
        ),
        seed_from_attempt=True,
    )
    store = MemoryStore()
    observe(panel, window_id="baseline", samples=10, fn=MockProvider(seed=1), store=store)
    observe(panel, window_id="same-provider", samples=10, fn=MockProvider(seed=2), store=store)
    observe(
        panel,
        window_id="changed-provider",
        samples=10,
        fn=MockProvider(seed=3, drift=Drift(match_rate=0.5)),
        store=store,
    )
    monitor = Monitor(
        "weekly", panel.digest, ("baseline",), AnalysisConfig(scorers=SCORERS, permutations=500)
    )
    for window_id in ("same-provider", "changed-provider"):
        verdict = analyze(store, monitor, window_id)
        pooled = verdict.payload["scorers"]["normalized_match"]["pooled"]
        opinion = verdict.payload["opinion"]
        print(
            f"{window_id}: {verdict.state} ({verdict.rule_fired}); normalized_match "
            f"T={pooled['T']} p_holm={pooled['p_holm']}; opinion belief {opinion['belief']} "
            f"uncertainty {opinion['uncertainty']}"
        )
        assert verdict.node_id is not None
        _, matches = recompute_verdict(store, monitor, verdict.node_id)
        print(f"  recompute matches: {matches}")


if __name__ == "__main__":
    main()
