#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/Users/yongyuanbuanzhede/quant"
DSA_DIR="${PROJECT_DIR}/vendor/daily_stock_analysis"
LOG_DIR="${PROJECT_DIR}/runtime_data/logs"
SECRETS_DIR="${PROJECT_DIR}/runtime_data/secrets"
LAUNCHER_LOG="${LOG_DIR}/us_dsa_daily_launcher.log"
US_DSA_LOG="${LOG_DIR}/us_dsa_daily_$(date "+%Y%m%d").log"
US_STOCKS="${US_STOCKS:-AAPL,NVDA,MSFT,JPM,SPCX}"

timestamp() {
  date "+%Y-%m-%d %H:%M:%S %z"
}

log() {
  printf "%s %s\n" "$(timestamp)" "$*" >> "${LAUNCHER_LOG}"
}

load_tavily_keys() {
  local key_file="${SECRETS_DIR}/tavily_api_key.txt"
  if [[ -s "${key_file}" ]]; then
    TAVILY_API_KEYS="$(tr -d '\r\n' < "${key_file}")"
    export TAVILY_API_KEYS
    log "tavily_keys=present"
  else
    log "tavily_keys=missing"
  fi
}

mkdir -p "${LOG_DIR}"

if ! /usr/bin/nc -z 127.0.0.1 7890; then
  log "proxy_check=fail host=127.0.0.1 port=7890 action=exit"
  exit 75
fi

load_tavily_keys
export STOCK_LIST="${US_STOCKS}"

cd "${DSA_DIR}"
log "proxy_check=ok action=start_us_daily_run stocks=${US_STOCKS} log=${US_DSA_LOG}"
if /usr/bin/caffeinate -i "${DSA_DIR}/.venv/bin/python" "${DSA_DIR}/main.py" --stocks "${US_STOCKS}" >> "${US_DSA_LOG}" 2>&1; then
  log "action=finish_us_daily_run status=ok log=${US_DSA_LOG}"
else
  status=$?
  log "action=finish_us_daily_run status=failed exit=${status} log=${US_DSA_LOG}"
  exit "${status}"
fi
