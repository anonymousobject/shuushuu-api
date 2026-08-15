# Postgres Migration Feasibility Analysis

**Date:** 2026-06-10
**Status:** Feasible, documented, deferred — no forcing function right now.

## Database usage today

**Stack:** FastAPI → SQLModel/SQLAlchemy async → **MariaDB 12** via `aiomysql` (runtime) and `pymysql` (Alembic), with Redis caching and Meilisearch handling primary tag search.

**Scale of the schema:** 36 tables across 25 model files, ~60+ FK constraints with explicit CASCADE/SET NULL, ~40 single-column FK indexes, 11 composite indexes, 5 unique constraints, and a 56-migration linear Alembic chain rooted in a baseline that recreates the legacy PHP schema verbatim (`alembic/versions/8d66158eb568`).

### MariaDB-specific surface, tiered by porting difficulty

**Tier 1 — genuine rewrites required:**

- **FULLTEXT search at runtime** in three route handlers: tag search (`app/api/v1/tags.py:620,679`), comment search (`app/api/v1/comments.py:118-122`), and image comment search (`app/api/v1/images.py:707-711`) — all raw `MATCH ... AGAINST (... IN BOOLEAN MODE)`. On top of that, `tags.py:108-204` contains ~100 lines simulating MySQL's InnoDB tokenizer (stopword list, 3-char min token size, boolean-operator stripping) so the app can predict what FULLTEXT will match.
- **8+ database triggers** maintaining denormalized counters (`tags.usage_count`, `images.posts`, `users.posts/image_posts/favorites`, comment soft-delete) in migrations `2cd4e874e956`, `5721ccce6a85`, `ec5c5fa4e3e5`. Postgres triggers require separate trigger *functions* — every one must be rewritten.
- **The migration chain itself.** 28 of 56 migrations use raw MySQL SQL (backticks, `ALTER ... MODIFY`, `ALGORITHM=INSTANT`, `JSON_EXTRACT`, charset conversions, `ENGINE=InnoDB`). The chain cannot run against Postgres; it would be squashed into a new Postgres baseline.

**Tier 2 — mechanical but pervasive:**

- `mysql.INTEGER(unsigned=True)` / `mysql.TINYINT` in 11+ migrations; one `sqlalchemy.dialects.mysql.JSON` column in app code (`app/models/admin_action.py:16,95`).
- Connection setup: `init_command: SET time_zone = '+00:00'` (`app/core/database.py:24`), `?charset=utf8mb4` URLs, `server_default=text("current_timestamp()")` everywhere (the empty-parens form is invalid in Postgres).
- Test infra: `SET FOREIGN_KEY_CHECKS = 0` + TRUNCATE, `CREATE DATABASE ... CHARACTER SET utf8mb4`, grants/`FLUSH PRIVILEGES` (`tests/conftest.py`).
- Scripts: `scripts/db_utils.py` shells out to the `mariadb` CLI, uses `INSERT IGNORE`, dump-import with sed surgery.

**Tier 3 — already portable:**

All ORM queries use standard SQLAlchemy constructs (no `GROUP_CONCAT`, no `ON DUPLICATE KEY UPDATE`, no `SELECT FOR UPDATE`, no stored procedures). The `UtcDateTime` TypeDecorator (`app/models/types.py`) works unchanged on Postgres. `GREATEST()` exists in Postgres.

## Feasibility: yes, clearly feasible

The disciplined SQLAlchemy usage means the ORM layer ports almost untouched. The MySQL coupling is concentrated in a handful of known files, not smeared through the codebase. Nothing here is a blocker — it's all known-pattern work.

## Benefits

1. **Better full-text search, and less code.** Postgres `tsvector` + `pg_trgm` would replace boolean-mode MATCH and allow deleting the ~100-line tokenizer simulation in `tags.py` — stopword/min-token-size guessing stops being an app concern. `pg_trgm` also gives real substring/fuzzy matching that InnoDB FULLTEXT can't do. (Note: Meilisearch already carries primary tag search, so this mostly improves the DB-side fallback paths.)
2. **Partial indexes.** `app/models/review_vote.py:69-70` documents wanting a partial unique index on `(review_id, user_id) WHERE review_id IS NOT NULL` — MariaDB doesn't support partial indexes at all; Postgres does natively.
3. **Transactional DDL.** A failed Alembic migration rolls back cleanly instead of leaving the schema half-altered — meaningful given the 100% hand-written migration culture here.
4. **JSONB** with indexing for `admin_actions.details`, richer types (arrays, ranges, real enums), and no more utf8mb3/utf8mb4 charset archaeology.
5. **Cleaner test story:** `TRUNCATE ... CASCADE` replaces the FK-checks toggle dance; template databases can make per-worker test DB creation faster than the current create+migrate.

## Challenges

1. **Case sensitivity is the sleeper risk.** The data lives in `utf8mb4_unicode_ci` — *every* string comparison (username uniqueness, login lookup, tag title matching, `idx_tags_title` searches) is case-insensitive today. Postgres comparisons are case-sensitive by default. Miss one lookup and users can register `Admin` next to `admin`. Fixing it means `citext`, `LOWER()` functional indexes, or ICU nondeterministic collations — applied consistently, and audited query-by-query. This is the most likely source of subtle production bugs.
2. **Data migration of a large legacy DB.** This is a mature image board (image IDs over 1.1M, legacy PHP-era rows). `pgloader` handles the bulk move and type coercion (TINYINT(1)→boolean, zero-dates, unsigned ints), but unsigned INT columns must be verified to not hold values above the signed 32-bit ceiling, and a cutover must be planned (downtime window or dual-write/replication).
3. **Migration chain reset.** Squashing 56 migrations into a Postgres baseline loses the "CI runs the full chain" verification property until re-established, and the schema-sync tests need reworking.
4. **Trigger rewrite** — 8+ triggers become Postgres trigger functions. Mechanical but must be behavior-verified (the comment-count `AFTER UPDATE` trigger has nontrivial logic).
5. **Operational retuning.** Driver swap to `asyncpg` (pool semantics differ — the pymysql `<1.2` pin problem goes away, but asyncpg has its own quirks), `innodb_buffer_pool_size` → `shared_buffers`/`effective_cache_size`, new backup tooling, vacuum/autovacuum monitoring. The query planner will pick different plans — carefully chosen indexes (e.g., the `has_theme/has_source` composite indexes, the `image_id`-as-date sort trick) need re-validation under Postgres's planner.

## Impact

Every layer gets touched, but unevenly: app code changes are small (3 search handlers, 1 JSON import, engine config); `tests/conftest.py` and `scripts/db_utils.py` need real rework; migrations get reset; docker-compose, .env, CI, backups, and production ops all change; production requires a data cutover event. Frontend/API contracts are untouched.

## Effort estimate

- **Code + schema + tests green on Postgres:** ~1–2 weeks of focused work.
- **Case-insensitivity audit, FTS behavior parity, trigger verification:** ~1 week — tedious, correctness-critical.
- **Data migration tooling, dry runs, validation, production cutover plan:** ~1–2 weeks, dominated by verification rather than coding.

Roughly **3–5 weeks** end-to-end for one person, with the long tail in validation, not code.

## Verdict

Feasible, but questionable ROI right now. MariaDB isn't causing pain: search already moved to Meilisearch, there's no replication/scaling pressure, and the MySQL coupling — while real — is stable and contained. The concrete wins (partial index for review_votes, deleting the tokenizer shim, transactional DDL) are nice-to-haves, not pain relievers. The case-collation audit alone carries more production risk than anything MariaDB is currently doing wrong.

If the migration happens, the moment to do it is *before* the next batch of trigger- or FULLTEXT-dependent features lands — the coupling surface only grows. Absent a forcing function (managed-hosting move, a feature that genuinely needs Postgres): feasible, documented, deferred.
