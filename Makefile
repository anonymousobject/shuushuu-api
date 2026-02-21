# Docker Compose environment management
# Run `make` or `make help` to see available targets

# Ensure bash is used for all commands (required for read -p in clean target)
SHELL := /bin/bash

.PHONY: help dev dev-up dev-down dev-logs dev-ps test test-up test-down test-logs test-ps test-build-frontend prod prod-up prod-down prod-logs prod-ps prod-build prod-build-frontend prod-restart clean

# Capture extra arguments for logs commands (e.g., `make dev-logs api`)
ARGS = $(filter-out $@,$(MAKECMDGOALS))

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
	@echo "Production (HTTPS on e-shuushuu.net):"
	@echo "  prod         Start production environment (foreground)"
	@echo "  prod-up      Start production environment (background)"
	@echo "  prod-down    Stop production environment"
	@echo "  prod-logs    Follow all logs"
	@echo "  prod-ps      Show running containers"
	@echo "  prod-build   Build all production images"
	@echo "  prod-build-frontend  Rebuild frontend image"
	@echo "  prod-restart Recreate service(s) (e.g., make prod-restart frontend)"
	@echo ""
	@echo "Other:"
	@echo "  clean        Stop all and remove volumes (DESTRUCTIVE)"
	@echo ""
	@echo "Tip: Follow specific service logs with:"
	@echo "  make dev-logs api"
	@echo "  make dev-logs api arq-worker"

# Common compose commands
# Dev uses .env by default (docker-compose's default behavior)
COMPOSE_DEV = docker compose -f docker-compose.yml -f docker-compose.override.yml
COMPOSE_TEST = docker compose -f docker-compose.yml -f docker-compose.test.yml --env-file .env.test
COMPOSE_PROD = docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod

# Development targets
dev:
	$(COMPOSE_DEV) up

dev-up:
	$(COMPOSE_DEV) up -d

dev-down:
	$(COMPOSE_DEV) down

dev-logs:
	$(COMPOSE_DEV) logs --tail 40 -f $(ARGS)

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
	$(COMPOSE_TEST) logs --tail 40 -f $(ARGS)

test-ps:
	$(COMPOSE_TEST) ps

test-build-frontend:
	$(COMPOSE_TEST) build --no-cache frontend

# Production targets
prod:
	$(COMPOSE_PROD) up

prod-up:
	$(COMPOSE_PROD) up -d

prod-down:
	$(COMPOSE_PROD) down

prod-logs:
	$(COMPOSE_PROD) logs --tail 40 -f $(ARGS)

prod-ps:
	$(COMPOSE_PROD) ps

prod-build:
	$(COMPOSE_PROD) build

prod-build-frontend:
	$(COMPOSE_PROD) build --no-cache frontend

prod-restart:
	$(COMPOSE_PROD) up -d $(ARGS)

# Cleanup (removes volumes - use with caution)
clean:
	@echo "This will stop containers and DELETE ALL DATA (volumes)."
	@read -p "Are you sure? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	$(COMPOSE_DEV) down -v 2>/dev/null || true
	$(COMPOSE_TEST) down -v 2>/dev/null || true
	$(COMPOSE_PROD) down -v 2>/dev/null || true

# Catch-all to allow passing service names as arguments (e.g., `make dev-logs api`)
%:
	@:
