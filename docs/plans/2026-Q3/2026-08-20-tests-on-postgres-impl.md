# Tests on Postgres Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The full pytest suite runs green against Postgres 17 (selected by `TEST_DATABASE_URL` scheme) with the MariaDB suite unchanged, plus a CI job gating it.

**Architecture:** Backend detection at conftest module level from the URL scheme; the PG session-setup path reuses the POC schema bootstrap (extracted to `app/core/pg_schema.py`); dialect-specific tests carry a `mariadb_only` marker that auto-skips on PG. See the design doc for decisions and triage rules.

**Tech Stack:** pytest / pytest-xdist, SQLAlchemy async (asyncpg), postgres:17 (POC compose container locally, service container in CI).

**Spec:** docs/plans/2026-Q3/2026-08-20-tests-on-postgres-design.md

## Global Constraints

- MariaDB suite behavior byte-identical: same fixtures, same timings, green throughout.
- `app/` mypy-clean; `tests/conftest.py` and any touched scripts mypy-clean.
- A test may be `mariadb_only` only if the behavior it asserts is defined by MariaDB itself (fulltext semantics, migration-chain comparison, the affinity guard). Every other PG failure is a bug to fix.

---

### Task 1: Extract the PG schema bootstrap to `app/core/pg_schema.py`

**Files:** Create `app/core/pg_schema.py`; modify `scripts/postgres_poc.py` (import instead of inline).

**Interfaces:** `async def build_pg_schema(conn: AsyncConnection) -> None` — drops/recreates `public`, creates citext, dedupes cross-table index names on `SQLModel.metadata`, `create_all`, adds the three `ck_*_len` CHECKs. Caller imports `app.main` first (model registration) — the helper asserts a sentinel table is present in metadata.

- [ ] Move `_dedupe_index_names` + the setup body from `scripts/postgres_poc.py` into the new module; script's `setup()` becomes a thin wrapper.
- [ ] `uv run mypy app/core/pg_schema.py scripts/postgres_poc.py` clean; `uv run python scripts/postgres_poc.py setup` still builds 45 tables (against the POC container; destroys migrated data — acceptable, documented).
- [ ] Commit: `refactor(tests-pg): extract PG schema bootstrap for conftest reuse`

### Task 2: Backend-aware conftest

**Files:** Modify `tests/conftest.py`.

**Interfaces:** module-level `IS_POSTGRES: bool` (from `make_url(TEST_DATABASE_URL).get_backend_name()`); everything else keeps its name.

- [ ] `_get_test_database_url`: when the env URL is postgres, derive no pymysql sync URL (sync URL only used on the MariaDB path; make it `None` there and guard its uses).
- [ ] `setup_test_database`: branch at the top. PG path (all via async engines + `asyncio.run`): connect to the `postgres` maintenance DB with AUTOCOMMIT, `CREATE DATABASE` if absent (per-worker name), then `build_pg_schema` on the worker DB, then the existing perms sync. MariaDB path untouched.
- [ ] `_truncate_all_tables`: PG branch — query `pg_tables` (or information_schema with `table_schema='public'`), one `TRUNCATE t1, t2, ... RESTART IDENTITY CASCADE`, then re-run perms sync as today.
- [ ] Gate: `TEST_DATABASE_URL=postgresql+asyncpg://shuushuu:pg_dev_password@localhost:5432/shuushuu_pytest uv run pytest tests/unit -q` — collection and session setup succeed (unit tests don't hit fulltext; expect green or near-green).
- [ ] Commit: `feat(tests-pg): conftest runs the suite against Postgres`

### Task 3: `mariadb_only` marker

**Files:** Modify `tests/conftest.py` (register marker + collection skip when `IS_POSTGRES`), `tests/services/test_user_tag_affinity.py` (module `pytestmark`), schema-sync handling (skip on PG regardless of `--schema-sync`).

- [ ] Register marker; in `pytest_collection_modifyitems`, skip `mariadb_only` items when `IS_POSTGRES` with reason naming the design doc.
- [ ] Gate: on PG, affinity + schema-sync tests report skipped, MariaDB run unchanged.
- [ ] Commit: `feat(tests-pg): mariadb_only marker`

### Task 4: Full-suite triage on Postgres

**Files:** As discovered — expect touches in fulltext-mode tests (`tests/api/v1/test_comments.py`, `test_images.py`, tags search tests) and any fixture with dialect-specific SQL.

- [ ] Run: `TEST_DATABASE_URL=postgresql+asyncpg://... uv run pytest tests/ -q -n 4 --dist loadgroup`; triage every failure per the design rule (fix bugs; `mariadb_only` only for MariaDB-defined semantics; never weaken a MariaDB assertion).
- [ ] Re-run both backends green.
- [ ] Commit per logical fix, then: `feat(tests-pg): suite green on Postgres`

### Task 5: Runner ergonomics

**Files:** Modify `run-tests.sh`; `tests/README.md` if present.

- [ ] `--pg` flag: exports `TEST_DATABASE_URL` (PG POC container, `shuushuu_pytest` DB) and `DATABASE_URL` to the same URL (see design §7), then passes remaining args through.
- [ ] Gate: `./run-tests.sh --pg tests/unit -q` works from a clean shell.
- [ ] Commit: `feat(tests-pg): run-tests.sh --pg`

### Task 6: CI job

**Files:** Modify `.github/workflows/ci.yml`.

- [ ] Add `test-postgres` job mirroring the MariaDB job: `postgres:17` service (POSTGRES_USER/PASSWORD/DB, health `pg_isready`, tmpfs data dir) + redis; env `TEST_DATABASE_URL`/`DATABASE_URL` set to the postgres URL; run `uv run pytest tests/ -v --tb=short --maxfail=5 -n auto --dist loadgroup` (no `--schema-sync`).
- [ ] Gate: both CI jobs green on the PR.
- [ ] Commit: `ci: run the suite against Postgres`

### Task 7: Verification and PR

- [ ] `uv run mypy app/` clean; both local suite runs green (MariaDB with `--schema-sync`, PG without); `scripts/postgres_poc.py setup && smoke` still 13/13 (rebuilds POC data note: smoke reseeds).
- [ ] Regenerate plans index; PR with `Plan:` line and both suite outputs.
