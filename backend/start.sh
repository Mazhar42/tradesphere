#!/bin/sh
# Start sequence for a single-container host (Render): migrate, seed reference data, create the
# first owner if none exists, then serve on $PORT (set by the host; 8000 when run locally).
set -eu

alembic upgrade head
python -m app.cli seed
python -m app.cli bootstrap-owner

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1 \
	--no-proxy-headers --no-access-log
