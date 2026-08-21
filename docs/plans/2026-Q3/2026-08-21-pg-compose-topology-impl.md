# Prod-shaped Postgres compose topology — plan

**Date:** 2026-08-21
**Follows:** docs/postgres-cutover-runbook.md (whose flip step this makes concrete)

## What ships

1. `postgres` service in the main `docker-compose.yml`, mirroring the mariadb
   conventions: pinned `${POSTGRES_IMAGE:-postgres:17.11}`, env-var
   credentials with dev defaults, tuning knobs
   (`POSTGRES_SHARED_BUFFERS`/`EFFECTIVE_CACHE_SIZE`), named `postgres_data`
   volume, `pg_isready` healthcheck, resource limits, **localhost-only** port
   publish (dev tooling; containers use `postgres:5432` in-network). Dev
   override adds `-dev` container/volume names and an api `depends_on`.
   Prod is unaffected: its DB tier is native/out-of-stack
   (`docker-compose.prod.yml` stubs it and passes `DATABASE_URL` through).
2. `COMPOSE_DATABASE_URL` selects the api/arq database for the dual window:
   unset → MariaDB (built from `MARIADB_*` as before); set → Postgres. This
   retires `docker-compose.postgres.yml` and `docker-compose.postgres-api.yml`
   (deleted), whose hardcoded creds, published-to-all-interfaces port, and
   cross-project `host.docker.internal` routing were POC scaffolding.
3. `.env.example` gains the `POSTGRES_*` block and `COMPOSE_DATABASE_URL`;
   runbook flip step rewritten with the concrete per-environment mechanics;
   `run-tests.sh`/`scripts/postgres_poc.py` references updated.

## The incident this surfaced

During the dev switchover, the two-day POC container turned out to have kept
its cluster **outside** the named volume its compose file declared (the
prefixed project volume provably did not exist until the switchover day), so
removing the container destroyed the dev Postgres data. Recovery: the
rehearsed cutover runbook, from the untouched MariaDB source — which is the
system working as designed. Verification for the new topology therefore
includes an explicit persistence check: recreate the postgres container and
confirm the data survives in `postgres_data_dev`.

## Acceptance

Compose configs validate (dev merge and prod merge); dev stack runs on the
new `postgres` service with `COMPOSE_DATABASE_URL` set, serving the full
migrated dataset through nginx; data survives `docker compose up -d
--force-recreate postgres`; `./run-tests.sh --pg tests/unit` green against
the new service.
