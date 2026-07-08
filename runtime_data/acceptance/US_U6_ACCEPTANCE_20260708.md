# US U6 Evidence 2026-07-08

## Scope

US DSA batch script plus Tavily and G5 wiring.

## Files

- `ops/run_us_dsa_daily.sh`
- `ops/run_us_g5_completion.sh`

## Wiring Evidence

- `ops/run_us_dsa_daily.sh`
  - runs `vendor/daily_stock_analysis/main.py --stocks AAPL,NVDA,MSFT,JPM,SPCX`
  - preserves the local proxy guard on `127.0.0.1:7890`
  - loads `TAVILY_API_KEYS` from `runtime_data/secrets/tavily_api_key.txt`
  - exports `STOCK_LIST` to the same US pool
  - does not log secret values
- `ops/run_us_g5_completion.sh`
  - runs `python -m executor.discipline_completion --all-active`
  - writes to `runtime_data/quant/paper_us.db` via `--store-db`
  - limits completion to the US pool via repeated `--stock-code`
  - uses `--retries 1 --retry-delay-seconds 10`
- `executor.discipline_completion --help` confirms:
  - `--store-db`
  - repeatable `--stock-code`
  - `--retries`
  - `--retry-delay-seconds`

## Verification

- `bash -n ops/run_us_dsa_daily.sh`
  - no output
- `bash -n ops/run_us_g5_completion.sh`
  - no output
- `runtime_data/secrets/tavily_api_key.txt`
  - present and non-empty

## Live Run Status

- Command attempted: `ops/run_us_dsa_daily.sh`
- Result: exited with code `75`.
- Launcher log:
  - `2026-07-08 11:23:42 +0800 proxy_check=fail host=127.0.0.1 port=7890 action=exit`
- Current DSA `decision_signals` market counts after the attempt:
  - `cn|23`
- Current US DSA state after the attempt:
  - no `AAPL/NVDA/MSFT/JPM/SPCX` rows in `decision_signals`
  - no `AAPL/NVDA/MSFT/JPM/SPCX` rows in `stock_daily`
- Current US ledger state after the attempt:
  - `runtime_data/quant/paper_us.db` missing

## Status

- Code wiring is complete.
- Live U6 acceptance is blocked until the local proxy at `127.0.0.1:7890` is online.
- No DSA US batch, Tavily news evidence, or US G5 output was produced in this run.

## Guardrail

- `executor/discipline_completion.py` was not edited.
- No A-share hot-path files were edited.
