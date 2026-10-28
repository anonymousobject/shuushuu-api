# Counters are maintained by database triggers on both backends

The six denormalized counter families — `tags.usage_count`, `images.favorites`,
`users.favorites`, `users.image_posts`, `images.posts`+`last_post`,
`users.posts` — are maintained by database triggers on MariaDB (migrations
`2cd4e874e956`, `5721ccce6a85`, `ec5c5fa4e3e5`) and by their PL/pgSQL twins on
Postgres (`app/core/pg_triggers.py`, installed by `build_pg_schema` and, when
it lands, the Postgres Alembic baseline). Settled in PR #353 during the
Postgres transition; the design record is
`docs/plans/2026-Q3/2026-08-21-pg-counter-triggers-design.md`.

The problem this settled: on a `create_all`-built Postgres schema the counters
were frozen at migrated values, and the transition forced the question of
whether trigger maintenance is the mechanism worth carrying forward at all.

## Considered Options

- **App-side maintenance** (services recompute affected counters
  in-transaction, the `tag_type_flags` pattern) is visible, unit-testable,
  and dialect-free — but its correctness is an enumeration burden: every
  write path and every FK-cascade site, found and kept found forever. The
  image-delete endpoint deletes via bare CASCADE; app code never sees the
  cascaded `tag_links`/`favorites`/`posts` rows. One missed site is silent
  drift discovered by users. Rejected as the primary mechanism; the
  recompute/backfill helpers remain as reconciliation tools.
- **Count-on-read** cannot serve the hot paths: `usage_count` sorts a 235k-row
  tag list through an index, and `images.posts` filters feeds at 1.1M-image
  scale. Rejected.
- **Triggers (chosen)** cover every write path — endpoints, scripts, manual
  SQL, and cascades — with zero app-code involvement.

## The asymmetry worth remembering

InnoDB does **not** fire triggers for rows removed by `ON DELETE CASCADE`;
Postgres does. Production MariaDB therefore silently drifts `usage_count`,
`users.favorites`, and `users.posts` on every image deletion, reconciled only
by the backfill scripts. The Postgres port is strictly more correct than
parity: cascaded deletes decrement counters, and the cascade-fired UPDATE
aimed at the row being deleted is a harmless no-op.
`tests/integration/test_counter_cascades.py` asserts this and is
`postgres_only` for exactly that reason — the same test would fail on MariaDB
by faithfully exposing the drift.

## Consequences

- `app/core/pg_triggers.py` is the single source for the Postgres trigger
  DDL: idempotent (safe against live databases), one function per
  (source table, event). The future Postgres baseline migration must inherit
  this SQL rather than re-derive it.
- Trigger writes bypass the ORM identity map: after a mutation, assert
  counters by re-querying (`expire_all()` first), never on a stale instance —
  and capture plain ids before expiring (async lazy-load raises
  MissingGreenlet).
- The `TestTagUsageCount` and alias-usage-count tests run on both backends
  and are the regression net for trigger behavior; the counters stay
  eventually-reconcilable via the backfill scripts if drift is ever suspected.
- Any future move away from triggers (e.g. app-side recompute after the
  MariaDB retirement) must re-litigate the cascade enumeration problem this
  ADR documents — that is the trade, not the trigger syntax.
