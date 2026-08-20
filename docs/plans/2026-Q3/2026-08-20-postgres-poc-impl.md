# Postgres Proof-of-Concept Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get the API booting and serving its core read/write paths against a real PostgreSQL container, with MariaDB remaining the default everywhere, to validate the 2026-06-10 feasibility analysis with running code.

**Architecture:** All changes are dialect-aware branches keyed off the SQLAlchemy engine/bind dialect name, so one codebase serves both databases; nothing MariaDB-facing changes behavior. Schema on Postgres is built by `SQLModel.metadata.create_all()` (the Alembic chain reset is deferred, per the feasibility doc). Verification is a smoke script that runs the real FastAPI app in-process via httpx's ASGI transport against the real Postgres container — no mocks.

**Tech Stack:** postgres:17 (docker compose, standalone project), asyncpg, SQLAlchemy 2.0 `with_variant`, httpx ASGI transport.

**Spec:** docs/plans/2026-Q2/2026-06-10-postgres-migration-feasibility.md

## Global Constraints

- MariaDB stays the default: no behavior change on the `mysql` dialect; the full existing test suite must stay green on MariaDB.
- `app/` stays at zero mypy errors; any touched `scripts/` file must be mypy-clean.
- Smallest reasonable change per site: dialect branch or `with_variant`, not rewrites.
- Deferred (out of scope, recorded here deliberately): Alembic chain reset / Postgres baseline migration, the 8+ counter triggers (counters simply don't self-maintain on PG in this POC), data migration, case-insensitivity (citext/ICU) work, running the pytest suite against Postgres, `scripts/db_utils.py`, `tests/conftest.py` rework.

## Explicitly deferred sites (guarded, not ported)

`app/services/user_tag_affinity.py` (batch taste-profile rebuild: `GET_LOCK`, `SELECT DATABASE()`, temp-table dance) is cron/batch-only and is NOT ported. It gets a loud guard raising `NotImplementedError` on non-mysql dialects so a PG run fails immediately and explicitly rather than mid-flow.

---

### Task 1: Postgres container + asyncpg dependency

**Files:**
- Create: `docker-compose.postgres.yml`
- Modify: `pyproject.toml` (via `uv add`)

**Interfaces:**
- Produces: a Postgres 17 server on `localhost:5432`, database `shuushuu`, user `shuushuu`, password `pg_dev_password`; the `postgresql+asyncpg://` driver installed. All later tasks assume the URL `postgresql+asyncpg://shuushuu:pg_dev_password@localhost:5432/shuushuu`.

- [ ] **Step 1: Add asyncpg**

Run: `uv add asyncpg`
Expected: `asyncpg>=0.30` lands in `[project.dependencies]` and `uv.lock`.

- [ ] **Step 2: Write the compose file**

```yaml
# Postgres proof-of-concept — deliberately a separate compose project so it
# never touches the main dev stack. See docs/plans/2026-Q3/2026-08-20-postgres-poc-impl.md.
#   docker compose -f docker-compose.postgres.yml up -d
#   docker compose -f docker-compose.postgres.yml down -v   # discard the POC data
name: shuushuu-postgres-poc

services:
  postgres:
    image: postgres:17
    container_name: shuushuu-postgres-poc
    environment:
      POSTGRES_DB: shuushuu
      POSTGRES_USER: shuushuu
      POSTGRES_PASSWORD: pg_dev_password
    ports:
      - "5432:5432"
    volumes:
      - postgres_poc_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U shuushuu -d shuushuu"]
      interval: 5s
      timeout: 3s
      retries: 10

volumes:
  postgres_poc_data:
    driver: local
```

- [ ] **Step 3: Verify the container comes up healthy**

Run: `docker compose -f docker-compose.postgres.yml up -d && docker compose -f docker-compose.postgres.yml ps`
Expected: `shuushuu-postgres-poc` healthy.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.postgres.yml pyproject.toml uv.lock
git commit -m "feat(postgres-poc): postgres 17 compose project + asyncpg"
```

### Task 2: Dialect-aware engine config and statement timeout

**Files:**
- Modify: `app/core/database.py`

**Interfaces:**
- Consumes: `settings.DATABASE_URL` (may now be `postgresql+asyncpg://...`).
- Produces: `engine` that connects on either dialect with UTC session timezone; `statement_timeout(db, seconds)` works on both dialects with unchanged signature.

- [ ] **Step 1: Branch connect_args on the URL's backend**

Replace the engine construction so the MariaDB `init_command` is only passed to mysql drivers, and asyncpg gets its `server_settings` equivalent:

```python
from sqlalchemy.engine import make_url

_url = make_url(settings.DATABASE_URL)
_is_postgres = _url.get_backend_name() == "postgresql"

# Both branches pin the session timezone to UTC; each driver spells it differently.
_connect_args: dict[str, Any] = (
    {"server_settings": {"timezone": "UTC"}}
    if _is_postgres
    else {"init_command": "SET time_zone = '+00:00'"}
)

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,  # Verify connections before using
    pool_recycle=3600,  # Recycle connections every hour (MariaDB wait_timeout is 8 hours)
    connect_args=_connect_args,
)
```

(`Any` is already importable; add `from typing import Any` if not present.)

- [ ] **Step 2: Branch statement_timeout on the bind dialect**

MariaDB's `max_statement_time` takes seconds; Postgres's `statement_timeout` takes milliseconds. Keep the restore-on-exit contract identical:

```python
    if seconds is None:
        yield
        return

    if db.get_bind().dialect.name == "postgresql":
        # int() coerces the value: SET does not take bind parameters, so this is
        # interpolated, and the coercion is what keeps that safe.
        await db.execute(sql_text(f"SET statement_timeout = {int(seconds * 1000)}"))
        try:
            yield
        finally:
            await db.execute(sql_text("SET statement_timeout = DEFAULT"))
    else:
        # float() coerces the value (same interpolation-safety rationale).
        await db.execute(sql_text(f"SET SESSION max_statement_time = {float(seconds)}"))
        try:
            yield
        finally:
            await db.execute(sql_text("SET SESSION max_statement_time = DEFAULT"))
```

- [ ] **Step 3: Verify types and MariaDB regression**

Run: `uv run mypy app/core/database.py` and `uv run pytest tests/ -x -q -n 4` (MariaDB suite).
Expected: mypy clean; suite green.

- [ ] **Step 4: Commit**

```bash
git add app/core/database.py
git commit -m "feat(postgres-poc): dialect-aware engine config and statement timeout"
```

### Task 3: Portable column types (unsigned ints, JSON, server defaults)

**Files:**
- Modify: `app/models/types.py`, `app/models/admin_action.py`, all `app/models/*.py` with `server_default=text("current_timestamp()")`

**Interfaces:**
- Produces: `UnsignedInt` / `UnsignedSmallInt` that compile as `INTEGER`/`SMALLINT` on Postgres (values here never exceed signed 32-bit today; the feasibility doc's data-migration check still applies before any real cutover); `AdminActions.details` as a portable JSON column; every timestamp `server_default` valid on both dialects.

- [ ] **Step 1: with_variant the unsigned types**

In `app/models/types.py`:

```python
from sqlalchemy import DateTime, Integer, SmallInteger
from sqlalchemy.dialects.mysql import INTEGER, SMALLINT

# Shared type instances, not classes — use as Column(UnsignedInt, ...); don't call them.
# Postgres has no unsigned ints; the variant maps to plain INTEGER/SMALLINT there.
UnsignedInt = INTEGER(unsigned=True).with_variant(Integer(), "postgresql")
UnsignedSmallInt = SMALLINT(unsigned=True).with_variant(SmallInteger(), "postgresql")
```

Update the module docstring's UnsignedInt/UnsignedSmallInt paragraphs to mention the Postgres variant.

- [ ] **Step 2: Portable JSON in admin_action.py**

Replace `from sqlalchemy.dialects.mysql import JSON` with the generic `from sqlalchemy import JSON` (both compile to `JSON` DDL on MariaDB; the generic type compiles to `JSON` on Postgres).

- [ ] **Step 3: Normalize server defaults**

Replace every `text("current_timestamp()")` in `app/models/` with `text("CURRENT_TIMESTAMP")` — the empty-parens spelling is a MariaDB-ism that Postgres rejects; the bare keyword is valid DDL on both, and MariaDB normalizes both to the same stored default (so `alembic` autogen and the schema-sync test see no difference; `alembic/env.py` does not enable `compare_server_default`).

```bash
grep -rl 'text("current_timestamp()")' app/models/ | xargs sed -i 's/text("current_timestamp()")/text("CURRENT_TIMESTAMP")/g'
```

- [ ] **Step 4: Verify — schema-sync test is the real gate**

Run: `uv run mypy app/models/` then `MYSQL_ROOT_PASSWORD=dev_root_password uv run pytest tests/integration/test_schema_sync.py -q`
Expected: mypy clean; schema-sync green (proves MariaDB DDL is unchanged in effect).

- [ ] **Step 5: Commit**

```bash
git add app/models/
git commit -m "feat(postgres-poc): portable column types and server defaults"
```

### Task 4: Dialect branches for raw-SQL sites

Audit result (2026-08-20, supersedes the June doc's narrower list):

| Site | MySQL-ism | Reached from | Disposition |
|---|---|---|---|
| `app/utils/comment_search.py` | `MATCH ... AGAINST` (3 modes) | GET /comments, GET /images comment search | dialect branch → LIKE path |
| `app/api/v1/tags.py` `list_tags` | `MATCH ... AGAINST` + tokenizer sim | GET /tags?search= | dialect branch → ILIKE per word |
| `app/services/tag_type_flags.py` | multi-table `UPDATE ... JOIN`, `MAX(bool)` | tag add/remove request paths | dialect branch → `UPDATE ... FROM` + `bool_or` |
| `app/services/repost.py` | `INSERT IGNORE ... SELECT` ×3 | mark-repost admin path | shared helper → `ON CONFLICT DO NOTHING` |
| `app/services/ml_raw_store.py` | `mysql_insert().prefix_with("IGNORE")` | ML ingest (arq/scripts) | dialect branch → `pg_insert().on_conflict_do_nothing()` |
| `app/services/user_tag_affinity.py` | `GET_LOCK`, `SELECT DATABASE()`, `CREATE TABLE ... ENGINE=InnoDB` | nightly cron / manual script | guard: `NotImplementedError` on non-mysql |
| `app/services/recommendations.py` | none — standard SQL (seeding is Python `random.Random`) | GET for-you feed | works as-is |
| `app/core/database.py` `statement_timeout` | `SET SESSION max_statement_time` | search endpoints | done in Task 2 |

**Files:**
- Modify: `app/utils/comment_search.py`, `app/api/v1/comments.py:130`, `app/api/v1/images.py:820`, `app/api/v1/tags.py` (search block in `list_tags`), `app/services/tag_type_flags.py`, `app/services/repost.py`, `app/services/ml_raw_store.py`, `app/services/user_tag_affinity.py`
- Test: `tests/unit/test_comment_search.py`

**Interfaces:**
- Produces: `parse_comment_search(raw, *, index_visible: bool = True)` — with `index_visible=False` every token lands on the LIKE lists and `boolean_query` stays empty; `apply_comment_text_search(query, raw, mode, *, use_fulltext: bool = True)` — with `use_fulltext=False` all modes except explicit `"like"` go through the parsed path with `index_visible=False`. Callers compute `use_fulltext=db.get_bind().dialect.name != "postgresql"`.

- [ ] **Step 1 (TDD): failing unit tests for the parse mode**

```python
class TestParseWithoutIndex:
    """index_visible=False (Postgres: no fulltext index) — everything rides LIKE."""

    def test_indexable_token_goes_to_like(self):
        parsed = parse_comment_search("birthday", index_visible=False)
        assert parsed.boolean_query == ""
        assert parsed.like_terms == ["birthday"]

    def test_negation_still_works(self):
        parsed = parse_comment_search("happy -birthday", index_visible=False)
        assert parsed.like_terms == ["happy"]
        assert parsed.not_like_terms == ["birthday"]

    def test_quoted_phrase_becomes_single_like_term(self):
        parsed = parse_comment_search('"happy birthday"', index_visible=False)
        assert parsed.boolean_query == ""
        assert parsed.like_terms == ["happy birthday"]
```

Run: `uv run pytest tests/unit/test_comment_search.py -q` — expect the new tests to FAIL (unexpected keyword argument).

- [ ] **Step 2: implement `index_visible` + `use_fulltext`, wire the two callers, verify green**

In `parse_comment_search`, gate the indexable checks: phrases use `if index_visible and tokens and all(_is_indexable(t) ...)`, bare tokens use `if index_visible and _is_indexable(token)`. In `apply_comment_text_search(query, raw, mode, *, use_fulltext=True)`: when `use_fulltext` is false, `"boolean"`/`"natural"`/default all take the parsed path with `index_visible=False` (`"like"` unchanged — negation and phrases survive; `+`/`*` operators degrade to plain ANDed words). Callers add the keyword: `apply_comment_text_search(query, s, mode, use_fulltext=db.get_bind().dialect.name != "postgresql")`.

- [ ] **Step 3: tags.py search branch**

At the top of the `if search:` block compute `use_fulltext = db.get_bind().dialect.name != "postgresql"`; short-query branch uses `.ilike` instead of `.like` when not `use_fulltext`; the ≥3-char branch gets a preceding `elif not use_fulltext:` arm — `for word in search.split(): query = query.where(Tags.title.ilike(f"%{_escape_like_pattern(word)}%"))` — leaving `fulltext_query_str` None so relevance ordering falls into the existing LIKE branch (which is already built on portable `lower()` comparisons).

- [ ] **Step 4: tag_type_flags PG twin**

```python
_RECOMPUTE_SQL_PG = text(
    """
    UPDATE images
    SET has_theme = COALESCE(agg.ht, FALSE),
        has_source = COALESCE(agg.hs, FALSE),
        has_artist = COALESCE(agg.ha, FALSE),
        has_character = COALESCE(agg.hc, FALSE)
    FROM (
        SELECT i2.image_id,
               bool_or(t.type = 1) AS ht,
               bool_or(t.type = 2) AS hs,
               bool_or(t.type = 3) AS ha,
               bool_or(t.type = 4) AS hc
        FROM images i2
        LEFT JOIN tag_links tl ON tl.image_id = i2.image_id
        LEFT JOIN tags t ON t.tag_id = tl.tag_id
        WHERE i2.image_id IN :ids
        GROUP BY i2.image_id
    ) AS agg
    WHERE images.image_id = agg.image_id
    """
).bindparams(bindparam("ids", expanding=True))
```

`refresh_images_tag_type_flags` picks by `db.get_bind().dialect.name`.

- [ ] **Step 5: repost helper + ml_raw_store branch**

repost.py: replace the three literals with one helper —

```python
def _copy_to_original_sql(db: AsyncSession, table: str, insert_cols: str, select_cols: str) -> TextClause:
    """INSERT-or-skip-duplicates, copying `table` rows from the repost to the original."""
    base = (
        f"INTO {table} ({insert_cols}) "
        f"SELECT {select_cols} FROM {table} WHERE image_id = :repost_id"
    )
    if db.get_bind().dialect.name == "postgresql":
        return text(f"INSERT {base} ON CONFLICT DO NOTHING")
    return text(f"INSERT IGNORE {base}")
```

ml_raw_store.py:

```python
if db.get_bind().dialect.name == "postgresql":
    stmt: Any = pg_insert(MlRawPredictions).values(batch).on_conflict_do_nothing()
else:
    stmt = mysql_insert(MlRawPredictions).values(batch).prefix_with("IGNORE")
```

- [ ] **Step 6: user_tag_affinity guard**

First statement of `refresh_user_tag_affinity`:

```python
    if db.get_bind().dialect.name != "mysql":
        raise NotImplementedError(
            "refresh_user_tag_affinity is MariaDB-only (GET_LOCK, ENGINE=InnoDB "
            "helper tables); see docs/plans/2026-Q3/2026-08-20-postgres-poc-impl.md"
        )
```

- [ ] **Step 7: verify**

Run: `uv run mypy app/` (clean), `MYSQL_ROOT_PASSWORD=dev_root_password uv run pytest tests/ -q -n 4` (green on MariaDB).

- [ ] **Step 8: Commit**

```bash
git add app/ tests/unit/test_comment_search.py
git commit -m "feat(postgres-poc): dialect branches for raw-SQL sites"
```

### Task 5: POC setup + smoke script

**Files:**
- Create: `scripts/postgres_poc.py`

**Interfaces:**
- Consumes: the Task 1 container URL (override via `POSTGRES_POC_DATABASE_URL`); `app.main.app`; `SQLModel.metadata`.
- Produces: `uv run python scripts/postgres_poc.py setup` (create schema) and `uv run python scripts/postgres_poc.py smoke` (seed + exercise the real app in-process, printing a pass/fail table and exiting non-zero on failure).

The script has two subcommands. `setup`: `DROP SCHEMA public CASCADE` + `CREATE SCHEMA public` (drop_all can't untangle the FK cycles without CASCADE), dedupe the two cross-table index-name collisions (`idx_date`, `idx_tag_id` — MySQL scopes index names per table, Postgres per schema), then `SQLModel.metadata.create_all` with models registered by importing `app.main` (the models package `__init__` misses some modules, e.g. `user_suspension`). `smoke`: seed permissions + a bcrypt login user + tag + image directly via the ORM, then run the real app in-process (httpx `ASGITransport`, real Redis from the dev stack, no dependency overrides) through: `/health`, login, `/auth/me`, images list + detail, tags list, `GET /tags?search=` (exercises the Task 4 ILIKE branch), comment create, comments list, `GET /comments?search_text=` (exercises the comment-search LIKE branch — the param is `search_text`, not `search`). Prints a PASS/FAIL table; exit 1 on any failure.

- [ ] **Step 1: write the script as specified above** (`scripts/postgres_poc.py`, mypy-clean)
- [ ] **Step 2: run it** — `setup` then `smoke`; all checks PASS
- [ ] **Step 3: commit**

```bash
git add scripts/postgres_poc.py
git commit -m "feat(postgres-poc): schema setup + e2e smoke script"
```

### Task 6: Full verification and PR

- [ ] **Step 1: mypy everything touched**

Run: `uv run mypy app/ && uv run mypy scripts/postgres_poc.py`
Expected: clean.

- [ ] **Step 2: Full MariaDB suite**

Run: `MYSQL_ROOT_PASSWORD=dev_root_password uv run pytest tests/ -q -n 4`
Expected: green — proves the POC changed nothing for MariaDB.

- [ ] **Step 3: End-to-end POC run**

```bash
docker compose -f docker-compose.postgres.yml up -d
uv run python scripts/postgres_poc.py setup
uv run python scripts/postgres_poc.py smoke
```
Expected: all smoke checks pass against Postgres.

- [ ] **Step 4: PR**

Push `feat/postgres-poc`, open PR with the smoke output and the discussion items (redesign opportunities) in the body.
