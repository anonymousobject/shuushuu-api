# Postgres cutover runbook

Migrating production from MariaDB to Postgres. Every step here was executed
at prod scale (46.5M rows) against the dev restore on 2026-08-20/21; the
sharp edges are annotated with what they cost when first hit. The executable
half lives in `scripts/pg_migration/` (`migrate.py` + the pgloader template).

Related decisions: ADR-0008 (citext), ADR-0009 (counter triggers), ADR-0010
(alembic_pg chain and frozen baseline).

## 0. Rehearse first

Run this entire runbook against a scratch target database before the real
window. All tooling is idempotent — a failed step reruns safely. Rehearsal
wall-clock at current scale: schema ~5s, load ~7 min, post ~4 min, validate
~2 min.

## 1. Prerequisites

- Postgres 18 with the `citext` extension available (bundled contrib; present
  in the official image and every managed provider). The migration connects
  as a superuser or owner (needed by `disable triggers` during load).
- Docker on the machine running the migration (`dimitri/pgloader:latest`),
  network reach to both databases, and this repo checked out at the cutover
  revision with `uv sync` done.
- Postgres sizing: the MariaDB box runs `innodb_buffer_pool_size=2G` to keep
  the images table hot; start with `shared_buffers≈2G`,
  `effective_cache_size≈` the host's real page-cache budget, and revisit
  after the feed queries have run against real traffic.
- Backups configured for the target before cutover day.

## 2. Pre-cutover source data fixes (days before, not during the window)

**NULL `fav_date` rows.** The model (and therefore the chain-built schema)
declares `favorites.fav_date NOT NULL`, but legacy data holds 157 NULLs —
the live MariaDB column is nullable (known model↔DB drift). The load rejects
those rows; `migrate.py preflight` refuses to proceed while they exist. Fix
on the source, once:

```sql
UPDATE favorites f
JOIN users u ON u.user_id = f.user_id
LEFT JOIN (
    SELECT user_id, MIN(fav_date) AS first_fav
    FROM favorites WHERE fav_date IS NOT NULL GROUP BY user_id
) x ON x.user_id = f.user_id
SET f.fav_date = COALESCE(x.first_fav, u.date_joined)
WHERE f.fav_date IS NULL;
```

Known-and-fine oddities (no action): `tags.tw_tagid` and the `tw_*` tables
are legacy strays — the tooling stages/drops a temp column and excludes the
tables; `bans` exists in the models but not the legacy data and simply loads
empty (`validate` prints a NOTE).

## 3. Build the target schema

```bash
createdb shuushuu   # or CREATE DATABASE via psql
ALEMBIC_DB_URL="postgresql+asyncpg://USER:PASS@HOST:5432/shuushuu" \
    uv run alembic -c alembic.pg.ini upgrade head
```

The chain applies the full baseline: 45 tables, citext keys, CHECK
constraints, and the counter triggers (ADR-0009 — on Postgres they also fire
on FK-cascaded deletes, so the counter drift MariaDB accumulates on image
deletion ends at cutover). Never build the migration target with
`create_all` or pgloader's schema mode.

## 4. Freeze writes — the window opens

- Stop **every** process pointed at either database: api, arq worker, crons.
  pgloader reads the source live; concurrent writes give an inconsistent
  copy. On the target side, *anything* that writes mid-load corrupts the
  run — a dev uvicorn with `--reload` re-seeding one table cost us a
  perms-table repair with FK fallout.
- `migrate.py preflight` hard-fails if any other client is connected to the
  target; it cannot see every source writer, so the freeze is on you.
- Optionally `SET GLOBAL read_only = 1` on MariaDB for belt and braces.

## 5. Raise source network timeouts

```sql
SET GLOBAL net_read_timeout = 600; SET GLOBAL net_write_timeout = 600;
```

Defaults (30/60s) drop the connection mid-copy on the big tables
(`tag_links` 14.8M, `favorites` 5.8M). Dynamic settings — they revert on
server restart. `preflight` warns when they're low.

## 6. Run the migration

```bash
export SOURCE_MYSQL_URL="mysql://USER:PASS@HOST:3306/shuushuu"
export TARGET_PG_URL="postgresql://USER:PASS@HOST:5432/shuushuu"
uv run python scripts/pg_migration/migrate.py all
```

`all` = preflight → prep (temp column + truncate) → load (pgloader in
docker) → post (FK normalization to model names, sequence setval, ANALYZE) →
validate (per-table source-vs-target counts; exits non-zero on any diff).
Steps can be rerun individually; `prep` + `load` restart a botched copy from
clean. The pgloader settings in the template are load-bearing — concurrency 1
(thread race), small batches (heap exhaustion on wide rows), docker `-t`
(SBCL /dev/tty crash); don't "optimize" them without re-rehearsing.

The rendered load file and pgloader log land in `/tmp/pg-migration-*/`
(mode 0700) and contain both database URLs **with credentials** — remove
those directories once the window closes: `rm -rf /tmp/pg-migration-*`.

## 7. Validate beyond counts

Counts are necessary, not sufficient — every smoke check passed while comment
search was silently missing case-variant rows; a source-vs-target *count
comparison on the same query* is what caught it. Minimum manual checks:

- Login with a deliberately wrong-cased username (citext, ADR-0008).
- The same search (`?search_text=...`) on both stacks: target count ≥ source
  (ILIKE substring-matches more than boolean-mode tokens; much fewer means
  case or dialect trouble).
- Favorite + unfavorite an image; comment; add/remove a tag — counters must
  move (triggers).
- `SELECT last_value FROM images_image_id_seq` vs `MAX(image_id)`.

## 8. Flip

- **Prod** (DB tier is native/out-of-stack; `docker-compose.prod.yml` stubs
  it and passes `DATABASE_URL=${DATABASE_URL}` through): set the prod `.env`
  `DATABASE_URL=postgresql+asyncpg://user:pass@dbhost:5432/shuushuu`, then
  `docker compose ... up -d api arq-worker`.
- **Compose-managed environments** (dev; anywhere the DB runs as the
  `postgres` service in `docker-compose.yml`): set
  `COMPOSE_DATABASE_URL=postgresql+asyncpg://user:pass@postgres:5432/shuushuu`
  in `.env` — unset means MariaDB — then `up -d api arq-worker`.
- nginx resolves upstreams at config load: if the api container was
  recreated, `nginx -s reload`, or 502s follow.
- Smoke through the public entry point, not just the api port.

## 9. Rollback

MariaDB is untouched by everything above. Rollback = flip `DATABASE_URL`
back and start services. Writes that landed on Postgres after the flip are
lost to MariaDB — decide the acceptable window in advance. A failed attempt's
Postgres data is disposable: next attempt starts at `prep` again.

## 10. Post-cutover

- The nightly `user_tag_affinity` rebuild raises `NotImplementedError` on
  Postgres **by design** (MariaDB-only guard). Expect the cron to fail loudly
  until the rebuild is ported or retired — do not "fix" it by silencing.
- Counter backfill scripts become reconciliation-only (triggers now cover
  cascades; ADR-0009).
- Watch `pg_stat_activity`/logs through the first feed-heavy hours; the
  planner may pick different plans than InnoDB did (feasibility doc flagged
  the composite-index sorts for re-validation).
- Schedule the MariaDB retirement PR: delete the dialect branches
  (`is_postgres` call sites keep the Postgres arm), drop
  `aiomysql`/`pymysql`, delete `alembic/` and rename `alembic_pg/` into
  place, retire `UnsignedInt`/`mariadb_only`, delete the FULLTEXT tokenizer
  simulation alongside the FTS decision, rework `scripts/db_utils.py`.
