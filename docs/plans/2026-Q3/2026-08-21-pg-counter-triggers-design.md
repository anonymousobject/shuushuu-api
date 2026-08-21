# Postgres counter triggers — design

**Date:** 2026-08-21
**Follows:** docs/plans/2026-Q3/2026-08-20-tests-on-postgres-design.md (the counters decision it deferred)

## Motivation

Six denormalized counter families (`tags.usage_count`, `images.favorites`,
`users.favorites`, `users.image_posts`, `images.posts`+`last_post`,
`users.posts`) are maintained by MariaDB triggers that exist only in the
migration chain. On the Postgres side (create_all schema) they were frozen at
migrated values, and their tests were `mariadb_only`-skipped as the placeholder
for this decision.

## Decision: port the triggers to PL/pgSQL

Options weighed:

- **Port triggers (chosen).** Zero app changes; every write path covered,
  including the ones app code never sees. The clincher: InnoDB triggers do NOT
  fire on FK-cascaded deletes, and the image-delete endpoint relies purely on
  CASCADE — so prod MariaDB silently drifts `usage_count`/`users.favorites`/
  `users.posts` on every image deletion (the backfill scripts are the de-facto
  reconciliation). Postgres triggers DO fire on cascades, so the port is
  strictly more correct than parity. The cascade-fired UPDATE aimed at the
  row being deleted is a harmless no-op.
- **App-side recompute** (the tag_type_flags pattern) is visible and
  dialect-free but must enumerate every write path and every cascade site,
  now and forever; one missed path is silent drift. Rejected as the primary
  mechanism; the recompute/backfill helpers remain the reconciliation tools.
- **Count-on-read** cannot serve the hot sorts (usage_count over 235k tags,
  posts at feed scale). Rejected.

## Shape

`app/core/pg_triggers.py`: one PL/pgSQL function per (source table, event) —
11 triggers — each covering every counter that event touches; idempotent DDL
(CREATE OR REPLACE + DROP TRIGGER IF EXISTS) so it applies to live databases
and fresh bootstraps alike. Installed by `build_pg_schema` (POC DB + every
test database); the future Postgres Alembic baseline inherits this SQL.
Behavior matches the MariaDB set verbatim, including the soft-delete-aware
posts pair and its `last_post = MAX(date)` recompute; the un-skipped
`TestTagUsageCount` and alias-usage-count tests are the acceptance suite.
