#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/Users/yongyuanbuanzhede/quant"
PYTHON_BIN="${PROJECT_DIR}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="/usr/bin/python3"
fi

cd "${PROJECT_DIR}"
exec "${PYTHON_BIN}" -m dashboard.server "$@"
