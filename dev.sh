#!/bin/bash
# Multi-environment Docker Compose helper script
# If you get a "Permission denied" error when running this script, run: chmod +x dev.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENVIRONMENT="${1:-development}"

case "$ENVIRONMENT" in
    dev|development)
        echo "Starting in DEVELOPMENT mode (HTTP on localhost:3000)"
        docker compose --env-file "$SCRIPT_DIR/.env.development" \
                       -f "$SCRIPT_DIR/docker-compose.yml" \
                       -f "$SCRIPT_DIR/docker-compose.dev.yml" \
                       ${2:-up} ${@:3}
        ;;
    test)
        echo "Starting in TEST mode (HTTPS on test.shuushuu.com)"
        echo "⚠️  Make sure you have SSL certificates in ./docker/certbot/conf"
        docker compose --env-file "$SCRIPT_DIR/.env.test" \
                       -f "$SCRIPT_DIR/docker-compose.yml" \
                       -f "$SCRIPT_DIR/docker-compose.test.yml" \
                       --profile test \
                       ${2:-up} ${@:3}
        ;;
    *)
        echo "Usage: $0 [dev|test] [command]"
        echo ""
        echo "Examples:"
        echo "  $0 dev up              # Start development environment"
        echo "  $0 dev down            # Stop development environment"
        echo "  $0 test up -d          # Start test environment in background"
        echo "  $0 test logs api       # View API logs in test environment"
        echo "  $0 test up -d --build frontend  # Start test environment with frontend build"
        echo ""
        exit 1
        ;;
esac
