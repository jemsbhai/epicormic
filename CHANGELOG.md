# Changelog

All notable changes to epicormic will be documented in this file.

The format is based on Keep a Changelog, and this project follows Semantic Versioning.

## [Unreleased]

### Documentation

- README: accurate status by phase, and a credential-free example that
  observes a baseline and a drifted window on `MockProvider` and runs the
  stratified permutation test, with its recorded output.
- docs/statistics.md: derivations of every Layer A test as implemented
  (Fisher, Mann-Whitney with the exact counting recurrence and the
  tie-corrected normal approximation, Cliff's delta, Hodges-Lehmann,
  permutation and stratified permutation, Benjamini-Hochberg, Holm) and the
  Layer B and opinion specifications, with references (three verified
  against the publishers on 2026-09-27).
- docs/PLAN.md: status by phase, D19 (header identity excludes the sample
  count and version) and D20 (mock lives in the package), corrected example
  payload numbers to what the decimal encoder emits, and the refusal
  handling rule in section 6.

### Added

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

## [0.0.1] - 2026-09-27

Name-claim release. The package installs, imports, and reports its version;
it implements nothing else yet. The first functional release is 0.1.0, per
docs/PLAN.md.

### Added

- Repository scaffold: packaging, CI, vocabulary scan, and the v0.1
  specification in docs/PLAN.md.
