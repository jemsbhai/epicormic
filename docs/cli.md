# Command line

The `epicormic` console script (also `python -m epicormic`) drives the
whole loop from a shell or a CI job: pin a panel, observe windows, compare
them, read the state, and export the evidence. Every command works on a
SQLite store path; remote pollard stores are used through the Python API.
Output is value-free: it names states, node ids, digests, counts, and
statistics, never a provider result.

Exit codes are the drift states, so a job can gate on them:

| code | meaning |
| --- | --- |
| 0 | `stable` (or a command that has no state, such as `panel digest`) |
| 2 | `warning` |
| 3 | `drift` |
| 4 | `unknown` (including a monitor with no verdict yet) |
| 1 | error, or an observation left incomplete by a budget |

## Step callables

`observe` and `calibrate` never construct provider clients. They take
`--fn module:attribute`, resolved by import, which must be a pollard step
callable `(request) -> result`. Client construction happens in that module
at import time, exactly as with pollard adapters. The attribute may be
dotted. `epicormic.mock:step` is a ready-made mock for trying the tool
without credentials.

```python
# steps_openai.py
from openai import OpenAI
from pollard.adapters.openai import make_responses_fn

client = OpenAI()
step = make_responses_fn(client)
```

## Commands

```
epicormic panel digest PANEL.json
```

Prints the panel digest that every window and monitor of this panel is
keyed by.

```
epicormic observe --panel PANEL.json --store runs.db --window 2026-W39 --samples 10 --fn steps_openai:step
                  [--contract CONTRACT.json] [--budget-tokens T] [--budget-usd U] [--budget-steps S]
```

Records one window in hybrid mode: rerunning the same command serves what
exists and dispatches only what is missing, so an interrupted window can be
resumed, and a window refused by a budget is completed by rerunning with a
larger one. `CONTRACT.json` is a pollard replay contract document
(`"format": "pollard/replay-contract/v1"`, `provider`, and optionally
`model_revision`, `api_version`, `adapter`, `adapter_version`, `sdk`,
`sdk_version`, `application_revision`, `environment`). `--budget-usd` needs
a cost meter; with pollard's default meters the USD dimension is never
charged, so use tokens or steps unless the store's runtime has
`TokenmasterCostMeter`. The command exits 1 when the window is incomplete.

```
epicormic verdict --store runs.db --monitor weekly --panel PANEL.json --baseline 2026-W36,2026-W37 --current 2026-W39
                  [--config CONFIG.json] [--recompute]
```

Compares the current window with the pooled baseline windows, appends the
verdict to the monitor chain, and exits with the state's code. A monitor's
configuration cannot change: use a new monitor id for a new configuration.
`CONFIG.json` is an analysis configuration document
(`"format": "epicormic/config/v1"`, see docs/PLAN.md section 5.5); numbers
are decimal strings. `--recompute` re-derives the latest recorded verdict
from the store over the history it was written against and exits 0 when
the payload matches, 1 otherwise.

```
epicormic calibrate --panel PANEL.json --store runs.db --fn steps_openai:step --samples 10
                    [--windows 2] [--prefix aa] [--monitor calibrate] [--config CONFIG.json]
```

A/A calibration: observes `--windows` windows named `aa-1`, `aa-2`, ... in
succession, uses the first as the baseline, and analyses the rest. Every
verdict should be `stable`; the exit code is the worst state seen. On the
mock provider use behavioural scorers in the configuration (docs/PLAN.md,
D22).

```
epicormic status --store runs.db --monitor weekly [--panel PANEL.json]
epicormic report --store runs.db --monitor weekly --out report.json [--panel PANEL.json] [--format json|jsonld] [--prov-o]
```

`status` prints the latest ledger entry and exits with its state's code.
`report` writes a JSON document (`epicormic/report/v1`) with the latest
verdict payload and the chain history. With the `[jsonld]` extra,
`--format jsonld` writes a JSON-LD `Report` document under the epicormic
vocabulary whose `latest` entry is the verdict as a JSON-LD document with
jsonld-ex provenance annotations and an integrity digest
(`epicormic.jsonld.verify_verdict_jsonld` checks it), and `--prov-o`
writes the PROV-O graph of that verdict document instead; `--prov-o`
without `--format jsonld`, or either without the extra installed, exits 1.
Pass `--panel` when the same monitor id exists for several panels. The
record formats are listed in docs/evidence-format.md.

## A CI recipe

```
epicormic observe --panel panel.json --store runs.db --window "$WEEK" --samples 10 --fn steps_openai:step --budget-usd 0.60
epicormic verdict --store runs.db --monitor weekly --panel panel.json --baseline 2026-W36 --current "$WEEK" --config config.json
```

The second command's exit code is the gate. The pytest plugin
(`--epicormic-store`, `--epicormic-monitor`, the `epicormic_gate` marker)
is the same gate for a test suite that runs after these commands.
