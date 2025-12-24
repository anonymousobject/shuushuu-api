#!/bin/bash
# Multi-environment Docker Compose helper script

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENVIRONMENT="${1:-development}"

case "$ENVIRONMENT" in
    dev|development)
        echo "Starting in DEVELOPMENT mode (HTTP on localhost:3000)"
        # Copy dev env to .env for docker-compose to pick up
        cp "$SCRIPT_DIR/.env.development" "$SCRIPT_DIR/.env"
        docker compose -f "$SCRIPT_DIR/docker-compose.yml" \
                       -f "$SCRIPT_DIR/docker-compose.dev.yml" \
                       ${2:-up} ${@:3}
        ;;
    test)
        echo "Starting in TEST mode (HTTPS on test.shuushuu.com)"
        echo "⚠️  Make sure you have SSL certificates in ./docker/certbot/conf"
        # Copy test env to .env for docker-compose to pick up
        cp "$SCRIPT_DIR/.env.test" "$SCRIPT_DIR/.env"
        docker compose -f "$SCRIPT_DIR/docker-compose.yml" \
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
