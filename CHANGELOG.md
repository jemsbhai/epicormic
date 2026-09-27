# Changelog

All notable changes to epicormic will be documented in this file.

The format is based on Keep a Changelog, and this project follows Semantic Versioning.

## [Unreleased]

## [0.1.0] - 2026-09-27

First functional release: Phases 0 to 8 of docs/PLAN.md.

### Added

- `epicormic.jsonld` (the `[jsonld]` extra, D13 and D18): panels as
  JSON-LD documents under the epicormic vocabulary (`panel_to_jsonld`,
  `panel_from_jsonld`, `load_panel_jsonld`, shape-validated with jsonld-ex,
  same digest as the plain document); verdict export (`verdict_to_jsonld`,
  alias `export_verdict`) with the verdict node and the window roots linked
  as `urn:pollard:node:` URNs, the state annotated with confidence, source,
  method, extraction time and `@humanVerified` false, a jsonld-ex `Opinion`
  beside the recorded opinion, and an integrity digest checked by
  `verify_verdict_jsonld`; `validate_verdict_jsonld`; PROV-O output
  (`verdict_to_prov_o`); cumulative and averaging fusion of verdict
  opinions (`fuse_opinions`). Importing the module without the extra raises
  `ImportError` naming it.
- `epicormic report --format jsonld` writes a JSON-LD `Report` document
  whose `latest` entry is the exported verdict; `--prov-o` writes its
  PROV-O graph. Both exit 1 without the extra.
- `examples/01_observe.py`, `02_verdict.py`, `03_levers.py`, `04_cli.py`:
  credential-free walkthroughs on the mock provider, run by
  `tests/test_examples.py`.
- CI: an installed-wheel smoke job that builds the sdist and wheel,
  installs the wheel into a clean environment, and checks the import, the
  console script, the pytest plugin entry point, and an example.

### Documentation

- docs/evidence-format.md: every record epicormic writes, field by field
  (panel document, window root and header, probe marker, observation,
  monitor root and header, verdict, opinion, lever refusals, report).
- docs/limitations.md: what a verdict is not, what is not measured, the
  statistical caveats, and the operational limits.
- README: the ninety-second start on the mock with locked output, the drift
  definition, the four states, the levers, the relation to pollard's
  `revalidate_model_call`, and the extras.
- docs/PLAN.md: Phase 8 done; section 10.2 records the implemented names
  and the `@source` URN.

### Changed

- Version 0.1.0; development status Alpha.
- Window headers record their creation ordinal (`epicormic.header_index`
  in the header's metadata, never in its identity), and
  `find_window_headers` orders by it. It ordered by `created_at` before,
  which is arbitrary whenever two headers share a timestamp, since every
  pollard store lists children by kind and id; on Windows, whose clock is
  coarse enough for two observes to collide, the verdict could judge the
  wrong header of a window whose contract had changed. Headers without an
  ordinal sort after those with one, by `created_at` then id.
- `find_window_root` is defined in `epicormic.window` (still exported from
  `epicormic.observation` and `epicormic`).
- mypy no longer follows numpy (pytest and jsonld-ex import it; epicormic
  does not use it): the numpy stubs shipped for Python 3.14 use the `type`
  statement, which mypy rejects under the 3.10 grammar the project checks
  with, and that failed the 3.14 CI jobs.

### Added in Phases 0 to 7

- `epicormic.cli` and `python -m epicormic`: `panel digest`, `observe`
  (hybrid, resumable, budgets, replay contract document), `verdict`
  (records to the chain, `--recompute`), `calibrate` (A/A windows),
  `status`, and `report` (JSON; JSON-LD wired for the `[jsonld]` extra),
  with exit codes 0 stable, 2 warning, 3 drift, 4 unknown, 1 error. Step
  callables come from `--fn module:attribute`; the CLI never constructs
  provider clients. `epicormic.mock.step` is a ready-made mock callable.
- pytest plugin (`pytest11` entry point): `--epicormic-store`,
  `--epicormic-monitor`, `--epicormic-panel-digest`, `--epicormic-fail-on`,
  the session-scoped `epicormic_state` fixture, and the `epicormic_gate`
  marker that fails a test in its call phase when the monitor is in a
  failing state.
- docs/cli.md with a CI recipe.
- `epicormic.levers`: `DriftMeter` (a pollard meter that refuses dispatch
  through `MeterPrecheckRefusal` while the monitor is in a listed state,
  with audit metadata naming the verdict), `DriftPolicy` (a pollard policy
  mapping the state to ALLOW, CONFIRM, or DENY for side-effectful tools),
  and `ContractGate` (a meter refusing model calls bound to an unexpected
  replay contract unless its digest is acknowledged, with the differing
  JSON pointer paths). `epicormic.verdict.contract_digest` and
  `difference_paths` are public for acknowledgement workflows.
- docs/levers.md: the three levers with full `Runtime` construction
  examples and the hybrid fallback recipe.
- `epicormic.verdict`: `AnalysisConfig` (decimal-string parameters,
  digested), `Monitor` (panel, pooled baseline windows, configuration,
  monitor chain), `compute_verdict` (Layer A per probe and pooled with
  Benjamini-Hochberg and Holm, Layer B over the recorded history, the
  section 9 state rules, contract change detection with JSON pointer
  paths, value-free decimal-encoded evidence with top-k truncation),
  `record_verdict` (appends to the chain, refuses stale sequence indices,
  patches the ledger index), `recompute_verdict` (re-derives a recorded
  verdict over the chain prefix that preceded it and reports whether the
  payload matches), and `analyze`.
- `epicormic.opinion`: the Subjective Logic opinion from the averaged
  e-value and the observations consumed, overflow-safe, matching the
  worked example in docs/PLAN.md.
- `epicormic.ledger`: `Ledger.state` and `Ledger.history`, index pointer
  verified against the chain (existence, verdict format, last link,
  descent from the monitor root) with the chain as fallback.
- `epicormic.stats.sequential` (Layer B, pure Python): two-sided CUSUM and
  Page-Hinkley on per-window statistics, and the anytime-valid betting
  e-process for indicator streams (grid of stake fractions, log-space
  wealth, finite `log_e_value` when the e-value overflows, first-crossing
  alarm index), with `sign_transform` for scalar scorers and
  `clipped_rate` for degenerate baseline rates. Tests check Ville's bound
  on 1,200 seeded null streams, detection delay under a rate shift, and
  agreement with the direct product on short streams.
- `epicormic.stats` (Layer A, pure Python): Fisher's exact test with risk
  difference and Haldane log odds ratio; Mann-Whitney U with an exact null
  distribution for untied samples up to 20 per side and the tie-corrected
  normal approximation otherwise, plus Cliff's delta and the Hodges-Lehmann
  shift; two-sample permutation tests (exact enumeration up to 20,000
  assignments, seeded Monte Carlo beyond); the stratified permutation test
  on mean per-probe Cliff's delta, permuting within probes through rank
  shuffling; Benjamini-Hochberg and Holm adjustments. Cross-checked against
  SciPy to 1e-10 over random inputs, with null calibration, power, and
  stratification tests.
- `epicormic.scorers`: the `Scorer` protocol, twelve built-in value-free
  scorers (`normalized_match`, `exact_match`, `tool_call_set_jaccard`,
  `tool_call_count`, `refusal`, `truncated`, `output_chars`,
  `output_tokens`, `input_tokens`, `latency_s`, `cost_usd`, `energy_j`),
  per-probe `Reference` summaries built from baseline windows, `ScoreTable`,
  and `resolve_scorers` with caller-supplied scorers. The normalized
  projection reproduces pollard's comparator semantics with a
  property-based conformance test; finish-reason detection covers the
  OpenAI Chat and Responses, Anthropic Messages, Bedrock Converse, and
  plain `finish_reason` result shapes.
- `epicormic.window.observe`: records N samples of every probe in one
  pollard run using the D5 layout (window header, probe branch anchors,
  probe headers, sample branch anchors, model calls), in hybrid mode so
  reruns serve existing samples and dispatch only the missing ones;
  requested sample count and epicormic version live in header metadata,
  never in identity; budget and meter refusals are recorded per probe and
  resumable; per-sample callbacks report served, dispatched, or refused.
- `epicormic.observation`: `read_window`, `find_window_headers`, and
  `find_window_root` turn a stored window back into `Observation` records
  per probe in probe and attempt order, with refusal ids.
- `epicormic.mock.MockProvider` and `Drift`: a seeded, credential-free step
  callable with injectable drift (match rate, length, latency, refusals,
  tool swap, truncation), used by tests and available to users (this lives
  in the package rather than under examples/ as docs/PLAN.md section 13
  first listed it, so tests and the CLI can share it).
- `epicormic.panel`: `Probe` and `Panel` with strict validation, document
  and JSON file round-trips, domain-separated digests (`epicormic/probe/v1`,
  `epicormic/panel/v1`), decimal-string sampling parameters decoded only in
  the dispatched request, and per-attempt seeding (section 5.2).
- Lazy public exports on `epicormic` with a `TYPE_CHECKING` block for static
  analysis.
- `epicormic._canon`: the documented pollard canonical identity
  serialization (`canonical_bytes`, `validate_identity_value`) and
  domain-separated SHA-256 digests (`domain_digest`), with a property-based
  conformance test against pollard 1.6.0 (D7).
- `epicormic._decimal`: fixed-precision decimal-string encoding for
  evidence numbers (`decimal_str`, `parse_decimal_str`, `encode_evidence`),
  six significant digits, half-even rounding, plain notation, non-finite
  values reported by path (D6).

### Changed in Phases 0 to 7

- The null rate of the sign-transformed e-process stream for scalar scorers
  is the baseline's above-median rate among non-ties, not one half (D21):
  with one half, a discrete score such as output length alarmed on nothing.
- The opinion excludes scorers whose e-process stream carried no
  observations from both the average and the minimum.

### Documentation in Phases 0 to 7

- README: accurate status by phase, and a credential-free example that
  observes a baseline and a drifted window on `MockProvider` and runs the
  stratified permutation test, with its recorded output.
- docs/statistics.md: derivations of every Layer A test as implemented
  (Fisher, Mann-Whitney with the exact counting recurrence and the
  tie-corrected normal approximation, Cliff's delta, Hodges-Lehmann,
  permutation and stratified permutation, Benjamini-Hochberg, Holm), Layer
  B and the opinion as implemented including the D21 correction and the
  absence of an effect-size floor in the e-process (D22), with references
  (three verified against the publishers on 2026-09-27).
- docs/PLAN.md: status by phase, D19 (header identity excludes the sample
  count and version), D20 (mock lives in the package), D21, D22, corrected
  example payload numbers to what the decimal encoder emits, the refusal
  handling rule in section 6, and the limitation on operational scorers.

## [0.0.1] - 2026-09-27

Name-claim release. The package installs, imports, and reports its version;
it implements nothing else yet. The first functional release is 0.1.0, per
docs/PLAN.md.

### Added

- Repository scaffold: packaging, CI, vocabulary scan, and the v0.1
  specification in docs/PLAN.md.
