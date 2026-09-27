# epicormic build plan

Status: v0.1 specification, approved, implemented. Phases 0 to 8
(scaffold, observation, scorers, Layer A and Layer B statistics, verdict,
opinion, ledger, levers, CLI, pytest plugin, JSON-LD extra, examples,
documentation set, installed-wheel smoke test) are done and tested at 100
percent line coverage; 0.1.0 is the first functional release. Next: Phase
9 (experiments and the paper) in a separate repository. Decisions D1 to
D11 and D13 to D23 are approved; D12 is declined. Last updated
2026-09-27.

This document is the single source of truth for what epicormic is, why each
part exists, and what remains. A fresh session or a coding agent should be
able to continue the build from this file plus the repository. Every
statement about pollard below was verified against the pollard 1.6.0 source
distribution from PyPI on 2026-09-27; file references are to that sdist.

## 1. What epicormic is

epicormic detects and contains provider drift for pollard-governed AI
systems. Epicormic shoots are the shoots that sprout from dormant buds after
a tree is pollarded: growth that reappears after the cut. epicormic watches
for model behaviour that reappears, or changes, after a baseline has been
pinned.

It does four things:

1. Records repeated observations of a pinned canary panel (a fixed set of
   probe requests) inside a pollard execution tree, so every observation is
   content-addressed, budgeted, sealed, and replayable.
2. Reduces each observation to value-free signals (match indicators, tool
   call sets, lengths, tokens, latency, cost, energy) through a scorer
   protocol that callers can extend.
3. Tests whether the current window of observations differs from the
   baseline window beyond sampling variance, per probe and pooled, and runs
   sequential change detection across windows.
4. Drives pollard's fail-closed levers from the resulting drift state: a
   meter that refuses dispatch, a policy that forces confirmation or denial
   of side-effectful tools, and a gate on unacknowledged execution
   fingerprint changes.

What it is not: it does not prevent a provider from changing a model. It
does not monitor production traffic in v0.1 (input-population drift is a
later release). It does not judge output quality with a second model in
v0.1; the scorer protocol allows it, the core does not ship it. It is not an
agent framework and it does not construct provider clients.

The claim to defend, stated so it can fail: on a locally pinned model whose
behaviour cannot drift, epicormic reports stable at the declared false alarm
rate; on a model with an injected behaviour change of a declared size,
epicormic reports drift with declared power; and when it reports drift,
pollard refuses or gates the affected steps and records why.

## 2. Why pollard is the substrate

pollard 1.6.0 already provides the experimental unit and the levers, and
lacks the statistics. Verified facts:

- Node identity is SHA-256 over `b"pollard/v1\n"` plus the canonical bytes of
  `{"a": attempt, "k": kind, "p": parent_or_empty, "pl": payload}`
  (docs/api-stability.md). "Same request under the same context" is exact,
  and `attempt` distinguishes repeated samples of one request.
- `run.branch(attempt=j)` creates an anchor note node with payload
  `{"branch": true}` and attempt `j` under the cursor, and returns a child
  `Run` whose cursor is that anchor (src/pollard/runtime.py, `Run.branch`).
  N branches under one parent therefore give N sibling anchors, each of
  which can hold one sample. examples/02_best_of_n.py uses this pattern.
- `run.note(payload, attempt=0)` creates a note node and advances the
  cursor to it.
- `Runtime(store, meters=[...], policies=[...], mode=..., dry_run=...)`
  accepts caller-supplied meters and policies. The precheck loop calls
  `meter.precheck_estimate(kind, payload)` on every meter and turns a raised
  `MeterPrecheckRefusal` into a refusal node carrying `meter_name`,
  `reason`, `detail`, and validated `audit_meta` (`Run._estimates`).
  Passing a `meters` list replaces the defaults `[StepMeter(), DepthMeter(),
  WallClockMeter(), TokenMeter()]`, so epicormic levers are appended to
  those defaults, never substituted for them.
- `Policy.decide(PolicyContext)` returns `Decision.ALLOW`, `DENY`, or
  `CONFIRM` for registered tool calls. DENY records a policy refusal.
  CONFIRM raises `ConfirmationRequired` with a prepared node id that
  `run.confirm(token)` later executes. `ActionSpec.side_effects` is the flag
  that marks side-effectful tools.
- `mode="hybrid"` serves a stored result when the computed node id already
  exists and otherwise executes and stores. That makes observation
  resumable with no extra code: rerunning a window skips samples already
  recorded and dispatches only the missing ones.
- Every model call node's `meta` holds `created_at`, `duration_s`, meter
  readings (NVML energy when measured), `charges`, and `usage` (`Run`
  model call path, runtime.py around line 570).
- `run.revalidate_model_call(...)` compares one golden node with one live
  observation through a `RevalidationComparator` and writes a value-free
  comparison note (src/pollard/revalidation.py). It is a single-sample,
  boolean regression check. epicormic does not call it; the window layout
  in section 6 generalizes it to N samples per probe per window and keeps
  the same value-free evidence discipline.
- `ReplayContract` is the caller-declared execution fingerprint (provider,
  model_revision, api_version, adapter, adapter_version, sdk, sdk_version,
  application_revision, environment). Its `to_dict()` form is canonical and
  can be stored in a note payload.
- Canonical identity values reject floats (src/pollard/_canon.py). Any
  number that must be committed to node identity is encoded as a decimal
  string. pollard's own EXP-001 keeps float sampling parameters inside the
  step callable and out of the identity payload.
- Registry digests: a run root already bound to a registry raises
  `IntegrityError` when the registry digest changes (`Runtime._bind_registry`),
  and every tool call payload records `spec_digest` and `registry_digest`.
  Tool-surface drift is therefore already fail-closed in pollard and is out
  of scope here.

What pollard lacks: cross-run aggregation (the `Store` protocol is
put/get/exists/children/update_meta/walk/roots), a baseline abstraction
(one golden node, not a reference distribution), any statistical test, any
separation of sampling variance from drift, and any drift taxonomy.

## 3. Decision log

Approved:

- D1 (2026-09-26). Packaging: a separate package that depends on pollard,
  the same pattern as pollard-jev. pollard stays zero-dependency and stable.
- D2 (2026-09-26). First target: provider or model drift observed through
  canary panels. Input-population drift and agent behavioural drift are
  later releases.
- D3 (2026-09-27). Name: epicormic. PyPI availability verified on
  2026-09-27; near-name variants (epicormics) are also clear.

Approved 2026-09-27:

- D4. Dependencies: the core depends on `pollard>=1.6.0,<2` and nothing
  else. All v0.1 statistics are pure Python on the standard library
  (`math`, `statistics`, `random`, `decimal`, `itertools`). NumPy and SciPy
  appear only in a `[vector]` extra (v0.2 embedding and MMD scorers) and in
  the `[dev]` extra for cross-check tests that skip when SciPy is absent.
- D5. Observation tree layout: one pollard run per window; a window header
  note; one branch per probe holding a probe header note; one nested branch
  per sample holding the model call. Verdicts live in a separate monitor
  run as a chain of notes. Section 6 gives the exact layout.
- D6. Evidence numbers are fixed-precision decimal strings inside identity
  payloads, because canonical identity rejects floats. Integers and booleans
  stay native. Section 10 gives the encoding.
- D7. Canonical serialization: epicormic implements the serialization
  documented in pollard's docs/api-stability.md in its own `_canon.py`, with
  a conformance test that compares byte output against pollard's private
  `pollard._canon.canonical_bytes` over a property-based corpus. In
  parallel, propose to pollard 1.7 that `canonical_bytes` be exported
  publicly; when that lands, epicormic imports it and keeps the conformance
  test as a guard.
- D8. Statistical design. Layer A (window comparison): a stratified
  permutation test on the mean per-probe Cliff's delta as the primary pooled
  test; Mann-Whitney U with Cliff's delta and Hodges-Lehmann shift for
  scalar scorers per probe; Fisher's exact test with risk difference for
  indicator scorers per probe; Benjamini-Hochberg across (probe, scorer)
  tests and Holm across pooled per-scorer tests. Layer B (sequential across
  windows): two-sided CUSUM and Page-Hinkley on the pooled statistic as
  baselines, and a betting e-process on per-observation indicator streams
  as the anytime-valid detector. Section 8 specifies each.
- D9. Verdict states are `stable`, `warning`, `drift`, `unknown`, with the
  rules in section 9. Lever defaults: `DriftMeter` refuses model calls on
  `drift` and allows on `warning` and `unknown`; `DriftPolicy` returns
  CONFIRM for side-effectful tools on `drift`, ALLOW otherwise;
  `ContractGate` refuses when a bound replay contract differs from the
  expected fingerprint and the live fingerprint digest is not in the
  acknowledged set. Every default is a constructor argument.
- D10. The CLI never constructs provider clients. `observe` and
  `calibrate` take `--fn module:attribute`, resolved by import, which must
  be a pollard step callable `(request: dict) -> dict`. Client construction
  happens in the user's module at import time.
- D11. Negative control: a locally pinned model (llama.cpp with the Qwen
  build used in pollard EXP-001) with per-attempt seeds is the control in
  every experiment. epicormic must report `stable` on it at the declared
  false alarm rate. Any drift verdict on the control is a defect in
  epicormic, not a finding.
- D13 (2026-09-27). Panel authoring formats: plain JSON, and JSON-LD
  documents carrying jsonld-ex annotations, loaded through an optional
  `epicormic[jsonld]` extra (`jsonld-ex>=0.7.4,<0.8`, which brings PyLD).
  The core never imports jsonld-ex; a JSON-LD panel is projected to the
  canonical plain-JSON panel document before digesting, so a panel authored
  either way has the same `panel_digest`. Section 5.2 gives the rules.
- D14 (2026-09-27). v0.1 ships a pytest plugin (`pytest11` entry point) with
  a session-scoped `epicormic_state` fixture and an `epicormic_gate` marker,
  in addition to the CLI exit codes. Section 12.1 specifies it.
- D15 (2026-09-27). The pollard 1.7 request to export `canonical_bytes`
  (D7) is raised after epicormic 0.1.0 ships, not in parallel.
- D16 (2026-09-27). EXP-D provider families: OpenAI (Responses API),
  Anthropic (Messages), Amazon Bedrock (Converse), and a LiteLLM route
  (Vertex AI, Azure AI, or another provider), each through the matching
  pollard adapter. Exact model names, tiers, and the split of the budget
  across providers are pinned in the EXP-D pre-registration when the
  experiment opens, never afterwards.
- D17 (2026-09-27). EXP-D spend cap: 10 USD per month across all hosted
  providers combined, enforced through pollard `Budget(usd=...)` on every
  window run with `TokenmasterCostMeter`, and derived as a per-window,
  per-provider allocation in the pre-registration. Section 16 gives the
  arithmetic; the cap constrains model tiers, N, and panel size, and a
  window that reaches its budget records refusal nodes rather than
  overspending.
- D18 (2026-09-27). jsonld-ex scope: in addition to panels (D13), every
  verdict carries a Subjective Logic opinion about drift computed in the
  core (pure arithmetic, no dependency), and the `[jsonld]` extra exports
  verdicts as JSON-LD documents with jsonld-ex provenance annotations and
  an integrity digest, and provides fusion recipes over jsonld-ex opinion
  operators. Sections 10.1 and 10.2 specify both. The opinion is a derived
  summary for downstream fusion, never an input to the state rules of
  section 9.
- D19 (2026-09-27, made during Phase 1 and reported without objection).
  The window header identity commits to the panel digest, panel name,
  window id, replay contract, and sampling and seed configuration only.
  The requested sample count and the epicormic version are recorded in the
  header note's metadata (`meta["epicormic"]`), not its payload: if they
  were in the identity, raising N or upgrading epicormic would produce a
  new header node and orphan every recorded sample. Section 5.3 and
  section 6 reflect this.
- D20 (2026-09-27, made during Phase 1 and reported without objection).
  The seeded mock provider is part of the package as `epicormic.mock`
  (`MockProvider`, `Drift`) rather than an example script, so the tests,
  the CLI's calibration command, and users share one implementation.
- D21 (2026-09-27, correction found during Phase 4). The null rate of the
  sign-transformed e-process stream for a scalar scorer is not one half. It
  is the baseline's own above-median rate among non-ties (per-probe
  medians, pooled across probes, clipped by `clipped_rate`), a plug-in
  with the same caveat as the indicator case. One half holds only when the
  score distribution has no mass at the median; a discrete score such as
  output length on a mostly matching provider has most of its mass exactly
  at the median, and with one half as the null the e-process alarmed on
  nothing. Section 8.2 and docs/statistics.md section 7 are corrected.
- D22 (2026-09-27, finding recorded during Phase 4). An anytime-valid test
  of a rate eventually detects any systematic shift, however small; the
  e-process therefore has no effect-size floor by design, and `effect_min`
  applies to the per-probe warning rule only. Consequence: `latency_s`
  stays in the default scorer set because latency drift is a real
  operational signal for hosted providers, but on the mock provider its
  microsecond timings shift systematically between runs, so tests,
  examples, and the CLI's calibration use behavioural scorer sets on the
  mock. Section 17 records the limitation.
- D23 (2026-09-27, made during Phase 8 release checks). Every pollard
  store lists a node's children by kind and id, never by creation, so the
  order of a window's headers (several exist only after a contract change)
  cannot be read from the tree, and `created_at` ties whenever two
  observes fall inside the platform clock's resolution (the release suite
  did on Windows). Each header therefore records its creation ordinal in
  its metadata (`meta["epicormic"]["header_index"]`, not identity, as in
  D19), assigned as the count of headers already under the root, kept on
  reruns, and `find_window_headers` orders by it; headers without one sort
  last by `created_at` then id. The verdict judges the last header, so the
  ordinal is what makes the current window of a changed contract
  well-defined.

Declined:

- D12 (2026-09-27). hashrope is not incorporated. hashrope 0.2.2 is a
  BB[2/7] weight-balanced rope with polynomial hash metadata over Mersenne
  primes: O(log) concat, split, substring hashing, and repetition encoding
  over large mutable sequences. epicormic's data are small fixed probe
  payloads and fixed results with no incremental editing, and their identity
  is already SHA-256 through pollard. hashrope's polynomial fingerprints
  are collision-constructible and excluded from adversarial integrity use
  by its own paper, so they must stay out of the evidence layer, whose
  tamper-evidence claim rests on SHA-256. The one place hashrope could touch
  (text-similarity scorers over outputs) operates on a few thousand
  characters, where direct comparison is trivial and a dependency is not
  justified. pollard's `HashRopeStore` remains available to any epicormic
  user as a store backend because epicormic is store-agnostic, but it is
  in-process and single-writer, which is the wrong shape for a ledger read
  by application processes. Revisit only if a later release probes
  long-context prompts (hundreds of thousands of tokens) where prefix-reuse
  identification would matter; even then the touch point is the pollard
  store, not epicormic.

## 4. Drift definition and threat model

For a fixed probe request r, let P_w(r) be the distribution of the
provider's observable result during window w. Provider drift between a
baseline window b and a current window c is a difference between P_b(r)
and P_c(r) in any monitored functional: match rate to the baseline modal
result, tool call set, output length, tokens, latency, refusal rate,
truncation rate, cost, energy. Sampling variance is variation among
samples drawn from one P_w(r). epicormic tests the null hypothesis that
samples from b and c are exchangeable within each probe, and reports the
evidence against it. Nothing in the design assumes the provider is
deterministic.

Sources of provider drift that this design can observe: silent weight or
checkpoint updates behind a fixed model name; routing or infrastructure
changes; safety-filter changes; hidden configuration changes; SDK or wire
format changes (partly captured by the fingerprint); changes in numerical
execution. Which of these caused a detected drift is not identifiable from
outputs alone, and the verdict never claims a cause.

Not drift: sample-to-sample variation under one P_w; a declared change,
where the caller changed the `ReplayContract` and acknowledged it. Declared
changes are compared the same way, and the verdict records
`contract_changed: true` so the reader can distinguish a migration from a
silent change.

Trust boundaries, inherited from pollard: evidence notes are value-free by
construction, but scorer code is caller-trusted and can emit sensitive
values if written to do so; the audit tree is tamper-evident, not
tamper-proof; there is no distributed dispatch lock, so concurrent
observers need provider idempotency.

## 5. Data model

All identity payloads obey pollard's canonical rules: null, strings,
booleans, integers, lists, and string-keyed objects; no floats.

### 5.1 Probe

```
Probe(
    probe_id: str,                 # unique within the panel, [A-Za-z0-9_.-]+
    payload: dict[str, IdentityValue],   # the identity payload passed to run.model_call
    tags: tuple[str, ...] = (),    # free labels, e.g. ("json", "tool-use")
    family: str | None = None,     # optional grouping for reports
)
probe_digest = sha256(b"epicormic/probe/v1\n" + canonical_bytes({"probe_id": ..., "payload": ..., "tags": [...], "family": ...}))
```

### 5.2 Panel

```
Panel(
    name: str,
    probes: tuple[Probe, ...],     # probe_id unique, order is significant
    sampling: dict[str, str] = {}, # decimal strings, e.g. {"temperature": "0.7", "top_p": "0.9"}
    seed_from_attempt: bool = False,
    seed_base: int = 0,
    seed_field: str = "seed",
    min_samples: int = 5,
)
panel_digest = sha256(b"epicormic/panel/v1\n" + canonical_bytes(panel document))
```

`sampling` holds request parameters that are floats in provider APIs.
They are committed to the panel digest as decimal strings. At dispatch,
epicormic decodes them (`Decimal` to `float`, integers stay `int`) and
merges them into the request handed to the caller's step callable. The
pollard node identity payload stays equal to `probe.payload`, so identity
never contains floats, while the window header commits to `panel_digest`
and therefore to the sampling configuration. This mirrors how pollard's
EXP-001 keeps `temperature` inside the callable.

When `seed_from_attempt` is true, the request also receives
`{seed_field: seed_base + attempt}`. This is how the local control (D11)
gets independent samples from a pinned model without changing the identity
payload.

Panel files are plain JSON or JSON-LD (D13).

Plain JSON: the file is the panel document itself, so the digest of the
file's canonical form equals `panel_digest`.

JSON-LD with jsonld-ex: the file carries an `@context` that maps the
epicormic vocabulary (`Panel`, `Probe`, `probes`, `payload`, `sampling`,
`seedFromAttempt`, `seedBase`, `seedField`, `minSamples`, `tags`,
`family`) to IRIs under an epicormic namespace, and may carry jsonld-ex
annotations on the panel and on individual probes: `@source`,
`@extractedAt`, `@method`, `@humanVerified`, `@confidence`, and a
jsonld-ex integrity digest for the context. `epicormic.jsonld.load_panel`
validates the document with jsonld-ex (`validate_document`, and
`verify_integrity` when an integrity digest is declared), compacts it with
the epicormic context, strips every annotation, and returns the canonical
plain-JSON panel document plus a separate `PanelProvenance` record. The
digest rules are:

- `panel_digest` is computed over the canonical projection only (name,
  probes with ids, payloads, tags, and families, sampling, seed settings,
  min_samples), so authoring format and provenance never change identity
  and a JSON-LD panel equals its plain-JSON projection.
- `panel_provenance_digest` is SHA-256 over the canonical bytes of the
  stripped annotations. The window header records both digests; the
  annotations themselves are not stored in the tree, so timestamps and
  author strings never enter node identity.
- Probe payloads inside a JSON-LD panel must still satisfy pollard's
  canonical rules after compaction; a payload that expands to IRIs or
  typed values is rejected with a message naming the probe.

`epicormic panel digest` prints both digests for a JSON-LD panel and only
`panel_digest` for a plain panel. jsonld-ex integrity digests are SHA-256,
SHA-384, or SHA-512, so the integrity discipline of section 10 is
preserved.

### 5.3 Window

A window is one observation campaign: one pollard run, one panel, one
declared `ReplayContract`, one sample count. `window_id` is caller-chosen
(for example `2026-09-27T12:00Z`, `weekly-39`, or `aa-1`), non-empty, and
unique per panel within a store.

```
WindowHeader (identity payload of the window header note):
{
  "format": "epicormic/window/v1",
  "panel_digest": "<64 hex>",
  "panel_provenance_digest": "<64 hex>" | null,
  "panel_name": "...",
  "window_id": "...",
  "contract": <ReplayContract.to_dict()> | null,
  "sampling": {...},                    # copied from the panel
  "seed_from_attempt": bool,
  "seed_base": int,
  "seed_field": "seed"
}
```

The requested sample count and the epicormic version are not part of the
identity (D19); `observe` records them in the header's metadata as
`{"epicormic": {"samples_requested": N, "epicormic_version": "..."}}`,
updated on every rerun. The `panel_provenance_digest` field arrives with
the JSON-LD panel loader (D13) and is absent until then.

### 5.4 Observation

An observation is one model call node under a sample branch. epicormic
reads, for scoring: `node.payload` (the probe payload), `node.result` (the
provider result as stored by pollard), `node.meta["duration_s"]`,
`node.meta.get("usage")`, `node.meta.get("charges")`, NVML readings if
present, and the parent chain to recover `probe_id` and `attempt`.
Refusal nodes (budget exhausted, meter refusal) under a sample branch count
as missing samples and are reported as such, never scored as results.

### 5.5 Verdict and Monitor

A monitor is a named comparison stream for one panel: it fixes the analysis
configuration and the baseline window set, and accumulates one verdict per
current window. A verdict is a value-free note (section 10) appended to the
monitor run. The ledger (section 11) reads the latest verdict.

```
Monitor(
    monitor_id: str,
    panel_digest: str,
    baseline_window_ids: tuple[str, ...],   # one or more windows pooled
    config: AnalysisConfig,
)
AnalysisConfig(
    scorers: tuple[str, ...],       # scorer names, order significant
    alpha: str = "0.05",            # pooled per-scorer test level (Holm)
    q: str = "0.05",                # BH false discovery rate for per-probe tests
    permutations: int = 2000,
    seed: int = 0,
    n_min: int = 5,
    effect_min: str = "0.2",        # minimum |Cliff's delta| for a warning
    unknown_fraction: str = "0.5",  # fraction of probes below n_min that makes a window unknown
    top_k: int = 50,                # per-probe entries kept in the verdict payload
    sequential: {"cusum": {"k": "0.1", "h": "0.4"},
                 "page_hinkley": {"delta": "0.05", "lambda": "0.5"},
                 "eprocess": {"alpha": "0.05", "grid": ["0.1", "0.25", "0.5", "0.75"]}}
)
config_digest = sha256(b"epicormic/config/v1\n" + canonical_bytes(config document))
```

## 6. Observation tree layout in the pollard store

Window run (label = `"epicormic/" + panel_digest[:16] + "/" + window_id`,
so distinct windows never collide on root identity):

```
ROOT {"run": "epicormic/<panel16>/<window_id>"}
`-- NOTE window header (section 5.3)                      <- cursor after run.note
    |-- NOTE {"branch": true} attempt=0                   <- run.branch(attempt=0) for probe index 0
    |   `-- NOTE probe header {"format": "epicormic/probe/v1", "probe_id": "...", "probe_digest": "...", "index": 0}
    |       |-- NOTE {"branch": true} attempt=0  `-- MODEL_CALL probe.payload attempt=0   (sample 0)
    |       |-- NOTE {"branch": true} attempt=1  `-- MODEL_CALL probe.payload attempt=1   (sample 1)
    |       `-- ... one sample branch per attempt 0..N-1
    |-- NOTE {"branch": true} attempt=1                   <- probe index 1
    |   `-- NOTE probe header ... `-- sample branches
    `-- ... one probe branch per probe, attempt = probe index
```

Rules:

- Probe branch attempt equals the probe's index in the panel; sample branch
  attempt equals the sample index. Both are therefore recoverable from the
  tree without reading payloads, and the verdict can cite sample node ids.
- The model call inside a sample branch uses `attempt=j` as well, so the
  model call node id also commits to the sample index.
- Observation runs in `mode="hybrid"` so reruns are resumable: existing
  sample node ids are served from the store and only missing samples
  dispatch. A refusal recorded for a sample (for example budget exhausted)
  has a different node kind, so a later rerun with a larger budget
  dispatches that sample.
- The window budget is passed to `runtime.run(label, budget=Budget(...))`
  from CLI flags or the API (`tokens`, `steps`, `usd`). Per-probe budgets
  may be passed through `run.branch(budget=...)` on the probe branch.
- Model calls are made with `keep_chunks=False`; streaming is not used.
- The window header note is written first, in `record` semantics that
  hybrid mode preserves (an existing header is served, a missing one is
  written); its metadata then receives the requested sample count, the
  epicormic version (D19), and the header's creation ordinal
  (`header_index`, D23). A window gains a second header only when the
  declared contract changes, and `find_window_headers` orders headers by
  the ordinal.
- A sample refused by a budget or meter leaves a refusal node under its
  sample anchor; the remaining samples of that probe are not attempted and
  observation continues with the next probe, so a window-level exhaustion
  costs at most one refusal node per probe. Refusal nodes have a different
  kind from model calls, so a rerun with a larger budget dispatches exactly
  the missing samples.

Monitor run (label = `"epicormic-monitor/" + panel_digest[:16] + "/" + monitor_id`):

```
ROOT {"run": "epicormic-monitor/<panel16>/<monitor_id>"}
`-- NOTE monitor header {"format": "epicormic/monitor/v1", "panel_digest", "monitor_id", "baseline_window_ids": [...], "baseline_roots": [...], "config": {...}, "config_digest": "..."}
    `-- NOTE verdict #1 (section 10)
        `-- NOTE verdict #2
            `-- ... one verdict per analysed current window, chained in analysis order
```

The chain order is the sequential order used by Layer B. Recomputing a
verdict with a different configuration is a different monitor, never an
edit. The monitor root's meta receives an index patch
`{"epicormic": {"latest_verdict": <node id>, "state": "...", "window_id": "..."}}`
through `store.update_meta` after each verdict; the chain remains the
source of truth and the ledger verifies that the indexed node exists and
is a descendant of the monitor root before trusting it.

## 7. Scorers

```
class Scorer(Protocol):
    name: str
    kind: Literal["indicator", "scalar"]      # "vector" reserved for v0.2
    reference_based: bool
    def score(self, observation: Observation, reference: Reference | None) -> float | None: ...
```

`Observation` carries `probe_id`, `attempt`, `payload`, `result`, `meta`,
`node_id`, `window_id`. `Reference` is built once per probe from the
baseline windows: the modal `result_digest`, the modal normalized semantic
projection (the same projection as pollard's `NormalizedModelComparator`:
`text`, normalized `tool_calls` without provider ids, `refusal`,
`structured_output`, or all fields except `usage`, `provider_usage`, and
`chunks` when none of those names are present), the modal tool call set,
and per-scorer baseline medians. A scorer returns `None` when the signal is
undefined for an observation (no usage field, no tool calls); `None` is
counted and excluded from tests.

Built-in scorers (all pure Python, all value-free in what they emit):

| name | kind | reference | definition |
| --- | --- | --- | --- |
| `exact_match` | indicator | yes | 1 if `result_digest` equals the baseline modal result digest |
| `normalized_match` | indicator | yes | 1 if the normalized semantic projection equals the baseline modal projection |
| `tool_call_set_jaccard` | scalar | yes | Jaccard similarity between this observation's set of (tool name, canonical arguments) and the baseline modal set; `None` when neither has tool calls |
| `tool_call_count` | scalar | no | number of tool calls |
| `refusal` | indicator | no | 1 if `result["refusal"]` is truthy or a provider finish reason denotes a content filter |
| `truncated` | indicator | no | 1 if the finish reason denotes a length stop |
| `output_chars` | scalar | no | length of `result["text"]` (or of the canonical result when no `text`) |
| `output_tokens` | scalar | no | `meta["usage"]["output_tokens"]` |
| `input_tokens` | scalar | no | `meta["usage"]["input_tokens"]` |
| `latency_s` | scalar | no | `meta["duration_s"]` |
| `cost_usd` | scalar | no | `meta["charges"]["usd"]` when present |
| `energy_j` | scalar | no | NVML energy reading when present |

Default scorer set: `normalized_match`, `exact_match`,
`tool_call_set_jaccard`, `refusal`, `truncated`, `output_chars`,
`output_tokens`, `latency_s`. Finish-reason detection covers the OpenAI
Responses and Chat Completions, Anthropic Messages, and Bedrock Converse
result shapes that pollard's adapters produce, and falls back to `None`.

Caller scorers register by name through `AnalysisConfig.scorers` and a
registry mapping names to instances; the verdict records scorer names only.
The documentation states, as pollard does for comparators, that scorer code
is caller-trusted.

## 8. Statistics

All randomness comes from `random.Random(seed)`; the seed and permutation
count are committed to the verdict. All p-values are two-sided unless a
one-sided direction is declared in the configuration.

### 8.1 Layer A: window comparison

For each probe p and scorer s, let x_B be the scores over all samples of p
in the pooled baseline windows and x_C the scores in the current window,
after dropping `None`.

Per-probe indicator test (kind = indicator): Fisher's exact test on the
2 by 2 table of (window, value). Two-sided p is the sum of hypergeometric
probabilities of tables at most as probable as the observed table, with a
relative tolerance of 1e-7 as SciPy and R use. Effect: risk difference
`rate_C - rate_B`. Implementation: log probabilities through `math.lgamma`.

Per-probe scalar test (kind = scalar): Mann-Whitney U with
`U = #{(c, b): c > b} + 0.5 * #{c == b}`. Exact p when both sizes are at
most 20 and there are no ties, through the standard counting recurrence
for the U distribution; otherwise the normal approximation with continuity
correction and the tie correction
`sigma^2 = n_B n_C / 12 * ((N + 1) - sum(t^3 - t) / (N (N - 1)))`, with
`Phi` from `math.erfc`. A seeded Monte Carlo permutation p over the same
statistic is computed alongside and reported; when the exact and
permutation paths are both available, the exact p is the decision value.
Effects: Cliff's delta `(2U - n_B n_C) / (n_B n_C)` and the Hodges-Lehmann
shift (median of all pairwise `c - b`), reported in the scorer's units.

Pooled test per scorer (primary): stratified permutation test. The
statistic is `T = mean over probes of delta_p`, where `delta_p` is Cliff's
delta for probe p. For indicators Cliff's delta reduces algebraically to the
rate difference, so one statistic serves both kinds. Under the null of
exchangeability within each probe, the sample labels are permuted within
probe (never across probes), `T*` is recomputed R times, and
`p = (1 + #{|T*| >= |T|}) / (R + 1)`. Probes with fewer than `n_min`
samples in either window are excluded from T and listed in the verdict.
The verdict reports T, p, R, the number of contributing probes, and the
fraction of contributing probes with `|delta_p| >= effect_min`.

Multiplicity: Benjamini-Hochberg at level q across all (probe, scorer)
per-probe tests in the window; Holm at level alpha across the pooled
per-scorer tests. Adjusted p-values are stored next to raw ones.

Minimum samples: a per-probe test is `unknown` when either window has fewer
than `n_min` scores. A probe whose indicator is constant with the same
value in both windows is `stable` without a test.

### 8.2 Layer B: sequential monitoring across windows

The monitor chain gives an ordered sequence of current windows
`w = 1, 2, ..., t`, each compared against the same pooled baseline. Two
detectors run on the per-window pooled statistic and one runs on the
per-observation stream.

CUSUM (two-sided) on `z_w = T_w` for each scorer:
`S+_w = max(0, S+_(w-1) + z_w - k)`, `S-_w = max(0, S-_(w-1) - z_w - k)`,
alarm when `max(S+_w, S-_w) >= h`. Initial defaults `k = 0.1`, `h = 0.4`
on the Cliff's delta scale, to be calibrated in EXP-C; the verdict records
the values in force.

Page-Hinkley on `z_w`: with running mean `m_w`, `PH_w = sum_(i<=w) (z_i - m_i - delta)`,
`M_w = min_(i<=w) PH_i`, alarm when `PH_w - M_w >= lambda` (and the mirrored
form for decreases). Initial defaults `delta = 0.05`, `lambda = 0.5`.

Betting e-process (anytime-valid) on per-observation indicator streams.
For an indicator scorer, order every current observation across windows by
(window, probe index, attempt) and let `x_i` in {0, 1}. Let `mu0` be the
pooled baseline rate for that scorer (plug-in). The upward wealth is
`K+_i = K+_(i-1) * (1 + lam * (x_i - mu0))` with `lam` in `(0, 1/mu0)`, the
downward wealth is `K-_i = K-_(i-1) * (1 - lam * (x_i - mu0))` with `lam` in
`(0, 1/(1 - mu0))`, each averaged over the configured grid of fractions of
its maximum `lam` (an average of e-processes is an e-process), and the
two-sided e-value is `E_i = (K+_i + K-_i) / 2`. Alarm when
`E_i >= 1 / alpha`. By Ville's inequality the probability of ever alarming
under the null (mean `mu0`) is at most alpha, at any stopping time. For
scalar scorers the stream is the per-probe sign transform
`x_i = 1[score_i > baseline median of that probe]` with ties excluded and
counted, and `mu0` is the baseline's own above-median rate among non-ties,
pooled across probes and clipped away from 0 and 1 (D21); it equals one
half only when the score distribution has no mass at the median. Documented
caveat: `mu0` is an estimate from the baseline in both cases; the guarantee
is exact only when the baseline is large relative to the current stream,
and the verdict records the baseline sample count so the reader can judge.
A two-sample sequential test that removes the plug-in is the v0.2 research
item.

The e-process state (wealth per grid point per scorer, count of observations
consumed) is recomputed from the store every time, never cached, so a
verdict is a pure function of the observations and the configuration.

### 8.3 Reproducibility

Every statistic in a verdict is recomputable from the store, the monitor
configuration, and the epicormic version. `epicormic verdict --recompute`
recomputes the latest verdict without writing and reports any mismatch.
The installed-wheel smoke test locks the statistics of a fixed synthetic
window pair across Linux, Windows, and macOS, in the way pollard's smoke
test locks its root and model call ids.

## 9. Verdict states and rules

Evaluated in order; the first matching rule sets the state and is recorded
as `rule_fired`:

1. `unknown`: no baseline window resolvable, or more than
   `unknown_fraction` of probes have fewer than `n_min` current samples, or
   the current window has no scored observations for any configured scorer.
2. `drift`: any pooled per-scorer test rejects after Holm at alpha, or any
   sequential detector alarms.
3. `warning`: at least one (probe, scorer) test rejects after
   Benjamini-Hochberg with `|effect| >= effect_min`, or any CUSUM or
   Page-Hinkley statistic is at or above half its threshold, or any
   e-process is at or above `1 / (2 alpha)`.
4. `stable`: otherwise.

`contract_changed` is orthogonal to the state: it is true when the current
window's contract digest differs from every baseline window's contract
digest, and the difference paths are listed as JSON pointers.

## 10. Evidence format

Verdict note identity payload, value-free:

```
{
  "format": "epicormic/verdict/v1",
  "event": "epicormic_verdict",
  "monitor_id": "...",
  "panel_digest": "...",
  "config_digest": "...",
  "epicormic_version": "0.1.0",
  "pollard_version": "1.6.0",
  "baseline_roots": ["<node id>", ...],
  "current_root": "<node id>",
  "current_window_id": "...",
  "sequence_index": 1,
  "state": "stable | warning | drift | unknown",
  "rule_fired": "...",
  "contract_changed": false,
  "contract_difference_paths": [],
  "sample_counts": {"baseline": n, "current": n, "current_refused": n, "probes_below_n_min": ["probe_id", ...]},
  "scorers": {
    "<scorer name>": {
      "kind": "indicator | scalar",
      "pooled": {"T": "0.0833333", "p": "0.031984", "p_holm": "0.127936", "permutations": 2000, "seed": 0,
                 "probes": 24, "fraction_effect_ge_min": "0.125", "rejected": false},
      "per_probe": [
        {"probe_id": "...", "n_b": 10, "n_c": 10, "effect": "-0.34", "shift": "-42",
         "p": "0.012345", "p_bh": "0.04938", "method": "mann_whitney_exact", "rejected": true}
        ... top_k entries by |effect|, then all rejected entries
      ],
      "per_probe_truncated": true,
      "sequential": {
        "cusum": {"s_plus": "0.12", "s_minus": "0", "k": "0.1", "h": "0.4", "alarm": false},
        "page_hinkley": {"ph": "...", "min": "...", "delta": "0.05", "lambda": "0.5", "alarm": false},
        "eprocess": {"e": "1.734", "threshold": "20", "observations": 240, "mu0": "0.9125", "ties_excluded": 0, "alarm": false}
      }
    }
  },
  "opinion": {
    "format": "epicormic/opinion/v1",
    "source": "eprocess-average/v1",
    "e": "1.734",
    "observations": 240,
    "prior_weight": "2",
    "base_rate": "0.5",
    "belief": "0.628994",
    "disbelief": "0.362742",
    "uncertainty": "0.00826446",
    "projected_probability": "0.633126"
  }
}
```

Encoding rules (D6): finite floats become decimal strings with 6
significant digits through `Decimal` quantization (round half even), never
`repr(float)`; integers and booleans keep their native types; non-finite
values become `null` and the field path is appended to a
`non_finite_fields` list. Strings are never derived from result text: the
payload may contain probe ids, node ids, digests, scorer names, method
names, and numbers, and nothing else. A test scans every verdict produced
in the suite for any substring of any provider result and fails on a hit.

The note's `meta` receives `created_at` from pollard. Sealing: `pollard
seal` over the monitor run and over each window run gives independent
tamper-evident digests; the verdict cites window roots, so a seal over the
monitor chain plus seals over the cited windows cover the whole evidence.

### 10.1 Subjective Logic opinion (D18)

Each verdict carries an opinion `omega = (b, d, u, a)` about the
proposition "the provider has drifted on this panel relative to the
baseline". It is computed in the core after the state rules of section 9
have run, from quantities already in the verdict, and is a derived summary
for downstream fusion. It never feeds the state rules or the levers.

Derivation (`source: "eprocess-average/v1"`):

- `E` is the average of the two-sided e-process values across configured
  scorers (an average of e-values is an e-value), and `m` is the number of
  current observations consumed by the e-processes (the minimum over
  scorers, so `m` never overstates the evidence).
- Base rate `a` is the configured prior probability of drift per window
  (default `0.5`; a pre-registration may set a lower value).
- `P = E a / (E a + (1 - a))`, reading the e-value as a conservative Bayes
  factor against the null of no drift, the e-posterior interpretation
  (Grünwald; cite and verify in docs/statistics.md).
- `u = W / (m + W)` with non-informative prior weight `W = 2`, the
  Subjective Logic convention that jsonld-ex `Opinion.from_evidence` also
  uses.
- `b = (1 - u) P` and `d = (1 - u) (1 - P)`, so `b + d + u = 1`, and the
  projected probability is `P(omega) = b + a u`.

Worked example, matching the payload above: `E = 1.734`, `m = 240`,
`a = 0.5`, `W = 2` give `u = 0.00826446`, `P = 0.634236`,
`b = 0.628994`, `d = 0.362742`, projected probability `0.633126`.

Properties tested: `b + d + u = 1` within decimal tolerance; `b` is
non-decreasing in `E` and `u` is non-increasing in `m`; `E = 1` gives
`P = a` for every `m`; `m = 0` gives the vacuous opinion `(0, 0, 1, a)`; a
window in state `unknown` records the vacuous opinion. The opinion is
encoded with the decimal rules of D6 inside the verdict payload, so it is
identity-committed and recomputable like every other field.

Caveats stated in docs/statistics.md: the plug-in baseline rate of the
e-process carries into `P`; `a` is a modelling choice and the projected
probability moves with it; the opinion summarizes evidence strength and
evidence mass, and is not a calibrated frequentist probability of drift
beyond what the e-posterior reading guarantees.

### 10.2 JSON-LD verdict export (D18)

With the `[jsonld]` extra, `epicormic report --format jsonld` and
`epicormic.jsonld.verdict_to_jsonld` (alias `export_verdict`) emit a
verdict as a JSON-LD document (implemented 2026-09-27 as described here;
deviations from the first draft of this section are marked):

- `@context` maps the epicormic vocabulary (the verdict fields of this
  section) to IRIs under the epicormic namespace `urn:epicormic:vocab:`
  and names the jsonld-ex and PROV namespaces. A `context_integrity`
  field carries the jsonld-ex digest of that context for pinning.
- The body is the verdict payload, unchanged, with `@id` set to the verdict
  node id as a URN (`urn:pollard:node:<id>`) and the baseline roots and
  current root linked as `baseline_root_nodes` and `current_root_node`,
  URNs of the same form. The monitor root is not linked, because the
  verdict payload does not carry it; the verdict node's parent chain in the
  store leads to it.
- jsonld-ex annotations on `state`: `@source` = `urn:pypi:epicormic:<version>`
  (a URN rather than the relative form `epicormic/<version>` first written
  here, because the annotation source becomes the PROV-O agent's `@id`),
  `@method` = the pooled test name plus the per-probe test names and the
  detector names, `@extractedAt` = the verdict note's `created_at`,
  `@humanVerified` = false, `@confidence` = the opinion's projected
  probability. The opinion is expressed through jsonld-ex
  `Opinion.to_jsonld()` beside the recorded decimal components, with
  uncertainty re-derived as 1 - belief - disbelief because jsonld-ex
  requires exact additivity and the recorded strings are rounded (parsed to
  floats only inside the export path).
- An `integrity` digest computed with jsonld-ex `compute_integrity`
  (SHA-256) over the document without that field;
  `epicormic.jsonld.verify_verdict_jsonld` checks it before a reader trusts
  the document. The export is derived evidence: the committed record is
  the pollard note, and the export cites it.
- The export passes the same value-free scan as the note, and a jsonld-ex
  shape (`VERDICT_SHAPE`) validates it.

Inbound, the same module loads a panel written as a JSON-LD document
(`panel_from_jsonld`, `load_panel_jsonld`, shape-validated; stripping the
JSON-LD keywords yields the plain document, so the digest is the same).

Fusion ships as one function rather than as examples: `fuse_opinions`
applies jsonld-ex cumulative fusion (independent monitors on the same
provider, different panels) or averaging fusion (consecutive windows of one
monitor) to verdict opinions. Trust discounting by an operator-declared
trust opinion is not implemented in 0.1. PROV-O output through jsonld-ex
`to_prov_o` is the `--prov-o` option of `report`.

## 11. Ledger and levers

`Ledger(store)`:

- `state(monitor_id, panel_digest=None) -> LedgerEntry(state, verdict_node_id,
  window_id, computed_at, sequence_index)`. Reads the monitor root's index
  patch, verifies the node exists and descends from the monitor root, and
  falls back to walking the chain when the index is absent or stale.
- `history(monitor_id) -> list[LedgerEntry]`.

Levers, all constructed by the application and passed into `Runtime(...)`:

`DriftMeter(ledger, monitor_id, *, refuse_on=("drift",), kinds=("model_call",), name="epicormic", on_lookup_error="raise")`
implements pollard's `Meter` protocol. `precheck_estimate(kind, payload)`
returns `None` when `kind` is not in `kinds` or the state is not in
`refuse_on`, and otherwise raises
`MeterPrecheckRefusal("epicormic_drift", detail=state, audit_meta={"monitor_id": ..., "verdict_node_id": ..., "window_id": ..., "state": ...})`.
`charge(...)` returns 0. The meter is appended to pollard's default meters;
documentation shows the exact `meters=[StepMeter(), DepthMeter(),
WallClockMeter(), TokenMeter(), DriftMeter(...)]` construction. A ledger
lookup failure raises normally (a programming or storage error), never a
refusal, matching pollard's guidance on `MeterPrecheckRefusal`.

`DriftPolicy(ledger, monitor_id, *, on_drift=Decision.CONFIRM, on_warning=Decision.ALLOW, on_unknown=Decision.ALLOW, side_effects_only=True)`
implements `Policy`. `decide(ctx)` returns ALLOW when `side_effects_only`
is true and `ctx.spec.side_effects` is false, and otherwise the decision
mapped from the current state.

`ContractGate(expected: ReplayContract, *, acknowledged: frozenset[str] = frozenset(), name="epicormic-contract")`
implements `Meter`. For model call payloads carrying a bound replay
contract (read through `pollard.revalidation.extract_replay_contract`), it
compares the canonical digest of the bound contract with the digest of
`expected`; on mismatch it raises `MeterPrecheckRefusal("contract_changed",
audit_meta={"expected_digest": ..., "live_digest": ..., "difference_paths": [...]})`
unless the live digest is in `acknowledged`. Payloads without a bound
contract pass. This is the "declared change" gate for the application's own
calls; canary probes are unbound by design (section 5.2).

Hybrid fallback is a documented recipe, not code: an application that runs
deterministic flows in `mode="hybrid"` against a last-known-good recording
continues to serve recorded results for identical requests while the state
is `drift`, and only novel requests reach the provider, where `DriftMeter`
refuses them. The documentation states plainly that this helps only for
requests whose identity already exists in the recording.

## 12. CLI

`epicormic` console script, argparse, mirrors pollard's CLI conventions
(SQLite path as the store argument; `--store` accepts a path only in v0.1,
remote stores are used through the Python API).

```
epicormic panel digest PANEL.json
epicormic observe --panel PANEL.json --store STORE.db --window WINDOW_ID --samples N --fn module:callable
                  [--contract CONTRACT.json] [--budget-tokens T] [--budget-usd U] [--budget-steps S]
epicormic verdict --store STORE.db --monitor MONITOR_ID --panel PANEL.json --baseline WINDOW_ID[,WINDOW_ID...] --current WINDOW_ID
                  [--config CONFIG.json] [--recompute]
epicormic calibrate --panel PANEL.json --store STORE.db --fn module:callable --samples N [--windows 2] [--config CONFIG.json]
epicormic status --store STORE.db --monitor MONITOR_ID
epicormic report --store STORE.db --monitor MONITOR_ID --out REPORT.json [--format json|jsonld] [--prov-o]
epicormic --version
```

`calibrate` runs an A/A experiment: two (or more) windows under the same
contract in immediate succession, then a verdict of each later window
against the first, and prints whether any state other than `stable`
occurred. `CONTRACT.json` is a `ReplayContract` document
(`format: pollard/replay-contract/v1`). Exit codes: 0 stable, 2 warning,
3 drift, 4 unknown, 1 error, so CI can gate on the verdict.

### 12.1 pytest plugin (D14)

`epicormic.pytest_plugin` registers under the `pytest11` entry point, as
pollard's plugin does, and epicormic's own suite disables it with
`-p no:epicormic`. It adds:

- Options `--epicormic-store PATH`, `--epicormic-monitor MONITOR_ID`, and
  `--epicormic-fail-on STATES` (comma-separated, default `drift`).
- A session-scoped fixture `epicormic_state` that opens the store
  read-only, reads the ledger, and returns the `LedgerEntry`. It errors
  with a clear message when the options are missing.
- A marker `@pytest.mark.epicormic_gate`: every marked test fails before
  its body runs when the ledger state is in the fail set, with the verdict
  node id and window id in the failure message. Unmarked tests are
  untouched, so the gate is explicit and never fails a suite by surprise.
- A one-line recipe in the docs: a single `test_provider_stable` gate test
  in the application suite, run in CI after `epicormic verdict`.

## 13. Package layout and dependencies

```
epicormic/
  .gitignore  .env.example  LICENSE (MIT)  README.md  CHANGELOG.md  pyproject.toml (hatchling)
  docs/PLAN.md  docs/statistics.md  docs/evidence-format.md  docs/levers.md  docs/limitations.md  docs/cli.md
  src/epicormic/
    __init__.py        # public API, lazy exports in pollard's style, __version__
    _canon.py          # documented canonical serialization (D7)
    _decimal.py        # decimal-string encoding (D6)
    panel.py           # Probe, Panel, load/save, digests
    window.py          # observe(): builds the window tree, dispatch wrapper, resumability
    observation.py     # Observation, WindowView, tree readers
    scorers.py         # Scorer protocol, Reference, built-ins, registry, ScoreTable
    mock.py            # MockProvider and Drift: seeded step callable with injectable drift (D20)
    stats/__init__.py
    stats/exact.py     # Fisher's exact test
    stats/rank.py      # Mann-Whitney U, Cliff's delta, Hodges-Lehmann
    stats/permutation.py  # stratified permutation test
    stats/multiplicity.py # Benjamini-Hochberg, Holm
    stats/sequential.py   # CUSUM, Page-Hinkley, betting e-process
    verdict.py         # AnalysisConfig, Monitor, compute + write verdicts, recompute check
    ledger.py          # Ledger, LedgerEntry, index patch handling
    levers.py          # DriftMeter, DriftPolicy, ContractGate
    opinion.py         # Subjective Logic opinion from verdict statistics (core, pure arithmetic)
    jsonld.py          # JSON-LD panel loading and verdict export through jsonld-ex (extra only)
    pytest_plugin.py   # epicormic_state fixture, epicormic_gate marker
    cli.py
  tests/  (section 14)
  examples/
    01_local_panel.py  # observe a window against epicormic.mock, print the tree
    02_inject_drift.py # baseline window, drifted window, verdict
    03_levers.py       # DriftMeter refusal node and DriftPolicy confirmation
    04_calibrate.py    # A/A run
  evidence/            # experiment outputs, added in Phase 9
```

`pyproject.toml`: `requires-python >= 3.10`, `dependencies = ["pollard>=1.6.0,<2"]`,
extras `jsonld = ["jsonld-ex>=0.7.4,<0.8"]`, `vector = ["numpy>=1.26"]`
(reserved, empty implementation in v0.1), `dev = ["pytest>=8",
"pytest-cov>=5", "hypothesis>=6.100", "ruff>=0.5", "mypy>=1.10", "build",
"twine", "scipy>=1.13", "jsonld-ex>=0.7.4,<0.8"]`, console script
`epicormic = "epicormic.cli:main"`, pytest entry point
`epicormic = "epicormic.pytest_plugin"`, ruff and mypy strict settings
copied from pollard. Version 0.1.0 at first release.

## 14. Testing requirements

pytest, `tests/` with `conftest.py` providing a `MemoryStore`-backed
runtime and the seeded mock provider. All public functions and classes have
tests; error paths and edge cases are covered. Specific obligations:

- `_canon`: property-based conformance against `pollard._canon.canonical_bytes`
  (Hypothesis strategies over the allowed value grammar), float rejection,
  non-string key rejection, non-ASCII preservation.
- `_decimal`: round-trip of representative values, 6 significant digits,
  half-even rounding, integers and booleans untouched, non-finite handling.
- `panel`: digest stability locked to a committed value; probe id
  uniqueness; sampling strings validated as decimals; JSON load and save
  round-trip.
- `window`: exact tree layout (kinds, attempts, counts, payload formats);
  identity payload equals `probe.payload`; dispatch receives merged
  sampling and seed; resumability (a second `observe` on the same window
  dispatches zero calls, counted through the mock); budget exhaustion
  produces refusal nodes and the window reports refused samples; the window
  header commits to the contract.
- `scorers`: each built-in against hand-built results for OpenAI Responses,
  Chat Completions, Anthropic Messages, and Bedrock Converse shapes as
  pollard's adapters produce them; `None` on undefined signals; the
  normalized projection matches pollard's `NormalizedModelComparator`
  outcome on the same inputs.
- `stats/exact`: Fisher p-values against published tables and a SciPy
  cross-check (skipped without SciPy).
- `stats/rank`: U, Cliff's delta, and Hodges-Lehmann against hand-computed
  small cases; exact p against enumeration; normal approximation with ties
  against SciPy (skipped without SciPy).
- `stats/permutation`: under the null (both windows drawn from the same
  seeded distribution) the rejection rate over 2000 replications at
  alpha 0.05 lies within [0.03, 0.07]; under a Cliff's delta of 0.5 with
  10 samples per side over 24 probes the rejection rate exceeds 0.9;
  stratification is verified by constructing probes with different
  locations and identical within-probe distributions, which must not
  reject.
- `stats/multiplicity`: Benjamini-Hochberg and Holm against textbook
  examples; monotonicity of adjusted p-values.
- `stats/sequential`: CUSUM and Page-Hinkley detect a step change and stay
  quiet on a null stream over seeds; the e-process exceeds `1/alpha` under
  the null on at most alpha of 2000 seeded streams at any point of the
  stream (Ville); the e-process alarms on a rate shift of 0.2 within a
  declared number of observations.
- `verdict`: each state rule fires on a constructed scenario; value-free
  scan (no substring of any provider result appears in any verdict
  payload); recompute equals the stored verdict; `contract_changed` paths.
- `ledger`: index patch honoured, stale index falls back to the chain,
  foreign node ids rejected.
- `levers`: `DriftMeter` produces a pollard refusal node with the meter
  name, reason, and audit meta, and does not fire on `warning` by default;
  `DriftPolicy` decisions per state and per `side_effects`; `ContractGate`
  acknowledged and unacknowledged paths; all three coexist with pollard's
  default meters and budgets.
- `cli`: `--help`, every subcommand end-to-end on a temporary SQLite store
  with the mock provider, exit codes.
- `jsonld` (skipped without jsonld-ex): a JSON-LD panel and its plain
  projection produce the same `panel_digest`; annotations are stripped and
  digested separately; an invalid document and a failed integrity check are
  rejected with the probe or field named; a payload that compacts to typed
  values is rejected. Verdict export: the document round-trips through
  jsonld-ex expansion and compaction without loss, `verify_integrity`
  passes on the emitted document and fails after any byte change, the
  opinion in the export equals the stored decimal components, and the
  value-free scan passes.
- `opinion`: the properties listed in section 10.1, plus agreement with
  jsonld-ex `Opinion` arithmetic on the same components (skipped without
  jsonld-ex).
- `pytest_plugin`: through `pytester`, the fixture reads a prepared store,
  the marker fails on `drift` and passes on `stable`, missing options give
  a clear error, and unmarked tests are untouched.
- Integration: A/A on the mock provider reports `stable`; each injected
  drift mode (match rate drop, length shift, latency shift, tool call swap,
  refusal rate rise) reports `drift` at the configured sample sizes.
- Installed-wheel smoke test: build the wheel, install into a clean venv,
  observe a fixed synthetic window pair, and assert the locked digests and
  statistics; run on Linux, Windows, and macOS in CI.

Coverage target: report with `pytest --cov=epicormic`; no release below
90 percent line coverage of `src/epicormic`.

## 15. Documentation set

`README.md` opens with a credential-free 90-second start on the mock
provider, in the style of pollard's README, ending with a locked digest
the reader can compare. It then states the drift definition, the four
states, the levers, the limits, and how epicormic relates to pollard's
`revalidate_model_call`. `docs/statistics.md` derives every test as in
section 8. `docs/evidence-format.md` is section 10. `docs/levers.md` is
section 11 with full `Runtime` construction examples. `docs/limitations.md`
is section 17 classified as pollard classifies its limits (provider or
backend constraint, design boundary, not implemented). Before every build,
an automated scan rejects em-dashes, en-dashes, curly quotes, and the
banned vocabulary list, kept in `tools/banned_words.txt` so the scan
never trips on its own definition, across docs and docstrings.

## 16. Experiment plan (post-release, pre-registered)

Run under the lab-runner discipline with hypotheses, seeds, environment,
and commit SHAs recorded before data collection. The local control (D11) is
present in every experiment.

- EXP-A, calibration. A/A windows on the mock provider and on the local
  control, 200 replications each at N in {5, 10, 20}. Hypothesis: the
  false alarm rate of the pooled test is within the binomial confidence
  interval of alpha; the e-process never alarms on more than alpha of
  streams.
- EXP-B, power. Injected drift on the mock provider by type (match rate,
  length, latency, tool call swap, refusal rate) and by effect size
  (Cliff's delta in {0.1, 0.2, 0.3, 0.5}) against N in {3, 5, 10, 20, 40}
  and panel size in {8, 24, 64}. Output: power surfaces with confidence
  intervals, and the smallest (N, panel) that reaches 0.8 power per type.
- EXP-C, sequential detection delay. Streams with a change point at a
  known window; compare CUSUM, Page-Hinkley, and the e-process on average
  detection delay against false alarm rate; calibrate the initial defaults
  and record the chosen values.
- EXP-D, longitudinal hosted canary. Weekly windows on hosted models from
  the four provider families of D16 through pollard's adapters, for at
  least twelve weeks, alongside the local control, under the 10 USD per
  month cap of D17. Budget arithmetic the pre-registration must reconcile:
  the default panel of 24 probes at N = 10 is 240 calls per model per
  window, roughly 144K input and 48K output tokens at 600 plus 200 tokens
  per call; on a cheap tier (order of 0.15 and 0.60 USD per million
  tokens) about 0.05 USD per model-window, on a frontier tier (order of 3
  and 15 USD per million) about 1.15 USD. Four frontier-tier models weekly
  would need about 20 USD per month, so the pre-registration picks tiers,
  N, and panel size that fit 10 USD, allocates a `Budget(usd=...)` per
  provider-window from it, and records the allocation before the first
  window. Pre-registered hypotheses: the control stays `stable`; any
  `drift` verdict on a hosted model coincides with a provider-announced
  change, a fingerprint change, or is reproduced by a second independent
  window within one week.
- EXP-E, cost and energy of monitoring. Tokens, USD, wall-clock, and NVML
  joules per window against the power reached in EXP-B: the monitoring
  efficiency frontier, connecting to the energy-aware line of work.

Publication decision follows the numbers, as with pollard.

## 17. Limitations to state up front

- Containment, not prevention: a provider can change a model at any time;
  epicormic detects the observable consequence and lets pollard refuse or
  gate, nothing more.
- A canary panel measures the panel, not production traffic; a probe set
  that does not exercise a behaviour cannot see it drift.
- Cause is not identifiable from outputs; `contract_changed` distinguishes
  declared from silent change and nothing finer.
- Power depends on N and panel size; with N below 5 most tests are
  `unknown` by design.
- The e-process plug-in baseline rate is an estimate; the anytime-valid
  guarantee is exact only in the limit of a large baseline.
- Repeated windows against one fixed baseline make per-window statistics
  dependent; CUSUM and Page-Hinkley are heuristics here and are labelled as
  such; the e-process is the guaranteed detector.
- The e-process has no effect-size floor: any systematic shift in a
  monitored rate, however small, is eventually detected (D22). Operational
  scorers such as latency therefore report real but possibly trivial
  shifts; choose the scorer set for the question being asked.
- Scorer code is caller-trusted and can leak values; built-ins do not.
- Temperature 0 does not make hosted endpoints deterministic; the design
  does not rely on determinism anywhere.
- Inherited from pollard: tamper-evident not tamper-proof, no distributed
  dispatch lock, no hosted-API energy measurement.

## 18. Build phases

Each phase ends with the full test suite passing and a conventional
commit; nothing is pushed without explicit approval.

- Phase 0, scaffold (done 2026-09-27): repository with `.gitignore` first,
  `.env.example`, LICENSE, README stub, CHANGELOG, pyproject, CI (lint,
  test, build on Linux, Windows, macOS), `_canon` with the conformance
  test, `_decimal`.
- Phase 1, observation (done 2026-09-27): `panel.py`, `window.py`,
  `observation.py`, `mock.py`, tree layout tests, resumability, budget
  refusal handling.
- Phase 2, scorers (done 2026-09-27): all built-ins with adapter-shape
  fixtures.
- Phase 3, Layer A statistics (done 2026-09-27): exact, rank, permutation,
  multiplicity, with the property tests in section 14 and SciPy
  cross-checks; derivations in docs/statistics.md.
- Phase 4, verdict, evidence, ledger (done 2026-09-27, together with
  Phase 6): `opinion.py`, `verdict.py` (configuration, monitor, chain,
  state rules, value-free evidence, recompute), `ledger.py`.
- Phase 5, levers (done 2026-09-27): `levers.py` (`DriftMeter`,
  `DriftPolicy`, `ContractGate`), `docs/levers.md`; example 03 arrives with
  the examples in Phase 8.
- Phase 6, Layer B statistics (done 2026-09-27): `stats/sequential.py`,
  chained verdicts.
- Phase 7, CLI (done 2026-09-27): all subcommands, exit codes,
  `calibrate`, `python -m epicormic`, the pytest plugin (D14), docs/cli.md;
  the JSON-LD report format is wired but deferred to the `[jsonld]` extra.
- Phase 8, release (done 2026-09-27): `jsonld.py` (D13, D18), examples 01
  to 04 with tests/test_examples.py, docs/evidence-format.md,
  docs/limitations.md, README start with locked digest, installed-wheel
  smoke test in CI, vocabulary scan, 0.1.0 (TestPyPI, PyPI, tag, GitHub
  release).
- Phase 9, experiments EXP-A to EXP-E and the paper, in a separate paper
  repository as with pollard-jev.

## 19. Open questions for Muntaser

None open as of 2026-09-27. Phases 0 to 8 are done; the next step is
Phase 9 (section 16, in a separate repository). Questions raised during
the build are appended here with a date and answered in the decision log.
