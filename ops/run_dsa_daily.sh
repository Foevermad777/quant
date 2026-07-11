#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/Users/yongyuanbuanzhede/quant}"
DSA_DIR="${DSA_DIR:-${PROJECT_DIR}/vendor/daily_stock_analysis}"
LOG_DIR="${PROJECT_DIR}/runtime_data/logs"
LAUNCHER_LOG="${LOG_DIR}/dsa_daily_launcher.log"
RUN_DATE="$(date "+%Y%m%d")"
DSA_LOG="${LOG_DIR}/dsa_daily_${RUN_DATE}.log"
DSA_STATUS="${LOG_DIR}/dsa_daily_status_${RUN_DATE}.json"
DSA_MARKET_CONTEXT_LOG="${LOG_DIR}/dsa_market_context_${RUN_DATE}.log"
DSA_MARKET_CONTEXT_STATUS="${LOG_DIR}/dsa_market_context_status_${RUN_DATE}.json"
DSA_STOCKS="${DSA_STOCKS:-${STOCK_LIST:-600519,300750,601318,600036,600900}}"
DSA_SINGLE_STOCK_TIMEOUT_SECONDS="${DSA_SINGLE_STOCK_TIMEOUT_SECONDS:-1200}"
DSA_MARKET_CONTEXT_TIMEOUT_SECONDS="${DSA_MARKET_CONTEXT_TIMEOUT_SECONDS:-1200}"
DSA_MARKET_CONTEXT_FORCE_REFRESH="${DSA_MARKET_CONTEXT_FORCE_REFRESH:-0}"
DSA_MARKET_CONTEXT_NOTIFY="${DSA_MARKET_CONTEXT_NOTIFY:-1}"
DSA_ALERT_ON_ZERO_SUCCESS="${DSA_ALERT_ON_ZERO_SUCCESS:-1}"
DSA_SKIP_PROXY_CHECK="${DSA_SKIP_PROXY_CHECK:-0}"
DSA_FORCE_RUN="${DSA_FORCE_RUN:-0}"
DSA_PROXY_HOST="${DSA_PROXY_HOST:-127.0.0.1}"
DSA_PROXY_PORT="${DSA_PROXY_PORT:-7890}"
DSA_MARKET_CONTEXT_RUNTIME_STATUS="not_run"
DSA_MARKET_CONTEXT_ACTION="none"
DSA_MARKET_CONTEXT_QUERY_ID=""
DSA_MARKET_CONTEXT_HISTORY_ID=""
DSA_PROXY_RUNTIME_STATUS="not_checked"
CAFFEINATE_BIN="${CAFFEINATE_BIN:-/usr/bin/caffeinate}"
PYTHON_BIN="${PYTHON_BIN:-${DSA_DIR}/.venv/bin/python}"
DSA_MAIN="${DSA_MAIN:-${DSA_DIR}/main.py}"
DSA_MARKET_CONTEXT_SCRIPT="${DSA_MARKET_CONTEXT_SCRIPT:-${PROJECT_DIR}/ops/prepare_dsa_market_context.py}"

timestamp() {
  date "+%Y-%m-%d %H:%M:%S %z"
}

log() {
  printf "%s %s\n" "$(timestamp)" "$*" >> "${LAUNCHER_LOG}"
}

terminate_tree() {
  local pid="${1}"
  local children

  children="$(pgrep -P "${pid}" 2>/dev/null || true)"
  if [[ -n "${children}" ]]; then
    kill -TERM ${children} 2>/dev/null || true
  fi
  kill -TERM "${pid}" 2>/dev/null || true
  sleep 5
  children="$(pgrep -P "${pid}" 2>/dev/null || true)"
  if [[ -n "${children}" ]]; then
    kill -KILL ${children} 2>/dev/null || true
  fi
  kill -KILL "${pid}" 2>/dev/null || true
}

run_with_timeout() {
  local timeout_seconds="${1}"
  shift
  local pid
  local watchdog_pid
  local status
  local marker

  "$@" &
  pid=$!
  marker="${TMPDIR:-/tmp}/dsa_timeout_$$_${pid}"
  (
    sleep "${timeout_seconds}"
    if kill -0 "${pid}" 2>/dev/null; then
      : > "${marker}"
      terminate_tree "${pid}"
    fi
  ) &
  watchdog_pid=$!

  set +e
  wait "${pid}"
  status=$?
  set -e
  kill "${watchdog_pid}" 2>/dev/null || true
  wait "${watchdog_pid}" 2>/dev/null || true

  if [[ -e "${marker}" ]]; then
    rm -f "${marker}"
    return 124
  fi
  return "${status}"
}

run_dsa_main() {
  local stock_arg="${1}"
  local args=(
    "--stocks" "${stock_arg}"
    "--no-market-review"
    "--reuse-market-context"
    "--market-context-query-id" "${DSA_MARKET_CONTEXT_QUERY_ID}"
  )
  if [[ "${DSA_FORCE_RUN}" == "1" ]]; then
    args+=("--force-run")
  fi

  if [[ -n "${CAFFEINATE_BIN}" ]]; then
    "${CAFFEINATE_BIN}" -i "${PYTHON_BIN}" "${DSA_MAIN}" "${args[@]}"
  else
    "${PYTHON_BIN}" "${DSA_MAIN}" "${args[@]}"
  fi
}

extract_success_count() {
  local log_file="${1}"
  sed -n 's/.*成功: \([0-9][0-9]*\), 失败: [0-9][0-9]*.*/\1/p' "${log_file}" | tail -1
}

extract_failure_count() {
  local log_file="${1}"
  sed -n 's/.*成功: [0-9][0-9]*, 失败: \([0-9][0-9]*\).*/\1/p' "${log_file}" | tail -1
}

count_stocks() {
  local stocks=()
  local stock
  local total=0
  IFS=',' read -r -a stocks <<< "${DSA_STOCKS}"
  for stock in "${stocks[@]}"; do
    stock="${stock//[[:space:]]/}"
    if [[ -n "${stock}" ]]; then
      total=$((total + 1))
    fi
  done
  printf '%s\n' "${total}"
}

write_status() {
  local status="${1}"
  local success_count="${2}"
  local failure_count="${3}"
  local total_count="${4}"
  local exit_code="${5}"

  printf '{"status":"%s","success":%s,"failed":%s,"total":%s,"exit_code":%s,"proxy":"%s","market_context":"%s","market_context_action":"%s","market_context_query_id":"%s","market_context_history_id":"%s","market_context_status_file":"%s","market_context_log":"%s","log":"%s","generated_at":"%s"}\n' \
    "${status}" \
    "${success_count}" \
    "${failure_count}" \
    "${total_count}" \
    "${exit_code}" \
    "${DSA_PROXY_RUNTIME_STATUS}" \
    "${DSA_MARKET_CONTEXT_RUNTIME_STATUS}" \
    "${DSA_MARKET_CONTEXT_ACTION}" \
    "${DSA_MARKET_CONTEXT_QUERY_ID}" \
    "${DSA_MARKET_CONTEXT_HISTORY_ID}" \
    "${DSA_MARKET_CONTEXT_STATUS}" \
    "${DSA_MARKET_CONTEXT_LOG}" \
    "${DSA_LOG}" \
    "$(timestamp)" \
    > "${DSA_STATUS}"
}

market_context_value() {
  local field="${1}"
  "${PYTHON_BIN}" -c \
    'import json,sys; data=json.load(open(sys.argv[1], encoding="utf-8")); value=data.get(sys.argv[2]); print("" if value is None else value)' \
    "${DSA_MARKET_CONTEXT_STATUS}" "${field}"
}

run_market_context() {
  local context_exit=0
  local total
  local args=(
    "${DSA_MARKET_CONTEXT_SCRIPT}"
    "--region" "cn"
    "--output" "${DSA_MARKET_CONTEXT_STATUS}"
    "--run-id" "a_dsa_${RUN_DATE}_$$"
  )
  if [[ "${DSA_MARKET_CONTEXT_FORCE_REFRESH}" == "1" ]]; then
    args+=("--force-refresh")
  fi
  if [[ "${DSA_FORCE_RUN}" != "1" ]]; then
    args+=("--skip-closed-market")
  fi
  if [[ "${DSA_MARKET_CONTEXT_NOTIFY}" == "1" ]]; then
    args+=("--notify")
  fi

  : > "${DSA_MARKET_CONTEXT_LOG}"
  log "action=start_market_context region=cn log=${DSA_MARKET_CONTEXT_LOG}"
  set +e
  run_with_timeout "${DSA_MARKET_CONTEXT_TIMEOUT_SECONDS}" \
    "${PYTHON_BIN}" "${args[@]}" >> "${DSA_MARKET_CONTEXT_LOG}" 2>&1
  context_exit=$?
  set -e

  if [[ -f "${DSA_MARKET_CONTEXT_STATUS}" ]]; then
    DSA_MARKET_CONTEXT_RUNTIME_STATUS="$(market_context_value status 2>/dev/null || printf 'blocked')"
    DSA_MARKET_CONTEXT_ACTION="$(market_context_value action 2>/dev/null || printf 'unknown')"
  else
    DSA_MARKET_CONTEXT_RUNTIME_STATUS="blocked"
    DSA_MARKET_CONTEXT_ACTION="missing_status"
  fi
  log "action=finish_market_context region=cn status=${DSA_MARKET_CONTEXT_RUNTIME_STATUS} action_detail=${DSA_MARKET_CONTEXT_ACTION} exit=${context_exit}"

  if [[ "${context_exit}" != "0" ]]; then
    total="$(count_stocks)"
    write_status "alert" 0 "${total}" "${total}" "${context_exit}"
    return "${context_exit}"
  fi
  if [[ "${DSA_MARKET_CONTEXT_RUNTIME_STATUS}" == "skipped" ]]; then
    return 0
  fi
  if [[ "${DSA_MARKET_CONTEXT_RUNTIME_STATUS}" != "ok" ]]; then
    total="$(count_stocks)"
    write_status "alert" 0 "${total}" "${total}" 68
    return 68
  fi

  DSA_MARKET_CONTEXT_QUERY_ID="$(market_context_value query_id)"
  DSA_MARKET_CONTEXT_HISTORY_ID="$(market_context_value history_id)"
  if [[ -z "${DSA_MARKET_CONTEXT_QUERY_ID}" || -z "${DSA_MARKET_CONTEXT_HISTORY_ID}" ]]; then
    DSA_MARKET_CONTEXT_RUNTIME_STATUS="blocked"
    DSA_MARKET_CONTEXT_ACTION="invalid_contract"
    total="$(count_stocks)"
    write_status "alert" 0 "${total}" "${total}" 68
    return 68
  fi
  export DSA_MARKET_CONTEXT_QUERY_ID
  log "action=select_market_context region=cn query_id=${DSA_MARKET_CONTEXT_QUERY_ID} history_id=${DSA_MARKET_CONTEXT_HISTORY_ID}"
}

run_isolated_mode() {
  local stocks=()
  local stock
  local normalized_stock
  local stock_log
  local stock_exit
  local stock_success
  local stock_failed
  local total=0
  local success_count=0
  local failure_count=0
  local status="ok"
  local final_exit=0

  IFS=',' read -r -a stocks <<< "${DSA_STOCKS}"
  : > "${DSA_LOG}"
  log "proxy_check=${DSA_PROXY_RUNTIME_STATUS} action=start_daily_run mode=isolated market=cn stocks=${DSA_STOCKS} context_query_id=${DSA_MARKET_CONTEXT_QUERY_ID} timeout_seconds=${DSA_SINGLE_STOCK_TIMEOUT_SECONDS} log=${DSA_LOG}"
  for stock in "${stocks[@]}"; do
    normalized_stock="${stock//[[:space:]]/}"
    if [[ -z "${normalized_stock}" ]]; then
      continue
    fi
    stock="${normalized_stock}"
    total=$((total + 1))
    stock_log="${LOG_DIR}/dsa_daily_${RUN_DATE}_${stock}.log"
    : > "${stock_log}"
    log "action=start_stock market=cn stock=${stock} context_query_id=${DSA_MARKET_CONTEXT_QUERY_ID} timeout_seconds=${DSA_SINGLE_STOCK_TIMEOUT_SECONDS} log=${stock_log}"
    printf '\n===== DSA market=cn stock=%s start=%s context_query_id=%s =====\n' \
      "${stock}" "$(timestamp)" "${DSA_MARKET_CONTEXT_QUERY_ID}" >> "${DSA_LOG}"

    if run_with_timeout "${DSA_SINGLE_STOCK_TIMEOUT_SECONDS}" \
      run_dsa_main "${stock}" >> "${stock_log}" 2>&1; then
      stock_exit=0
    else
      stock_exit=$?
    fi
    cat "${stock_log}" >> "${DSA_LOG}"
    printf '===== DSA market=cn stock=%s end=%s exit=%s =====\n' \
      "${stock}" "$(timestamp)" "${stock_exit}" >> "${DSA_LOG}"

    stock_success="$(extract_success_count "${stock_log}")"
    stock_failed="$(extract_failure_count "${stock_log}")"
    stock_success="${stock_success:-0}"
    stock_failed="${stock_failed:-0}"
    if [[ "${stock_exit}" == "124" ]]; then
      failure_count=$((failure_count + 1))
      log "action=finish_stock market=cn status=timeout stock=${stock} exit=${stock_exit}"
      continue
    fi
    if [[ "${stock_exit}" != "0" || "${stock_success}" == "0" ]]; then
      failure_count=$((failure_count + 1))
      log "action=finish_stock market=cn status=business_failed stock=${stock} exit=${stock_exit} success=${stock_success} failed=${stock_failed}"
      continue
    fi
    success_count=$((success_count + stock_success))
    failure_count=$((failure_count + stock_failed))
    log "action=finish_stock market=cn status=ok stock=${stock} exit=${stock_exit} success=${stock_success} failed=${stock_failed}"
  done

  if [[ "${success_count}" == "0" && "${failure_count}" != "0" && "${DSA_ALERT_ON_ZERO_SUCCESS}" == "1" ]]; then
    status="alert"
    final_exit=70
  elif [[ "${failure_count}" != "0" ]]; then
    status="degraded"
  fi
  write_status "${status}" "${success_count}" "${failure_count}" "${total}" "${final_exit}"
  log "action=finish_daily_run market=cn status=${status} success=${success_count} failed=${failure_count} total=${total} context_query_id=${DSA_MARKET_CONTEXT_QUERY_ID}"
  return "${final_exit}"
}

mkdir -p "${LOG_DIR}"
if [[ "${DSA_SKIP_PROXY_CHECK}" != "1" ]]; then
  if /usr/bin/nc -z "${DSA_PROXY_HOST}" "${DSA_PROXY_PORT}"; then
    DSA_PROXY_RUNTIME_STATUS="ok"
  else
    DSA_PROXY_RUNTIME_STATUS="fail"
    total="$(count_stocks)"
    write_status "alert" 0 "${total}" "${total}" 75
    log "proxy_check=fail host=${DSA_PROXY_HOST} port=${DSA_PROXY_PORT} action=exit"
    exit 75
  fi
else
  DSA_PROXY_RUNTIME_STATUS="skipped"
  log "proxy_check=skipped reason=DSA_SKIP_PROXY_CHECK"
fi

export STOCK_LIST="${DSA_STOCKS}"
export MARKET_REVIEW_REGION="cn"
export DAILY_MARKET_CONTEXT_ENABLED="true"
if run_market_context; then
  :
else
  exit $?
fi
if [[ "${DSA_MARKET_CONTEXT_RUNTIME_STATUS}" == "skipped" ]]; then
  write_status "skipped" 0 0 "$(count_stocks)" 0
  log "action=finish_daily_run market=cn status=skipped reason=market_closed"
  exit 0
fi

cd "${DSA_DIR}"
if run_isolated_mode; then
  exit 0
else
  exit $?
fi
