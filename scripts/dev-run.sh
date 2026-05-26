#!/usr/bin/env bash
# Локальный запуск без GPIO (для отладки UI на ноуте).
set -e
cd "$(dirname "$0")/.."
python3 -m venv .venv 2>/dev/null || true
.venv/bin/pip install -q -r backend/requirements.txt 2>/dev/null || \
  .venv/bin/pip install -q fastapi 'uvicorn[standard]' pydantic
GIMLI_MOCK_MOTORS=1 .venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8080 --reload
