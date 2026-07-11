#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/Users/yongyuanbuanzhede/quant}"
DSA_DIR="${DSA_DIR:-${PROJECT_DIR}/vendor/daily_stock_analysis}"
LOG_DIR="${PROJECT_DIR}/runtime_data/logs"
SECRETS_DIR="${PROJECT_DIR}/runtime_data/secrets"
LAUNCHER_LOG="${LOG_DIR}/us_dsa_daily_launcher.log"
RUN_DATE="$(date "+%Y%m%d")"
US_DSA_LOG="${LOG_DIR}/us_dsa_daily_${RUN_DATE}.log"
US_DSA_STATUS="${LOG_DIR}/us_dsa_daily_status_${RUN_DATE}.json"
US_STOCKS="${US_STOCKS:-AAPL,NVDA,MSFT,JPM,SPCX}"
US_DSA_ISOLATE_STOCKS="${US_DSA_ISOLATE_STOCKS:-1}"
US_DSA_SINGLE_STOCK_TIMEOUT_SECONDS="${US_DSA_SINGLE_STOCK_TIMEOUT_SECONDS:-1200}"
US_DSA_ALERT_ON_ZERO_SUCCESS="${US_DSA_ALERT_ON_ZERO_SUCCESS:-1}"
US_DSA_SKIP_PROXY_CHECK="${US_DSA_SKIP_PROXY_CHECK:-0}"
US_DSA_FORCE_RUN="${US_DSA_FORCE_RUN:-1}"
CAFFEINATE_BIN="${CAFFEINATE_BIN:-/usr/bin/caffeinate}"
PYTHON_BIN="${PYTHON_BIN:-${DSA_DIR}/.venv/bin/python}"
DSA_MAIN="${DSA_MAIN:-${DSA_DIR}/main.py}"

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

load_deepseek_fallback() {
  local key_file="${SECRETS_DIR}/deepseek_api_key.txt"
  if [[ -s "${key_file}" ]]; then
    DEEPSEEK_API_KEY="$(tr -d '\r\n' < "${key_file}")"
    DEEPSEEK_API_KEYS="${DEEPSEEK_API_KEY}"
    export DEEPSEEK_API_KEY DEEPSEEK_API_KEYS
    if [[ -z "${LITELLM_FALLBACK_MODELS:-}" ]]; then
      LITELLM_FALLBACK_MODELS="deepseek/deepseek-chat"
      export LITELLM_FALLBACK_MODELS
    fi
    log "deepseek_fallback=enabled model=deepseek/deepseek-chat"
  else
    log "deepseek_fallback=missing key_file=${key_file}"
  fi
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
  marker="${TMPDIR:-/tmp}/us_dsa_timeout_$$_${pid}"

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
  shift
  local args=("--stocks" "${stock_arg}")
  if [[ "${US_DSA_FORCE_RUN}" == "1" ]]; then
    args+=("--force-run")
  fi
  args+=("$@")

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

write_status() {
  local status="${1}"
  local success_count="${2}"
  local failure_count="${3}"
  local total_count="${4}"
  local exit_code="${5}"

  printf '{"status":"%s","success":%s,"failed":%s,"total":%s,"exit_code":%s,"log":"%s","generated_at":"%s"}\n' \
    "${status}" \
    "${success_count}" \
    "${failure_count}" \
    "${total_count}" \
    "${exit_code}" \
    "${US_DSA_LOG}" \
    "$(timestamp)" \
    > "${US_DSA_STATUS}"
}

run_batch_mode() {
  log "proxy_check=ok action=start_us_daily_run mode=batch stocks=${US_STOCKS} force_run=${US_DSA_FORCE_RUN} log=${US_DSA_LOG}"
  if run_dsa_main "${US_STOCKS}" >> "${US_DSA_LOG}" 2>&1; then
    local success_count
    local failure_count
    success_count="$(extract_success_count "${US_DSA_LOG}")"
    failure_count="$(extract_failure_count "${US_DSA_LOG}")"
    success_count="${success_count:-0}"
    failure_count="${failure_count:-0}"
    if [[ "${US_DSA_ALERT_ON_ZERO_SUCCESS}" == "1" && "${success_count}" == "0" && "${failure_count}" != "0" ]]; then
      write_status "alert" "${success_count}" "${failure_count}" "$((success_count + failure_count))" 70
      log "action=finish_us_daily_run status=alert reason=zero_success success=${success_count} failed=${failure_count} log=${US_DSA_LOG} status_file=${US_DSA_STATUS}"
      return 70
    fi
    write_status "ok" "${success_count}" "${failure_count}" "$((success_count + failure_count))" 0
    log "action=finish_us_daily_run status=ok success=${success_count} failed=${failure_count} log=${US_DSA_LOG} status_file=${US_DSA_STATUS}"
  else
    local status=$?
    write_status "failed" 0 0 0 "${status}"
    log "action=finish_us_daily_run status=failed exit=${status} log=${US_DSA_LOG} status_file=${US_DSA_STATUS}"
    return "${status}"
  fi
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

  IFS=',' read -r -a stocks <<< "${US_STOCKS}"
  log "proxy_check=ok action=start_us_daily_run mode=isolated stocks=${US_STOCKS} force_run=${US_DSA_FORCE_RUN} timeout_seconds=${US_DSA_SINGLE_STOCK_TIMEOUT_SECONDS} log=${US_DSA_LOG}"
  : > "${US_DSA_LOG}"

  for stock in "${stocks[@]}"; do
    normalized_stock="${stock//[[:space:]]/}"
    normalized_stock="$(printf "%s" "${normalized_stock}" | tr '[:lower:]' '[:upper:]')"
    if [[ -z "${normalized_stock}" ]]; then
      continue
    fi
    stock="${normalized_stock}"
    total=$((total + 1))
    stock_log="${LOG_DIR}/us_dsa_daily_${RUN_DATE}_${stock}.log"
    : > "${stock_log}"
    log "action=start_us_stock stock=${stock} timeout_seconds=${US_DSA_SINGLE_STOCK_TIMEOUT_SECONDS} log=${stock_log}"
    {
      printf '\n===== US DSA stock=%s start=%s =====\n' "${stock}" "$(timestamp)"
    } >> "${US_DSA_LOG}"

    if run_with_timeout "${US_DSA_SINGLE_STOCK_TIMEOUT_SECONDS}" \
      run_dsa_main "${stock}" --no-market-review >> "${stock_log}" 2>&1; then
      stock_exit=0
    else
      stock_exit=$?
    fi

    cat "${stock_log}" >> "${US_DSA_LOG}"
    {
      printf '===== US DSA stock=%s end=%s exit=%s =====\n' "${stock}" "$(timestamp)" "${stock_exit}"
    } >> "${US_DSA_LOG}"

    stock_success="$(extract_success_count "${stock_log}")"
    stock_failed="$(extract_failure_count "${stock_log}")"
    stock_success="${stock_success:-0}"
    stock_failed="${stock_failed:-0}"

    if [[ "${stock_exit}" == "124" ]]; then
      failure_count=$((failure_count + 1))
      log "action=finish_us_stock status=timeout stock=${stock} exit=${stock_exit} success=${stock_success} failed=${stock_failed} log=${stock_log}"
      continue
    fi
    if [[ "${stock_exit}" != "0" ]]; then
      failure_count=$((failure_count + 1))
      log "action=finish_us_stock status=failed stock=${stock} exit=${stock_exit} success=${stock_success} failed=${stock_failed} log=${stock_log}"
      continue
    fi
    if [[ "${stock_success}" == "0" ]]; then
      failure_count=$((failure_count + 1))
      log "action=finish_us_stock status=business_failed stock=${stock} exit=${stock_exit} success=${stock_success} failed=${stock_failed} log=${stock_log}"
      continue
    fi

    success_count=$((success_count + stock_success))
    failure_count=$((failure_count + stock_failed))
    log "action=finish_us_stock status=ok stock=${stock} exit=${stock_exit} success=${stock_success} failed=${stock_failed} log=${stock_log}"
  done

  if [[ "${success_count}" == "0" && "${failure_count}" != "0" && "${US_DSA_ALERT_ON_ZERO_SUCCESS}" == "1" ]]; then
    status="alert"
    final_exit=70
    log "action=finish_us_daily_run status=alert reason=zero_success success=${success_count} failed=${failure_count} total=${total} log=${US_DSA_LOG}"
  elif [[ "${failure_count}" != "0" ]]; then
    status="degraded"
    log "action=finish_us_daily_run status=degraded success=${success_count} failed=${failure_count} total=${total} log=${US_DSA_LOG}"
  else
    log "action=finish_us_daily_run status=ok success=${success_count} failed=${failure_count} total=${total} log=${US_DSA_LOG}"
  fi

  write_status "${status}" "${success_count}" "${failure_count}" "${total}" "${final_exit}"
  return "${final_exit}"
}

mkdir -p "${LOG_DIR}"

if [[ "${US_DSA_SKIP_PROXY_CHECK}" != "1" ]]; then
  if ! /usr/bin/nc -z 127.0.0.1 7890; then
    log "proxy_check=fail host=127.0.0.1 port=7890 action=exit"
    exit 75
  fi
else
  log "proxy_check=skipped reason=US_DSA_SKIP_PROXY_CHECK"
fi

load_tavily_keys
load_deepseek_fallback
export STOCK_LIST="${US_STOCKS}"

cd "${DSA_DIR}"
if [[ "${US_DSA_ISOLATE_STOCKS}" == "1" ]]; then
  if run_isolated_mode; then
    exit 0
  else
    exit $?
  fi
else
  if run_batch_mode; then
    exit 0
  else
    exit $?
  fi
fi
