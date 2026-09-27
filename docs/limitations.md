# Limitations

What epicormic cannot do, what it does not claim, and what it costs.
Read this before trusting a verdict.

## What a verdict is not

**A verdict is evidence about a panel, not about a provider.** The
statistics are computed over the probes in one panel under one sampling
configuration. A stable verdict says the provider's behaviour on those
probes has not measurably changed; it says nothing about prompts the
panel does not cover, and a panel that never exercises tool use cannot
detect a change in tool use. Panel design is the user's, and the docs
carry no recommended panel because the right one depends on the
application.

**Detection is not diagnosis.** A drift verdict says a distribution
moved; it does not say why. A provider may have changed its model, its
sampling defaults, its safety filters, its tokenizer, or its
infrastructure, and a panel can also move because the panel's own inputs
depend on time (a probe that asks for today's date drifts daily).
Epicormic records the contract digest so a changed fingerprint can be
told apart from a silent change, but a silent change is only ever a
statistical fact.

**Anytime-valid tests have no effect-size floor.** The sequential
detectors (CUSUM, Page-Hinkley, the betting e-process) will eventually
alarm on any systematic shift, however small, given enough observations.
This is the correct behaviour for a monitor, but it means a scorer whose
measurements shift for reasons unrelated to the provider will alarm.
`latency_s` is the standing example: it is a real operational signal on a
hosted provider, but on a local mock its microsecond timings shift with
machine load, so calibration on the mock uses behavioural scorers only.
Choose scorers for what you want to be alarmed about.

**The per-probe and pooled tests are exact only under exchangeability.**
The permutation test and the Fisher and Mann-Whitney tests assume the
baseline and current samples of a probe are exchangeable under the null.
Samples taken minutes apart through the same endpoint usually are;
samples taken through different endpoints, regions, or accounts may not
be, and a baseline observed under load may differ from a quiet current
window for reasons the panel cannot see.

## What epicormic does not measure

**Quality.** No scorer knows whether an answer is right. The scorers
measure agreement with the baseline (exact and normalized match, tool-call
set overlap), refusal, length, and latency. A provider that becomes
better at the task will read as drift exactly as one that becomes worse.
A judge scorer (a second model grading outputs) is not included because
it would bind a monitor's verdicts to a second provider that can itself
drift.

**Semantics.** There is no embedding similarity scorer in 0.1 (the
`[vector]` extra is reserved for 0.2), and when one arrives it will depend
on an embedding model that is one more thing that can change. Normalized
match with a deterministic panel is the recommended primary signal.

**Cost and tokens beyond pollard's meters.** Token counts come from the
provider's usage report through pollard; epicormic does not tokenize.

## Statistical caveats

**Multiplicity is controlled per scorer, not across scorers.** Holm
corrects the pooled test across probes and Benjamini-Hochberg the per-
probe tests across probes, both within one scorer. Running eight scorers
gives eight families; the default state rules fire on any one of them, so
the family-wise false-alarm rate across scorers is roughly the sum. The
A/A calibration reports the observed rate for the scorer set you use;
run it (`epicormic calibrate`) before choosing thresholds.

**Small samples give the unknown state, not a weak verdict.** Probes
with fewer than `n_min` observations on either side are excluded from the
per-probe test, and when too many are excluded the verdict is `unknown`.
This is deliberate: a verdict from three samples is not evidence.

**The opinion is a summary, not a probability.** The subjective-logic
opinion is derived from the average e-process wealth across scorers with
a fixed prior weight. Its projected probability is a monotone summary of
the evidence, calibrated on nothing. Use it to rank and to fuse, not as a
frequency.

**Sequential detectors carry state across windows.** CUSUM and
Page-Hinkley statistics accumulate along the monitor's verdict chain; a
monitor that has drifted and then been re-baselined must be a new monitor
id, because a monitor's configuration and baseline are immutable.

## Operational limits

**Refusal is a runtime decision, not a rollback.** `DriftMeter` refuses
new model calls after a drift verdict; it does not undo what earlier
calls did. The containment is forward-looking.

**A monitor without a verdict reads as unknown.** The levers read the
monitor's latest verdict from the store at each call. A monitor that has
not recorded a verdict yet is `unknown`, and the defaults allow unknown;
decide explicitly whether unknown should refuse (`refuse_on`,
`on_unknown`). A store that cannot be opened raises, so a lever pointed
at an unreachable store fails the call rather than silently allowing it.

**Windows are not free.** A window of 12 probes times 10 samples is 120
provider calls under the panel's sampling configuration. The observe loop
honours pollard budgets (tokens, dollars, steps) and records a partial
window as incomplete rather than silently under-sampling.

**Stores grow.** Every observation is kept: pollard's normalized result
(text, finish reason, refusal, tool calls, usage) and its digest. Archive
or rotate whole stores; a store that has been edited no longer
recomputes, and a window with nodes removed is a different window.

**Platform determinism is for records, not for providers.** Verdict
payloads recompute byte-identically on every platform because every
number is rounded to six significant digits before it is written and the
permutation draws come from Python's seeded generator, which behaves the
same everywhere. The observations themselves depend on the provider and
will differ between windows even when nothing has changed; that is what
the tests measure.
