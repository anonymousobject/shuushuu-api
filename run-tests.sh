#!/bin/bash
# Test runner script for shuushuu-api
# Usage: ./run-tests.sh [pytest args]

set -e

# Load environment variables from .env file if it exists
# This ensures test credentials stay in sync with actual database credentials
if [ -f .env ]; then
    echo "Loading database credentials from .env..."
    # Export only the variables we need for tests
    export $(grep -E '^(MARIADB_ROOT_PASSWORD|MARIADB_USER|MARIADB_PASSWORD)=' .env | xargs)
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

# Run pytest with all arguments passed through
uv run pytest "$@"
