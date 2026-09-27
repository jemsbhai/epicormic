# epicormic

Provider drift detection and containment for pollard-governed AI systems.

Epicormic shoots sprout from dormant buds after a tree is pollarded: growth
that reappears after the cut. epicormic watches for model behaviour that
reappears, or changes, after a baseline has been pinned.

Status: pre-release, under construction. The 0.0.1 release on PyPI is a
name claim: it installs and reports its version and nothing more. The
specification is [docs/PLAN.md](docs/PLAN.md), and the first functional
release will be 0.1.0.

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
