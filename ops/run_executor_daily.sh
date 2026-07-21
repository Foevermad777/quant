#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/Users/yongyuanbuanzhede/quant"
DSA_DIR="${PROJECT_DIR}/vendor/daily_stock_analysis"
LOG_DIR="${PROJECT_DIR}/runtime_data/logs"
LAUNCHER_LOG="${LOG_DIR}/executor_daily_launcher.log"
PYTHON_BIN="${PROJECT_DIR}/.venv/bin/python"
DSA_PYTHON_BIN="${DSA_DIR}/.venv/bin/python"
GAP_BACKFILL_LOG="${LOG_DIR}/dsa_gap_backfill_$(date "+%Y%m%d").log"
G5_COMPLETION_LOG="${LOG_DIR}/g5_discipline_completion_$(date "+%Y%m%d").log"

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

log "action=start_g5_discipline_completion workers=4 timeout_seconds=30 fallback=deepseek slow_threshold_ms=15000 primary_failure_threshold=2"
if /usr/bin/caffeinate -i "${PYTHON_BIN}" -m executor.discipline_completion --all-active --market cn --workers 4 --timeout-seconds 30 --fallback-provider deepseek --fallback-model deepseek-chat --fallback-timeout-seconds 20 --slow-threshold-ms 15000 --primary-failure-threshold 2 --retries 1 --retry-delay-seconds 1 >> "${G5_COMPLETION_LOG}" 2>&1; then
  log "action=finish_g5_discipline_completion status=ok log=${G5_COMPLETION_LOG}"
else
  log "action=finish_g5_discipline_completion status=partial_or_failed log=${G5_COMPLETION_LOG}"
fi

SHADOW_LOG="${LOG_DIR}/shadow_intent_cn_$(date "+%Y%m%d").log"
log "action=start_shadow_intent_probe market=cn"
if "${PYTHON_BIN}" -m executor.shadow_intent --market cn >> "${SHADOW_LOG}" 2>&1; then
  log "action=finish_shadow_intent_probe status=ok log=${SHADOW_LOG}"
else
  log "action=finish_shadow_intent_probe status=failed log=${SHADOW_LOG}"
fi

log "action=start_executor_daily"
exec /usr/bin/caffeinate -i "${PYTHON_BIN}" -m executor.engine
