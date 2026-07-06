#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/Users/yongyuanbuanzhede/quant"
LOG_DIR="${PROJECT_DIR}/runtime_data/logs"
LAUNCHER_LOG="${LOG_DIR}/executor_daily_launcher.log"
PYTHON_BIN="${PROJECT_DIR}/.venv/bin/python"

timestamp() {
  date "+%Y-%m-%d %H:%M:%S %z"
}

log() {
  printf "%s %s\n" "$(timestamp)" "$*" >> "${LAUNCHER_LOG}"
}

mkdir -p "${LOG_DIR}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  log "python_check=fail path=${PYTHON_BIN} action=exit"
  exit 74
fi

cd "${PROJECT_DIR}"
log "action=start_executor_daily"
exec /usr/bin/caffeinate -i "${PYTHON_BIN}" -m executor.engine
