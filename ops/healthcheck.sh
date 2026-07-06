#!/usr/bin/env bash
set -u

PROJECT_DIR="/Users/yongyuanbuanzhede/Documents/量化系统"
DSA_DIR="${PROJECT_DIR}/vendor/daily_stock_analysis"
DB_PATH="${PROJECT_DIR}/runtime_data/dsa/stock_analysis.db"
LOG_DIR="${PROJECT_DIR}/runtime_data/logs"
REPORT_DIR="${DSA_DIR}/reports"
EXPECTED_STOCKS="${EXPECTED_STOCKS:-5}"
MIN_DAILY_ROWS="${MIN_DAILY_ROWS:-3}"
MAX_DAILY_ROWS="${MAX_DAILY_ROWS:-7}"

default_check_date() {
  if [[ "$(date "+%u")" == "1" ]]; then
    date -v-3d "+%Y-%m-%d"
  else
    date -v-1d "+%Y-%m-%d"
  fi
}

if [[ $# -gt 0 ]]; then
  CHECK_DATE="$1"
else
  CHECK_DATE="$(default_check_date)"
fi

CHECK_DATE_COMPACT="${CHECK_DATE//-/}"
REPORT_FILE="${REPORT_DIR}/report_${CHECK_DATE_COMPACT}.md"
LOG_FILE="${LOG_DIR}/stock_analysis_${CHECK_DATE_COMPACT}.log"
STATUS=0

pass() {
  printf "PASS %s\n" "$*"
}

fail() {
  printf "FAIL %s\n" "$*" >&2
  STATUS=1
}

check_report() {
  if [[ -s "${REPORT_FILE}" ]]; then
    pass "report_exists path=${REPORT_FILE}"
  else
    fail "report_missing path=${REPORT_FILE}"
  fi
}

count_rows() {
  local table="$1"
  local column="$2"
  /usr/bin/sqlite3 -readonly "${DB_PATH}" "select count(*) from ${table} where date(${column}) = '${CHECK_DATE}';"
}

check_table_count() {
  local table="$1"
  local column="$2"
  local count

  if [[ ! -f "${DB_PATH}" ]]; then
    fail "db_missing path=${DB_PATH}"
    return
  fi

  if ! count="$(count_rows "${table}" "${column}")"; then
    fail "db_query_failed table=${table}"
    return
  fi

  if (( count >= MIN_DAILY_ROWS && count <= MAX_DAILY_ROWS )); then
    pass "table_count table=${table} date=${CHECK_DATE} rows=${count} expected_around=${EXPECTED_STOCKS}"
  else
    fail "table_count table=${table} date=${CHECK_DATE} rows=${count} expected_range=${MIN_DAILY_ROWS}-${MAX_DAILY_ROWS}"
  fi
}

check_errors() {
  local errors

  if [[ ! -f "${LOG_FILE}" ]]; then
    fail "log_missing path=${LOG_FILE}"
    return
  fi

  errors="$(/usr/bin/grep -c "ERROR" "${LOG_FILE}" || true)"
  if [[ "${errors}" == "0" ]]; then
    pass "log_errors date=${CHECK_DATE} count=0"
  else
    fail "log_errors date=${CHECK_DATE} count=${errors} path=${LOG_FILE}"
  fi
}

check_proxy() {
  if /usr/bin/nc -z 127.0.0.1 7890; then
    pass "proxy_listening host=127.0.0.1 port=7890"
  else
    fail "proxy_not_listening host=127.0.0.1 port=7890"
  fi
}

printf "DSA healthcheck date=%s expected_stocks=%s\n" "${CHECK_DATE}" "${EXPECTED_STOCKS}"
check_report
check_table_count "analysis_history" "created_at"
check_table_count "decision_signals" "created_at"
check_table_count "llm_usage" "called_at"
check_errors
check_proxy

exit "${STATUS}"
