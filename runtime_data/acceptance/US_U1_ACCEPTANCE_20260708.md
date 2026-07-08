# US U1 Acceptance 2026-07-08

## Scope

U1 independent US executor config.

## Files

- `executor/us/config_us.py`
- `executor/us/signal_reader_us.py`
- `executor/us/tests/test_config_us.py`

## Evidence

- `UsExecutorConfig` is an independent frozen dataclass and does not inherit from A-share `ExecutorConfig`.
- US paths are defined inside the US package:
  - `PAPER_US_DB_PATH = runtime_data/quant/paper_us.db`
  - `disciplined_db_path = PAPER_US_DB_PATH`
  - `DSA_DB_PATH = runtime_data/dsa/stock_analysis.db`
- US trading defaults:
  - `market = "us"`
  - `stock_pool = ("AAPL", "NVDA", "MSFT", "JPM", "SPCX")`
  - `benchmark_codes = ("SPY",)`
  - `t_plus = 0`
  - `lot_size = 1`
  - `bar_available_time = "16:00"`
  - `bar_available_timezone = "America/New_York"`
  - `honor_luld = False`
- Fee defaults include `sec_fee_rate` and intentionally omit `stamp_tax_rate`.

## Verification

- `.venv/bin/python -m unittest executor.us.tests.test_config_us executor.us.tests.test_signal_reader_us`
  - `Ran 6 tests`
  - `OK`
- `grep -R -E "from executor\\.(engine|ledger|rules|models|config|time_guard|signal_reader) import|import executor\\.(engine|ledger|rules|models|config|time_guard|signal_reader)" executor/us`
  - no matches
- `git diff --stat -- executor/engine.py executor/ledger.py executor/rules.py executor/models.py executor/config.py executor/time_guard.py executor/signal_reader.py`
  - no output
- `git diff --check`
  - no output

## Guardrail

- No A-share hot-path files were edited.
- US config is not imported from, derived from, or coupled to A-share config.
