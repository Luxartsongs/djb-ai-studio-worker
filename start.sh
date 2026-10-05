#!/usr/bin/env bash
set -euo pipefail

echo "[DJB] Starting ACE-Step API..."

cd /app

/app/.venv/bin/python -m acestep.api_server \
    --host 127.0.0.1 \
    --port 8001 &

ACESTEP_PID=$!

cleanup() {
    echo "[DJB] Shutting down ACE-Step..."
    kill "${ACESTEP_PID}" 2>/dev/null || true
}

trap cleanup EXIT SIGTERM SIGINT

echo "[DJB] Waiting for ACE-Step API..."

for i in $(seq 1 300); do

    if curl -fsS http://127.0.0.1:8001/health >/dev/null 2>&1; then
        echo "[DJB] ACE-Step API ready."
        break
    fi

    if ! kill -0 "${ACESTEP_PID}" 2>/dev/null; then
        echo "[DJB] ERROR: ACE-Step API stopped during startup."
        wait "${ACESTEP_PID}"
        exit 1
    fi

    if [ "${i}" -eq 300 ]; then
        echo "[DJB] ERROR: ACE-Step API startup timeout."
        exit 1
    fi

    sleep 2
done

echo "[DJB] Starting RunPod Serverless worker..."

exec /app/.venv/bin/python -u /app/djb/handler.py
