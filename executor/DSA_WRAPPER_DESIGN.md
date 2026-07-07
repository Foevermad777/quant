# G4 DSA Wrapper Design

## Boundary

DSA remains read-only. The wrapper lives under `executor/` and treats each DSA analysis result as an input payload. It returns a guarded signal payload plus gate metadata before any future executor ingestion step writes or acts on the signal.

## Interface

Input: a mapping produced from a DSA analysis or decision signal extraction result.

Output: `GuardrailResult` with:

- `accepted`: whether the signal may continue to storage/execution.
- `signal`: copied signal payload with a `guardrail` block appended.
- `gate_reasons`: machine-readable rejection/degradation reasons.
- `confidence_before` and `confidence_after`.
- `action`: `pass`, `reject`, or `degrade`.

Primary entrypoint: `executor.guardrails.gate_dsa_output(payload, mode="reject")`.

## First Validator

`DisciplineOutputValidator` enforces the hard fields that G2 only asks the model to produce:

- data source attribution via `sources`, `citations`, `data_sources`, `source_attribution`, or evidence items with source/url/published time;
- invalidation via `invalid_conditions`, `invalidation`, or `invalidations`;
- Base/Bull/Bear scenarios via `scenarios`, `scenario_analysis`, or explicit `*_scenario` / `*_case` fields.

Missing fields generate one or more gate reasons:

- `missing_data_source_attribution`
- `missing_invalid_conditions`
- `missing_base_bull_bear_scenarios`

## Gate Modes

`reject` is the default for baseline measurement: missing required discipline fields stop the signal from entering the executor path.

`degrade` is available for a softer launch: the signal remains accepted, confidence is reduced, and the same gate reasons are recorded for audit.

## Next Integration Point

After Claude accepts this validator, the next step is wiring it between DSA signal extraction and executor ingestion/backfill. The wrapper should record `guardrail` metadata alongside the signal so weekly review can separate model quality failures from market outcome failures.
