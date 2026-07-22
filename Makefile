# Docker Compose environment management
# Run `make` or `make help` to see available targets

# Ensure bash is used for all commands (required for read -p in clean target)
SHELL := /bin/bash

.PHONY: help dev dev-up dev-down dev-logs dev-ps test test-up test-down test-logs test-ps test-build-frontend pytest pytest-db-up pytest-db-down prod prod-up prod-down prod-logs prod-ps prod-build prod-build-frontend prod-migrate prod-restart prod-deploy clean check-env-test check-env-prod

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
	@echo "Python test suite (isolated DB on :3316):"
	@echo "  pytest       Run the pytest suite (-n auto) against an isolated MariaDB"
	@echo "  pytest-db-up   Start the isolated pytest MariaDB"
	@echo "  pytest-db-down Stop the isolated pytest MariaDB"
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
# One env per host: every environment reads the host's .env (docker-compose's
# default behavior). The check-env-* guards below assert the .env matches the
# stack being started, replacing the safety the old per-env filenames provided.
COMPOSE_DEV = docker compose -f docker-compose.yml -f docker-compose.override.yml
COMPOSE_TEST = docker compose -f docker-compose.yml -f docker-compose.test.yml
COMPOSE_PROD = docker compose -f docker-compose.yml -f docker-compose.prod.yml

# Zero-downtime rollout via the docker-rollout CLI plugin. Same files/env as
# COMPOSE_PROD so it acts on the identical merged config. Install once on the
# host (see docs/deployment.md); pinned to a release tag, not main, to avoid
# silently picking up upstream changes:
#   curl -fsSL https://raw.githubusercontent.com/wowu/docker-rollout/v0.13/docker-rollout \
#     -o ~/.docker/cli-plugins/docker-rollout && chmod +x ~/.docker/cli-plugins/docker-rollout
#
# --timeout 90: the new replica's healthcheck can take up to ~50s to pass
# (start_period 20s + interval 10s x 3 retries); 90 leaves headroom above that
# and self-documents the expected window (plugin default is only 60s).
ROLLOUT_PROD = docker rollout --timeout 90 -f docker-compose.yml -f docker-compose.prod.yml

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

# Guards: with a single .env per host, the filename no longer encodes the
# environment, so assert the config matches the stack before acting on it
# (stops e.g. `make prod` on the dev box from consuming the dev .env).
# Read-only targets (down/logs/ps) stay unguarded so cleanup always works.
check-env-test:
	@grep -q '^DOMAIN=test\.shuushuu\.com' .env || \
		{ echo "ERROR: .env is not the test config (expected DOMAIN=test.shuushuu.com) — wrong host?"; exit 1; }

check-env-prod:
	@grep -q '^NGINX_HOST=e-shuushuu\.net' .env || \
		{ echo "ERROR: .env is not the prod config (expected NGINX_HOST=e-shuushuu.net) — wrong host?"; exit 1; }

# Test targets
test: check-env-test
	$(COMPOSE_TEST) up

test-up: check-env-test
	$(COMPOSE_TEST) up -d

test-down:
	$(COMPOSE_TEST) down

test-logs:
	$(COMPOSE_TEST) logs --tail 40 -f $(ARGS)

test-ps:
	$(COMPOSE_TEST) ps

test-build-frontend: check-env-test
	$(COMPOSE_TEST) build --no-cache frontend

# pytest targets — run the Python unit/integration suite against an isolated,
# right-sized MariaDB (docker-compose.pytest.yml) instead of the shared,
# memory-saturated dev container. See that file's header for the OOM root cause
# this avoids. This is the supported way to run `pytest -n auto` locally.
COMPOSE_PYTEST = docker compose -f docker-compose.pytest.yml
# Pin the suite at the isolated DB (port 3316) regardless of what .env holds.
# DATABASE_URL is what the app engine builds from at import; the TEST_DB_*
# components are what conftest builds the test engine AND the root admin engine
# (which creates the per-worker databases) from -- the root path reads
# TEST_DB_HOST/TEST_DB_PORT, not TEST_DATABASE_URL, so set the components.
PYTEST_DB_ENV = \
	DATABASE_URL="mysql+aiomysql://shuushuu:shuushuu_password@127.0.0.1:3316/shuushuu_pytest?charset=utf8mb4" \
	DATABASE_URL_SYNC="mysql+pymysql://shuushuu:shuushuu_password@127.0.0.1:3316/shuushuu_pytest?charset=utf8mb4" \
	TEST_DB_HOST=127.0.0.1 \
	TEST_DB_PORT=3316 \
	TEST_DB_USER=shuushuu \
	TEST_DB_PASSWORD=shuushuu_password \
	TEST_DB_NAME=shuushuu_pytest \
	MARIADB_ROOT_PASSWORD=root_password

pytest-db-up:
	$(COMPOSE_PYTEST) up -d --wait

pytest-db-down:
	$(COMPOSE_PYTEST) down

pytest: pytest-db-up
	$(PYTEST_DB_ENV) uv run pytest -n auto --dist loadgroup $(ARGS)

# Production targets
prod: check-env-prod
	$(COMPOSE_PROD) up

prod-up: check-env-prod
	$(COMPOSE_PROD) up -d

prod-down:
	$(COMPOSE_PROD) down

prod-logs:
	$(COMPOSE_PROD) logs --tail 40 -f $(ARGS)

prod-ps:
	$(COMPOSE_PROD) ps

prod-build: check-env-prod
	$(COMPOSE_PROD) build

prod-build-frontend: check-env-prod
	$(COMPOSE_PROD) build --no-cache frontend

# Apply database migrations as an explicit, gated step. Run this BEFORE
# prod-deploy when a release includes a migration. Migrations must be
# backward-compatible (expand/contract): during the rollout the old and new app
# versions briefly run against the same DB at once, so a column the old code
# still reads must not disappear yet. Defer destructive (contract) changes to a
# later release, once no old container remains.
prod-migrate: check-env-prod
	# prod-deploy rebuilds too; this build ensures the migrator runs the NEW
	# migration code even if prod-migrate is run on its own. The duplicate
	# build is a cache-cheap no-op.
	$(COMPOSE_PROD) build api
	$(COMPOSE_PROD) run --rm --no-deps api uv run --no-project alembic upgrade head

# Zero-downtime deploy: build the app image(s), then roll each service one at a
# time. docker-rollout starts a new replica, waits for its healthcheck, then
# drains the old one — nginx re-resolves the service name via Docker DNS, so no
# requests are dropped. Does NOT run migrations (see prod-migrate).
prod-deploy: check-env-prod
	$(COMPOSE_PROD) build $(DEPLOY_SERVICES)
	@for svc in $(DEPLOY_SERVICES); do \
		echo "==> Rolling out $$svc (zero-downtime)"; \
		$(ROLLOUT_PROD) $$svc || exit 1; \
	done

# Force-recreate service(s). This is the OLD deploy path and CAUSES a brief
# outage (stop-then-start with no overlap), and with no args it recreates nginx
# and everything else. Use it for nginx/config/infra changes, not app code —
# for api/frontend use prod-deploy.
prod-restart: check-env-prod
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
