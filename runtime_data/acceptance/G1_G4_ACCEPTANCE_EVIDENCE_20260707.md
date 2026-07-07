# G1-G4 Acceptance Evidence 2026-07-07

## Scope

Source task file: `REDTEAM_TASKS_G1_G4.md`.

Implemented without editing DSA source under `vendor/daily_stock_analysis/src`.

## G1 Fill Model

Implemented:

- Default fill model is `next_open`.
- Legacy model remains available as `limit_entry_high` for A/B comparison.
- Buy-side slippage uses `open_slippage_multiplier=2.0`; sell-side remains single slippage.
- Unit fixture verifies `10.5 * (1 + 0.001 * 2) = 10.521` for buy execution price.

Temp-ledger A/B smoke against current DSA DB for 2026-07-06 to 2026-07-07:

```text
model=next_open stats={'analysis_count': 7, 'bars': 5, 'data_gaps': 0, 's1_conflicts': 1, 'open_candidates': 0, 'exit_candidates': 0, 'filled': 0, 'unfilled': 0, 'blocked': 0, 'pending_exits': 0, 'sells': 0} buy_fills=0 attempts=[]
model=limit_entry_high stats={'analysis_count': 7, 'bars': 5, 'data_gaps': 0, 's1_conflicts': 1, 'open_candidates': 0, 'exit_candidates': 0, 'filled': 0, 'unfilled': 0, 'blocked': 0, 'pending_exits': 0, 'sells': 0} buy_fills=0 attempts=[]
```

Current live DB has no open candidates in that window, so real-data bias delta is not measurable yet. Unit tests cover the divergence: default `next_open` fills when open is above `entry_high`; `limit_entry_high` leaves the same setup unfilled.

## G2 Discipline Skill

Implemented:

- Added `dsa_skills/discipline.yaml`.
- Updated `vendor/daily_stock_analysis/.env` keys only:
  - `AGENT_SKILLS=discipline`
  - `AGENT_SKILL_DIR=/Users/yongyuanbuanzhede/quant/dsa_skills`

DSA venv skill activation probe:

```text
active=discipline
has_data_trace=True
has_invalid_conditions=True
has_base_bull_bear=True
```

Live 5-stock LLM cost/time run was not executed in this pass to avoid spending API budget during code implementation. This remains the external acceptance probe for Claude/CEO if approved to spend.

## G3 Time Guardrail

Implemented:

- Added `executor.time_guard` with decision timestamp, predicted bar availability, and news attribution classification.
- `ops/weekly_review.py` now renders a `时点校验` section with columns for `decision_timestamp`, `bar_available_at`, `news_published_at`, `attribution_status`, and `reason`.
- Unit test constructs a 2026-07-06 15:30 post-market news item and verifies it is marked `excluded_after_bar_available` with reason `published_after_predicted_bar_available`.

## G4 Wrapper Validator

Implemented:

- Added `executor.guardrails.gate_dsa_output`.
- Added design doc: `executor/DSA_WRAPPER_DESIGN.md`.
- Validator rejects or degrades DSA payloads missing:
  - source attribution;
  - invalid conditions;
  - Base/Bull/Bear scenarios.
- Gate reasons are recorded in the returned signal payload under `guardrail`.

## Test Evidence

Command:

```text
/Users/yongyuanbuanzhede/quant/.venv/bin/python -m unittest discover executor/tests
```

Result:

```text
Ran 42 tests in 0.064s
OK
```

Note: running with system Python 3.9 fails on pre-existing `float | None` syntax in tests; project venv is required.
