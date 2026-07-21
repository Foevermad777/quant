#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/Users/yongyuanbuanzhede/quant"
LOG_DIR="${PROJECT_DIR}/runtime_data/logs"
LAUNCHER_LOG="${LOG_DIR}/us_executor_daily_launcher.log"
PYTHON_BIN="${PROJECT_DIR}/.venv/bin/python"
US_G5_LOG="${LOG_DIR}/us_g5_discipline_completion_$(date "+%Y%m%d").log"

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

log "action=skip_us_dsa_gap_backfill reason=first_version_uses_0510_us_dsa_launchd"

log "action=start_us_g5_discipline_completion workers=4 timeout_seconds=30 fallback=deepseek slow_threshold_ms=15000 primary_failure_threshold=2"
if "${PROJECT_DIR}/ops/run_us_g5_completion.sh" >> "${US_G5_LOG}" 2>&1; then
  log "action=finish_us_g5_discipline_completion status=ok log=${US_G5_LOG}"
else
  log "action=finish_us_g5_discipline_completion status=partial_or_failed log=${US_G5_LOG}"
fi

SHADOW_LOG="${LOG_DIR}/shadow_intent_us_$(date "+%Y%m%d").log"
log "action=start_shadow_intent_probe market=us"
if "${PYTHON_BIN}" -m executor.shadow_intent --market us >> "${SHADOW_LOG}" 2>&1; then
  log "action=finish_shadow_intent_probe status=ok log=${SHADOW_LOG}"
else
  log "action=finish_shadow_intent_probe status=failed log=${SHADOW_LOG}"
fi

log "action=start_us_executor_daily"
exec /usr/bin/caffeinate -i "${PYTHON_BIN}" -m executor.us
