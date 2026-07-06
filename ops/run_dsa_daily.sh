#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/Users/yongyuanbuanzhede/quant"
DSA_DIR="${PROJECT_DIR}/vendor/daily_stock_analysis"
LOG_DIR="${PROJECT_DIR}/runtime_data/logs"
LAUNCHER_LOG="${LOG_DIR}/dsa_daily_launcher.log"

timestamp() {
  date "+%Y-%m-%d %H:%M:%S %z"
}

log() {
  printf "%s %s\n" "$(timestamp)" "$*" >> "${LAUNCHER_LOG}"
}

mkdir -p "${LOG_DIR}"

if ! /usr/bin/nc -z 127.0.0.1 7890; then
  log "proxy_check=fail host=127.0.0.1 port=7890 action=exit"
  exit 75
fi

cd "${DSA_DIR}"
log "proxy_check=ok action=start_daily_run"
exec /usr/bin/caffeinate -i "${DSA_DIR}/.venv/bin/python" "${DSA_DIR}/main.py"
