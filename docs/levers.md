# Levers

Detection alone alerts a person. epicormic's levers act: they drive
pollard's fail-closed hooks from the drift state that the ledger reports,
so a drifted provider is refused, gated, or contained by the same runtime
that governs every other step. This document is docs/PLAN.md section 11 as
implemented in `epicormic.levers`.

Every lever is constructed by the application, reads the ledger on each
decision, and is passed into `pollard.Runtime`. None of them caches the
state, so a new verdict takes effect on the next step. A ledger lookup
that fails (an ambiguous monitor id across panels, a branching chain)
raises normally, because that is a configuration or storage error rather
than a governance decision; a monitor with no verdict yet has the state
`unknown`, which every lever allows by default.

## The state a lever sees

`Ledger(store).state(monitor_id, panel_digest)` returns the latest verdict
of the monitor: one of `stable`, `warning`, `drift`, `unknown`, with the
verdict node id, the window id, and the sequence index. Pass
`panel_digest` when the same monitor id exists for several panels; with a
single panel the id alone is enough.

## DriftMeter

A pollard `Meter`. Before a `model_call` (by default) is dispatched, the
meter reads the ledger and, if the state is in `refuse_on` (default
`("drift",)`), raises `MeterPrecheckRefusal`. pollard turns that into a
refusal node with `meter = "epicormic"`, `reason = "epicormic_drift"`, and
audit metadata naming the monitor, panel digest, state, verdict node id,
window id, and sequence index, then raises `BudgetExceeded` to the caller.
The meter charges nothing.

```python
from pollard import Runtime
from pollard.meters import DepthMeter, StepMeter, TokenMeter, WallClockMeter

from epicormic import DriftMeter, Ledger

ledger = Ledger(monitoring_store)
meter = DriftMeter(ledger, "weekly", panel_digest=panel.digest)

runtime = Runtime(
    application_store,
    meters=[StepMeter(), DepthMeter(), WallClockMeter(), TokenMeter(), meter],
)
```

Passing `meters=` replaces pollard's defaults, so list them explicitly as
above; the drift meter is appended, never substituted. Options:
`refuse_on=("drift", "warning")` to refuse earlier, `("unknown",)` to
refuse until a first verdict exists, `kinds=("model_call", "tool_call")`
to cover tools as well, and `name` for the meter name that appears in
refusal nodes. `meter.current_state()` returns what the meter would act
on.

## DriftPolicy

A pollard `Policy` for registered tool calls. It maps the state to a
`Decision`: by default `drift` gives `CONFIRM`, everything else `ALLOW`,
and only side-effectful actions (`ActionSpec.side_effects` true) are
considered. A `CONFIRM` makes pollard raise `ConfirmationRequired` with a
resume token; the application resumes with `run.confirm(token)` once a
person has approved. A `DENY` records a policy refusal and raises
`PolicyViolation`.

```python
from pollard import Decision, Registry, Runtime

from epicormic import DriftPolicy, Ledger

policy = DriftPolicy(
    Ledger(monitoring_store),
    "weekly",
    panel_digest=panel.digest,
    on_drift=Decision.CONFIRM,
    on_warning=Decision.ALLOW,
    on_unknown=Decision.ALLOW,
    on_stable=Decision.ALLOW,
    side_effects_only=True,
)

runtime = Runtime(application_store, registry=Registry([send, read]), policies=[policy])

with runtime.run("support-ticket") as run:
    run.tool_call("read", {"id": 7})            # no side effects: allowed during drift
    try:
        run.tool_call("send", {"to": "customer"})
    except ConfirmationRequired as pending:
        # a person reviews; then
        run.confirm(pending.resume_token)
```

## ContractGate

A pollard `Meter` that guards the application's own model calls against
an undeclared change of execution fingerprint. The application binds a
`ReplayContract` into each payload with `contract.bind(payload)`, as
pollard's revalidation flow does; the gate compares the bound contract's
digest with the expected one and refuses on a mismatch, with
`reason = "contract_changed"` and the JSON pointer paths that differ, for
example `["/model_revision"]`. Payloads with no bound contract pass.

A declared change is acknowledged by listing the new contract's digest,
which `epicormic.verdict.contract_digest(contract.to_dict())` computes:

```python
from pollard import ReplayContract, Runtime
from pollard.meters import StepMeter

from epicormic import ContractGate
from epicormic.verdict import contract_digest

expected = ReplayContract(provider="openai", model_revision="2026-06-01")
migrated = ReplayContract(provider="openai", model_revision="2026-09-01")

gate = ContractGate(expected, acknowledged={contract_digest(migrated.to_dict())})
runtime = Runtime(application_store, meters=[StepMeter(), gate])
```

Canary probes are unbound by design (docs/PLAN.md, section 5.2): the
window header commits to the contract, so the gate is for the
application's calls, not for observation.

## Hybrid fallback, a recipe rather than code

pollard's hybrid mode serves a recorded result when a request's identity
already exists and dispatches otherwise. An application whose flows are
deterministic can therefore run in `mode="hybrid"` against a last-known-good
recording while a monitor reads `drift`: identical requests keep being
served from the recording, only novel requests reach the provider, and
`DriftMeter` refuses those. This helps exactly for requests whose identity
already exists in the recording and for nothing else; it is containment of
a known surface, not a substitute for the provider.

## What the levers do not do

They do not change a verdict, they do not consult the opinion (docs/PLAN.md,
section 10.1), and they do not prevent the provider from changing. They
make a drifted provider's effect on the application explicit, auditable,
and stoppable, which is the containment epicormic promises.
