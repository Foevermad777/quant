#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/Users/yongyuanbuanzhede/quant}"
DSA_DIR="${DSA_DIR:-${PROJECT_DIR}/vendor/daily_stock_analysis}"
LOG_DIR="${PROJECT_DIR}/runtime_data/logs"
SECRETS_DIR="${PROJECT_DIR}/runtime_data/secrets"
LAUNCHER_LOG="${LOG_DIR}/dsa_daily_launcher.log"
ALERTS_LOG="${LOG_DIR}/dsa_alerts.log"
RUN_DATE="$(date "+%Y%m%d")"
RUN_STARTED_AT="$(date "+%Y-%m-%d %H:%M:%S")"
DSA_LOG="${LOG_DIR}/dsa_daily_${RUN_DATE}.log"
DSA_STATUS="${LOG_DIR}/dsa_daily_status_${RUN_DATE}.json"
DSA_PREFLIGHT_LOG="${LOG_DIR}/dsa_preflight_${RUN_DATE}.log"
DSA_PREFLIGHT_STATUS="${LOG_DIR}/dsa_preflight_status_${RUN_DATE}.json"
DSA_MARKET_CONTEXT_LOG="${LOG_DIR}/dsa_market_context_${RUN_DATE}.log"
DSA_MARKET_CONTEXT_STATUS="${LOG_DIR}/dsa_market_context_status_${RUN_DATE}.json"
DSA_DB_VERIFY_STATUS="${LOG_DIR}/dsa_db_verify_${RUN_DATE}.json"
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
DSA_PREFLIGHT_ENABLED="${DSA_PREFLIGHT_ENABLED:-1}"
DSA_PREFLIGHT_FAIL_CLOSED="${DSA_PREFLIGHT_FAIL_CLOSED:-1}"
DSA_PREFLIGHT_TIMEOUT_SECONDS="${DSA_PREFLIGHT_TIMEOUT_SECONDS:-12}"
DSA_PREFLIGHT_SYMBOL="${DSA_PREFLIGHT_SYMBOL:-600519}"
DSA_DB_VERIFY_ENABLED="${DSA_DB_VERIFY_ENABLED:-1}"
DSA_RETRY_ON_MISSING="${DSA_RETRY_ON_MISSING:-1}"
DSA_RETRY_DELAY_SECONDS="${DSA_RETRY_DELAY_SECONDS:-600}"
DSA_ALERT_NOTIFY="${DSA_ALERT_NOTIFY:-1}"
DSA_DB_PATH="${DSA_DB_PATH:-${PROJECT_DIR}/runtime_data/dsa/stock_analysis.db}"
DSA_MARKET_CONTEXT_RUNTIME_STATUS="not_run"
DSA_MARKET_CONTEXT_ACTION="none"
DSA_MARKET_CONTEXT_QUERY_ID=""
DSA_MARKET_CONTEXT_HISTORY_ID=""
DSA_PROXY_RUNTIME_STATUS="not_checked"
DSA_PREFLIGHT_RUNTIME_STATUS="not_run"
DSA_DB_VERIFIED_COUNT=""
DSA_DB_MISSING=""
DSA_RETRY_ROUNDS=0
CAFFEINATE_BIN="${CAFFEINATE_BIN:-/usr/bin/caffeinate}"
PYTHON_BIN="${PYTHON_BIN:-${DSA_DIR}/.venv/bin/python}"
DSA_MAIN="${DSA_MAIN:-${DSA_DIR}/main.py}"
DSA_PREFLIGHT_SCRIPT="${DSA_PREFLIGHT_SCRIPT:-${PROJECT_DIR}/ops/us_dsa_preflight.py}"
DSA_MARKET_CONTEXT_SCRIPT="${DSA_MARKET_CONTEXT_SCRIPT:-${PROJECT_DIR}/ops/prepare_dsa_market_context.py}"
DSA_VERIFY_SCRIPT="${DSA_VERIFY_SCRIPT:-${PROJECT_DIR}/ops/verify_dsa_analysis.py}"

timestamp() {
  date "+%Y-%m-%d %H:%M:%S %z"
}

log() {
  printf "%s %s\n" "$(timestamp)" "$*" >> "${LAUNCHER_LOG}"
}

send_alert() {
  local title="${1}"
  local message="${2}"
  printf "%s market=cn %s | %s\n" "$(timestamp)" "${title}" "${message}" >> "${ALERTS_LOG}"
  log "action=alert market=cn title=${title// /_} message=${message// /_}"
  if [[ "${DSA_ALERT_NOTIFY}" == "1" ]]; then
    /usr/bin/osascript \
      -e 'on run argv' \
      -e 'display notification (item 2 of argv) with title (item 1 of argv) sound name "Basso"' \
      -e 'end run' \
      "${title}" "${message}" >/dev/null 2>&1 || true
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
    log "deepseek_fallback=enabled market=cn model=deepseek/deepseek-chat"
  else
    log "deepseek_fallback=missing market=cn key_file=${key_file}"
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
  marker="${TMPDIR:-/tmp}/dsa_timeout_$$_${pid}"
  # Watchdog polls a wall-clock deadline instead of one long sleep: `sleep N`
  # is suspended during macOS system sleep, so a single sleep silently grants
  # a hung payload extra wall time (the 07-18 5244s hang outlived its 1200s
  # budget this way). Short sleeps re-check the real clock after every wake.
  (
    deadline=$(( $(date +%s) + timeout_seconds ))
    while kill -0 "${pid}" 2>/dev/null; do
      if [[ "$(date +%s)" -ge "${deadline}" ]]; then
        : > "${marker}"
        terminate_tree "${pid}"
        break
      fi
      sleep 5
    done
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

  printf '{"status":"%s","success":%s,"failed":%s,"total":%s,"exit_code":%s,"proxy":"%s","preflight":"%s","market_context":"%s","market_context_action":"%s","market_context_query_id":"%s","market_context_history_id":"%s","market_context_status_file":"%s","market_context_log":"%s","preflight_status_file":"%s","preflight_log":"%s","db_verified":"%s","db_missing":"%s","retry_rounds":%s,"run_started_at":"%s","log":"%s","generated_at":"%s"}\n' \
    "${status}" \
    "${success_count}" \
    "${failure_count}" \
    "${total_count}" \
    "${exit_code}" \
    "${DSA_PROXY_RUNTIME_STATUS}" \
    "${DSA_PREFLIGHT_RUNTIME_STATUS}" \
    "${DSA_MARKET_CONTEXT_RUNTIME_STATUS}" \
    "${DSA_MARKET_CONTEXT_ACTION}" \
    "${DSA_MARKET_CONTEXT_QUERY_ID}" \
    "${DSA_MARKET_CONTEXT_HISTORY_ID}" \
    "${DSA_MARKET_CONTEXT_STATUS}" \
    "${DSA_MARKET_CONTEXT_LOG}" \
    "${DSA_PREFLIGHT_STATUS}" \
    "${DSA_PREFLIGHT_LOG}" \
    "${DSA_DB_VERIFIED_COUNT}" \
    "${DSA_DB_MISSING}" \
    "${DSA_RETRY_ROUNDS}" \
    "${RUN_STARTED_AT}" \
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

preflight_value() {
  local field="${1}"
  "${PYTHON_BIN}" -c \
    'import json,sys; data=json.load(open(sys.argv[1], encoding="utf-8")); print((data.get("routes", {}).get(sys.argv[2]) if sys.argv[2] != "status" else data.get("status")) or "")' \
    "${DSA_PREFLIGHT_STATUS}" "${field}"
}

db_verify_value() {
  local field="${1}"
  "${PYTHON_BIN}" -c \
    'import json,sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
value = data.get(sys.argv[2])
if isinstance(value, list):
    print(",".join(str(item) for item in value))
elif value is None:
    print("")
else:
    print(value)' \
    "${DSA_DB_VERIFY_STATUS}" "${field}"
}

run_provider_preflight() {
  local preflight_exit=0
  local summary=""
  local selected_llm=""
  local total

  if [[ "${DSA_PREFLIGHT_ENABLED}" != "1" ]]; then
    DSA_PREFLIGHT_RUNTIME_STATUS="disabled"
    log "action=skip_cn_dsa_preflight reason=DSA_PREFLIGHT_ENABLED"
    return 0
  fi
  if [[ ! -f "${DSA_PREFLIGHT_SCRIPT}" ]]; then
    DSA_PREFLIGHT_RUNTIME_STATUS="blocked"
    log "action=finish_cn_dsa_preflight status=blocked reason=script_missing path=${DSA_PREFLIGHT_SCRIPT}"
    if [[ "${DSA_PREFLIGHT_FAIL_CLOSED}" == "1" ]]; then
      total="$(count_stocks)"
      write_status "alert" 0 "${total}" "${total}" 69
      send_alert "DSA CN preflight 缺失" "脚本不存在: ${DSA_PREFLIGHT_SCRIPT}"
      return 69
    fi
    return 0
  fi

  : > "${DSA_PREFLIGHT_LOG}"
  set +e
  "${PYTHON_BIN}" "${DSA_PREFLIGHT_SCRIPT}" \
    --output "${DSA_PREFLIGHT_STATUS}" \
    --region cn \
    --gemini-key-file "${SECRETS_DIR}/gemini_api_key.txt" \
    --deepseek-key-file "${SECRETS_DIR}/deepseek_api_key.txt" \
    --tavily-key-file "${SECRETS_DIR}/tavily_api_key.txt" \
    --bocha-key-file "${SECRETS_DIR}/bocha_api_key.txt" \
    --proxy-host "${DSA_PROXY_HOST}" \
    --proxy-port "${DSA_PROXY_PORT}" \
    --timeout "${DSA_PREFLIGHT_TIMEOUT_SECONDS}" \
    --symbol "${DSA_PREFLIGHT_SYMBOL}" \
    >> "${DSA_PREFLIGHT_LOG}" 2>&1
  preflight_exit=$?
  set -e
  summary="$(tail -1 "${DSA_PREFLIGHT_LOG}" 2>/dev/null || true)"

  if [[ -f "${DSA_PREFLIGHT_STATUS}" ]]; then
    DSA_PREFLIGHT_RUNTIME_STATUS="$(preflight_value status 2>/dev/null || printf 'blocked')"
  else
    DSA_PREFLIGHT_RUNTIME_STATUS="blocked"
  fi
  log "action=finish_cn_dsa_preflight exit=${preflight_exit} ${summary:-status=${DSA_PREFLIGHT_RUNTIME_STATUS}}"

  if [[ "${preflight_exit}" != "0" ]]; then
    if [[ "${DSA_PREFLIGHT_FAIL_CLOSED}" == "1" ]]; then
      total="$(count_stocks)"
      write_status "alert" 0 "${total}" "${total}" "${preflight_exit}"
      send_alert "DSA CN preflight 阻断" "Gemini/DeepSeek 均不可达，今日 CN 跑批中止（exit=${preflight_exit}）"
      return "${preflight_exit}"
    fi
    log "action=continue_cn_dsa_after_preflight_failure fail_closed=0"
    return 0
  fi

  selected_llm="$(preflight_value llm)"
  if [[ "${selected_llm}" == "deepseek" ]]; then
    LITELLM_MODEL="deepseek/deepseek-chat"
    LITELLM_FALLBACK_MODELS=""
    export LITELLM_MODEL LITELLM_FALLBACK_MODELS
    send_alert "DSA CN 降级 DeepSeek" "Gemini 路由不可用（代理出口/配额），今日 CN 主模型切换为 deepseek-chat"
  fi
  log "action=select_cn_dsa_routes llm=${selected_llm}"
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
    send_alert "DSA CN 大盘上下文失败" "context exit=${context_exit}，今日 CN 跑批中止，需人工介入"
    return "${context_exit}"
  fi
  if [[ "${DSA_MARKET_CONTEXT_RUNTIME_STATUS}" == "skipped" ]]; then
    return 0
  fi
  if [[ "${DSA_MARKET_CONTEXT_RUNTIME_STATUS}" != "ok" ]]; then
    total="$(count_stocks)"
    write_status "alert" 0 "${total}" "${total}" 68
    send_alert "DSA CN 大盘上下文异常" "status=${DSA_MARKET_CONTEXT_RUNTIME_STATUS} action=${DSA_MARKET_CONTEXT_ACTION}，今日 CN 跑批中止"
    return 68
  fi

  DSA_MARKET_CONTEXT_QUERY_ID="$(market_context_value query_id)"
  DSA_MARKET_CONTEXT_HISTORY_ID="$(market_context_value history_id)"
  if [[ -z "${DSA_MARKET_CONTEXT_QUERY_ID}" || -z "${DSA_MARKET_CONTEXT_HISTORY_ID}" ]]; then
    DSA_MARKET_CONTEXT_RUNTIME_STATUS="blocked"
    DSA_MARKET_CONTEXT_ACTION="invalid_contract"
    total="$(count_stocks)"
    write_status "alert" 0 "${total}" "${total}" 68
    send_alert "DSA CN 大盘上下文契约异常" "query_id/history_id 缺失，今日 CN 跑批中止"
    return 68
  fi
  export DSA_MARKET_CONTEXT_QUERY_ID
  log "action=select_market_context region=cn query_id=${DSA_MARKET_CONTEXT_QUERY_ID} history_id=${DSA_MARKET_CONTEXT_HISTORY_ID}"
}

run_isolated_mode() {
  local stocks_csv="${1:-${DSA_STOCKS}}"
  local round="${2:-1}"
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

  IFS=',' read -r -a stocks <<< "${stocks_csv}"
  log "proxy_check=${DSA_PROXY_RUNTIME_STATUS} action=start_daily_run mode=isolated market=cn round=${round} stocks=${stocks_csv} context_query_id=${DSA_MARKET_CONTEXT_QUERY_ID} timeout_seconds=${DSA_SINGLE_STOCK_TIMEOUT_SECONDS} log=${DSA_LOG}"
  for stock in "${stocks[@]}"; do
    normalized_stock="${stock//[[:space:]]/}"
    if [[ -z "${normalized_stock}" ]]; then
      continue
    fi
    stock="${normalized_stock}"
    total=$((total + 1))
    if [[ "${round}" == "1" ]]; then
      stock_log="${LOG_DIR}/dsa_daily_${RUN_DATE}_${stock}.log"
    else
      stock_log="${LOG_DIR}/dsa_daily_${RUN_DATE}_${stock}_retry${round}.log"
    fi
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
  if [[ "${round}" == "1" ]]; then
    write_status "${status}" "${success_count}" "${failure_count}" "${total}" "${final_exit}"
  fi
  log "action=finish_daily_run market=cn round=${round} status=${status} success=${success_count} failed=${failure_count} total=${total} context_query_id=${DSA_MARKET_CONTEXT_QUERY_ID}"
  return "${final_exit}"
}

run_db_verify() {
  local verify_exit=0
  local analyzed_value=""
  local missing_value=""
  set +e
  "${PYTHON_BIN}" "${DSA_VERIFY_SCRIPT}" \
    --db "${DSA_DB_PATH}" \
    --stocks "${DSA_STOCKS}" \
    --since "${RUN_STARTED_AT}" \
    --output "${DSA_DB_VERIFY_STATUS}" >> "${DSA_LOG}" 2>&1
  verify_exit=$?
  set -e
  if [[ "${verify_exit}" != "0" && "${verify_exit}" != "3" ]]; then
    log "action=db_verify market=cn status=error exit=${verify_exit}"
    return 1
  fi
  if ! analyzed_value="$(db_verify_value analyzed_count)"; then
    log "action=db_verify market=cn status=readback_error field=analyzed_count"
    return 1
  fi
  if ! missing_value="$(db_verify_value missing)"; then
    log "action=db_verify market=cn status=readback_error field=missing"
    return 1
  fi
  if [[ "${verify_exit}" == "3" && -z "${missing_value}" ]]; then
    log "action=db_verify market=cn status=inconsistent exit=3 missing_readback=empty"
    return 1
  fi
  DSA_DB_VERIFIED_COUNT="${analyzed_value}"
  DSA_DB_MISSING="${missing_value}"
  log "action=db_verify market=cn status=done verified=${DSA_DB_VERIFIED_COUNT} missing=${DSA_DB_MISSING:-none} since=${RUN_STARTED_AT}"
  return 0
}

finalize_run() {
  local mode_exit="${1}"
  local total
  local success
  local failed
  local status
  total="$(count_stocks)"

  if [[ "${DSA_DB_VERIFY_ENABLED}" != "1" ]]; then
    exit "${mode_exit}"
  fi

  if ! run_db_verify; then
    write_status "alert" 0 "${total}" "${total}" 72
    send_alert "DSA CN 校验层异常" "无法核对 analysis_history，DB 真值未知（mode_exit=${mode_exit}），需人工检查"
    exit 72
  fi

  if [[ -n "${DSA_DB_MISSING}" && "${DSA_RETRY_ON_MISSING}" == "1" ]]; then
    DSA_RETRY_ROUNDS=1
    send_alert "DSA CN 首轮缺口" "missing=${DSA_DB_MISSING}，${DSA_RETRY_DELAY_SECONDS}s 后自动重试一轮"
    log "action=schedule_retry market=cn missing=${DSA_DB_MISSING} delay_seconds=${DSA_RETRY_DELAY_SECONDS}"
    if [[ -n "${CAFFEINATE_BIN}" ]]; then
      "${CAFFEINATE_BIN}" -i /bin/sleep "${DSA_RETRY_DELAY_SECONDS}" || /bin/sleep "${DSA_RETRY_DELAY_SECONDS}" || true
    else
      /bin/sleep "${DSA_RETRY_DELAY_SECONDS}" || true
    fi
    run_isolated_mode "${DSA_DB_MISSING}" 2 || true
    if ! run_db_verify; then
      write_status "alert" 0 "${total}" "${total}" 72
      send_alert "DSA CN 校验层异常" "重试后无法核对 analysis_history，DB 真值未知，需人工检查"
      exit 72
    fi
  fi

  success="${DSA_DB_VERIFIED_COUNT:-0}"
  if [[ -n "${DSA_DB_MISSING}" ]]; then
    failed=$((total - success))
    write_status "alert" "${success}" "${failed}" "${total}" 70
    send_alert "DSA CN 当日分析缺口" "missing=${DSA_DB_MISSING} success=${success}/${total}，重试 ${DSA_RETRY_ROUNDS} 轮后仍缺，需人工介入"
    log "action=finish_daily_run market=cn status=alert reason=db_missing missing=${DSA_DB_MISSING} success=${success} total=${total} retry_rounds=${DSA_RETRY_ROUNDS}"
    exit 70
  fi

  status="ok"
  if [[ "${DSA_RETRY_ROUNDS}" != "0" || "${mode_exit}" != "0" || "${DSA_PREFLIGHT_RUNTIME_STATUS}" == "degraded" ]]; then
    status="degraded"
  fi
  write_status "${status}" "${success}" 0 "${total}" 0
  log "action=finish_daily_run market=cn status=${status} source=db_verify success=${success} total=${total} retry_rounds=${DSA_RETRY_ROUNDS}"
  exit 0
}

mkdir -p "${LOG_DIR}"
if [[ "${DSA_SKIP_PROXY_CHECK}" != "1" ]]; then
  if /usr/bin/nc -z "${DSA_PROXY_HOST}" "${DSA_PROXY_PORT}"; then
    DSA_PROXY_RUNTIME_STATUS="ok"
  else
    DSA_PROXY_RUNTIME_STATUS="fail"
    if [[ "${DSA_PREFLIGHT_ENABLED}" == "1" ]]; then
      log "proxy_check=fail host=${DSA_PROXY_HOST} port=${DSA_PROXY_PORT} action=continue_to_provider_preflight"
    else
      total="$(count_stocks)"
      write_status "alert" 0 "${total}" "${total}" 75
      send_alert "DSA CN 代理不可用" "127.0.0.1:${DSA_PROXY_PORT} 未监听且 preflight 关闭，今日 CN 跑批中止"
      log "proxy_check=fail host=${DSA_PROXY_HOST} port=${DSA_PROXY_PORT} action=exit"
      exit 75
    fi
  fi
else
  DSA_PROXY_RUNTIME_STATUS="skipped"
  log "proxy_check=skipped reason=DSA_SKIP_PROXY_CHECK"
fi

load_deepseek_fallback
export STOCK_LIST="${DSA_STOCKS}"
export MARKET_REVIEW_REGION="cn"
export DAILY_MARKET_CONTEXT_ENABLED="true"
if run_provider_preflight; then
  :
else
  exit $?
fi
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
: > "${DSA_LOG}"
MODE_EXIT=0
run_isolated_mode "${DSA_STOCKS}" || MODE_EXIT=$?
finalize_run "${MODE_EXIT}"
