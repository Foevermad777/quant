# US U0 Acceptance 2026-07-08

## Scope

U0 market isolation guard for the US executor fork.

## Files

- `executor/us/__init__.py`
- `executor/us/signal_reader_us.py`
- `executor/us/tests/test_signal_reader_us.py`

## Preflight Evidence

- A-share disciplined store exists in `runtime_data/quant/paper.db`.
- `disciplined_signals` count: 16.
- `disciplined_signals` completed date range: `2026-07-07` to `2026-07-07`.
- `portfolio_snapshots` count: 2.
- Latest `portfolio_snapshots.snapshot_date`: `2026-07-07`.

## U0 Evidence

- `UsSignalReader.active_signals_before()` filters both stores by:
  - `market = 'us'`
  - `stock_code in stock_pool`
- The same filtered source feeds:
  - `open_candidates()`
  - `exit_candidates()`
  - `s1_conflicts()`
- Covered paths:
  - raw DSA `decision_signals`
  - G5 `disciplined_signals` in the US ledger store
  - cn/us mixed signal set with non-pool US ticker

## Verification

- `.venv/bin/python -m unittest executor.us.tests.test_signal_reader_us`
  - `Ran 3 tests`
  - `OK`
- `grep -R -E "from executor\\.(engine|ledger|rules|models|config|time_guard|signal_reader) import|import executor\\.(engine|ledger|rules|models|config|time_guard|signal_reader)" executor/us`
  - no matches
- `git diff --stat -- executor/engine.py executor/ledger.py executor/rules.py executor/models.py executor/config.py executor/time_guard.py executor/signal_reader.py`
  - no output

## Guardrail

- No A-share hot-path files were edited.
- The US reader does not import A-share executor hot-path modules at runtime.
