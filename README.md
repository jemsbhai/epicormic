# epicormic

Provider drift detection and containment for pollard-governed AI systems.

Epicormic shoots sprout from dormant buds after a tree is pollarded: growth
that reappears after the cut. epicormic watches for model behaviour that
reappears, or changes, after a baseline has been pinned.

Status: pre-release, under construction. The 0.0.1 release on PyPI is a
name claim: it installs and reports its version and nothing more. The
specification is [docs/PLAN.md](docs/PLAN.md), the statistics are derived
in [docs/statistics.md](docs/statistics.md), and the first functional
release will be 0.1.0.

## What works today (from a checkout)

Phases 0 to 3 of the plan are implemented and tested at 100 percent line
coverage:

- `Probe` and `Panel`: pinned canary requests with domain-separated
  digests, plain JSON files, and sampling parameters kept as decimal strings
  so node identity never contains floats.
- `observe`: records N samples of every probe as one pollard run in the
  documented tree layout, in hybrid mode, so a rerun serves what exists and
  dispatches only what is missing; budget and meter refusals are recorded
  per probe and resumable.
- `read_window` and friends: turn a stored window back into observations.
- Scorers: twelve value-free signals (normalized and exact match, tool-call
  set Jaccard and count, refusal, truncation, output characters and tokens,
  input tokens, latency, cost, energy) with references built from baseline
  windows, and finish-reason detection for the OpenAI Chat and Responses,
  Anthropic Messages, and Bedrock Converse result shapes.
- Layer A statistics, pure Python and cross-checked against SciPy: Fisher's
  exact test, Mann-Whitney U with Cliff's delta and the Hodges-Lehmann
  shift, two-sample and stratified permutation tests, Benjamini-Hochberg
  and Holm.
- `MockProvider`: a seeded, credential-free stand-in with injectable drift.

Not yet implemented: verdicts and their value-free evidence notes, the
Subjective Logic opinion, the ledger, the three levers (`DriftMeter`,
`DriftPolicy`, `ContractGate`), sequential detectors, the CLI, and the
pytest plugin. Until those exist the package detects nothing on its own;
the pieces below have to be composed by hand.

## A credential-free example

```python
from pollard import MemoryStore

from epicormic import (
    Drift,
    MockProvider,
    Panel,
    Probe,
    build_references,
    find_window_headers,
    observe,
    read_window,
    resolve_scorers,
    score_window,
)
from epicormic.stats import stratified_permutation_test

panel = Panel(
    name="demo",
    probes=tuple(
        Probe(f"q{i}", {"model": "demo", "input": f"question {i}"}) for i in range(8)
    ),
    sampling={"temperature": "0.7"},
    seed_from_attempt=True,
)
store = MemoryStore()

# baseline window, then a window from a "provider" whose match rate dropped
observe(panel, window_id="week-1", samples=10, fn=MockProvider(seed=1), store=store)
drifted = MockProvider(seed=2, drift=Drift(match_rate=0.4))
observe(panel, window_id="week-2", samples=10, fn=drifted, store=store)

views = {
    window_id: read_window(store, find_window_headers(store, panel.digest, window_id)[0])
    for window_id in ("week-1", "week-2")
}
scorers = resolve_scorers(["normalized_match", "output_chars", "latency_s"])
references = build_references([views["week-1"]], scorers)
baseline = score_window(views["week-1"], scorers, references)
current = score_window(views["week-2"], scorers, references)

strata = [
    (baseline.series("normalized_match", probe_id), current.series("normalized_match", probe_id))
    for probe_id in baseline.probe_ids
]
result = stratified_permutation_test(strata, permutations=2000, seed=0)
print(f"mean Cliff's delta {result.statistic:+.3f}, p = {result.p_value:.4f}")
```

Output on Python 3.12 with pollard 1.6.0:

```
mean Cliff's delta -0.550, p = 0.0005
```

The same code with `drifted = MockProvider(seed=2)` (a second provider with
no injected drift) gives `mean Cliff's delta -0.038, p = 0.6132`: sampling
variance alone does not trip the test.

## What it will do

- Record repeated observations of a pinned canary panel inside a
  [pollard](https://github.com/jemsbhai/pollard) execution tree, so every
  observation is content-addressed, budgeted, sealed, and replayable.
- Reduce each observation to value-free signals through an extensible
  scorer protocol.
- Test whether the current window differs from the baseline beyond
  sampling variance, per probe and pooled, and run anytime-valid sequential
  change detection across windows.
- Drive pollard's fail-closed levers from the drift state: a meter that
  refuses dispatch, a policy that forces confirmation of side-effectful
  tools, and a gate on unacknowledged execution fingerprint changes.

## What it will not do

It does not prevent a provider from changing a model. It observes the
consequence, records evidence, and contains the effect.

## Installation

```
python -m pip install epicormic
```

Development install from a checkout:

```
python -m pip install -e ".[dev]"
```

## License

MIT.
