# Docker Compose environment management
# Run `make` or `make help` to see available targets

# Ensure bash is used for all commands (required for read -p in clean target)
SHELL := /bin/bash

.PHONY: help dev dev-up dev-down dev-logs dev-ps test test-up test-down test-logs test-ps test-build-frontend prod prod-up prod-down prod-logs prod-ps prod-build prod-build-frontend prod-migrate prod-restart prod-deploy clean

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
	@echo "  prod-migrate Apply DB migrations (run BEFORE prod-deploy when a release has one)"
	@echo "  prod-deploy  Zero-downtime rollout of app service(s) (default: api frontend)"
	@echo "  prod-restart Force-recreate service(s) — CAUSES DOWNTIME; use for nginx/infra"
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

# Zero-downtime rollout via the docker-rollout CLI plugin. Same files/env as
# COMPOSE_PROD so it acts on the identical merged config. Install once on the
# host (see docs/deployment.md):
#   curl -fsSL https://raw.githubusercontent.com/wowu/docker-rollout/main/docker-rollout \
#     -o ~/.docker/cli-plugins/docker-rollout && chmod +x ~/.docker/cli-plugins/docker-rollout
ROLLOUT_PROD = docker rollout -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod

# App services that support zero-downtime rollout: stateless, fronted by nginx
# which re-resolves their service name via Docker DNS at request time. Override
# by passing service names, e.g. `make prod-deploy api`.
ROLLOUT_SERVICES = api frontend
DEPLOY_SERVICES = $(if $(ARGS),$(ARGS),$(ROLLOUT_SERVICES))

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

# Apply database migrations as an explicit, gated step. Run this BEFORE
# prod-deploy when a release includes a migration. Migrations must be
# backward-compatible (expand/contract): during the rollout the old and new app
# versions briefly run against the same DB at once, so a column the old code
# still reads must not disappear yet. Defer destructive (contract) changes to a
# later release, once no old container remains.
prod-migrate:
	$(COMPOSE_PROD) build api
	$(COMPOSE_PROD) run --rm --no-deps api uv run --no-project alembic upgrade head

# Zero-downtime deploy: build the app image(s), then roll each service one at a
# time. docker-rollout starts a new replica, waits for its healthcheck, then
# drains the old one — nginx re-resolves the service name via Docker DNS, so no
# requests are dropped. Does NOT run migrations (see prod-migrate).
prod-deploy:
	$(COMPOSE_PROD) build $(DEPLOY_SERVICES)
	@for svc in $(DEPLOY_SERVICES); do \
		echo "==> Rolling out $$svc (zero-downtime)"; \
		$(ROLLOUT_PROD) $$svc || exit 1; \
	done

# Force-recreate service(s). This is the OLD deploy path and CAUSES a brief
# outage (stop-then-start with no overlap), and with no args it recreates nginx
# and everything else. Use it for nginx/config/infra changes, not app code —
# for api/frontend use prod-deploy.
prod-restart:
	$(COMPOSE_PROD) up -d --force-recreate $(ARGS)

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
