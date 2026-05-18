#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/smsrag}"
HOST_PORT="${SMSRAG_HOST_PORT:-8010}"
DATA_DIR="${SMSRAG_DATA_DIR:-/opt/sms-rag-index-data}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
CONTAINER_NAME="${CONTAINER_NAME:-sms-rag-app}"

cd "$PROJECT_DIR"

echo "[smsrag] Project: $PROJECT_DIR"
echo "[smsrag] Host port: $HOST_PORT"
echo "[smsrag] Data dir: $DATA_DIR"

if docker ps --format '{{.Names}}' | grep -Fxq "$CONTAINER_NAME"; then
  echo "[smsrag] Existing $CONTAINER_NAME container found. It will be recreated by docker compose."
fi

if ss -tln | awk '{print $4}' | grep -Eq "(:|\\.)${HOST_PORT}$"; then
  owner="$(docker ps --format '{{.Names}} {{.Ports}}' | grep -F ":${HOST_PORT}->" || true)"
  if [[ "$owner" != *"$CONTAINER_NAME"* ]]; then
    echo "[smsrag] ERROR: host port $HOST_PORT is already in use."
    echo "$owner"
    echo "[smsrag] Choose another port: SMSRAG_HOST_PORT=8011 $0"
    exit 1
  fi
fi

if [[ ! -f .env ]]; then
  echo "[smsrag] ERROR: .env not found in $PROJECT_DIR"
  echo "[smsrag] Create it first. Minimum:"
  echo "OPENAI_API_KEY=your_key"
  echo "SMSRAG_HOST_PORT=$HOST_PORT"
  echo "SMSRAG_DATA_DIR=$DATA_DIR"
  exit 1
fi

if ! grep -q '^OPENAI_API_KEY=' .env; then
  echo "[smsrag] ERROR: OPENAI_API_KEY is missing from .env"
  exit 1
fi

mkdir -p "$DATA_DIR"
touch chat_history.db feedback.db query_logs.db

export SMSRAG_HOST_PORT="$HOST_PORT"
export SMSRAG_DATA_DIR="$DATA_DIR"

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose -f "$COMPOSE_FILE")
else
  COMPOSE=(docker-compose -f "$COMPOSE_FILE")
fi

echo "[smsrag] Building and starting only this compose project..."
"${COMPOSE[@]}" up -d --build app

echo "[smsrag] Waiting for health check..."
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${HOST_PORT}/" >/dev/null; then
    echo "[smsrag] Healthy: http://127.0.0.1:${HOST_PORT}/"
    echo "[smsrag] Container status:"
    docker ps --filter "name=${CONTAINER_NAME}" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
    exit 0
  fi
  sleep 2
done

echo "[smsrag] ERROR: health check failed. Recent logs:"
docker logs --tail 80 "$CONTAINER_NAME" || true
exit 1
