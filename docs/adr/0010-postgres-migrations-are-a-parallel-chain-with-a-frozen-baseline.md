# Postgres migrations are a parallel Alembic chain with a frozen-DDL baseline

Postgres schema is owned by `alembic_pg/` (own `alembic.pg.ini`, async env.py
on asyncpg) — a second Alembic environment alongside `alembic/` (MariaDB) —
whose single root revision executes 209 frozen DDL statements from a companion
SQL file. Shipped in PR #354; design record is
`docs/plans/2026-Q3/2026-08-21-alembic-pg-baseline-design.md`.

The problem this settled: the transition needed a deployable, reviewable home
for Postgres schema changes. The MariaDB chain cannot host them (19
mysql-dialect migrations; a baseline that replays the legacy PHP schema), and
runtime `create_all` is not a migration story.

## Considered Options

- **Branch labels inside the existing chain** keeps one history graph but
  makes every plain `alembic upgrade head` a multiple-heads error, breaks the
  `alembic heads` hygiene check this repo relies on, and forces every
  invocation (conftest, CI, muscle memory) to grow a `mysql@head` qualifier.
  Rejected: it taxes the stable side to subsidize the new one.
- **`create_all` forever** (no PG chain until cutover) leaves schema changes
  with no reviewable, orderable record on PG and no path to apply them to a
  long-lived database (the dev POC DB, eventually prod). Rejected.
- **A baseline that imports the models** (calls `create_all` at migration
  time) re-derives its schema from whatever the models say when it runs — a
  fresh database built next year would silently include next year's columns,
  breaking the invariant that baseline + later migrations = current schema.
  Rejected; this is the classic migration anti-pattern.
- **Parallel chain + frozen DDL (chosen).** The baseline's companion SQL was
  generated once (`scripts/gen_pg_baseline.py`: mock-engine DDL capture +
  extension/CHECK/trigger statements, `-- ==stmt==` markers because asyncpg
  refuses multi-command statements) and is now frozen under the repo's
  never-edit-merged-migrations rule.

## The dual-window rule

Until MariaDB retires, a schema change ships as a **pair**: one migration in
`alembic/`, one in `alembic_pg/`. This is enforced, not aspirational — each
side's schema-sync test (`test_schema_sync.py` on MariaDB,
`test_pg_schema_sync.py` on Postgres, both under `--schema-sync` in CI)
compares a models-built schema against its chain, so a missing half goes red.

## Consequences

- The frozen SQL file is canonical; the generator is not rerun against a
  merged baseline (its statement order is per-process nondeterministic —
  `table.indexes` is a set — so regeneration cannot even reproduce the file
  byte-for-byte; content equality was verified by normalized statement-set
  comparison when the pre-commit whitespace hook touched it).
- `.gitignore` carries `!alembic_pg/versions/*.sql`: migration companion DDL
  is source, not a dump — the blanket `*.sql` rule once swallowed the
  baseline and shipped the migration without its DDL.
- Index-name normalization (`idx_date`/`idx_tag_id` collisions) lives in the
  baseline; `dedupe_index_names` remains only for the models-side comparator
  (`build_pg_schema`), which persists as the schema-sync reference and POC
  bootstrap.
- Test databases and CI build from the chain (at-head → truncate fast path),
  so a broken PG migration fails CI the same way a broken MariaDB one does.
- At cutover: delete `alembic/` and its config, rename `alembic_pg/` into
  place, and the pair rule dissolves.
