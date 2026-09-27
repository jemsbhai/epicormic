# epicormic

Provider drift detection and containment for pollard-governed AI systems.

Epicormic shoots sprout from dormant buds after a tree is pollarded: growth
that reappears after the cut. epicormic watches for model behaviour that
reappears, or changes, after a baseline has been pinned.

Status: 0.1.0, the first functional release. The specification is
[docs/PLAN.md](docs/PLAN.md), the statistics are derived in
[docs/statistics.md](docs/statistics.md), every record is described in
[docs/evidence-format.md](docs/evidence-format.md), the levers in
[docs/levers.md](docs/levers.md), the command line in
[docs/cli.md](docs/cli.md), and what a verdict does not claim in
[docs/limitations.md](docs/limitations.md).

## Ninety seconds, no credentials

```
python -m pip install epicormic
```

```python
from pollard import MemoryStore

from epicormic import AnalysisConfig, Drift, MockProvider, Monitor, Panel, Probe, analyze, observe

panel = Panel(
    name="demo",
    probes=tuple(Probe(f"q{i}", {"model": "demo", "input": f"question {i}"}) for i in range(8)),
    sampling={"temperature": "0.7"},
    seed_from_attempt=True,
)
print("panel", panel.digest)

store = MemoryStore()
observe(panel, window_id="week-1", samples=10, fn=MockProvider(seed=1), store=store)
observe(panel, window_id="week-2", samples=10, fn=MockProvider(seed=2), store=store)
drifted = MockProvider(seed=3, drift=Drift(match_rate=0.4))
observe(panel, window_id="week-3", samples=10, fn=drifted, store=store)

config = AnalysisConfig(scorers=("normalized_match", "output_chars"), permutations=2000)
monitor = Monitor("weekly", panel.digest, ("week-1",), config)
for window_id in ("week-2", "week-3"):
    verdict = analyze(store, monitor, window_id)
    pooled = verdict.payload["scorers"]["normalized_match"]["pooled"]
    print(window_id, verdict.state, "T", pooled["T"], "p_holm", pooled["p_holm"], "node", verdict.node_id[:16])
```

Output, locked (the panel digest is the same on every platform and every
version; the verdict node ids are the same wherever epicormic 0.1.0 runs on
pollard 1.6, because the mock is seeded and every record is
content-addressed):

```
panel 5cd5533a081de63dfa77c821f26b12ed56e8f842caed287304a6ad9718d3666c
week-2 stable T -0.0375 p_holm 0.675662 node 4aac2d7f0eed1482
week-3 drift T -0.5375 p_holm 0.0009995 node 291011f05749f1c3
```

Week 2 is a second provider instance with no injected change: sampling
variance alone does not trip the test. Week 3 is a provider whose answers
match the baseline forty percent of the time, and the stratified
permutation test rejects. Both verdicts are appended to the monitor's chain
in the pollard store as value-free evidence; `recompute_verdict` re-derives
either from the observations alone.

The same loop from a shell, with the mock provider as the step callable:

```
epicormic panel digest panel.json
epicormic observe --panel panel.json --store runs.db --window week-1 --samples 10 --fn epicormic.mock:step
epicormic observe --panel panel.json --store runs.db --window week-2 --samples 10 --fn epicormic.mock:step
epicormic verdict --store runs.db --monitor weekly --panel panel.json --baseline week-1 --current week-2
epicormic status --store runs.db --monitor weekly
epicormic report --store runs.db --monitor weekly --out report.json
```

Exit codes are the states: 0 stable, 2 warning, 3 drift, 4 unknown, 1
error; `--config` supplies an analysis configuration document, and
`--fn module:attribute` names any step callable, so the CLI never
constructs provider clients. `examples/` holds four runnable scripts: observation, verdicts with
recomputation, the levers, and the command line loop.

## What drift means here

For a fixed probe request, provider drift between a baseline window and a
current window is a difference between the two distributions of the
provider's observable result in any monitored functional: match rate to
the baseline modal answer, tool-call set, output length, tokens, latency,
refusal rate, truncation, cost, energy. Sampling variance is variation
among samples from one window. epicormic tests the null hypothesis that the
baseline and current samples of each probe are exchangeable and reports the
evidence against it, per probe (Fisher's exact test or Mann-Whitney U with
Benjamini-Hochberg), pooled across probes (a stratified permutation test on
Cliff's delta with Holm), and across windows (CUSUM, Page-Hinkley, and an
anytime-valid betting e-process). A verdict never names a cause; a declared
change of `ReplayContract` is recorded as `contract_changed` so a migration
can be told apart from a silent change.

## The four states

Evaluated in order; the first rule that fires is recorded:

1. `unknown`: no baseline, or too few samples to judge.
2. `drift`: a pooled test rejects after Holm, or a sequential detector alarms.
3. `warning`: a per-probe test rejects with a material effect, or a detector is halfway to its threshold.
4. `stable`: otherwise.

## The levers

Three components plug into pollard's fail-closed hooks and read the
monitor's latest verdict from the store at every call:

- `DriftMeter` refuses model calls while the monitor is in a listed state,
  with a refusal node naming the verdict.
- `DriftPolicy` maps the state to allow, confirm, or deny for
  side-effectful tool calls.
- `ContractGate` refuses model calls bound to an execution fingerprint
  other than the one acknowledged, listing the differing fields.

Containment is forward-looking: nothing is rolled back, and a person
decides what happens next. See [docs/levers.md](docs/levers.md).

## Relation to pollard

epicormic records every observation as a node in a
[pollard](https://github.com/jemsbhai/pollard) execution tree, so each is
content-addressed, budgeted, sealed, and replayable, and rerunning a window
serves what exists and dispatches only what is missing. pollard's own
`revalidate_model_call` is a single-sample boolean regression check
between one golden node and one live observation; epicormic generalizes it
to N samples per probe per window with the same value-free evidence
discipline, and never calls it.

## Extras

- `epicormic[jsonld]`: panels as JSON-LD documents, verdicts exported as
  JSON-LD with jsonld-ex provenance annotations and a verifiable integrity
  digest, PROV-O output (`epicormic report --format jsonld [--prov-o]`), and
  opinion fusion across monitors.
- `epicormic[cost]`: pollard's tokenmaster meters, so the `cost_usd` and
  `energy_j` scorers have readings to score.
- `epicormic[vector]`: reserved for the embedding and MMD scorers of 0.2;
  it installs numpy and nothing in 0.1 uses it.

## What it will not do

It does not prevent a provider from changing a model. It observes the
consequence, records evidence, and contains the effect. Read
[docs/limitations.md](docs/limitations.md) before trusting a verdict.

## Development

```
python -m pip install -e ".[dev]"
python -m pytest --cov=epicormic
python -m ruff check src tests examples tools
python -m mypy src
python tools/scan_vocabulary.py
```

## License

MIT.
