# Docker Compose environment management
# Run `make` or `make help` to see available targets

# Ensure bash is used for all commands (required for read -p in clean target)
SHELL := /bin/bash

.PHONY: help dev dev-up dev-down dev-logs dev-ps test test-up test-down test-logs test-ps test-build-frontend clean

# Default target
help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Development (HTTP on localhost:3000):"
	@echo "  dev          Start development environment (foreground)"
	@echo "  dev-up       Start development environment (background)"
	@echo "  dev-down     Stop development environment"
	@echo "  dev-logs     Follow all logs"
	@echo "  dev-ps       Show running containers"
	@echo ""
	@echo "Test (HTTPS on test.shuushuu.com):"
	@echo "  test         Start test environment (foreground)"
	@echo "  test-up      Start test environment (background)"
	@echo "  test-down    Stop test environment"
	@echo "  test-logs    Follow all logs"
	@echo "  test-ps      Show running containers"
	@echo "  test-build-frontend     Rebuild frontend image"
	@echo ""
	@echo "Other:"
	@echo "  clean        Stop all and remove volumes (DESTRUCTIVE)"
	@echo ""
	@echo "Tip: Follow specific service logs with:"
	@echo "  make dev-logs s=api"
	@echo "  make dev-logs s='api arq-worker'"

# Common compose commands
COMPOSE_DEV = docker compose -f docker-compose.yml -f docker-compose.override.yml --env-file .env.development
COMPOSE_TEST = docker compose -f docker-compose.yml -f docker-compose.test.yml --env-file .env.test

# Development targets
dev:
	$(COMPOSE_DEV) up

dev-up:
	$(COMPOSE_DEV) up -d

dev-down:
	$(COMPOSE_DEV) down

dev-logs:
	$(COMPOSE_DEV) logs -f $(s)

dev-ps:
	$(COMPOSE_DEV) ps

# Test targets
test:
	$(COMPOSE_TEST) up

test-up:
	$(COMPOSE_TEST) up -d

test-down:
	$(COMPOSE_TEST) down

test-logs:
	$(COMPOSE_TEST) logs -f $(s)

test-ps:
	$(COMPOSE_TEST) ps

test-build-frontend:
	$(COMPOSE_TEST) build --no-cache frontend

# Cleanup (removes volumes - use with caution)
clean:
	@echo "This will stop containers and DELETE ALL DATA (volumes)."
	@read -p "Are you sure? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	$(COMPOSE_DEV) down -v 2>/dev/null || true
	$(COMPOSE_TEST) down -v 2>/dev/null || true
