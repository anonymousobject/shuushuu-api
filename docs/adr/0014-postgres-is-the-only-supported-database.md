# Postgres is the only supported database

Production cut over from MariaDB to Postgres 18 on 2026-08-22 (PR #358) and
MariaDB was retired from the codebase in September 2026 (PRs #389–#392;
design record: `docs/plans/2026-Q3/2026-09-10-mariadb-retirement-design.md`).
There is one backend. Code is written for Postgres directly: no `is_postgres`
switch, no dialect-neutral SQL for its own sake, no second migration chain,
no backend markers in the test suite.

## Considered Options

- **Keeping the dialect branches** as insurance against returning to MariaDB
  was rejected. There is no plan to return, every branch was dead code in
  prod, and the pair rule for migrations taxed every schema change. Two of the
  branches were live defects on Postgres: the conflict-retry helper matched
  MariaDB errnos and never fired, and the affinity rebuild raised
  `NotImplementedError` nightly.
- **Porting the MariaDB full-text search to Postgres** in the same effort was
  rejected as scope creep. Text search keeps the ILIKE path prod has run
  since cutover; a tsvector or Meilisearch upgrade is its own plan.

## Consequences

- The migration chain lives at `alembic/` and a bare `alembic upgrade head`
  targets it. The frozen-baseline rule (ADR-0010) stays in force.
- Transient write conflicts are recognized by SQLSTATE (`40P01`, `40001`),
  per ADR-0004 as amended.
- Postgres-specific constructs are used where they are the right tool:
  advisory locks, `ON CONFLICT DO NOTHING`, `UPDATE ... FROM`, `NULLS LAST`,
  temp tables, `citext` (ADR-0008), PL/pgSQL counter triggers (ADR-0009).
- The comment-search `natural` and `boolean` mode values are still accepted
  and behave as `all_words`; collapsing the enum is a frontend-visible API
  change and a separate decision.
- Adding a dialect branch, a `sqlalchemy.dialects.mysql` import, or a second
  driver is a regression against this decision.
- The nightly `user_tag_affinity` rebuild is one transaction (about five
  minutes at prod scale after the temp tables are analyzed; measured 4 min
  59 s on the prod-scale dev restore). Delete-then-insert leaves one dead
  tuple per old row for autovacuum each night (the dev table is 1.19M rows
  in 160 MB, so the autovacuum threshold fires right after the first run),
  and the transaction pins `xmin` for its duration. Operators check
  `transaction_timeout`, `idle_in_transaction_session_timeout`,
  `lock_timeout`, and `statement_timeout` are 0 for the app role before the
  first run.
- `updated_at` on `user_tag_affinity` records the rebuild's transaction start
  (`CURRENT_TIMESTAMP` is transaction start on Postgres), so it reads a few
  minutes before the commit. That is the intended "as of" time.
- Temp tables used by a session are invisible to autovacuum and
  `CREATE TABLE AS` collects no statistics; any future job building temp
  tables must `ANALYZE` them before joining (the affinity rebuild does).
