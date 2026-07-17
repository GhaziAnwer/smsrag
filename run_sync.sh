#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/opt/sms-rag"
CONTAINER_NAME="sms-rag-app"
RESTART_APP_AFTER_INDEX="${RESTART_APP_AFTER_INDEX:-true}"

cd "$PROJECT_DIR"

# Load env
set -a
. "$PROJECT_DIR/.env"
set +a

# Validate required vars
: "${SMS_DOCS_REPO_URL:?SMS_DOCS_REPO_URL is required}"
: "${OPENAI_API_KEY:?OPENAI_API_KEY is required}"

mkdir -p "${PROJECT_DIR}/logs"
RUN_LOG="$(mktemp)"
trap 'rm -f "$RUN_LOG"' EXIT

echo "[$(date -Is)] Starting SMS document sync"
set +e
python3 "$PROJECT_DIR/tools/sync_sms_documents.py" \
  --data-dir "${SMS_RAG_DATA_DIR:-/opt/sms-rag/data}" \
  --rules "${SMS_RAG_RULES:-/opt/sms-rag/data/rules.yaml}" \
  --clients "${SMS_RAG_CLIENTS}" \
  "$@" 2>&1 | tee "$RUN_LOG"
SYNC_STATUS="${PIPESTATUS[0]}"
set -e

if [[ "$SYNC_STATUS" -ne 0 ]]; then
  echo "[$(date -Is)] SMS document sync failed with exit code ${SYNC_STATUS}"
  exit "$SYNC_STATUS"
fi

# Restart container if any client was indexed
if grep -Eq "Indexed clients: [^n]" "$RUN_LOG"; then
  if [[ "${RESTART_APP_AFTER_INDEX,,}" == "true" ]]; then
    echo "[$(date -Is)] Index changed; restarting ${CONTAINER_NAME}"
    docker restart "$CONTAINER_NAME"
  else
    echo "[$(date -Is)] Index changed; app restart skipped"
  fi
else
  echo "[$(date -Is)] No clients indexed; app restart not needed"
fi

echo "[$(date -Is)] Finished SMS document sync"
