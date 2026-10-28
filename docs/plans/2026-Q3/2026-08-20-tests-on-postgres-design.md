# Tests on Postgres — design

**Date:** 2026-08-20
**Follows:** docs/plans/2026-Q3/2026-08-20-postgres-poc-impl.md (merged, PR #350)

## Motivation

The Postgres POC is verified by a 13-check smoke script; every subsequent PG
change deserves the real suite. The ILIKE bug (comment search silently missing
differently-cased rows) is the cautionary tale: every endpoint check passed
while results were wrong, and only a MariaDB-vs-Postgres count comparison
caught it. Suite-level parity is the tool that makes the remaining transition
work (alembic baseline, counters, FTS) safe to build.

## Decisions

1. **Backend selection: `TEST_DATABASE_URL` scheme.** A `postgresql+asyncpg://`
   URL flips the whole session to Postgres; no new flags or fixtures-of-fixtures.
   One backend per pytest session — running both in one session doubles runtime
   and fights xdist's per-worker database naming.
2. **PG test schema comes from `create_all`** (the POC bootstrap: citext
   extension → create_all with deduped index names → length CHECKs), extracted
   to `app/core/pg_schema.py` and shared with `scripts/postgres_poc.py`. The
   MariaDB path keeps running the migration chain — that asymmetry is honest:
   there is no PG migration chain yet, and the schema-sync suite that guards
   the models↔migrations contract stays MariaDB-only. When the PG baseline
   migration lands, the test schema source switches to it.
3. **No new drivers, no root.** All PG admin work (per-worker
   `CREATE DATABASE`, schema reset) runs through async engines with the
   bootstrap superuser the container already has. The MariaDB root/grant
   machinery is untouched.
4. **A `mariadb_only` marker**, auto-skipped when the session backend is
   Postgres, for tests of genuinely MariaDB-specific behavior: the schema-sync
   suite, `test_user_tag_affinity.py` (the service deliberately raises
   `NotImplementedError` off-MariaDB), and fulltext `natural`/`boolean`
   search-mode tests whose *semantics* are MySQL's. The rule for triage:
   a test may be skipped on PG only if the behavior it asserts is defined by
   MariaDB itself; anything else that fails is a bug to fix, not to skip.
5. **Isolation parity.** The SAVEPOINT-rollback default is dialect-agnostic
   and unchanged. The `needs_commit` truncate path becomes one
   `TRUNCATE ... RESTART IDENTITY CASCADE` statement on PG (no FK-checks
   toggle; RESTART IDENTITY matches MariaDB TRUNCATE's auto-increment reset).
6. **CI: a second job** with `postgres:17` + redis service containers, same
   xdist invocation, no `--schema-sync`. The MariaDB job is untouched, so the
   suite gates both backends independently.
7. **Local ergonomics: `./run-tests.sh --pg`** exports the PG test URL
   (against the POC container) *and* sets `DATABASE_URL` to the same URL —
   mirroring what CI already does for MariaDB — so nothing that reaches the
   app-level engine (`AsyncSessionLocal` background paths) can touch the dev
   database from a test run.

## Non-goals

Running both backends in one pytest invocation; porting schema-sync to PG
(meaningless without a PG migration chain); performance work (template
databases are noted in the feasibility doc as a later win).
