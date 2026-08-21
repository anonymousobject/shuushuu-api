#!/bin/bash
# Test runner script for shuushuu-api
# Usage: ./run-tests.sh [--pg] [pytest args]
# With no args, runs the full suite in parallel (-n 4 --dist loadgroup).
# Pass any args (e.g. a test path) for a plain serial pytest run.
# --pg runs against the dev-stack Postgres container instead of MariaDB
# (docker compose up -d postgres first).

set -e

PG_MODE=0
if [ "$1" = "--pg" ]; then
    shift
    PG_MODE=1
fi

# Load environment variables from .env file if it exists
# This ensures test credentials stay in sync with actual database credentials
if [ -f .env ]; then
    echo "Loading database credentials from .env..."
    # Safely load variables from .env using Bash's own parser
    set -a
    . .env
    set +a
fi

if [ "$PG_MODE" = "1" ]; then
    # After .env so these win. Runs against the dev-stack Postgres container
    # (docker compose up -d postgres first).
    PG_TEST_URL="postgresql+asyncpg://shuushuu:pg_dev_password@localhost:5432/shuushuu_pytest"
    export TEST_DATABASE_URL="$PG_TEST_URL"
    # Mirror CI: point the app-level engine at the test DB too, so nothing
    # reaching AsyncSessionLocal outside the get_db override can touch the
    # dev database (or pick the wrong dialect) during a test run.
    export DATABASE_URL="$PG_TEST_URL"
    echo "Running against Postgres ($PG_TEST_URL)"
fi

# Set test-specific credentials (can be overridden by environment)
# These default to production user credentials if not explicitly set
export TEST_DB_USER=${TEST_DB_USER:-${MARIADB_USER:-shuushuu}}
export TEST_DB_PASSWORD=${TEST_DB_PASSWORD:-${MARIADB_PASSWORD:-shuushuu_password}}

echo "Running tests with:"
echo "  Root password: ${MARIADB_ROOT_PASSWORD:+***set***}"
echo "  Test user: $TEST_DB_USER"
echo "  Test password: ${TEST_DB_PASSWORD:+***set***}"
echo ""

# Run pytest with all arguments passed through; default to the parallel
# sweet spot (see tests/README.md) when none are given
if [ $# -eq 0 ]; then
    uv run pytest -n 4 --dist loadgroup
else
    uv run pytest "$@"
fi
