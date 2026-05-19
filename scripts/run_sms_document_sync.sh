#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/smsrag}"
DATA_DIR="${SMSRAG_DATA_DIR:-/opt/sms-rag-index-data}"
ENV_FILE="${ENV_FILE:-${PROJECT_DIR}/.env}"
PYTHON_BIN="${PYTHON_BIN:-}"
CONTAINER_NAME="${CONTAINER_NAME:-sms-rag-app}"
RESTART_APP_AFTER_INDEX="${RESTART_APP_AFTER_INDEX:-true}"

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
RUN_LOG="$(mktemp)"
trap 'rm -f "$RUN_LOG"' EXIT

echo "[$(date -Is)] Starting SMS document sync"
set +e
"$PYTHON_BIN" tools/sync_sms_documents.py --data-dir "$DATA_DIR" 2>&1 | tee "$RUN_LOG"
SYNC_STATUS="${PIPESTATUS[0]}"
set -e

if [[ "$SYNC_STATUS" -ne 0 ]]; then
  echo "[$(date -Is)] SMS document sync failed with exit code ${SYNC_STATUS}"
  exit "$SYNC_STATUS"
fi

if grep -Eq "Indexed clients: [^n]" "$RUN_LOG"; then
  if [[ "${RESTART_APP_AFTER_INDEX,,}" == "true" ]]; then
    echo "[$(date -Is)] Index changed; restarting ${CONTAINER_NAME} so the app reloads retriever cache"
    docker restart "$CONTAINER_NAME"
  else
    echo "[$(date -Is)] Index changed; app restart skipped because RESTART_APP_AFTER_INDEX=${RESTART_APP_AFTER_INDEX}"
  fi
else
  echo "[$(date -Is)] No clients indexed; app restart not needed"
fi

echo "[$(date -Is)] Finished SMS document sync"
