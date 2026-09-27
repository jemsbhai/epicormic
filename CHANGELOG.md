# Changelog

All notable changes to epicormic will be documented in this file.

The format is based on Keep a Changelog, and this project follows Semantic Versioning.

## [Unreleased]

### Added

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
