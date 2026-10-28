# MariaDB retirement — design

**Date:** 2026-09-10
**Follows:** docs/postgres-cutover-runbook.md §10 (prod cut over to Postgres 18
on 2026-08-22, PR #358) and ADR-0010.

## Motivation

Prod has run on Postgres for three weeks and MariaDB will not return. The
codebase still carries the whole transition scaffold: twenty `is_postgres`
branches, mysql-dialect column types, a FULLTEXT tokenizer simulation, a
second Alembic chain, a MariaDB CI job, a MariaDB compose service in every
compose file, and two drivers nobody loads. Every schema change still ships as
a migration pair. That is maintenance with no beneficiary.

Two of the leftovers are live defects on Postgres, not just cruft:

- `app/core/db_retry.py` recognizes a transient conflict by reading an integer
  MariaDB errno from the driver exception. asyncpg exceptions carry a message
  string and a SQLSTATE, and SQLAlchemy wraps a Postgres deadlock as
  `DBAPIError`, not `OperationalError`. The helper therefore never retries on
  Postgres, and ADR-0004's contract is void: a deadlock surfaces as a 500.
- `refresh_user_tag_affinity` raises `NotImplementedError` off MariaDB by
  design, and the arq worker still runs it nightly at 05:00 UTC. The
  recommended feed and the taste-profile endpoint have read pre-cutover data
  since 2026-08-22.

## Decisions

1. **Postgres is the only backend.** `is_postgres` is deleted; every branch
   keeps its Postgres arm verbatim. Deletions carry no behavior change, so
   text search keeps today's ILIKE semantics. Postgres full-text search is a
   separate plan.
2. **The retry helper matches SQLSTATE.** It catches `DBAPIError` (a
   superclass of `OperationalError`, so nothing narrows) and retries on
   `40P01` deadlock_detected and `40001` serialization_failure. The errno
   constants go; the log line carries the SQLSTATE. `tests/transient_conflict.py`
   fabricates errors the way the asyncpg adapter does, so the five call-site
   tests keep asserting the retry contract unchanged. One new integration test
   provokes a real deadlock: two sessions update two rows in opposite order,
   and the wrapped side succeeds on retry. ADR-0004 is rewritten.
3. **The affinity rebuild is ported, not retired.** The recommended feed
   depends on it (ADR-0007). It becomes one Postgres transaction:
   - a transaction-scoped advisory lock (`pg_try_advisory_xact_lock` on the
     hashed lock name, still scoped by database name) replaces the MariaDB
     named lock, so rollback releases it and the wedged-connection failure
     mode disappears;
   - the helper tables become temp tables, which Postgres can self-join;
   - delete-then-insert inside the transaction replaces the rename swap.
     Readers see the old rows until commit. A nightly rename on Postgres would
     accumulate suffixed index names, and the old swap silently dropped the
     table's foreign keys;
   - the arithmetic gets explicit float casts. `pool_cnt / pool_size` is
     integer division on Postgres, so lift and affinity would compute as zero.
     The existing thirteen tests check values with `approx` and catch this;
   - the two lock tests are re-expressed with Postgres advisory functions and
     a `lock_timeout` on the service session for the mid-run failure case;
   - the `!= "mysql"` guard goes.
4. **One Alembic chain at `alembic/`.** The legacy chain and `alembic.pg.ini`
   are deleted and `alembic_pg/` moves to `alembic/`. Revision IDs do not
   change, so prod's `alembic_version` table needs nothing. The pyproject
   alembic config already names `alembic`, which closes the bare-upgrade
   footgun the Makefile warns about (2026-08-29, PR #370). The frozen-baseline
   rule from ADR-0010 stays in force.
5. **One CI job, named `Tests`.** The MariaDB job is deleted and the Postgres
   job renamed. The main-branch ruleset requires the MariaDB job's check name
   and does not require the Postgres one, so the ruleset is repointed before
   the infra PR merges. That is a manual gate, not a code change.
6. **Local tests run against the dev-stack Postgres.** `run-tests.sh` loses
   `--pg` and always targets it; `make pytest` does the same. The dedicated
   pytest compose file and its `pytest-db-up`/`pytest-db-down` targets are
   deleted. The isolation existed because the shared dev MariaDB was
   OOM-killed under `-n auto`; if Postgres shows the same problem the fix is
   a dedicated Postgres container, added then, not kept speculatively.
7. **Backend markers go.** `mariadb_only` and `postgres_only` are deleted
   along with the skip logic. Tests carrying `postgres_only` run
   unconditionally. The affinity tests lose their `mariadb_only` marker in
   PR 2 and keep running; every test still carrying it after that is
   deleted, not skipped. The list is below.
8. **Compose knows only Postgres.** The mariadb service, its volume
   declarations, the prod busybox stub, and the alloy log-discovery match are
   removed. `api` and `arq-worker` depend on `postgres` in the base file and
   build `DATABASE_URL` from the `POSTGRES_*` variables, so
   `COMPOSE_DATABASE_URL` goes. Adminer defaults to `postgres`.
9. **Dependencies and settings.** `aiomysql`, `pymysql` and its pin, the mypy
   override, the Dockerfile's MySQL client headers, and the aiomysql getpass
   workaround are removed and the lock regenerated. `DATABASE_URL_SYNC` leaves
   `Settings` and the env example; its one remaining user,
   `scripts/bench_feed_count.py`, switches to the async engine.
10. **Model types.** `UnsignedInt` and `UnsignedSmallInt` become plain
    `Integer` and `SmallInteger`; `ci_string` becomes `CITEXT`. Neither
    changes Postgres DDL. The length CHECK constraints move from
    `app/core/pg_schema.py` into the models' `__table_args__`, and the
    colliding index names (`idx_date`, `idx_tag_id`) are renamed in the models
    to what the baseline already emits. That deletes both transition shims;
    the schema-sync test proves the models still match the chain.
11. **Search keeps its API shape.** Tag search loses the stopword list,
    tokenizer, and operator sanitizer. Comment search loses its fulltext
    parameter and the MATCH/AGAINST branches. The `natural` and `boolean` mode
    values stay accepted and keep routing to ILIKE, as they do on Postgres
    today; collapsing the enum is a frontend-visible API change and a
    separate plan.
12. **ADR-0014 records the decision.** "Postgres is the only supported
    database": no dialect branches, one chain at `alembic/`, no backend
    markers, SQLSTATE-based retry. ADR-0010 gets a closing note. Plans and
    other point-in-time docs are untouched; live docs are updated.

## PR sequence

Order is strict. Each PR is green on its own.

1. **Retry helper on Postgres** (decision 2). Removes the last `pymysql`
   imports from the tests.
2. **Affinity port** (decision 3). Removes the last `mariadb_only` marker
   that guards live code.
3. **Infrastructure** (decisions 4–9): CI and ruleset, Alembic move, conftest,
   compose, Makefile and `run-tests.sh`, dependencies, settings, and the
   `mariadb_only` test deletions. After this PR the repo runs only on
   Postgres.
4. **Code deletion** (decisions 1, 10, 11): `is_postgres` and its twenty call
   sites, the mysql dialect imports, the model types and shims, the FULLTEXT
   machinery, `app/models/generated.py.backup`, and the tests that exercised
   the MariaDB arm.

Docs and ADRs (decision 12) commit directly to `main` after PR 4.

## Tests deleted

| Test | Why it goes |
| --- | --- |
| `tests/integration/test_schema_sync.py` | Compares models against the MariaDB chain, which is deleted. |
| `tests/integration/test_fk_constraint_names.py` (the `mariadb_only` case) | Guards FK names in the MariaDB chain. The `postgres_only` cases stay. |
| `tests/api/v1/test_images.py` (two `mariadb_only` cases) | Assert MySQL boolean-mode and natural-language-mode semantics. |
| `tests/api/v1/test_tags.py` (one `mariadb_only` case) | Asserts the InnoDB stopword list. |
| `tests/unit/test_comment_search.py::test_mysql_path_is_unchanged` | Compiles the MATCH/AGAINST branch, which is deleted. |

Every other test stays. Tests whose docstrings describe MySQL tokenization
still pass on Postgres because they assert results through the API, not the
mechanism.

## Operational steps (run by hand, listed in the plan)

- Repoint the main ruleset's required status check before merging PR 3.
- Remove `MARIADB_*`, `DATABASE_URL_SYNC`, and `COMPOSE_DATABASE_URL` from the
  dev `.env`. They are inert once PR 3 lands.
- Remove the dev MariaDB container and its `mariadb_data_dev` volume when
  satisfied. No PR deletes a volume.
- Decommissioning the native MariaDB server on the prod DB tier is outside
  this repo.

## Non-goals

Postgres full-text search; collapsing the comment-search mode enum; a
dedicated pytest Postgres container; rewording the three frontend comments
that describe the retry in MariaDB terms (harmless, a small follow-up in that
repo).
