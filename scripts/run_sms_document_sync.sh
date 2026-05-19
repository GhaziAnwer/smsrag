#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/smsrag}"
DATA_DIR="${SMSRAG_DATA_DIR:-/opt/sms-rag-index-data}"
ENV_FILE="${ENV_FILE:-${PROJECT_DIR}/.env}"
PYTHON_BIN="${PYTHON_BIN:-}"

cd "$PROJECT_DIR"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "/home/ubuntu/.conda/envs/py311/bin/python" ]]; then
    PYTHON_BIN="/home/ubuntu/.conda/envs/py311/bin/python"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
  else
    PYTHON_BIN="$(command -v python3)"
  fi
fi

mkdir -p "${DATA_DIR}/_document_sync_logs"

echo "[$(date -Is)] Starting SMS document sync"
"$PYTHON_BIN" tools/sync_sms_documents.py --data-dir "$DATA_DIR"
echo "[$(date -Is)] Finished SMS document sync"
