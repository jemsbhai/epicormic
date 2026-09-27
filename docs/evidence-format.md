# Evidence format

Everything epicormic records is a pollard node: content-addressed,
append-only, and readable by any pollard tool. This page lists every
record epicormic writes, field by field, so that a reviewer can read a
store without epicormic installed and so that a future version knows what
it must keep reading. Every format string carries a version suffix; a
change to a record's fields changes the suffix.

Numbers in analysis records are decimal strings with six significant
digits (`"0.0348259"`), never floats, so a record digests the same on
every platform; counts are integers. Analysis records (verdicts,
reports) never contain provider output text, only digests and derived
scores; the observations keep pollard's normalized result.

## Panel document `epicormic/panel/v1`

A panel is a JSON file (`save_panel`, `load_panel`). Its digest is the
SHA-256 of pollard's canonical JSON of this document with the fields
below in this order; `Panel.digest` and `epicormic panel digest` compute
it, and it is the first sixteen hex characters of the window and monitor
labels.

| Field | Meaning |
| --- | --- |
| `format` | `epicormic/panel/v1`. |
| `name` | Free text; part of the digest. |
| `probes` | Ordered list of probe objects; order is part of the digest. |
| `probes[].probe_id` | Unique within the panel. |
| `probes[].payload` | The identity payload: the exact model-call payload recorded for every sample of this probe. |
| `probes[].tags` | List of strings (may be empty). |
| `probes[].family` | Optional string grouping related probes. |
| `sampling` | Object of decimal strings (`{"temperature": "0.7"}`) added to every request; part of the digest. |
| `seed_from_attempt` | When true, the request for attempt *k* carries `seed_field = seed_base + k`. |
| `seed_base` | Integer, default 0. |
| `seed_field` | Request field name for the seed, default `seed`. |

A JSON-LD form of the same document exists under the `[jsonld]` extra
(`panel_to_jsonld`, `panel_from_jsonld`); stripping its `@` keywords
yields exactly this document, so both forms have the same digest.

## Window

One observation window is one pollard run root labelled
`epicormic/<panel digest, 16 hex>/<window_id>`. Its first child is the
header note; the observations hang below it in one branch per probe, one
branch per sample.

```
ROOT  {"run": "epicormic/<panel16>/<window_id>"}
  |-- NOTE  epicormic/window/v1                     (header)
  |-- NOTE  {"branch": true}  attempt = probe index
  |     |-- NOTE  epicormic/probe/v1                (probe marker)
  |     |-- NOTE  {"branch": true}  attempt = sample index
  |     |     `-- MODEL_CALL  attempt = sample index (observation)
  |     `-- ...
  `-- ...
```

### Header note `epicormic/window/v1`

| Field | Meaning |
| --- | --- |
| `format` | `epicormic/window/v1`. |
| `panel_digest` | Full panel digest. |
| `panel_name` | Copied from the panel. |
| `window_id` | The window label. |
| `contract` | The `ReplayContract` document the window was observed under, or null. |
| `sampling`, `seed_from_attempt`, `seed_base`, `seed_field` | Copied from the panel so a reader can rebuild every request without the panel file. |

Node meta: `created_at`, and under an `epicormic` key `samples_requested`,
`epicormic_version`, and `header_index`, the header's ordinal among the
window's headers (0 for the first). A window gains a second header only
when the declared contract changes; `find_window_headers` orders headers by
this ordinal, because every pollard store lists children by kind and id,
never by creation, and a verdict judges the latest header of the current
window.

### Probe marker note `epicormic/probe/v1`

`probe_id`, `probe_digest` (SHA-256 of the canonical probe object) and
`index` (position in the panel). It sits in the probe branch beside the
sample branches.

### Observation (`model_call` node)

A pollard model-call node written by pollard's runtime, not by
epicormic:

| Field | Meaning |
| --- | --- |
| `attempt` | Sample index within the probe. |
| `payload` | The probe's identity payload, exactly as in the panel. The request actually sent adds the sampling parameters and, when enabled, the seed; those are derived from the header and the attempt index, never recorded per call. |
| `result` | Pollard's normalized result: `text`, `finish_reason`, `refusal`, `tool_calls`, `usage`. |
| `result_digest` | SHA-256 of the canonical result. |
| `meta` | `created_at`, `duration_s`, `charges`, `usage` from pollard's meters. |

Refused samples appear as pollard refusal nodes in the sample branch
(reason and audit meta from the refusing meter); samples that were never
attempted because the probe's budget was exhausted leave no node and are
counted in the window report.

## Monitor

A monitor is a run root labelled
`epicormic-monitor/<panel digest, 16 hex>/<monitor_id>`. Its first child
is the monitor header; every verdict is a note appended as a child of the
previous verdict (the first verdict's parent is the header), so the
verdict chain is a single path and the sequence index is the depth.

```
ROOT  {"run": "epicormic-monitor/<panel16>/<monitor_id>"}
  `-- NOTE  epicormic/monitor/v1                    (header)
        `-- NOTE  epicormic/verdict/v1   sequence_index 1
              `-- NOTE  epicormic/verdict/v1   sequence_index 2
                    `-- ...
```

### Monitor header note `epicormic/monitor/v1`

| Field | Meaning |
| --- | --- |
| `format` | `epicormic/monitor/v1`. |
| `panel_digest`, `monitor_id` | Identity. |
| `baseline_window_ids` | The baseline windows, in order. |
| `baseline_roots` | Their root node ids at the time the monitor was created. |
| `config` | The analysis configuration document (`epicormic/config/v1`). |
| `config_digest` | SHA-256 of the canonical configuration under the domain `epicormic/config/v1`. |

A monitor's configuration cannot change: a second header, or a verdict
whose configuration digest differs from the header's, is refused. A new
configuration is a new monitor id.

Root meta (`epicormic.latest_verdict`, `epicormic.state`,
`epicormic.window_id`, `epicormic.sequence_index`) is an index the ledger
verifies against the chain before trusting; it is a convenience, not
evidence.

### Verdict note `epicormic/verdict/v1`

| Field | Meaning |
| --- | --- |
| `format` | `epicormic/verdict/v1`. |
| `event` | `epicormic_verdict`, the vocabulary token for log consumers. |
| `monitor_id`, `panel_digest`, `config_digest` | Identity. |
| `epicormic_version` | The version that computed the verdict. |
| `baseline_roots`, `baseline_header_ids` | The baseline windows read. |
| `current_root`, `current_header_id`, `current_window_id` | The window judged. |
| `sequence_index` | 1 for the first verdict of the monitor, then +1 per verdict. |
| `state` | `stable`, `warning`, `drift`, or `unknown`. |
| `rule_fired` | The section-9 rule that produced the state, in words, or `no rule fired`. |
| `contract_changed` | Whether the current window's replay contract differs from the baseline's. |
| `contract_difference_paths` | JSON pointers to the differing contract fields. |
| `sample_counts` | `baseline`, `current`, `current_refused`, `probes_below_n_min`. |
| `scorers` | One entry per scorer, described below. |
| `opinion` | The subjective-logic opinion, described below. |
| `non_finite_fields` | JSON pointers to fields that overflowed (`inf`) or were undefined (`nan`) and were written as null. |

Each `scorers.<name>` entry:

| Field | Meaning |
| --- | --- |
| `kind` | `binary` or `scalar`. |
| `pooled` | The stratified permutation test: `T` (mean over probes of Cliff's delta, current against baseline; negative when the current window scores lower), `p`, `p_holm`, `permutations`, `seed`, `probes`, `fraction_effect_ge_min`, `rejected`. |
| `per_probe` | Per-probe entries sorted by absolute effect: `probe_id`, `n_b`, `n_c`, `method` (`fisher_exact` or `mann_whitney`), `effect`, `shift` (Hodges-Lehmann, scalar scorers only), `p`, `p_bh`, `rejected`. Truncated to `top_k` entries; rejected entries are always kept. |
| `per_probe_truncated` | Whether truncation dropped entries. |
| `probes_below_n_min` | Probes with too few observations for the per-probe test. |
| `sequential.cusum` | `s_plus`, `s_minus`, `k`, `h`, `windows`, `alarm`. |
| `sequential.page_hinkley` | `excursion`, `delta`, `lambda`, `windows`, `alarm`. |
| `sequential.eprocess` | `e`, `log_e`, `max_e`, `threshold`, `observations`, `mu0`, `baseline_observations`, `ties_excluded`, `alarm`, `alarm_index`. |

The `opinion` object (`epicormic/opinion/v1`): `source`
(`eprocess-average/v1`), `observations`, `prior_weight`, `base_rate`,
`belief`, `disbelief`, `uncertainty`, `projected_probability`. The
JSON-LD export (`verdict_to_jsonld`) adds a jsonld-ex `Opinion` beside
these numbers, re-deriving uncertainty as 1 - belief - disbelief because
jsonld-ex requires exact additivity and the recorded strings are rounded.

### Recomputation

`recompute_verdict(store, monitor, verdict_node_id)` re-derives a
recorded verdict from the observations and configuration alone and
reports whether the result is byte-identical to the record. It reads the
verdict chain only up to the verdict being recomputed, so a verdict
recorded before later observations were appended still recomputes
exactly. `epicormic verdict --recompute` does the same from the command
line.

## Refusals and audits written by the levers

`DriftMeter` refuses with reason `epicormic_drift` and audit meta
`monitor_id`, `panel_digest`, `state`, `verdict_node_id`, `window_id`,
`sequence_index`. `ContractGate` refuses with reason `contract_changed`
and audit meta `expected_digest`, `live_digest`, `difference_paths`.
`DriftPolicy` writes pollard's own confirmation and denial records. All
three appear in the application's store, not the monitoring store, as
ordinary pollard refusal, confirmation and denial nodes.

## Report `epicormic/report/v1`

`epicormic report` writes `monitor_id`, `latest` (the latest verdict
payload), `latest_created_at`, and `history` (one entry per verdict:
`sequence_index`, `window_id`, `state`, `verdict_node_id`,
`computed_at`). With `--format jsonld` the latest verdict is wrapped as a
JSON-LD document under the epicormic vocabulary (`urn:epicormic:vocab:`)
with jsonld-ex provenance annotations; with `--prov-o` the report is the
PROV-O graph of that document instead.
