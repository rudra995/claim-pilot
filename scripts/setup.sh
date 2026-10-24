#!/usr/bin/env bash
# One-time setup: Python 3.11 environment, trained models, frontend packages.
#   ./scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3.11}"
command -v "$PYTHON" >/dev/null || { echo "Python 3.11 is needed (set PYTHON=/path/to/python3.11)"; exit 1; }
[ -d .venv ] || "$PYTHON" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -e '.[dev]'

# The models are trained from the seeded synthetic data, so every machine gets the same ones.
[ -f models/denial.ubj ] || .venv/bin/python -m backend.models.train_denial
[ -f models/cost_bill_p50.ubj ] || .venv/bin/python -m backend.models.train_cost

command -v npm >/dev/null || { echo "Node 20+ and npm are needed for the frontend"; exit 1; }
(cd frontend && npm ci --silent)
[ -f .env ] || cp .env.example .env
echo "Setup done. Start with ./scripts/start.sh"
