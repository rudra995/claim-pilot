#!/usr/bin/env bash
# Start the API (port 8000) and the frontend (port 5173). Ctrl+C stops both.
#   ./scripts/start.sh
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
[ -x .venv/bin/python ] || { echo "Run ./scripts/setup.sh first"; exit 1; }

.venv/bin/python -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000 &
API=$!
(cd frontend && npm run dev -- --host 127.0.0.1) &
WEB=$!
trap 'kill $API $WEB 2>/dev/null' EXIT INT TERM
echo "API:      http://127.0.0.1:8000/api/ready"
echo "Frontend: http://127.0.0.1:5173"
wait
