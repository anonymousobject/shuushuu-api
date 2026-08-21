# Alembic Postgres Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Postgres schema comes from an Alembic chain (`alembic_pg/`, frozen-DDL baseline), test databases build from it, and a PG schema-sync test enforces model↔chain parity through the dual window.

**Architecture / Spec:** docs/plans/2026-Q3/2026-08-21-alembic-pg-baseline-design.md

## Global Constraints

- MariaDB tooling byte-untouched: its chain, config, conftest path, CI invocations, `alembic heads` all behave exactly as before.
- The baseline migration and its companion SQL are frozen once merged (repo migration rule); `scripts/gen_pg_baseline.py` exists for the initial generation and is not rerun against a merged baseline.
- `mypy` clean on `app/` and touched scripts; both suites green (`--schema-sync` on both backends).

## Tasks

- [ ] **1. Generator + frozen baseline SQL** — `scripts/gen_pg_baseline.py`: mock-engine DDL capture of `SQLModel.metadata` (after `dedupe_index_names`), prepended by `CREATE EXTENSION citext`, followed by the length CHECKs and `pg_triggers._TRIGGER_DDL`; statements joined with `-- ==stmt==` marker lines into `alembic_pg/versions/0001_pg_baseline.sql`.
- [ ] **2. Alembic environment** — `alembic.pg.ini` (script_location `alembic_pg`), async `alembic_pg/env.py` (asyncpg; URL from `ALEMBIC_DB_URL` env, else `settings.DATABASE_URL`; `target_metadata` from `app.main` import for future autogenerate), `script.py.mako`, and `versions/<rev>_pg_baseline.py` that splits the companion file on the marker and executes each statement (`downgrade` drops schema public cascade + recreates it).
- [ ] **3. conftest** — `_setup_postgres_test_database` runs the chain (programmatic `alembic upgrade head` with script_location `alembic_pg`, `ALEMBIC_DB_URL` set) with the at-head → truncate fast path; PG `_truncate_all_tables` branch spares `alembic_version`. Marker decoupling: collection hook no longer treats `schema_sync` as MariaDB-implying; the three existing schema-sync tests gain explicit `mariadb_only`.
- [ ] **4. PG schema-sync test** — `tests/integration/test_pg_schema_sync.py` (`schema_sync` + `postgres_only` + `xdist_group("schema_sync")`): build `shuushuu_schema_models_pg` via `build_pg_schema` and `shuushuu_schema_migrations_pg` via the chain; compare tables, columns (type/nullable/default), indexes, constraints, and triggers via information_schema/pg_catalog; assert no diff.
- [ ] **5. CI** — PG job gains `--schema-sync`.
- [ ] **6. Dev DB** — `alembic stamp head` (pg config) against the POC `shuushuu` database.
- [ ] **7. Gates + PR** — mypy; PG suite with `--schema-sync`; MariaDB suite with `--schema-sync`; plans index regen; PR.
