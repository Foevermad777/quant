#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/Users/yongyuanbuanzhede/quant"
PYTHON_BIN="${PROJECT_DIR}/.venv/bin/python"
PAPER_US_DB="${PROJECT_DIR}/runtime_data/quant/paper_us.db"
US_STOCKS="${US_STOCKS:-AAPL,NVDA,MSFT,JPM,SPCX}"

IFS=',' read -r -a STOCK_ARRAY <<< "${US_STOCKS}"
STOCK_ARGS=()
for stock in "${STOCK_ARRAY[@]}"; do
  stock="${stock//[[:space:]]/}"
  if [[ -n "${stock}" ]]; then
    STOCK_ARGS+=(--stock-code "${stock}")
  fi
done

if [[ ${#STOCK_ARGS[@]} -eq 0 ]]; then
  echo "No US stocks configured" >&2
  exit 64
fi

exec /usr/bin/caffeinate -i "${PYTHON_BIN}" -m executor.discipline_completion \
  --all-active \
  --market us \
  --store-db "${PAPER_US_DB}" \
  "${STOCK_ARGS[@]}" \
  --workers 4 \
  --timeout-seconds 30 \
  --fallback-provider deepseek \
  --fallback-model deepseek-chat \
  --fallback-timeout-seconds 20 \
  --slow-threshold-ms 15000 \
  --primary-failure-threshold 2 \
  --retries 1 \
  --retry-delay-seconds 1
