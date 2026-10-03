#!/bin/bash
set -e

# Ensure data directory exists and is writable
DATA_DIR="${DATA_DIR:-/data}"
mkdir -p "$DATA_DIR"
export DATA_DIR

# Point SQLite into the volume if DATABASE_URL is not explicitly set.
# This keeps platform.db on the mounted volume across container restarts.
if [ -z "$DATABASE_URL" ]; then
    export DATABASE_URL="sqlite+aiosqlite:///${DATA_DIR}/platform.db"
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000 "$@"
