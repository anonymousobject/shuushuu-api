#!/usr/bin/env bash
#
# Run arq worker for local development
#

set -e

echo "Starting arq worker..."
echo "Redis: ${ARQ_REDIS_URL:-redis://localhost:6379/1}"
echo ""

uv run arq app.tasks.worker.WorkerSettings --verbose
