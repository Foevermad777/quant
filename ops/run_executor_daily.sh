#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/Users/yongyuanbuanzhede/quant"
DSA_DIR="${PROJECT_DIR}/vendor/daily_stock_analysis"
LOG_DIR="${PROJECT_DIR}/runtime_data/logs"
LAUNCHER_LOG="${LOG_DIR}/executor_daily_launcher.log"
PYTHON_BIN="${PROJECT_DIR}/.venv/bin/python"
DSA_PYTHON_BIN="${DSA_DIR}/.venv/bin/python"
GAP_BACKFILL_LOG="${LOG_DIR}/dsa_gap_backfill_$(date "+%Y%m%d").log"

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
if [[ -x "${DSA_PYTHON_BIN}" ]]; then
  log "action=start_dsa_gap_backfill"
  if "${DSA_PYTHON_BIN}" "${PROJECT_DIR}/ops/backfill_dsa_gaps.py" --days 10 >> "${GAP_BACKFILL_LOG}" 2>&1; then
    log "action=finish_dsa_gap_backfill status=ok log=${GAP_BACKFILL_LOG}"
  else
    log "action=finish_dsa_gap_backfill status=failed log=${GAP_BACKFILL_LOG}"
  fi
else
  log "dsa_python_check=fail path=${DSA_PYTHON_BIN} action=skip_gap_backfill"
fi

log "action=start_executor_daily"
exec /usr/bin/caffeinate -i "${PYTHON_BIN}" -m executor.engine
