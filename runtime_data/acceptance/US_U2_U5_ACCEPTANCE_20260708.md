# US U2-U5 Acceptance 2026-07-08

## Scope

US rules, fee/time models, T+0 ledger, and isolated engine fork.

## Files

- `executor/us/rules_us.py`
- `executor/us/models_us.py`
- `executor/us/time_guard_us.py`
- `executor/us/ledger_us.py`
- `executor/us/engine_us.py`
- `executor/us/tests/test_us_executor_components.py`

## Evidence

- Rules:
  - `round_lot_shares()` defaults to `lot_size=1`.
  - `T0Position.closable == quantity`.
  - A-share涨跌停 helpers are not present in `rules_us.py`.
- Fees:
  - `UsFeeModel` has commission plus sell-side SEC fee.
  - Buy-side SEC fee is zero.
  - No stamp-tax field or method exists in US fee model/config.
- Time:
  - `bar_available_at()` uses `16:00 America/New_York` through stdlib `zoneinfo`.
- Ledger:
  - `UsPaperLedger` writes the same table family to the configured US DB.
  - Buy sets `old_quantity = quantity`, making shares immediately sellable.
  - Sell checks total quantity, not T+1 old quantity.
- Engine:
  - `UsPaperEngine` imports only US package modules.
  - No limit-up/limit-down open gates are present.
  - Same-day stop/take-profit is checked immediately after a T+0 buy.
  - Test case covers a large gap open that still fills, then same-day stop sells.

## Verification

- `.venv/bin/python -m unittest discover executor/us/tests`
  - `Ran 11 tests`
  - `OK`
- `.venv/bin/python -m unittest discover executor/tests`
  - `Ran 48 tests`
  - `OK`
- `grep -R -E "from executor\\.(engine|ledger|rules|models|config|time_guard|signal_reader) import|import executor\\.(engine|ledger|rules|models|config|time_guard|signal_reader)" executor/us`
  - no matches
- `git diff --stat -- executor/engine.py executor/ledger.py executor/rules.py executor/models.py executor/config.py executor/time_guard.py executor/signal_reader.py`
  - no output
- `rg -n "is_limit|limit_price|limit_rate|same_day_stop_pending|block_limit|stamp_tax_rate|LimitFillModel" executor/us`
  - only `executor/us/tests/test_config_us.py` asserts `stamp_tax_rate` is absent
- `git diff --check`
  - no output

## Guardrail

- No A-share hot-path files were edited.
- The US fork remains physically isolated from A-share executor runtime modules.
