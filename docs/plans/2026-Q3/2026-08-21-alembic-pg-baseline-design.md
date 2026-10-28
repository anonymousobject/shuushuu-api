# Alembic Postgres baseline — design

**Date:** 2026-08-21
**Follows:** ADR-0008 (citext), ADR-0009 (counter triggers), docs/plans/2026-Q3/2026-08-20-tests-on-postgres-design.md

## Motivation

Postgres schema currently comes from `build_pg_schema` (create_all + citext +
CHECKs + triggers) — fine for a POC, but migrations are the deployable,
reviewable record, and the transition needs a place for future PG schema
changes to land. The MariaDB chain cannot be that place: 19 of its migrations
are mysql-dialect, and its baseline recreates the legacy PHP schema.

## Decisions

1. **A parallel Alembic environment** (`alembic_pg/`, `alembic.pg.ini`,
   async env.py running on asyncpg — no sync PG driver exists in this repo),
   not branch labels inside the existing chain. Branch labels would make every
   existing `alembic upgrade head` ambiguous (two heads by design) and break
   the `alembic heads` hygiene check; a separate environment leaves the
   MariaDB tooling byte-untouched and simply becomes *the* environment when
   MariaDB retires.
2. **The baseline is frozen DDL, not code that imports models.** A migration
   calling `create_all` would re-derive its schema from whatever the models
   say at run time — the opposite of a migration. Instead
   `scripts/gen_pg_baseline.py` captures the bootstrap's exact statement list
   (mock-engine DDL capture for tables/indexes, plus the extension, length
   CHECKs, and `pg_triggers` statements) into a companion
   `0001_pg_baseline.sql`, statements separated by an explicit
   `-- ==stmt==` marker line (asyncpg refuses multi-command prepared
   statements, and parsing pg_dump output around $$-quoted bodies is fragile —
   the marker sidesteps both). The index-name dedupe happens at generation, so
   the baseline is where `idx_date`/`idx_tag_id` collisions are truly
   normalized rather than runtime-renamed.
3. **A PG schema-sync test enforces the dual window.** One database built by
   `build_pg_schema` (the models' view), one by `alembic_pg upgrade head`;
   their schemas are diffed. A model change without a matching PG migration
   turns it red — the same discipline the MariaDB schema-sync test provides
   for its chain. Gated by the existing `--schema-sync` opt-in and
   `postgres_only`; the old schema-sync tests get an explicit `mariadb_only`
   (the collection hook's "schema_sync implies MariaDB" coupling is removed).
4. **Test databases build from the chain.** The conftest PG path switches
   from `build_pg_schema` to `alembic_pg upgrade head`, with the MariaDB
   path's at-head → truncate fast path (the PG truncate branch learns to
   spare `alembic_version`). CI therefore validates the PG migrations on
   every run. The dev POC database is `alembic stamp`ed at the baseline.
   `build_pg_schema` remains as the models-side comparator for the sync test
   and the POC script.

## Dual-window rule (the cost accepted here)

Until MariaDB retires, a schema change ships as a pair: a migration in
`alembic/` and one in `alembic_pg/`. The PG schema-sync test (CI) catches a
missing PG half; the MariaDB schema-sync test catches the other. At cutover,
`alembic/` and `alembic.ini`'s mysql plumbing are deleted and `alembic_pg/`
is renamed into place.

## Non-goals

Migrating the 56-migration history (the baseline is a squash by design);
autogenerate polish for variant types (future PG migrations can hand-write or
autogenerate and clean up, same as the MariaDB culture).
