#!/bin/bash
# Test runner script for shuushuu-api
# Usage: ./run-tests.sh [pytest args]
# With no args, runs the full suite in parallel (-n 4 --dist loadgroup).
# Pass any args (e.g. a test path) for a plain serial pytest run.
# Runs against the dev-stack Postgres container (docker compose up -d postgres
# first); each xdist worker gets its own shuushuu_pytest_<worker> database.

set -e

# Load environment variables from .env file if it exists
# This ensures test credentials stay in sync with actual database credentials
if [ -f .env ]; then
    echo "Loading database credentials from .env..."
    # Safely load variables from .env using Bash's own parser
    set -a
    . .env
    set +a
fi

# Credentials come from .env, falling back to the compose dev defaults. The
# app-level engine is pointed at the test DB too (mirrors CI) so nothing that
# reaches AsyncSessionLocal outside the get_db override can touch the dev
# database during a run.
PG_TEST_URL="postgresql+asyncpg://${POSTGRES_USER:-shuushuu}:${POSTGRES_PASSWORD:-pg_dev_password}@localhost:5432/shuushuu_pytest"
export TEST_DATABASE_URL="${TEST_DATABASE_URL:-$PG_TEST_URL}"
export DATABASE_URL="$TEST_DATABASE_URL"
echo "Running against Postgres ($TEST_DATABASE_URL)"

# Run pytest with all arguments passed through; default to the parallel
# sweet spot (see tests/README.md) when none are given
if [ $# -eq 0 ]; then
    uv run pytest -n 4 --dist loadgroup
else
    uv run pytest "$@"
fi
