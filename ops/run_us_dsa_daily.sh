#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/Users/yongyuanbuanzhede/quant}"
DSA_DIR="${DSA_DIR:-${PROJECT_DIR}/vendor/daily_stock_analysis}"
LOG_DIR="${PROJECT_DIR}/runtime_data/logs"
SECRETS_DIR="${PROJECT_DIR}/runtime_data/secrets"
LAUNCHER_LOG="${LOG_DIR}/us_dsa_daily_launcher.log"
ALERTS_LOG="${LOG_DIR}/dsa_alerts.log"
RUN_DATE="$(date "+%Y%m%d")"
RUN_STARTED_AT="$(date "+%Y-%m-%d %H:%M:%S")"
US_DSA_LOG="${LOG_DIR}/us_dsa_daily_${RUN_DATE}.log"
US_DSA_STATUS="${LOG_DIR}/us_dsa_daily_status_${RUN_DATE}.json"
US_DSA_PREFLIGHT_LOG="${LOG_DIR}/us_dsa_preflight_${RUN_DATE}.log"
US_DSA_PREFLIGHT_STATUS="${LOG_DIR}/us_dsa_preflight_status_${RUN_DATE}.json"
US_DSA_MARKET_CONTEXT_LOG="${LOG_DIR}/us_dsa_market_context_${RUN_DATE}.log"
US_DSA_MARKET_CONTEXT_STATUS="${LOG_DIR}/us_dsa_market_context_status_${RUN_DATE}.json"
US_DSA_DB_VERIFY_STATUS="${LOG_DIR}/us_dsa_db_verify_${RUN_DATE}.json"
US_STOCKS="${US_STOCKS:-AAPL,NVDA,MSFT,JPM,SPCX}"
US_DSA_ISOLATE_STOCKS="${US_DSA_ISOLATE_STOCKS:-1}"
US_DSA_SINGLE_STOCK_TIMEOUT_SECONDS="${US_DSA_SINGLE_STOCK_TIMEOUT_SECONDS:-1200}"
US_DSA_ALERT_ON_ZERO_SUCCESS="${US_DSA_ALERT_ON_ZERO_SUCCESS:-1}"
US_DSA_SKIP_PROXY_CHECK="${US_DSA_SKIP_PROXY_CHECK:-0}"
US_DSA_FORCE_RUN="${US_DSA_FORCE_RUN:-1}"
US_DSA_PREFLIGHT_ENABLED="${US_DSA_PREFLIGHT_ENABLED:-1}"
US_DSA_PREFLIGHT_FAIL_CLOSED="${US_DSA_PREFLIGHT_FAIL_CLOSED:-1}"
US_DSA_PREFLIGHT_TIMEOUT_SECONDS="${US_DSA_PREFLIGHT_TIMEOUT_SECONDS:-12}"
US_DSA_PREFLIGHT_SYMBOL="${US_DSA_PREFLIGHT_SYMBOL:-AAPL}"
US_DSA_PREFLIGHT_PROXY_HOST="${US_DSA_PREFLIGHT_PROXY_HOST:-127.0.0.1}"
US_DSA_PREFLIGHT_PROXY_PORT="${US_DSA_PREFLIGHT_PROXY_PORT:-7890}"
US_DSA_MARKET_CONTEXT_TIMEOUT_SECONDS="${US_DSA_MARKET_CONTEXT_TIMEOUT_SECONDS:-1200}"
US_DSA_MARKET_CONTEXT_FORCE_REFRESH="${US_DSA_MARKET_CONTEXT_FORCE_REFRESH:-0}"
US_DSA_MARKET_CONTEXT_NOTIFY="${US_DSA_MARKET_CONTEXT_NOTIFY:-0}"
US_DSA_DB_VERIFY_ENABLED="${US_DSA_DB_VERIFY_ENABLED:-1}"
US_DSA_RETRY_ON_MISSING="${US_DSA_RETRY_ON_MISSING:-1}"
US_DSA_RETRY_DELAY_SECONDS="${US_DSA_RETRY_DELAY_SECONDS:-600}"
US_DSA_ALERT_NOTIFY="${US_DSA_ALERT_NOTIFY:-1}"
US_DSA_DB_PATH="${US_DSA_DB_PATH:-${PROJECT_DIR}/runtime_data/dsa/stock_analysis.db}"
US_DSA_DB_VERIFIED_COUNT=""
US_DSA_DB_MISSING=""
US_DSA_RETRY_ROUNDS=0
US_DSA_PREFLIGHT_RUNTIME_STATUS="not_run"
US_DSA_PROXY_RUNTIME_STATUS="not_checked"
US_DSA_MARKET_CONTEXT_RUNTIME_STATUS="not_run"
US_DSA_MARKET_CONTEXT_ACTION="none"
US_DSA_MARKET_CONTEXT_QUERY_ID=""
US_DSA_MARKET_CONTEXT_HISTORY_ID=""
CAFFEINATE_BIN="${CAFFEINATE_BIN:-/usr/bin/caffeinate}"
PYTHON_BIN="${PYTHON_BIN:-${DSA_DIR}/.venv/bin/python}"
DSA_MAIN="${DSA_MAIN:-${DSA_DIR}/main.py}"
US_DSA_PREFLIGHT_SCRIPT="${US_DSA_PREFLIGHT_SCRIPT:-${PROJECT_DIR}/ops/us_dsa_preflight.py}"
US_DSA_MARKET_CONTEXT_SCRIPT="${US_DSA_MARKET_CONTEXT_SCRIPT:-${PROJECT_DIR}/ops/prepare_dsa_market_context.py}"
US_DSA_VERIFY_SCRIPT="${US_DSA_VERIFY_SCRIPT:-${PROJECT_DIR}/ops/verify_dsa_analysis.py}"

timestamp() {
  date "+%Y-%m-%d %H:%M:%S %z"
}

log() {
  printf "%s %s\n" "$(timestamp)" "$*" >> "${LAUNCHER_LOG}"
}

send_alert() {
  local title="${1}"
  local message="${2}"
  printf "%s market=us %s | %s\n" "$(timestamp)" "${title}" "${message}" >> "${ALERTS_LOG}"
  log "action=alert market=us title=${title// /_} message=${message// /_}"
  if [[ "${US_DSA_ALERT_NOTIFY}" == "1" ]]; then
    /usr/bin/osascript \
      -e 'on run argv' \
      -e 'display notification (item 2 of argv) with title (item 1 of argv) sound name "Basso"' \
      -e 'end run' \
      "${title}" "${message}" >/dev/null 2>&1 || true
  fi
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

load_bocha_keys() {
  local key_file="${SECRETS_DIR}/bocha_api_key.txt"
  if [[ -s "${key_file}" ]]; then
    BOCHA_API_KEYS="$(tr -d '\r\n' < "${key_file}")"
    export BOCHA_API_KEYS
    log "bocha_keys=present"
  else
    log "bocha_keys=missing"
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
  shift
  local args=(
    "--stocks" "${stock_arg}"
    "--no-market-review"
    "--reuse-market-context"
    "--market-context-query-id" "${US_DSA_MARKET_CONTEXT_QUERY_ID}"
  )
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

  printf '{"status":"%s","success":%s,"failed":%s,"total":%s,"exit_code":%s,"proxy":"%s","preflight":"%s","market_context":"%s","market_context_action":"%s","market_context_query_id":"%s","market_context_history_id":"%s","market_context_status_file":"%s","market_context_log":"%s","preflight_status_file":"%s","preflight_log":"%s","db_verified":"%s","db_missing":"%s","retry_rounds":%s,"run_started_at":"%s","log":"%s","generated_at":"%s"}\n' \
    "${status}" \
    "${success_count}" \
    "${failure_count}" \
    "${total_count}" \
    "${exit_code}" \
    "${US_DSA_PROXY_RUNTIME_STATUS}" \
    "${US_DSA_PREFLIGHT_RUNTIME_STATUS}" \
    "${US_DSA_MARKET_CONTEXT_RUNTIME_STATUS}" \
    "${US_DSA_MARKET_CONTEXT_ACTION}" \
    "${US_DSA_MARKET_CONTEXT_QUERY_ID}" \
    "${US_DSA_MARKET_CONTEXT_HISTORY_ID}" \
    "${US_DSA_MARKET_CONTEXT_STATUS}" \
    "${US_DSA_MARKET_CONTEXT_LOG}" \
    "${US_DSA_PREFLIGHT_STATUS}" \
    "${US_DSA_PREFLIGHT_LOG}" \
    "${US_DSA_DB_VERIFIED_COUNT}" \
    "${US_DSA_DB_MISSING}" \
    "${US_DSA_RETRY_ROUNDS}" \
    "${RUN_STARTED_AT}" \
    "${US_DSA_LOG}" \
    "$(timestamp)" \
    > "${US_DSA_STATUS}"
}

count_stocks() {
  local stocks=()
  local stock
  local total=0
  IFS=',' read -r -a stocks <<< "${US_STOCKS}"
  for stock in "${stocks[@]}"; do
    stock="${stock//[[:space:]]/}"
    if [[ -n "${stock}" ]]; then
      total=$((total + 1))
    fi
  done
  printf '%s\n' "${total}"
}

preflight_value() {
  local field="${1}"
  "${PYTHON_BIN}" -c \
    'import json,sys; data=json.load(open(sys.argv[1], encoding="utf-8")); print((data.get("routes", {}).get(sys.argv[2]) if sys.argv[2] != "status" else data.get("status")) or "")' \
    "${US_DSA_PREFLIGHT_STATUS}" "${field}"
}

run_provider_preflight() {
  local preflight_exit=0
  local summary=""
  local selected_llm=""
  local selected_market_data=""
  local selected_news=""
  local total

  if [[ "${US_DSA_PREFLIGHT_ENABLED}" != "1" ]]; then
    US_DSA_PREFLIGHT_RUNTIME_STATUS="disabled"
    log "action=skip_us_dsa_preflight reason=US_DSA_PREFLIGHT_ENABLED"
    return 0
  fi
  if [[ ! -f "${US_DSA_PREFLIGHT_SCRIPT}" ]]; then
    US_DSA_PREFLIGHT_RUNTIME_STATUS="blocked"
    log "action=finish_us_dsa_preflight status=blocked reason=script_missing path=${US_DSA_PREFLIGHT_SCRIPT}"
    if [[ "${US_DSA_PREFLIGHT_FAIL_CLOSED}" == "1" ]]; then
      total="$(count_stocks)"
      write_status "alert" 0 "${total}" "${total}" 69
      return 69
    fi
    return 0
  fi

  : > "${US_DSA_PREFLIGHT_LOG}"
  set +e
  "${PYTHON_BIN}" "${US_DSA_PREFLIGHT_SCRIPT}" \
    --output "${US_DSA_PREFLIGHT_STATUS}" \
    --region us \
    --gemini-key-file "${SECRETS_DIR}/gemini_api_key.txt" \
    --deepseek-key-file "${SECRETS_DIR}/deepseek_api_key.txt" \
    --tavily-key-file "${SECRETS_DIR}/tavily_api_key.txt" \
    --bocha-key-file "${SECRETS_DIR}/bocha_api_key.txt" \
    --proxy-host "${US_DSA_PREFLIGHT_PROXY_HOST}" \
    --proxy-port "${US_DSA_PREFLIGHT_PROXY_PORT}" \
    --timeout "${US_DSA_PREFLIGHT_TIMEOUT_SECONDS}" \
    --symbol "${US_DSA_PREFLIGHT_SYMBOL}" \
    >> "${US_DSA_PREFLIGHT_LOG}" 2>&1
  preflight_exit=$?
  set -e
  summary="$(tail -1 "${US_DSA_PREFLIGHT_LOG}" 2>/dev/null || true)"

  if [[ -f "${US_DSA_PREFLIGHT_STATUS}" ]]; then
    US_DSA_PREFLIGHT_RUNTIME_STATUS="$(preflight_value status 2>/dev/null || printf 'blocked')"
  else
    US_DSA_PREFLIGHT_RUNTIME_STATUS="blocked"
  fi
  log "action=finish_us_dsa_preflight exit=${preflight_exit} ${summary:-status=${US_DSA_PREFLIGHT_RUNTIME_STATUS}}"

  if [[ "${preflight_exit}" != "0" ]]; then
    if [[ "${US_DSA_PREFLIGHT_FAIL_CLOSED}" == "1" ]]; then
      total="$(count_stocks)"
      write_status "alert" 0 "${total}" "${total}" "${preflight_exit}"
      send_alert "DSA US preflight 阻断" "无可用 LLM/行情路由，今日 US 跑批中止（exit=${preflight_exit}）"
      return "${preflight_exit}"
    fi
    log "action=continue_us_dsa_after_preflight_failure fail_closed=0"
    return 0
  fi

  selected_llm="$(preflight_value llm)"
  selected_market_data="$(preflight_value market_data)"
  selected_news="$(preflight_value news)"
  if [[ "${selected_llm}" == "deepseek" ]]; then
    LITELLM_MODEL="deepseek/deepseek-chat"
    LITELLM_FALLBACK_MODELS=""
    export LITELLM_MODEL LITELLM_FALLBACK_MODELS
  fi
  if [[ "${selected_market_data}" == "nasdaq" ]]; then
    US_DSA_NASDAQ_PREFERRED="1"
    export US_DSA_NASDAQ_PREFERRED
  fi
  if [[ "${selected_news}" == "bocha" ]]; then
    TAVILY_API_KEYS=""
    export TAVILY_API_KEYS
  elif [[ -z "${selected_news}" ]]; then
    TAVILY_API_KEYS=""
    BOCHA_API_KEYS=""
    export TAVILY_API_KEYS BOCHA_API_KEYS
  fi
  log "action=select_us_dsa_routes llm=${selected_llm} market_data=${selected_market_data} news=${selected_news:-none}"
}

market_context_value() {
  local field="${1}"
  "${PYTHON_BIN}" -c \
    'import json,sys; data=json.load(open(sys.argv[1], encoding="utf-8")); value=data.get(sys.argv[2]); print("" if value is None else value)' \
    "${US_DSA_MARKET_CONTEXT_STATUS}" "${field}"
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
    "${US_DSA_DB_VERIFY_STATUS}" "${field}"
}

run_db_verify() {
  local verify_exit=0
  local analyzed_value=""
  local missing_value=""
  set +e
  "${PYTHON_BIN}" "${US_DSA_VERIFY_SCRIPT}" \
    --db "${US_DSA_DB_PATH}" \
    --stocks "${US_STOCKS}" \
    --since "${RUN_STARTED_AT}" \
    --output "${US_DSA_DB_VERIFY_STATUS}" >> "${US_DSA_LOG}" 2>&1
  verify_exit=$?
  set -e
  if [[ "${verify_exit}" != "0" && "${verify_exit}" != "3" ]]; then
    log "action=db_verify market=us status=error exit=${verify_exit}"
    return 1
  fi
  if ! analyzed_value="$(db_verify_value analyzed_count)"; then
    log "action=db_verify market=us status=readback_error field=analyzed_count"
    return 1
  fi
  if ! missing_value="$(db_verify_value missing)"; then
    log "action=db_verify market=us status=readback_error field=missing"
    return 1
  fi
  if [[ "${verify_exit}" == "3" && -z "${missing_value}" ]]; then
    log "action=db_verify market=us status=inconsistent exit=3 missing_readback=empty"
    return 1
  fi
  US_DSA_DB_VERIFIED_COUNT="${analyzed_value}"
  US_DSA_DB_MISSING="${missing_value}"
  log "action=db_verify market=us status=done verified=${US_DSA_DB_VERIFIED_COUNT} missing=${US_DSA_DB_MISSING:-none} since=${RUN_STARTED_AT}"
  return 0
}

finalize_run() {
  local mode_exit="${1}"
  local total
  local success
  local failed
  local status
  total="$(count_stocks)"

  if [[ "${US_DSA_DB_VERIFY_ENABLED}" != "1" ]]; then
    exit "${mode_exit}"
  fi

  if ! run_db_verify; then
    write_status "alert" 0 "${total}" "${total}" 72
    send_alert "DSA US 校验层异常" "无法核对 analysis_history，DB 真值未知（mode_exit=${mode_exit}），需人工检查"
    exit 72
  fi

  if [[ -n "${US_DSA_DB_MISSING}" && "${US_DSA_RETRY_ON_MISSING}" == "1" ]]; then
    US_DSA_RETRY_ROUNDS=1
    send_alert "DSA US 首轮缺口" "missing=${US_DSA_DB_MISSING}，${US_DSA_RETRY_DELAY_SECONDS}s 后自动重试一轮"
    log "action=schedule_retry market=us missing=${US_DSA_DB_MISSING} delay_seconds=${US_DSA_RETRY_DELAY_SECONDS}"
    if [[ -n "${CAFFEINATE_BIN}" ]]; then
      "${CAFFEINATE_BIN}" -i /bin/sleep "${US_DSA_RETRY_DELAY_SECONDS}" || /bin/sleep "${US_DSA_RETRY_DELAY_SECONDS}" || true
    else
      /bin/sleep "${US_DSA_RETRY_DELAY_SECONDS}" || true
    fi
    run_isolated_mode "${US_DSA_DB_MISSING}" 2 || true
    if ! run_db_verify; then
      write_status "alert" 0 "${total}" "${total}" 72
      send_alert "DSA US 校验层异常" "重试后无法核对 analysis_history，DB 真值未知，需人工检查"
      exit 72
    fi
  fi

  success="${US_DSA_DB_VERIFIED_COUNT:-0}"
  if [[ -n "${US_DSA_DB_MISSING}" ]]; then
    failed=$((total - success))
    write_status "alert" "${success}" "${failed}" "${total}" 70
    send_alert "DSA US 当日分析缺口" "missing=${US_DSA_DB_MISSING} success=${success}/${total}，重试 ${US_DSA_RETRY_ROUNDS} 轮后仍缺，需人工介入"
    log "action=finish_us_daily_run status=alert reason=db_missing missing=${US_DSA_DB_MISSING} success=${success} total=${total} retry_rounds=${US_DSA_RETRY_ROUNDS}"
    exit 70
  fi

  status="ok"
  if [[ "${US_DSA_RETRY_ROUNDS}" != "0" || "${mode_exit}" != "0" || "${US_DSA_PREFLIGHT_RUNTIME_STATUS}" == "degraded" ]]; then
    status="degraded"
  fi
  write_status "${status}" "${success}" 0 "${total}" 0
  log "action=finish_us_daily_run status=${status} source=db_verify success=${success} total=${total} retry_rounds=${US_DSA_RETRY_ROUNDS}"
  exit 0
}

run_market_context() {
  local context_exit=0
  local total
  local args=(
    "${US_DSA_MARKET_CONTEXT_SCRIPT}"
    "--region" "us"
    "--output" "${US_DSA_MARKET_CONTEXT_STATUS}"
    "--run-id" "us_dsa_${RUN_DATE}_$$"
  )
  if [[ "${US_DSA_MARKET_CONTEXT_FORCE_REFRESH}" == "1" ]]; then
    args+=("--force-refresh")
  fi
  if [[ "${US_DSA_FORCE_RUN}" != "1" ]]; then
    args+=("--skip-closed-market")
  fi
  if [[ "${US_DSA_MARKET_CONTEXT_NOTIFY}" == "1" ]]; then
    args+=("--notify")
  fi

  : > "${US_DSA_MARKET_CONTEXT_LOG}"
  log "action=start_us_market_context region=us log=${US_DSA_MARKET_CONTEXT_LOG}"
  set +e
  run_with_timeout "${US_DSA_MARKET_CONTEXT_TIMEOUT_SECONDS}" \
    "${PYTHON_BIN}" "${args[@]}" >> "${US_DSA_MARKET_CONTEXT_LOG}" 2>&1
  context_exit=$?
  set -e

  if [[ -f "${US_DSA_MARKET_CONTEXT_STATUS}" ]]; then
    US_DSA_MARKET_CONTEXT_RUNTIME_STATUS="$(market_context_value status 2>/dev/null || printf 'blocked')"
    US_DSA_MARKET_CONTEXT_ACTION="$(market_context_value action 2>/dev/null || printf 'unknown')"
  else
    US_DSA_MARKET_CONTEXT_RUNTIME_STATUS="blocked"
    US_DSA_MARKET_CONTEXT_ACTION="missing_status"
  fi
  log "action=finish_us_market_context region=us status=${US_DSA_MARKET_CONTEXT_RUNTIME_STATUS} action_detail=${US_DSA_MARKET_CONTEXT_ACTION} exit=${context_exit}"

  if [[ "${context_exit}" != "0" ]]; then
    total="$(count_stocks)"
    write_status "alert" 0 "${total}" "${total}" "${context_exit}"
    send_alert "DSA US 大盘上下文失败" "context exit=${context_exit}，今日 US 跑批中止，需人工介入"
    return "${context_exit}"
  fi
  if [[ "${US_DSA_MARKET_CONTEXT_RUNTIME_STATUS}" == "skipped" ]]; then
    return 0
  fi
  if [[ "${US_DSA_MARKET_CONTEXT_RUNTIME_STATUS}" != "ok" ]]; then
    total="$(count_stocks)"
    write_status "alert" 0 "${total}" "${total}" 68
    send_alert "DSA US 大盘上下文异常" "status=${US_DSA_MARKET_CONTEXT_RUNTIME_STATUS} action=${US_DSA_MARKET_CONTEXT_ACTION}，今日 US 跑批中止"
    return 68
  fi

  US_DSA_MARKET_CONTEXT_QUERY_ID="$(market_context_value query_id)"
  US_DSA_MARKET_CONTEXT_HISTORY_ID="$(market_context_value history_id)"
  if [[ -z "${US_DSA_MARKET_CONTEXT_QUERY_ID}" || -z "${US_DSA_MARKET_CONTEXT_HISTORY_ID}" ]]; then
    US_DSA_MARKET_CONTEXT_RUNTIME_STATUS="blocked"
    US_DSA_MARKET_CONTEXT_ACTION="invalid_contract"
    total="$(count_stocks)"
    write_status "alert" 0 "${total}" "${total}" 68
    send_alert "DSA US 大盘上下文契约异常" "query_id/history_id 缺失，今日 US 跑批中止"
    return 68
  fi
  export US_DSA_MARKET_CONTEXT_QUERY_ID
  log "action=select_us_market_context region=us query_id=${US_DSA_MARKET_CONTEXT_QUERY_ID} history_id=${US_DSA_MARKET_CONTEXT_HISTORY_ID}"
}

run_batch_mode() {
  log "proxy_check=${US_DSA_PROXY_RUNTIME_STATUS} action=start_us_daily_run mode=batch stocks=${US_STOCKS} force_run=${US_DSA_FORCE_RUN} context_query_id=${US_DSA_MARKET_CONTEXT_QUERY_ID} log=${US_DSA_LOG}"
  if run_dsa_main "${US_STOCKS}" >> "${US_DSA_LOG}" 2>&1; then
    local success_count
    local failure_count
    local expected_total
    local observed_total
    success_count="$(extract_success_count "${US_DSA_LOG}")"
    failure_count="$(extract_failure_count "${US_DSA_LOG}")"
    success_count="${success_count:-0}"
    failure_count="${failure_count:-0}"
    expected_total="$(count_stocks)"
    observed_total=$((success_count + failure_count))
    if [[ "${observed_total}" -lt "${expected_total}" ]]; then
      failure_count=$((failure_count + expected_total - observed_total))
    fi
    if [[ "${US_DSA_ALERT_ON_ZERO_SUCCESS}" == "1" && "${success_count}" == "0" && "${expected_total}" != "0" ]]; then
      write_status "alert" "${success_count}" "${failure_count}" "${expected_total}" 70
      log "action=finish_us_daily_run status=alert reason=zero_success success=${success_count} failed=${failure_count} log=${US_DSA_LOG} status_file=${US_DSA_STATUS}"
      return 70
    fi
    local batch_status="ok"
    if [[ "${failure_count}" != "0" || "${US_DSA_PREFLIGHT_RUNTIME_STATUS}" == "degraded" ]]; then
      batch_status="degraded"
    fi
    write_status "${batch_status}" "${success_count}" "${failure_count}" "${expected_total}" 0
    log "action=finish_us_daily_run status=${batch_status} success=${success_count} failed=${failure_count} log=${US_DSA_LOG} status_file=${US_DSA_STATUS}"
  else
    local status=$?
    write_status "failed" 0 0 0 "${status}"
    log "action=finish_us_daily_run status=failed exit=${status} log=${US_DSA_LOG} status_file=${US_DSA_STATUS}"
    return "${status}"
  fi
}

run_isolated_mode() {
  local stocks_csv="${1:-${US_STOCKS}}"
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
  log "proxy_check=${US_DSA_PROXY_RUNTIME_STATUS} action=start_us_daily_run mode=isolated round=${round} stocks=${stocks_csv} force_run=${US_DSA_FORCE_RUN} context_query_id=${US_DSA_MARKET_CONTEXT_QUERY_ID} timeout_seconds=${US_DSA_SINGLE_STOCK_TIMEOUT_SECONDS} log=${US_DSA_LOG}"

  for stock in "${stocks[@]}"; do
    normalized_stock="${stock//[[:space:]]/}"
    normalized_stock="$(printf "%s" "${normalized_stock}" | tr '[:lower:]' '[:upper:]')"
    if [[ -z "${normalized_stock}" ]]; then
      continue
    fi
    stock="${normalized_stock}"
    total=$((total + 1))
    if [[ "${round}" == "1" ]]; then
      stock_log="${LOG_DIR}/us_dsa_daily_${RUN_DATE}_${stock}.log"
    else
      stock_log="${LOG_DIR}/us_dsa_daily_${RUN_DATE}_${stock}_retry${round}.log"
    fi
    : > "${stock_log}"
    log "action=start_us_stock stock=${stock} context_query_id=${US_DSA_MARKET_CONTEXT_QUERY_ID} timeout_seconds=${US_DSA_SINGLE_STOCK_TIMEOUT_SECONDS} log=${stock_log}"
    {
      printf '\n===== US DSA stock=%s start=%s context_query_id=%s =====\n' \
        "${stock}" "$(timestamp)" "${US_DSA_MARKET_CONTEXT_QUERY_ID}"
    } >> "${US_DSA_LOG}"

    if run_with_timeout "${US_DSA_SINGLE_STOCK_TIMEOUT_SECONDS}" \
      run_dsa_main "${stock}" >> "${stock_log}" 2>&1; then
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
    log "action=finish_us_daily_run round=${round} status=alert reason=zero_success success=${success_count} failed=${failure_count} total=${total} log=${US_DSA_LOG}"
  elif [[ "${failure_count}" != "0" ]]; then
    status="degraded"
    log "action=finish_us_daily_run round=${round} status=degraded success=${success_count} failed=${failure_count} total=${total} log=${US_DSA_LOG}"
  elif [[ "${US_DSA_PREFLIGHT_RUNTIME_STATUS}" == "degraded" ]]; then
    status="degraded"
    log "action=finish_us_daily_run round=${round} status=degraded reason=preflight_degraded success=${success_count} failed=${failure_count} total=${total} log=${US_DSA_LOG}"
  else
    log "action=finish_us_daily_run round=${round} status=ok success=${success_count} failed=${failure_count} total=${total} log=${US_DSA_LOG}"
  fi

  if [[ "${round}" == "1" ]]; then
    write_status "${status}" "${success_count}" "${failure_count}" "${total}" "${final_exit}"
  fi
  return "${final_exit}"
}

mkdir -p "${LOG_DIR}"

if [[ "${US_DSA_SKIP_PROXY_CHECK}" != "1" ]]; then
  if ! /usr/bin/nc -z "${US_DSA_PREFLIGHT_PROXY_HOST}" "${US_DSA_PREFLIGHT_PROXY_PORT}"; then
    US_DSA_PROXY_RUNTIME_STATUS="fail"
    if [[ "${US_DSA_PREFLIGHT_ENABLED}" == "1" ]]; then
      log "proxy_check=fail host=${US_DSA_PREFLIGHT_PROXY_HOST} port=${US_DSA_PREFLIGHT_PROXY_PORT} action=continue_to_provider_preflight"
    else
      US_DSA_PREFLIGHT_RUNTIME_STATUS="blocked"
      total="$(count_stocks)"
      write_status "alert" 0 "${total}" "${total}" 75
      log "proxy_check=fail host=${US_DSA_PREFLIGHT_PROXY_HOST} port=${US_DSA_PREFLIGHT_PROXY_PORT} action=exit"
      exit 75
    fi
  else
    US_DSA_PROXY_RUNTIME_STATUS="ok"
  fi
else
  US_DSA_PROXY_RUNTIME_STATUS="skipped"
  log "proxy_check=skipped reason=US_DSA_SKIP_PROXY_CHECK"
fi

load_tavily_keys
load_bocha_keys
load_deepseek_fallback
export STOCK_LIST="${US_STOCKS}"
export MARKET_REVIEW_REGION="us"
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
if [[ "${US_DSA_MARKET_CONTEXT_RUNTIME_STATUS}" == "skipped" ]]; then
  write_status "skipped" 0 0 "$(count_stocks)" 0
  log "action=finish_us_daily_run status=skipped reason=market_closed"
  exit 0
fi

cd "${DSA_DIR}"
: > "${US_DSA_LOG}"
MODE_EXIT=0
if [[ "${US_DSA_ISOLATE_STOCKS}" == "1" ]]; then
  run_isolated_mode "${US_STOCKS}" || MODE_EXIT=$?
else
  run_batch_mode || MODE_EXIT=$?
fi
finalize_run "${MODE_EXIT}"
