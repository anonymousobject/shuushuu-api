# Postgres Tag Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Answer `GET /api/v1/search` from Postgres with pg_trgm, then delete Meilisearch from the codebase and the compose stacks.

**Architecture:** A pure SQL builder (`build_search`) turns a query plus filters into two parameterised statements: an ids statement that unions four index-backed candidate branches and ranks them by tier, typo count, word position, and effective usage; and an exact count. A thin async wrapper runs them with two `SET LOCAL`s. The route keeps its hydration and artist-identity code and swaps only the engine call. Part B removes every Meilisearch touchpoint.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async + asyncpg, Alembic, Postgres 18 with `pg_trgm`, `unaccent`, `fuzzystrmatch`; pytest with `./run-tests.sh`; `uv run ruff`, `uv run mypy`.

**Spec:** `docs/plans/2026-Q3/2026-09-11-postgres-tag-search-design.md` — read it first; every threshold and gate below is copied from it.

## Global Constraints

- Work in `/home/dtaylor/shuu/shuushuu-api`. Read its `AGENTS.md` first. Never `git add -A`; stage files by name.
- Part A is branch `feat/postgres-tag-search` off `main`; Part B is branch `chore/delete-meilisearch` off `main` after Part A merges.
- Before Part A: discard the uncommitted POC. `git checkout -- app/api/v1/search.py && rm -f app/services/search_pg.py && git branch -D poc/pg-trigram-search`. In the frontend repo run `git checkout -- src/lib/api/client.ts` to drop the POC-only `engine=pg` line. No other frontend change in this plan.
- Fold function name: `public.fold_search_text(text)`, defined as `lower(public.unaccent('public.unaccent'::regdictionary, $1))`, `IMMUTABLE PARALLEL SAFE STRICT`.
- Index names: `ix_tags_title_fold_trgm`, `ix_tags_desc_fold_trgm`, `ix_tag_external_links_url_fold_trgm`, `ix_tags_title_fold_prefix`.
- Word split regex, both sides: `[^[:alnum:]_]+`. Fuzzy threshold `pg_trgm.word_similarity_threshold = 0.5` via `SET LOCAL`; also `SET LOCAL jit = off`. Fuzzy branch needs a token with 5+ letters; description/URL branches need every token 2+ characters; prefix-only path when the query is under 3 characters or has no 3-character alphanumeric run.
- Every user value is a bind parameter. Never interpolate query text into SQL. Never write `:param::type` (SQLAlchemy will not bind it); use `CAST(:param AS type)`. One SQL command per `execute` (asyncpg rejects multi-command statements).
- Tests: `./run-tests.sh <path>` for one file, `./run-tests.sh` for the suite (needs `docker compose up -d postgres`). Lint: `uv run ruff check app tests && uv run ruff format --check app tests && uv run mypy app`.
- Every commit ends with:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01MNoxNBcTrbQyAD55cVmfCh
  ```
  and every PR description ends with:
  ```
  🤖 Generated with [Claude Code](https://claude.com/claude-code)

  https://claude.ai/code/session_01MNoxNBcTrbQyAD55cVmfCh
  ```

## File structure

Part A creates:
- `alembic/versions/0004_tag_search_fold.py` — extensions, fold function, four indexes.
- `app/services/tag_search.py` — `TagSearchResult`, `SearchGates`, `gates_for`, `build_search`, `search_tags`. One module: the gates and builder are pure and unit-tested; `search_tags` is the only function that touches the DB.
- `tests/integration/test_tag_search_schema.py` — the migration's objects exist and fold correctly.
- `tests/unit/test_tag_search_gates.py`, `tests/unit/test_tag_search_sql.py` — pure tests.
- `tests/integration/test_tag_search_corpus.py` — the acceptance corpus from the spec appendix against the real test DB.

Part A modifies `app/api/v1/search.py` (engine swap), `app/main.py` (drop the route override), and rewrites `tests/api/v1/test_search.py`.

Part B deletes `app/services/search.py`, `scripts/reindex_search.py`, four test files, the compose service, the dependency, and every sync call; adds `docs/adr/0015-tag-search-runs-in-postgres.md`.

---

# Part A — Postgres search behind `GET /search` (PR 1)

### Task 1: Migration — extensions, fold function, indexes

**Files:**
- Create: `alembic/versions/0004_tag_search_fold.py`
- Test: `tests/integration/test_tag_search_schema.py`

**Interfaces:**
- Produces: SQL function `public.fold_search_text(text) -> text` and the four indexes named in Global Constraints. Later tasks call the function by that exact name.

- [ ] **Step 1: Write the failing schema test**

```python
"""The tag-search migration leaves the objects the search SQL depends on."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

EXPECTED_INDEXES = {
    "ix_tags_title_fold_trgm",
    "ix_tags_desc_fold_trgm",
    "ix_tag_external_links_url_fold_trgm",
    "ix_tags_title_fold_prefix",
}


@pytest.mark.integration
class TestTagSearchSchema:
    async def test_extensions_installed(self, db_session: AsyncSession):
        rows = await db_session.execute(
            text("SELECT extname FROM pg_extension WHERE extname IN ('pg_trgm', 'unaccent', 'fuzzystrmatch')")
        )
        assert set(rows.scalars()) == {"pg_trgm", "unaccent", "fuzzystrmatch"}

    @pytest.mark.parametrize(
        ("raw", "folded"),
        [
            ("Märchen-Noir C++", "marchen-noir c++"),
            ("EB十", "eb十"),
            ("Pokémon", "pokemon"),
            ("Louise Françoise", "louise francoise"),
        ],
    )
    async def test_fold_search_text(self, db_session: AsyncSession, raw: str, folded: str):
        value = await db_session.execute(text("SELECT public.fold_search_text(:raw)"), {"raw": raw})
        assert value.scalar_one() == folded

    async def test_search_indexes_exist(self, db_session: AsyncSession):
        rows = await db_session.execute(
            text("SELECT indexname FROM pg_indexes WHERE indexname LIKE 'ix_tag%\\_fold\\_%'")
        )
        assert set(rows.scalars()) == EXPECTED_INDEXES
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./run-tests.sh tests/integration/test_tag_search_schema.py`
Expected: FAIL — `function public.fold_search_text(unknown) does not exist` and an empty index set.

- [ ] **Step 3: Write the migration**

```python
"""tag search: fold function, trigram and prefix indexes

Revision ID: 0004_tag_search_fold
Revises: e20bac5f3ac3
Create Date: 2026-09-11

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_tag_search_fold"
down_revision: str | Sequence[str] | None = "e20bac5f3ac3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXTENSIONS = ("pg_trgm", "unaccent", "fuzzystrmatch")

# IMMUTABLE is a promise that lets the function sit inside an index
# expression; changing the unaccent dictionary later means reindexing.
# Both names are schema-qualified so the migration and the app inline the
# same function whatever search_path says.
FOLD_FUNCTION = """
CREATE OR REPLACE FUNCTION public.fold_search_text(text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
AS $$ SELECT lower(public.unaccent('public.unaccent'::regdictionary, $1)) $$
"""

# (name, table, index definition after "ON <table>")
INDEXES = (
    ("ix_tags_title_fold_trgm", "tags", "USING gin (public.fold_search_text(title::text) gin_trgm_ops)"),
    ("ix_tags_desc_fold_trgm", "tags", 'USING gin (public.fold_search_text("desc") gin_trgm_ops)'),
    (
        "ix_tag_external_links_url_fold_trgm",
        "tag_external_links",
        "USING gin (public.fold_search_text(url) gin_trgm_ops)",
    ),
    ("ix_tags_title_fold_prefix", "tags", "(public.fold_search_text(title::text) text_pattern_ops)"),
)


def upgrade() -> None:
    """Upgrade schema."""
    for extension in EXTENSIONS:
        op.execute(f"CREATE EXTENSION IF NOT EXISTS {extension}")
    op.execute(FOLD_FUNCTION)
    # CONCURRENTLY cannot run inside a transaction; the autocommit block
    # commits the migration so far and runs these statements on their own.
    with op.get_context().autocommit_block():
        for name, table, definition in INDEXES:
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {table} {definition}")


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        for name, _table, _definition in INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    op.execute("DROP FUNCTION IF EXISTS public.fold_search_text(text)")
    # The extensions stay: dropping them is an operator decision.
```

- [ ] **Step 4: Run the schema test to verify it passes**

Run: `./run-tests.sh tests/integration/test_tag_search_schema.py`
Expected: PASS (6 tests). The test DB is built by `alembic upgrade head`, so the new revision runs automatically.

- [ ] **Step 5: Apply the migration to the dev database and clean the POC objects**

The dev database already carries the function and the four `ix_*_fold_*` indexes (created by hand during the POC; the migration's `IF NOT EXISTS` makes them no-ops), plus throwaway POC objects. Drop the throwaways, then run the migration:

```bash
U=$(docker exec shuushuu-postgres-dev printenv POSTGRES_USER)
docker exec shuushuu-postgres-dev psql -U "$U" -d shuushuu -c "
DROP INDEX IF EXISTS poc_tags_title_trgm, poc_tags_title_unaccent_trgm, poc_tags_desc_trgm,
  poc_tag_external_links_url_trgm, poc_tags_title_prefix_btree;
DROP FUNCTION IF EXISTS poc_unaccent(text);"
uv run alembic upgrade head
uv run alembic current
```
Expected: `0004_tag_search_fold (head)`.

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/0004_tag_search_fold.py tests/integration/test_tag_search_schema.py
git commit -m "feat(search): migration for the fold function and trigram indexes"
```

---

### Task 2: Gates — which branches a query runs

**Files:**
- Create: `app/services/tag_search.py`
- Test: `tests/unit/test_tag_search_gates.py`

**Interfaces:**
- Produces: `SearchGates(prefix_only: bool, fuzzy: bool, secondary: bool)` and `gates_for(query: str, tokens: list[str]) -> SearchGates`. `tokens` is `query.split()`.

- [ ] **Step 1: Write the failing gate tests**

```python
"""Gates decide which candidate branches a query runs (spec: Candidate set)."""

import pytest

from app.services.tag_search import SearchGates, gates_for


@pytest.mark.unit
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        # Under 3 chars, or no 3-char alphanumeric run: prefix-only path.
        ("sa", SearchGates(prefix_only=True, fuzzy=False, secondary=False)),
        ("C++", SearchGates(prefix_only=True, fuzzy=False, secondary=False)),
        ("C.C.", SearchGates(prefix_only=True, fuzzy=False, secondary=False)),
        # "the" is 3 chars but has only 3 letters: no fuzzy; "f" is 1 char: no desc/URL.
        ("the f", SearchGates(prefix_only=False, fuzzy=False, secondary=False)),
        # Digits form the run; no letters at all: no fuzzy, but desc/URL run.
        ("100%", SearchGates(prefix_only=False, fuzzy=False, secondary=True)),
        # CJK ideographs are letters and alphanumerics.
        ("EB十", SearchGates(prefix_only=False, fuzzy=False, secondary=True)),
        ("neko", SearchGates(prefix_only=False, fuzzy=False, secondary=True)),
        ("sakura", SearchGates(prefix_only=False, fuzzy=True, secondary=True)),
        ("sakrua kinomto", SearchGates(prefix_only=False, fuzzy=True, secondary=True)),
        ("yano_0o0", SearchGates(prefix_only=False, fuzzy=False, secondary=True)),
        ("21412050", SearchGates(prefix_only=False, fuzzy=False, secondary=True)),
    ],
)
def test_gates_for(query: str, expected: SearchGates):
    assert gates_for(query, query.split()) == expected
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./run-tests.sh tests/unit/test_tag_search_gates.py`
Expected: FAIL — `ModuleNotFoundError: app.services.tag_search`.

- [ ] **Step 3: Create the module with the gates**

```python
"""Tag search in Postgres with pg_trgm.

Design: docs/plans/2026-Q3/2026-09-11-postgres-tag-search-design.md.

`gates_for` and `build_search` are pure: they turn a query into SQL text and
bind parameters and are unit-tested without a database. `search_tags` runs
the statements. The SQL depends on the objects created by migration
0004_tag_search_fold: `public.fold_search_text(text)` and the four
`ix_*_fold_*` indexes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.utils.like_escape import escape_like_pattern

logger = get_logger(__name__)

# Three consecutive letters or digits (underscore excluded): the shortest
# literal a trigram index can look up.
_ALNUM_RUN = re.compile(r"[^\W_]{3}")
# A token of only digits is an identifier (pixiv artist ID, year): it must
# match literally, never fuzzily.
_DIGITS = re.compile(r"^\d+$")

# Meilisearch's minimum word length for one typo; below it a word matches
# literally only.
MIN_FUZZY_LETTERS = 5
# A one-character token contains-matches nearly every description.
MIN_SECONDARY_TOKEN_CHARS = 2


@dataclass(frozen=True)
class TagSearchResult:
    """Ordered tag ids for one page, plus the exact total."""

    tag_ids: list[int]
    total: int


@dataclass(frozen=True)
class SearchGates:
    """Which candidate branches a query runs."""

    prefix_only: bool  # title-prefix path only: no CTE, no fuzzy/desc/URL
    fuzzy: bool  # trigram word-similarity branch
    secondary: bool  # description and external-URL branches


def _letter_count(token: str) -> int:
    return sum(char.isalpha() for char in token)


def gates_for(query: str, tokens: list[str]) -> SearchGates:
    """Decide the branches for `query` (already stripped) and its whitespace tokens."""
    prefix_only = len(query) < 3 or _ALNUM_RUN.search(query) is None
    fuzzy = not prefix_only and any(_letter_count(token) >= MIN_FUZZY_LETTERS for token in tokens)
    secondary = not prefix_only and all(len(token) >= MIN_SECONDARY_TOKEN_CHARS for token in tokens)
    return SearchGates(prefix_only=prefix_only, fuzzy=fuzzy, secondary=secondary)
```

- [ ] **Step 4: Run the gate tests to verify they pass**

Run: `./run-tests.sh tests/unit/test_tag_search_gates.py`
Expected: PASS (11 cases).

- [ ] **Step 5: Commit**

```bash
git add app/services/tag_search.py tests/unit/test_tag_search_gates.py
git commit -m "feat(search): gates that pick a query's candidate branches"
```

---

### Task 3: SQL builder

**Files:**
- Modify: `app/services/tag_search.py` (append)
- Test: `tests/unit/test_tag_search_sql.py`

**Interfaces:**
- Consumes: `gates_for`, `escape_like_pattern`.
- Produces: `SearchStatements(ids_sql: str, count_sql: str, params: dict[str, Any])` and `build_search(query, *, limit, offset, type_filter, exclude_aliases, sort) -> SearchStatements`. `sort` is the route's Meilisearch-style list, e.g. `["title:asc"]`, or `None`.

- [ ] **Step 1: Write the failing builder tests**

```python
"""build_search is pure: assert the SQL shape and bind parameters, no database."""

import pytest

from app.services.tag_search import build_search


def _build(query: str, **overrides):
    kwargs = {"limit": 10, "offset": 0, "type_filter": None, "exclude_aliases": False, "sort": None}
    kwargs.update(overrides)
    return build_search(query, **kwargs)


@pytest.mark.unit
class TestBuildSearch:
    def test_empty_query_lists_all_by_effective_usage(self):
        st = _build("")
        assert "candidates" not in st.ids_sql
        assert "COALESCE(parent.usage_count, t.usage_count) DESC, t.tag_id ASC" in st.ids_sql
        assert st.count_sql.strip().startswith("SELECT count(*) FROM tags t")
        assert st.params == {"limit": 10, "offset": 0}

    def test_prefix_only_query_has_no_cte(self):
        st = _build("sa")
        assert "WITH" not in st.ids_sql
        assert "fold_search_text(t.title::text) LIKE public.fold_search_text(:prefix_q)" in st.ids_sql
        assert st.params["prefix_q"] == "sa%"
        assert "levenshtein" not in st.ids_sql

    def test_general_query_unions_all_four_branches(self):
        st = _build("sakura kino")
        assert st.ids_sql.count("UNION") == 3
        assert "<%" in st.ids_sql  # fuzzy: "sakura" has 6 letters
        assert 'fold_search_text("desc")' in st.ids_sql
        assert "FROM tag_external_links WHERE" in st.ids_sql
        assert st.params["like_tok0"] == "%sakura%"
        assert st.params["like_tok1"] == "%kino%"
        assert st.params["like_q"] == "%sakura kino%"
        assert st.params["q"] == "sakura kino"

    def test_short_letters_skip_fuzzy_and_one_char_token_skips_secondary(self):
        st = _build("the f")
        assert "<%" not in st.ids_sql
        assert "tag_external_links" not in st.ids_sql
        assert "UNION" not in st.ids_sql

    def test_numeric_token_adds_literal_clause_and_no_fuzzy(self):
        st = _build("21412050")
        assert "<%" not in st.ids_sql
        assert st.ids_sql.count("EXISTS (SELECT 1 FROM tag_external_links l") == 1
        assert st.count_sql.count("EXISTS (SELECT 1 FROM tag_external_links l") == 1

    def test_like_metacharacters_are_escaped(self):
        st = _build("100%")
        assert st.params["like_tok0"] == "%100\\%%"
        assert st.params["prefix_q"] == "100\\%%"

    def test_filters_apply_to_ids_and_count(self):
        st = _build("sakura", type_filter=4, exclude_aliases=True)
        for sql in (st.ids_sql, st.count_sql):
            assert "t.type = :type_filter" in sql
            assert "t.alias_of IS NULL" in sql
        assert st.params["type_filter"] == 4

    def test_explicit_sort_replaces_relevance(self):
        st = _build("sakura", sort=["title:asc"])
        assert "ORDER BY t.title ASC, t.tag_id ASC" in st.ids_sql
        assert "levenshtein" not in st.ids_sql

    def test_usage_count_sort_uses_effective_count(self):
        st = _build("", sort=["usage_count:desc"])
        assert "ORDER BY COALESCE(parent.usage_count, t.usage_count) DESC, t.tag_id DESC" in st.ids_sql

    def test_unknown_sort_field_is_rejected(self):
        with pytest.raises(ValueError, match="Unsupported sort field"):
            _build("sakura", sort=["not_a_field:asc"])

    def test_user_text_never_appears_in_sql(self):
        st = _build("'; DROP TABLE tags; --")
        assert "DROP TABLE" not in st.ids_sql
        assert "DROP TABLE" not in st.count_sql
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./run-tests.sh tests/unit/test_tag_search_sql.py`
Expected: FAIL — `ImportError: cannot import name 'build_search'`.

- [ ] **Step 3: Append the builder to `app/services/tag_search.py`**

This SQL was validated against the dev corpus on 2026-09-11 (every appendix query ranked as expected; 1–76 ms with the indexes). Copy it exactly.

```python
# Meilisearch sort fields -> SQL. usage_count resolves to the effective count so
# alias rows rank by their parent's popularity.
_EFFECTIVE_USAGE = "COALESCE(parent.usage_count, t.usage_count)"
_SORT_COLUMNS = {
    "usage_count": _EFFECTIVE_USAGE,
    "title": "t.title",
    "type": "t.type",
    "date_added": "t.date_added",
    "tag_id": "t.tag_id",
}

# The word list of a folded string: split on runs that are not letters,
# digits, or underscore; drop empties. The same expression tokenizes the
# query (`:q`) and each title, so both sides agree on what a word is.
_WORDS = "array_remove(regexp_split_to_array(public.fold_search_text({expr}), '[^[:alnum:]_]+'), '')"

_BASE_FROM = "FROM tags t LEFT JOIN tags parent ON parent.tag_id = t.alias_of"


@dataclass(frozen=True)
class SearchStatements:
    """The two statements one search runs, sharing one parameter dict."""

    ids_sql: str
    count_sql: str
    params: dict[str, Any]


def _contains_all(expr: str, token_count: int) -> str:
    """`expr` contains every token: AND of folded LIKE per token."""
    return " AND ".join(
        f"{expr} LIKE public.fold_search_text(:like_tok{i})" for i in range(token_count)
    )


def _order_clause(sort: list[str] | None) -> str | None:
    if not sort:
        return None
    field, _, direction_token = sort[0].partition(":")
    if field not in _SORT_COLUMNS:
        raise ValueError(f"Unsupported sort field: {field!r}")
    direction = "ASC" if direction_token.lower() == "asc" else "DESC"
    return f"{_SORT_COLUMNS[field]} {direction}, t.tag_id {direction}"


def build_search(
    query: str,
    *,
    limit: int,
    offset: int,
    type_filter: int | None,
    exclude_aliases: bool,
    sort: list[str] | None,
) -> SearchStatements:
    """Build the ids and count statements for one search. Pure."""
    query = query.strip()
    tokens = query.split()
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    filters: list[str] = []
    if type_filter is not None:
        filters.append("t.type = :type_filter")
        params["type_filter"] = type_filter
    if exclude_aliases:
        filters.append("t.alias_of IS NULL")
    order = _order_clause(sort)

    # Empty query: list every tag.
    if not tokens:
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        ids_sql = (
            f"SELECT t.tag_id {_BASE_FROM} {where} "
            f"ORDER BY {order or f'{_EFFECTIVE_USAGE} DESC, t.tag_id ASC'} "
            "LIMIT :limit OFFSET :offset"
        )
        return SearchStatements(ids_sql, f"SELECT count(*) FROM tags t {where}", params)

    params["q"] = query
    params["prefix_q"] = f"{escape_like_pattern(query)}%"
    for i, token in enumerate(tokens):
        params[f"like_tok{i}"] = f"%{escape_like_pattern(token)}%"
    gates = gates_for(query, tokens)

    # Short or sub-trigram query: title prefix on the btree index, nothing else.
    if gates.prefix_only:
        filters.insert(
            0, "public.fold_search_text(t.title::text) LIKE public.fold_search_text(:prefix_q)"
        )
        where = f"WHERE {' AND '.join(filters)}"
        exact_first = (
            "CASE WHEN public.fold_search_text(t.title::text) = public.fold_search_text(:q) "
            "THEN 0 ELSE 1 END"
        )
        ids_sql = (
            f"SELECT t.tag_id {_BASE_FROM} {where} "
            f"ORDER BY {order or f'{exact_first}, {_EFFECTIVE_USAGE} DESC, t.tag_id ASC'} "
            "LIMIT :limit OFFSET :offset"
        )
        return SearchStatements(ids_sql, f"SELECT count(*) FROM tags t {where}", params)

    # General path: the candidate set is a UNION, never an OR — an OR that
    # mixes these branches defeats the planner's bitmap and scans the table.
    branches = [
        f"SELECT tag_id FROM tags WHERE {_contains_all('public.fold_search_text(title::text)', len(tokens))}"
    ]
    if gates.fuzzy:
        branches.append(
            "SELECT tag_id FROM tags WHERE public.fold_search_text(:q) <% public.fold_search_text(title::text)"
        )
    if gates.secondary:
        params["like_q"] = f"%{escape_like_pattern(query)}%"
        branches.append(
            f'SELECT tag_id FROM tags WHERE {_contains_all("public.fold_search_text(\\"desc\\")", len(tokens))}'
        )
        branches.append(
            "SELECT tag_id FROM tag_external_links "
            "WHERE public.fold_search_text(url) LIKE public.fold_search_text(:like_q)"
        )
    # Digits match literally somewhere, whatever branch admitted the row.
    for i, token in enumerate(tokens):
        if _DIGITS.match(token):
            filters.append(
                f"(public.fold_search_text(t.title::text) LIKE public.fold_search_text(:like_tok{i})"
                f' OR public.fold_search_text(t."desc") LIKE public.fold_search_text(:like_tok{i})'
                f" OR EXISTS (SELECT 1 FROM tag_external_links l WHERE l.tag_id = t.tag_id"
                f" AND public.fold_search_text(l.url) LIKE public.fold_search_text(:like_tok{i})))"
            )
    candidates = "candidates AS (\n    " + "\n    UNION\n    ".join(branches) + "\n)"
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    count_sql = (
        f"WITH {candidates} SELECT count(*) FROM tags t JOIN candidates c ON c.tag_id = t.tag_id {where}"
    )

    if order:
        ids_sql = (
            f"WITH {candidates} SELECT t.tag_id {_BASE_FROM} JOIN candidates c ON c.tag_id = t.tag_id "
            f"{where} ORDER BY {order} LIMIT :limit OFFSET :offset"
        )
        return SearchStatements(ids_sql, count_sql, params)

    # Relevance. Tiers 0-3 are literal matches, so their typo count is 0 by
    # construction and the CASE keeps the levenshtein work for tier 4 only.
    qwords = (
        "qwords AS (SELECT w, ord, ord = max(ord) OVER () AS is_last "
        f"FROM unnest({_WORDS.format(expr=':q')}) WITH ORDINALITY AS u(w, ord))"
    )
    ids_sql = f"""WITH {qwords},
{candidates},
scored AS (
    SELECT t.tag_id, {_EFFECTIVE_USAGE} AS eff_usage, tiers.tier,
        CASE WHEN tiers.tier < 4 THEN 0 ELSE (
            SELECT sum(best.d) FROM qwords CROSS JOIN LATERAL (
                SELECT min(CASE WHEN qwords.is_last
                               THEN least(levenshtein(qwords.w, x), levenshtein(qwords.w, left(x, length(qwords.w))))
                               ELSE levenshtein(qwords.w, x) END) AS d
                FROM unnest(f.tw) AS x) AS best) END AS typos,
        CASE WHEN tiers.tier < 4 THEN COALESCE((
            SELECT min(xo.ord) FROM unnest(f.tw) WITH ORDINALITY AS xo(x, ord), qwords
            WHERE position(qwords.w IN xo.x) > 0), 999)
        ELSE COALESCE((
            SELECT min(xo.ord) FROM unnest(f.tw) WITH ORDINALITY AS xo(x, ord), qwords
            WHERE levenshtein(qwords.w, xo.x) = (SELECT min(levenshtein(qwords.w, x2)) FROM unnest(f.tw) AS x2)), 999) END AS pos
    {_BASE_FROM}
    JOIN candidates c ON c.tag_id = t.tag_id
    CROSS JOIN LATERAL (SELECT public.fold_search_text(t.title::text) AS ft, {_WORDS.format(expr='t.title::text')} AS tw) AS f
    CROSS JOIN LATERAL (SELECT CASE
        WHEN f.ft = public.fold_search_text(:q) THEN 0
        WHEN f.ft LIKE public.fold_search_text(:prefix_q) THEN 1
        WHEN NOT EXISTS (SELECT 1 FROM qwords WHERE NOT is_last AND NOT (w = ANY(f.tw)))
         AND EXISTS (SELECT 1 FROM unnest(f.tw) AS x, qwords WHERE qwords.is_last AND left(x, length(qwords.w)) = qwords.w) THEN 2
        WHEN {_contains_all('f.ft', len(tokens))} THEN 3
        ELSE 4 END AS tier) AS tiers
    {where}
)
SELECT tag_id FROM scored ORDER BY tier, typos, pos, eff_usage DESC, tag_id ASC LIMIT :limit OFFSET :offset"""
    return SearchStatements(ids_sql, count_sql, params)
```

- [ ] **Step 4: Run the builder tests to verify they pass**

Run: `./run-tests.sh tests/unit/test_tag_search_sql.py`
Expected: PASS (11 tests). If `ruff format` rewraps the long f-strings, keep its output; the SQL text is unchanged by formatting.

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff check app tests && uv run ruff format app/services/tag_search.py tests/unit/test_tag_search_sql.py && uv run mypy app`
Expected: clean.

```bash
git add app/services/tag_search.py tests/unit/test_tag_search_sql.py
git commit -m "feat(search): pure SQL builder for Postgres tag search"
```

---

### Task 4: `search_tags` and the acceptance corpus

**Files:**
- Modify: `app/services/tag_search.py` (append)
- Test: `tests/integration/test_tag_search_corpus.py`

**Interfaces:**
- Produces: `async def search_tags(db: AsyncSession, query: str, *, limit: int = 20, offset: int = 0, type_filter: int | None = None, exclude_aliases: bool = False, sort: list[str] | None = None) -> TagSearchResult`. Task 5's route calls exactly this.

- [ ] **Step 1: Write the failing corpus test**

The fixture seeds every title the appendix queries need plus the decoys that made Meilisearch and the POC disagree. Titles and counts come from the dev database; the test asserts by title, never by id.

```python
"""Acceptance corpus for Postgres tag search (spec appendix).

Seeds the titles behind every query in the design doc's appendix, with the
decoys that broke earlier ranking attempts, and asserts the top hit by title.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.models.tag import Tags
from app.models.tag_external_link import TagExternalLinks
from app.services.tag_search import search_tags

# (title, type, usage_count, desc, alias_of_title, urls)
CORPUS = [
    ("cherry blossoms", TagType.THEME, 21476, "", None, []),
    ("sakura", TagType.THEME, 0, "", "cherry blossoms", []),
    ("Sakura", TagType.CHARACTER, 797, "", None, []),
    ("Sakura (source)", TagType.SOURCE, 3, "", None, []),
    ("Kinomoto Sakura", TagType.CHARACTER, 4594, "Cardcaptor Sakura", None, []),
    ("Kinoshita Sakura", TagType.CHARACTER, 41, "", None, []),
    ("Kinomoto Sakuya", TagType.CHARACTER, 10, "", None, []),
    ("Kinomoto Touya", TagType.CHARACTER, 94, "", None, []),
    ("Kinomoto Nadeshiko", TagType.CHARACTER, 27, "", None, []),
    ("Sakurai Yukino", TagType.CHARACTER, 28, "", None, []),
    ("Sakura Yukino", TagType.CHARACTER, 18, "", None, []),
    ("Sakurakinoshita Ashita", TagType.CHARACTER, 14, "", None, []),
    ("Kinom", TagType.ARTIST, 8, "", None, []),
    ("Kinoto", TagType.ARTIST, 2, "", None, []),
    ("Kino", TagType.CHARACTER, 504, "", None, []),
    ("cat", TagType.THEME, 18416, "", None, []),
    ("neko", TagType.THEME, 0, "", "cat", []),
    ("Neko", TagType.CHARACTER, 10, "", None, []),
    ("neko mimi", TagType.THEME, 5000, "", None, []),
    ("Nekopara", TagType.SOURCE, 100, "", None, []),
    ("maid", TagType.THEME, 23093, "", None, []),
    ("school uniform", TagType.THEME, 154188, "", None, []),
    ("school bag", TagType.THEME, 10191, "", None, []),
    ("school swimsuit", TagType.THEME, 6409, "", None, []),
    ("School Days", TagType.SOURCE, 900, "", None, []),
    ("swimsuit", TagType.THEME, 8829, "", None, []),
    ("mizugi", TagType.THEME, 0, "", "swimsuit", []),
    ("bikini", TagType.THEME, 41801, "", None, []),
    ("two-piece swimsuit", TagType.THEME, 0, "", "bikini", []),
    ("Swim Swim", TagType.CHARACTER, 23, "", None, []),
    ("long hair", TagType.THEME, 729274, "", None, []),
    ("short hair", TagType.THEME, 474227, "", None, []),
    ("blonde hair", TagType.THEME, 279469, "", None, []),
    ("Somali Longhaired", TagType.CHARACTER, 1, "", None, []),
    ("Shiroko Terror", TagType.CHARACTER, 42, "Blue Archive; long hair variant", None, []),
    ("long", TagType.ARTIST, 1, "", None, []),
    ("long kimono", TagType.THEME, 40260, "", None, []),
    ("Sa", TagType.ARTIST, 1, "", None, []),
    ("Sa.", TagType.ARTIST, 9, "", None, []),
    ("sad", TagType.THEME, 5000, "", None, []),
    ("Saber", TagType.CHARACTER, 20000, "", None, []),
    ("co", TagType.ARTIST, 4, "", None, []),
    ("cosplay", TagType.THEME, 3000, "", None, []),
    ("Hatsune Miku", TagType.CHARACTER, 35622, "", None, []),
    ("Hatsune Mikuo", TagType.CHARACTER, 513, "", None, []),
    ("Hatsune", TagType.ARTIST, 10, "", None, []),
    ("thigh highs", TagType.THEME, 162274, "", None, []),
    ("boots", TagType.THEME, 90000, "Includes thigh-high boots", None, []),
    ("C.C.", TagType.CHARACTER, 2788, "", None, []),
    ("C++", TagType.THEME, 5, "", None, []),
    ("C++ 11", TagType.THEME, 1, "", None, []),
    ("[C]", TagType.SOURCE, 138, "", None, []),
    ("100", TagType.ARTIST, 7, "", None, []),
    ("100% Perfect Girl", TagType.SOURCE, 3, "", None, []),
    ("100% Orange Juice!", TagType.SOURCE, 1, "", None, []),
    ("Ichigo 100%", TagType.SOURCE, 47, "", None, []),
    ("Mob Psycho 100", TagType.SOURCE, 26, "", None, []),
    ("Pixiv 103175", TagType.ARTIST, 1, "", None, []),
    ("Deep-Blue Series", TagType.SOURCE, 50, "", None, []),
    ("Deep Blue Sky & Pure White Wings", TagType.SOURCE, 92, "", None, []),
    ("Yano (yano_0o0)", TagType.ARTIST, 1, "", None, []),
    ("Yano Mitsuki", TagType.CHARACTER, 30, "", None, []),
    ("The Forgotten Field", TagType.SOURCE, 1, "", None, []),
    ("The Familiar of Zero", TagType.SOURCE, 607, "", None, []),
    ("The Fly", TagType.CHARACTER, 4, "", None, []),
    ("The Forest of Drizzling Rain", TagType.SOURCE, 437, "", None, []),
    ("THE", TagType.ARTIST, 6, "", None, []),
    ("TKennshou", TagType.ARTIST, 1, "", None, ["https://www.pixiv.net/users/21412050"]),
    ("Pixiv 21412050", TagType.ARTIST, 0, "", "TKennshou", []),
    ("Tsunekichi", TagType.ARTIST, 14, "", None, []),
    ("Pokémon", TagType.SOURCE, 16529, "", None, []),
    ("Pokémon Adventures", TagType.SOURCE, 3279, "", None, []),
    ("Pokemon Heroes", TagType.SOURCE, 12, "", None, []),
    ("Märchen von Friedhof", TagType.CHARACTER, 1047, "", None, []),
    ("Marchen Girl Runs", TagType.SOURCE, 3, "", None, []),
    ("MarchAB", TagType.ARTIST, 41, "", None, []),
    ("Maruchan", TagType.ARTIST, 68, "", None, []),
    ("EB十", TagType.ARTIST, 152, "", None, []),
    ("magical girl", TagType.THEME, 25205, "", None, []),
    ("Mahou no Stage Fancy Lala", TagType.SOURCE, 100, "magical girl idol anime", None, []),
    ("Louise Françoise le Blanc de la Vallière", TagType.CHARACTER, 447, "", None, []),
    ("Francoise", TagType.CHARACTER, 3, "", None, []),
    ("Claire Francois", TagType.CHARACTER, 50, "", None, []),
]

# (query, expected top-1 title) — spec appendix, asserted by title.
EXPECTED_TOP1 = [
    ("sakura", "sakura"),
    ("cat", "cat"),
    ("maid", "maid"),
    ("neko", "neko"),
    ("school", "school uniform"),
    ("swimsuit", "swimsuit"),
    ("long hair", "long hair"),
    ("sakura kinomoto", "Kinomoto Sakura"),
    ("kinomoto sakura", "Kinomoto Sakura"),
    ("sa", "Sa"),
    ("co", "co"),
    ("long", "long"),
    ("kinomto", "Kinomoto Sakura"),
    ("sakrua kinomto", "Kinomoto Sakura"),
    ("hatsune mikuu", "Hatsune Miku"),
    ("swimsiut", "swimsuit"),
    ("thig", "thigh highs"),
    ("C.C.", "C.C."),
    ("C++", "C++"),
    ("100%", "100% Perfect Girl"),
    ("deep-blue", "Deep-Blue Series"),
    ("yano_0o0", "Yano (yano_0o0)"),
    ("The Forgotten", "The Forgotten Field"),
    ("The F", "The Familiar of Zero"),
    ("21412050", "Pixiv 21412050"),
    ("pixiv.net/users/21412050", "Pixiv 21412050"),
    ("tsunekichi", "Tsunekichi"),
    ("pokemon", "Pokémon"),
    ("Pokémon", "Pokémon"),
    ("marchen", "Märchen von Friedhof"),
    ("EB十", "EB十"),
    ("magical girl", "magical girl"),
    ("Louise Francoise", "Louise Françoise le Blanc de la Vallière"),
    ("marchan", "Märchen von Friedhof"),
    ("kinomoto", "Kinomoto Sakura"),
    ("hatsune", "Hatsune"),
    ("sakura kino", "Kinomoto Sakura"),
    ("kinomt", "Kinomoto Sakura"),
]


async def seed_corpus(db_session: AsyncSession) -> dict[str, Tags]:
    """Insert the corpus; aliases are wired by title after the first insert."""
    by_title: dict[str, Tags] = {}
    for title, tag_type, usage, desc, _alias, _urls in CORPUS:
        tag = Tags(title=title, type=tag_type, usage_count=usage, desc=desc or None)
        db_session.add(tag)
        by_title[title] = tag
    await db_session.flush()
    for title, _tag_type, _usage, _desc, alias_of_title, urls in CORPUS:
        if alias_of_title:
            by_title[title].alias_of = by_title[alias_of_title].tag_id
        for url in urls:
            db_session.add(TagExternalLinks(tag_id=by_title[title].tag_id, url=url))
    await db_session.commit()
    return by_title


def titles_for(by_title: dict[str, Tags], result) -> list[str]:
    id_to_title = {tag.tag_id: title for title, tag in by_title.items()}
    return [id_to_title[tag_id] for tag_id in result.tag_ids]


@pytest.mark.integration
class TestSearchCorpus:
    @pytest.mark.parametrize(("query", "expected"), EXPECTED_TOP1)
    async def test_top_hit(self, db_session: AsyncSession, query: str, expected: str):
        by_title = await seed_corpus(db_session)
        result = await search_tags(db_session, query, limit=10)
        titles = titles_for(by_title, result)
        assert titles, f"{query!r} returned nothing"
        assert titles[0] == expected, f"{query!r} -> {titles}"

    async def test_numeric_query_matches_digits_literally_only(self, db_session: AsyncSession):
        by_title = await seed_corpus(db_session)
        found = titles_for(by_title, await search_tags(db_session, "21412050"))
        assert "TKennshou" in found  # via its external URL
        off_by_one = await search_tags(db_session, "21412051")
        assert off_by_one.tag_ids == []
        assert off_by_one.total == 0

    async def test_description_only_match_is_found(self, db_session: AsyncSession):
        by_title = await seed_corpus(db_session)
        found = titles_for(by_title, await search_tags(db_session, "magical girl"))
        assert "Mahou no Stage Fancy Lala" in found

    async def test_alias_rows_rank_by_parent_count(self, db_session: AsyncSession):
        by_title = await seed_corpus(db_session)
        # "neko" is an alias of "cat" (18,416); "Neko" the character has 10.
        found = titles_for(by_title, await search_tags(db_session, "neko"))
        assert found[:2] == ["neko", "Neko"]

    async def test_type_filter_and_exclude_aliases(self, db_session: AsyncSession):
        by_title = await seed_corpus(db_session)
        by_type = {tag.tag_id: tag.type for tag in by_title.values()}
        only_characters = await search_tags(db_session, "sakura", type_filter=TagType.CHARACTER)
        assert only_characters.tag_ids
        assert all(by_type[tag_id] == TagType.CHARACTER for tag_id in only_characters.tag_ids)
        no_aliases = titles_for(by_title, await search_tags(db_session, "sakura", exclude_aliases=True))
        assert "sakura" not in no_aliases  # the alias row
        assert "Sakura" in no_aliases

    async def test_total_is_exact_and_pagination_is_stable(self, db_session: AsyncSession):
        by_title = await seed_corpus(db_session)
        first = await search_tags(db_session, "sakura", limit=3, offset=0)
        second = await search_tags(db_session, "sakura", limit=3, offset=3)
        assert first.total == second.total
        assert not set(first.tag_ids) & set(second.tag_ids)
        everything = await search_tags(db_session, "sakura", limit=100)
        assert everything.total == len(everything.tag_ids)

    async def test_explicit_sort_overrides_relevance(self, db_session: AsyncSession):
        by_title = await seed_corpus(db_session)
        found = titles_for(by_title, await search_tags(db_session, "sakura", sort=["title:asc"], limit=100))
        # Relevance would lead with the exact "sakura"; a title sort leads with "Kinomoto ...".
        assert found[0] == "Kinomoto Nadeshiko"
        assert "sakura" in found

    async def test_empty_query_lists_all_by_effective_usage(self, db_session: AsyncSession):
        await seed_corpus(db_session)
        result = await search_tags(db_session, "", limit=2)
        assert result.total >= len(CORPUS)
        assert len(result.tag_ids) == 2
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./run-tests.sh tests/integration/test_tag_search_corpus.py`
Expected: FAIL — `ImportError: cannot import name 'search_tags'`.

- [ ] **Step 3: Append `search_tags` to `app/services/tag_search.py`**

```python
async def search_tags(
    db: AsyncSession,
    query: str,
    *,
    limit: int = 20,
    offset: int = 0,
    type_filter: int | None = None,
    exclude_aliases: bool = False,
    sort: list[str] | None = None,
) -> TagSearchResult:
    """Run one tag search and return the page's ids in rank order with an exact total.

    Args:
        db: Session; the SET LOCALs bind to its current transaction.
        query: Search text. Empty lists every tag.
        limit: Page size.
        offset: Rows to skip.
        type_filter: TagType constant, or None for all types.
        exclude_aliases: Drop rows whose alias_of is set.
        sort: Meilisearch-style spec such as ["title:asc"]; None means relevance.
    """
    statements = build_search(
        query,
        limit=limit,
        offset=offset,
        type_filter=type_filter,
        exclude_aliases=exclude_aliases,
        sort=sort,
    )
    # One command per execute: asyncpg rejects multi-statement strings. SET
    # LOCAL lasts for this transaction only, so a pooled connection never
    # carries it to the next request.
    await db.execute(text("SET LOCAL pg_trgm.word_similarity_threshold = 0.5"))
    await db.execute(text("SET LOCAL jit = off"))
    rows = await db.execute(text(statements.ids_sql), statements.params)
    tag_ids = [row.tag_id for row in rows.all()]
    total = int((await db.execute(text(statements.count_sql), statements.params)).scalar_one())
    logger.debug("tag_search", query=query, hits=len(tag_ids), total=total)
    return TagSearchResult(tag_ids=tag_ids, total=total)
```

- [ ] **Step 4: Run the corpus test to verify it passes**

Run: `./run-tests.sh tests/integration/test_tag_search_corpus.py`
Expected: PASS (38 top-hit cases + 7 behaviour tests). A failing top-hit case names the query and the returned order; compare against the ranking rules in the spec before touching the SQL.

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff check app tests && uv run ruff format app/services/tag_search.py tests/integration/test_tag_search_corpus.py && uv run mypy app`

```bash
git add app/services/tag_search.py tests/integration/test_tag_search_corpus.py
git commit -m "feat(search): search_tags runner and the acceptance corpus"
```

---

### Task 5: Route swap and route tests

**Files:**
- Modify: `app/api/v1/search.py`
- Modify: `app/main.py` (the `get_search_service` override, around lines 159–194)
- Rewrite: `tests/api/v1/test_search.py`

**Interfaces:**
- Consumes: `search_tags` from Task 4.
- Produces: `GET /api/v1/search` with an unchanged contract. `get_search_service` no longer exists.

- [ ] **Step 1: Rewrite the route tests against the real engine**

Replace the whole file. The identity-layer cases keep their intent; the seeds replace the mock. Note `search_client`/`client_with_search` fixtures are gone: the plain `client` fixture already wires `db_session` into the app.

```python
"""Tests for the search API endpoint (/api/v1/search), Postgres engine."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.models.tag import Tags
from app.models.tag_external_link import TagExternalLinks


async def _seed(db_session: AsyncSession, *tags: Tags) -> list[Tags]:
    db_session.add_all(tags)
    await db_session.commit()
    for tag in tags:
        await db_session.refresh(tag)
    return list(tags)


async def _seed_identity_owner(
    db_session: AsyncSession, *, title: str = "TKennshou", alias_titles: tuple[str, ...] = ()
) -> tuple[Tags, list[Tags]]:
    """An artist owning pixiv id 21412050, plus optional alias rows pointing at it."""
    (owner,) = await _seed(db_session, Tags(title=title, type=TagType.ARTIST, usage_count=1))
    db_session.add(
        TagExternalLinks(
            tag_id=owner.tag_id,
            url="https://www.pixiv.net/users/21412050",
            site="pixiv",
            external_id="21412050",
        )
    )
    aliases = [Tags(title=t, type=TagType.ARTIST, alias_of=owner.tag_id) for t in alias_titles]
    db_session.add_all(aliases)
    await db_session.commit()
    for alias in aliases:
        await db_session.refresh(alias)
    return owner, aliases


@pytest.mark.api
class TestSearchEndpoint:
    async def test_search_returns_matching_tags_exact_first(self, client: AsyncClient, db_session: AsyncSession):
        await _seed(
            db_session,
            Tags(title="Sakura Kinomoto", desc="Card Captor", type=TagType.CHARACTER, usage_count=50),
            Tags(title="Sakura", desc="Cherry blossom", type=TagType.CHARACTER, usage_count=5),
        )
        response = await client.get("/api/v1/search", params={"q": "sakura"})
        assert response.status_code == 200
        data = response.json()
        assert data["query"] == "sakura"
        assert data["entity"] == "tags"
        assert data["total"] == 2
        assert data["limit"] == 20
        assert data["offset"] == 0
        assert [hit["title"] for hit in data["hits"]] == ["Sakura", "Sakura Kinomoto"]

    @pytest.mark.parametrize("params", [{}, {"q": ""}])
    async def test_missing_or_empty_query_lists_all(self, client: AsyncClient, db_session: AsyncSession, params):
        await _seed(
            db_session,
            Tags(title="popular", type=TagType.THEME, usage_count=100),
            Tags(title="rare", type=TagType.THEME, usage_count=1),
        )
        response = await client.get("/api/v1/search", params=params)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert [hit["title"] for hit in data["hits"]] == ["popular", "rare"]

    async def test_search_with_type_filter(self, client: AsyncClient, db_session: AsyncSession):
        await _seed(
            db_session,
            Tags(title="Naruto", type=TagType.SOURCE),
            Tags(title="Naruto Uzumaki", type=TagType.CHARACTER),
        )
        response = await client.get("/api/v1/search", params={"q": "naruto", "type": TagType.SOURCE})
        assert response.status_code == 200
        assert [hit["title"] for hit in response.json()["hits"]] == ["Naruto"]

    async def test_search_with_exclude_aliases(self, client: AsyncClient, db_session: AsyncSession):
        (canonical,) = await _seed(db_session, Tags(title="test canonical", type=TagType.THEME))
        await _seed(db_session, Tags(title="test alias", type=TagType.THEME, alias_of=canonical.tag_id))
        response = await client.get("/api/v1/search", params={"q": "test", "exclude_aliases": True})
        assert response.status_code == 200
        assert [hit["title"] for hit in response.json()["hits"]] == ["test canonical"]

    async def test_search_no_results(self, client: AsyncClient):
        response = await client.get("/api/v1/search", params={"q": "nonexistent"})
        assert response.status_code == 200
        assert response.json() == {
            "query": "nonexistent", "entity": "tags", "hits": [], "total": 0, "limit": 20, "offset": 0,
        }

    async def test_search_with_limit_and_offset(self, client: AsyncClient, db_session: AsyncSession):
        await _seed(db_session, *[Tags(title=f"test {i:02d}", type=TagType.THEME, usage_count=100 - i) for i in range(8)])
        response = await client.get("/api/v1/search", params={"q": "test", "limit": 3, "offset": 5})
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 8
        assert data["limit"] == 3
        assert data["offset"] == 5
        assert [hit["title"] for hit in data["hits"]] == ["test 05", "test 06", "test 07"]

    async def test_search_rejects_offset_over_max(self, client: AsyncClient):
        response = await client.get("/api/v1/search", params={"q": "test", "offset": 500_001})
        assert response.status_code == 422

    async def test_search_honours_sort(self, client: AsyncClient, db_session: AsyncSession):
        await _seed(
            db_session,
            Tags(title="sakura b", type=TagType.THEME, usage_count=1),
            Tags(title="sakura a", type=TagType.THEME, usage_count=100),
        )
        response = await client.get("/api/v1/search", params={"q": "sakura", "sort_by": "title", "sort_order": "ASC"})
        assert [hit["title"] for hit in response.json()["hits"]] == ["sakura a", "sakura b"]

    async def test_search_rejects_invalid_sort_by(self, client: AsyncClient):
        response = await client.get("/api/v1/search", params={"q": "sakura", "sort_by": "not_a_field"})
        assert response.status_code == 422

    async def test_search_populates_alias_of_name_for_alias_hits(self, client: AsyncClient, db_session: AsyncSession):
        (canonical,) = await _seed(db_session, Tags(title="feline", type=TagType.THEME, usage_count=42))
        await _seed(db_session, Tags(title="cat", type=TagType.THEME, alias_of=canonical.tag_id))
        response = await client.get("/api/v1/search", params={"q": "cat"})
        hit = response.json()["hits"][0]
        assert hit["title"] == "cat"
        assert hit["is_alias"] is True
        assert hit["alias_of_name"] == "feline"
        assert hit["alias_of_usage_count"] == 42


@pytest.mark.api
class TestExactIdentityLayer:
    async def test_bare_id_prepends_owner_with_matched_identity(self, client: AsyncClient, db_session: AsyncSession):
        owner, _ = await _seed_identity_owner(db_session)
        response = await client.get("/api/v1/search", params={"q": "21412050"})
        assert response.status_code == 200
        data = response.json()
        assert data["hits"][0]["tag_id"] == owner.tag_id
        assert data["hits"][0]["matched_identity"] == "Pixiv 21412050"
        # The owner is also a text hit through its URL, so the total is not inflated.
        assert data["total"] == 1
        assert [hit["tag_id"] for hit in data["hits"]].count(owner.tag_id) == 1

    async def test_text_query_has_no_matched_identity(self, client: AsyncClient, db_session: AsyncSession):
        await _seed_identity_owner(db_session, title="Tsunekichi")
        response = await client.get("/api/v1/search", params={"q": "tsunekichi"})
        assert response.json()["hits"][0]["matched_identity"] is None

    async def test_alias_rows_of_the_owner_are_dropped(self, client: AsyncClient, db_session: AsyncSession):
        owner, aliases = await _seed_identity_owner(db_session, alias_titles=("Pixiv 21412050",))
        response = await client.get("/api/v1/search", params={"q": "21412050"})
        data = response.json()
        ids = [hit["tag_id"] for hit in data["hits"]]
        assert ids == [owner.tag_id]
        assert data["total"] == 1

    async def test_prepend_respects_limit(self, client: AsyncClient, db_session: AsyncSession):
        owner, _ = await _seed_identity_owner(db_session)
        await _seed(db_session, Tags(title="21412050 fan club", type=TagType.THEME, usage_count=999))
        response = await client.get("/api/v1/search", params={"q": "21412050", "limit": 1})
        data = response.json()
        assert [hit["tag_id"] for hit in data["hits"]] == [owner.tag_id]
        assert data["hits"][0]["matched_identity"] == "Pixiv 21412050"

    async def test_later_page_does_not_inject_and_total_is_consistent(self, client: AsyncClient, db_session: AsyncSession):
        owner, _ = await _seed_identity_owner(db_session)
        await _seed(db_session, Tags(title="21412050 fan club", type=TagType.THEME, usage_count=999))
        first = (await client.get("/api/v1/search", params={"q": "21412050", "limit": 1, "offset": 0})).json()
        second = (await client.get("/api/v1/search", params={"q": "21412050", "limit": 1, "offset": 1})).json()
        # The identity layer resolves "already found" against page 1 for both requests,
        # so the totals agree; their exact value is the engine's count plus the
        # injected owner when it sits beyond page 1.
        assert first["total"] == second["total"]
        assert all(hit["matched_identity"] is None for hit in second["hits"])

    async def test_mismatched_type_filter_suppresses_injection(self, client: AsyncClient, db_session: AsyncSession):
        await _seed_identity_owner(db_session)
        response = await client.get("/api/v1/search", params={"q": "21412050", "type": TagType.THEME})
        assert response.json()["hits"] == []

    async def test_matching_type_filter_keeps_injection(self, client: AsyncClient, db_session: AsyncSession):
        owner, _ = await _seed_identity_owner(db_session)
        response = await client.get("/api/v1/search", params={"q": "21412050", "type": TagType.ARTIST})
        assert response.json()["hits"][0]["tag_id"] == owner.tag_id

    async def test_exclude_aliases_blocks_an_alias_owner(self, client: AsyncClient, db_session: AsyncSession):
        # An owner that is itself an alias must not be injected when aliases are excluded.
        (canonical,) = await _seed(db_session, Tags(title="Canonical Artist", type=TagType.ARTIST))
        (alias_owner,) = await _seed(db_session, Tags(title="Legacy", type=TagType.ARTIST, alias_of=canonical.tag_id))
        db_session.add(TagExternalLinks(tag_id=alias_owner.tag_id, url="https://www.pixiv.net/users/21412050", site="pixiv", external_id="21412050"))
        await db_session.commit()
        response = await client.get("/api/v1/search", params={"q": "21412050", "exclude_aliases": True})
        assert all(hit["tag_id"] != alias_owner.tag_id for hit in response.json()["hits"])
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./run-tests.sh tests/api/v1/test_search.py`
Expected: FAIL — the route still calls Meilisearch through `get_search_service`, so every test 503s.

- [ ] **Step 3: Swap the engine in `app/api/v1/search.py`**

Delete `get_search_service` and the `SearchService`/`TagSearchResult` import; import `search_tags` from `app.services.tag_search`; drop the `search_service` parameter and the try/except that raised 503. The two engine calls become `search_tags(db, ...)`. The file's search function and helper end up as:

```python
"""Search endpoint, answered from Postgres (app/services/tag_search.py)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.api.dependencies import SortOrder, TagSortBy
from app.core.database import get_db
from app.core.logging import get_logger
from app.models.tag import Tags
from app.schemas.search import SearchResponse, TagSearchHit
from app.services.artist_identity import parse_identity_query, resolve_identity, site_display_name
from app.services.tag_search import search_tags

logger = get_logger(__name__)

router = APIRouter(prefix="/search", tags=["search"])


async def _identity_hit_already_on_first_page(
    db: AsyncSession,
    tag_id: int,
    q: str,
    *,
    limit: int,
    type_id: int | None,
    exclude_aliases: bool,
    sort: list[str] | None,
) -> bool:
    """Whether the first page (offset=0) for this query already has tag_id.

    Used only when the caller requested a later page, so the exact-identity
    layer's "already found" check has a stable, page-independent answer —
    see the comment in `search()`.
    """
    first_page = await search_tags(
        db, q, limit=limit, offset=0, type_filter=type_id, exclude_aliases=exclude_aliases, sort=sort
    )
    return tag_id in first_page.tag_ids


@router.get("", response_model=SearchResponse)
async def search(
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[
        str,
        Query(max_length=200, description="Search query (empty = list all, filter+sort still apply)"),
    ] = "",
    type_id: Annotated[int | None, Query(description="Filter by tag type", alias="type")] = None,
    exclude_aliases: Annotated[bool, Query(description="Exclude alias tags")] = False,
    limit: Annotated[int, Query(ge=1, le=100, description="Max results")] = 20,
    offset: Annotated[int, Query(ge=0, le=500_000, description="Results to skip")] = 0,
    sort_by: Annotated[
        TagSortBy | None,
        Query(description="Sort field (omit for relevance ranking)"),
    ] = None,
    sort_order: Annotated[SortOrder, Query(description="Sort order")] = "DESC",
) -> SearchResponse:
    """Search tags. Relevance order unless sort_by is given, in which case the sort dominates."""
    sort = [f"{sort_by}:{sort_order.lower()}"] if sort_by is not None else None

    result = await search_tags(
        db, q, limit=limit, offset=offset, type_filter=type_id, exclude_aliases=exclude_aliases, sort=sort
    )
```

Everything from `# Fetch full tag records` onward stays as it is, except the one later call:

```python
            else:
                already_found = await _identity_hit_already_on_first_page(
                    db,
                    exact_tag.tag_id,  # type: ignore[arg-type]
                    q,
                    limit=limit,
                    type_id=type_id,
                    exclude_aliases=exclude_aliases,
                    sort=sort,
                )
```

Also update the comment above the identity block that says "Runs after the Meilisearch call above, so Meilisearch downtime still 503s" to: `# Runs after the engine call above; the owning tag must still satisfy the request's own filters.`

- [ ] **Step 4: Drop the override in `app/main.py`**

In the lifespan, delete `from app.api.v1.search import get_search_service`, the line `app.dependency_overrides[get_search_service] = lambda: search_service`, and `app.dependency_overrides.pop(get_search_service, None)`. Keep the Meilisearch client, `set_search_service`, and `configure_tags_index` for now: the write-path sync still runs until Part B.

- [ ] **Step 5: Run the route tests and the full suite**

Run: `./run-tests.sh tests/api/v1/test_search.py`
Expected: PASS (19 tests).

Run: `./run-tests.sh`
Expected: PASS. `tests/unit/test_search_service.py`, `tests/unit/test_search_sync.py`, and `tests/integration/test_search_integration.py` still pass because the service module is untouched.

- [ ] **Step 6: Lint and commit**

Run: `uv run ruff check app tests && uv run ruff format --check app tests && uv run mypy app`

```bash
git add app/api/v1/search.py app/main.py tests/api/v1/test_search.py
git commit -m "feat(search): answer GET /search from Postgres"
```

---

### Task 6: Verify on dev and open PR 1

**Files:** none new.

- [ ] **Step 1: Confirm the dev API answers from Postgres**

The dev container hot-reloads `app/`. With the branch checked out:

```bash
for q in kinomto "sakura%20kino" 21412051 "the%20f"; do
  curl -s "http://localhost:8000/api/v1/search?q=$q&limit=3" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['total'], [h['title'] for h in d['hits']])"
done
```
Expected first titles: Kinomoto Sakura; Kinomoto Sakura; (empty, total 0); The Familiar of Zero.

- [ ] **Step 2: Run the frontend typeahead and tag list e2e specs against dev**

From `/home/dtaylor/shuu/shuushuu-frontend` (its `AGENTS.md` applies there): `npm run test:e2e -- tests/e2e/tags-list.spec.ts tests/e2e/tag-autocomplete-races.spec.ts tests/e2e/search-tag-builder.spec.ts`. Note the frontend memory: passing a spec path may run the whole suite; if so, run the whole suite and report every failure.
Expected: green, with no frontend change.

- [ ] **Step 3: Regenerate the frontend API types and confirm nothing changed**

In the frontend repo: `./scripts/generate-api-types.sh && git diff --stat src/lib/types/api-generated.ts`.
Expected: an empty diff, or only description strings. If only descriptions changed, leave them uncommitted; they ship with the next frontend change.

- [ ] **Step 4: Open PR 1**

```bash
git push -u origin feat/postgres-tag-search
gh pr create --title "feat(search): answer GET /search from Postgres with pg_trgm" --body "$(cat <<'BODY'
## Summary
- Migration 0004: pg_trgm, unaccent, fuzzystrmatch; `public.fold_search_text`; three folded trigram indexes and one prefix btree
- `app/services/tag_search.py`: gates, pure SQL builder, `search_tags`
- `GET /api/v1/search` keeps its contract and answers from Postgres; the Meilisearch dependency and 503 path are gone from the route
- Acceptance corpus from the design doc appendix runs as an integration test

Design: docs/plans/2026-Q3/2026-09-11-postgres-tag-search-design.md
Plan: docs/plans/2026-Q3/2026-09-11-postgres-tag-search-impl.md

## Deploy
`make prod-migrate` creates the extensions (the DB role must own the database), the function, and the indexes concurrently. Verify with `/api/v1/search?q=kinomto`.

## Test plan
- [ ] `./run-tests.sh` green
- [ ] frontend e2e tag list + typeahead specs green against dev

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01MNoxNBcTrbQyAD55cVmfCh
BODY
)"
```

---

# Part B — Delete Meilisearch (PR 2)

Start after PR 1 merges: `git checkout main && git pull && git checkout -b chore/delete-meilisearch`.

### Task 7: Remove the write-path sync calls and the service module

**Files:**
- Delete: `app/services/search.py`, `tests/unit/test_search_service.py`, `tests/unit/test_search_sync.py`, `tests/integration/test_search_integration.py`
- Modify: `app/api/v1/tags.py`, `app/api/v1/images.py`, `app/services/batch_tag.py`, `app/services/ml_suggestion_review.py`, `scripts/repair_alias_chains.py`, `app/schemas/search.py`, `tests/api/v1/test_ml_tag_suggestions.py`

- [ ] **Step 1: Write the failing guard test**

Create `tests/unit/test_no_meilisearch.py`:

```python
"""Meilisearch is gone: nothing in app/ or scripts/ may import or mention it."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_no_meilisearch_references_outside_history():
    result = subprocess.run(
        ["git", "grep", "-il", "meili", "--", "app", "scripts", "tests", "docker-compose.yml",
         "docker-compose.prod.yml", "pyproject.toml", ".env.example"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    offenders = [line for line in result.stdout.splitlines() if line != "tests/unit/test_no_meilisearch.py"]
    assert offenders == [], f"Meilisearch references remain: {offenders}"
```

Run: `./run-tests.sh tests/unit/test_no_meilisearch.py`
Expected: FAIL listing every file in the Part B inventory. This test stays red until Task 11 and is the checklist.

- [ ] **Step 2: Delete the service and its tests**

```bash
git rm app/services/search.py tests/unit/test_search_service.py tests/unit/test_search_sync.py tests/integration/test_search_integration.py
```

- [ ] **Step 3: Remove every sync call**

Each site follows one of two shapes. Remove the import line and:

`app/api/v1/tags.py`
- Line ~79: delete `from app.services.search import sync_tag_delete_to_search, sync_tag_to_search`.
- `create_tag` (~1592): delete `await sync_tag_to_search(new_tag, db=db)`.
- `update_tag` (~2009–2030): delete `await sync_tag_to_search(tag, db=db)`, then the whole block from the comment `# If alias was set and tag_links migrated, also sync the canonical tag` through `await sync_tag_to_search(cascaded_alias, db=db)`. The `reparented_alias_ids` list and `type_cascaded_alias_ids` set then have no reader: delete their definitions and the appends/updates that fed them (the audit-log `db.add(TagAuditLog(...))` calls in those loops stay).
- `delete_tag` (~2062): delete `await sync_tag_delete_to_search(tag_id)`.
- `add_external_link` (~2158–2162): delete the comment beginning `# Refresh tag: commit expires its attributes, and sync_tag_to_search reads`, the `await db.refresh(tag)` it explains, and `await sync_tag_to_search(tag, db=db)`. `tag` is not read after that point.
- `delete_external_link` (~2253): delete `await sync_tag_to_search(tag, db=db)`.

`app/api/v1/images.py`
- Line ~138: delete the import.
- Two sites (~2640 and ~2727): delete the comment `# Re-fetch tag to get updated usage_count (maintained by DB trigger)`, the `tag_result = await db.execute(select(Tags)...)`/`updated_tag = ...` pair, and the `if updated_tag: await sync_tag_to_search(...)` block. If `select`/`Tags` become unused imports, ruff will say so; remove them.

`app/services/batch_tag.py`
- Line 19: delete the import.
- Two sites (~176–178, ~333–335): delete the `tag_results = await db.execute(select(Tags).where(...))` statement whose only consumer is the `await sync_tags_to_search(...)` line, and that line.

`app/services/ml_suggestion_review.py`
- Line 29: delete the import.
- Two sites (~313–315, ~415–417): same shape as batch_tag. Update the two docstrings (~147, ~336) that mention `sync_tags_to_search` to describe only the commit.

`scripts/repair_alias_chains.py` (~158–185): delete the block from `print(f"\nRe-syncing {len(affected_ids)} affected tag(s) to Meilisearch...")` through the matching `except Exception:` handler's `print(...)`, leaving the `if not affected_ids: ... return` guard above it. Remove the now-unused `settings` import if ruff flags it.

`app/schemas/search.py`: change the `TagSearchHit` docstring to `"""A tag search hit, extending the standard tag response."""` and the comment to `# Set when this hit was resolved by the exact artist-identity layer rather than (or in addition to) the text search.`

`tests/api/v1/test_ml_tag_suggestions.py` (~1437–1480): the test whose docstring says "syncs the affected tag to Meilisearch" patches `app.services.ml_suggestion_review.sync_tags_to_search`. Delete the patch context manager and any assertion on the mock; keep the TagLink assertions; rename the docstring to `"""Approving creates a TagLink."""`.

- [ ] **Step 4: Run the affected tests**

Run: `./run-tests.sh tests/api/v1/test_tags.py tests/api/v1/test_images.py tests/api/v1/test_ml_tag_suggestions.py tests/unit`
Expected: PASS except `test_no_meilisearch.py` (still red: main.py, worker.py, config, scripts, compose remain).

Run: `uv run ruff check app scripts tests && uv run mypy app`
Expected: clean (fix any unused imports ruff reports).

- [ ] **Step 5: Commit**

```bash
git add -u app scripts tests
git add tests/unit/test_no_meilisearch.py
git commit -m "chore(search): remove the Meilisearch service and its write-path sync"
```

---

### Task 8: Lifespan, worker, config, env

**Files:**
- Modify: `app/main.py` (~159–194), `app/tasks/worker.py` (~146–206), `app/config.py` (~63–65), `.env.example` (~30–34), `tests/test_worker.py`

- [ ] **Step 1: Remove the Meilisearch block from `app/main.py`**

Delete from the comment `# Initialize Meilisearch search service` through the `except` that logs `meilisearch_unavailable`, and in the shutdown section delete `set_search_service(None)` and the `if meilisearch_client: await meilisearch_client.aclose()` lines. Delete the `meilisearch_client = None` assignment.

- [ ] **Step 2: Remove it from `app/tasks/worker.py`**

Delete the `MeilisearchClient` and `app.services.search` imports, the comment block starting `# Initialize Meilisearch search service so worker tasks calling`, the `client: MeilisearchClient | None = None` through the `except` that logs `meilisearch_unavailable`, and in the shutdown function the `from app.services.search import set_search_service`, `set_search_service(None)`, and the `client = ctx.get("meilisearch_client")` cleanup.

- [ ] **Step 3: Remove the settings and env lines**

`app/config.py`: delete the `# Meilisearch` comment and the `MEILISEARCH_URL` / `MEILISEARCH_API_KEY` fields.
`.env.example`: delete lines 30–34 (`# Meilisearch` through `MEILISEARCH_API_KEY=dev_master_key`).

- [ ] **Step 4: Fix `tests/test_worker.py`**

Delete the `_no_meilisearch` fixture (lines ~12–30) and remove `_no_meilisearch` from the parameter lists of the three tests that use it (~65, ~73, ~86).

- [ ] **Step 5: Run the worker tests and the app import**

Run: `./run-tests.sh tests/test_worker.py && uv run python -c "import app.main"`
Expected: PASS; the import prints nothing.

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/tasks/worker.py app/config.py .env.example tests/test_worker.py
git commit -m "chore(search): drop Meilisearch from the lifespan, worker, and settings"
```

---

### Task 9: Scripts — reindex and restore

**Files:**
- Delete: `scripts/reindex_search.py`, `tests/unit/test_db_utils_reindex.py`
- Modify: `scripts/db_utils.py` (~334–372), `scripts/restore_prod_db.py`

- [ ] **Step 1: Delete the reindex script and its test**

```bash
git rm scripts/reindex_search.py tests/unit/test_db_utils_reindex.py
```

- [ ] **Step 2: Remove `reindex_search` from `scripts/db_utils.py`**

Delete the whole `async def reindex_search(project_root: Path) -> bool:` function. Update the module docstring line `The alembic and search-reindex steps run in the api service...` to mention only alembic.

- [ ] **Step 3: Remove the restore step and flag from `scripts/restore_prod_db.py`**

Delete: the `reindex_search` import; the `skip_reindex` parameter and its docstring; step 8 in the printed plan; the `search_reindexed` block (~175–186); the summary lines (~200–205); the `--skip-reindex` argparse option and its pass-through (~231, ~254, ~270). Renumber the printed steps so they stay contiguous.

- [ ] **Step 4: Verify the script still parses and the unit tests pass**

Run: `uv run python scripts/restore_prod_db.py --help && ./run-tests.sh tests/unit`
Expected: help text without `--skip-reindex`; unit tests PASS except `test_no_meilisearch.py`.

- [ ] **Step 5: Commit**

```bash
git add -u scripts tests/unit
git commit -m "chore(scripts): restore no longer reindexes a search engine"
```

---

### Task 10: Compose stacks

**Files:**
- Modify: `docker-compose.yml`, `docker-compose.prod.yml`

- [ ] **Step 1: Remove the service from `docker-compose.yml`**

Delete the `# Meilisearch (search engine)` service block (lines ~65–80), the two `- MEILISEARCH_URL=http://meilisearch:7700` environment lines (~101, ~189), the two `meilisearch:` entries under `depends_on` with their `condition:` lines (~122, ~210), and the `meilisearch_data:` volume (~287).

- [ ] **Step 2: Remove the override from `docker-compose.prod.yml`**

Delete the `meilisearch:` service override with its `ports: !override []` and the comment above it (~55–58); edit the header comment that lists provided services (line 2) to drop `meilisearch`, and the two comments mentioning `postgres/meilisearch entries still merge` (~211, ~255) to say `postgres entries`.

- [ ] **Step 3: Validate both files**

Run: `docker compose config -q && docker compose -f docker-compose.yml -f docker-compose.prod.yml config -q`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml docker-compose.prod.yml
git commit -m "chore(compose): remove the meilisearch service and volume"
```

---

### Task 11: Dependency and the guard test

**Files:**
- Modify: `pyproject.toml`, `uv.lock`

- [ ] **Step 1: Remove the dependency**

Delete `"meilisearch-python-sdk>=3.0.0",` from `pyproject.toml` and run `uv lock`.

- [ ] **Step 2: Run the guard test and the full suite**

Run: `./run-tests.sh tests/unit/test_no_meilisearch.py`
Expected: PASS — no offenders.

Run: `./run-tests.sh && uv run ruff check app scripts tests && uv run ruff format --check app scripts tests && uv run mypy app`
Expected: all green.

- [ ] **Step 3: Rebuild the dev images so the lockfile hash matches**

Run: `docker compose build api arq-worker && docker compose up -d api arq-worker && docker compose ps`
Expected: both services healthy; `docker logs --tail 20 shuushuu-api` shows no `meilisearch` lines.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): drop meilisearch-python-sdk"
```

---

### Task 12: Docs, ADR, PR 2

**Files:**
- Create: `docs/adr/0015-tag-search-runs-in-postgres.md`
- Modify: `docs/ml-tag-suggestions.md` (line ~160)

- [ ] **Step 1: Write the ADR**

```markdown
# Tag search runs in Postgres

Tag search (`GET /api/v1/search`, the tag list page, and every typeahead)
is answered by Postgres with `pg_trgm`, `unaccent`, and `fuzzystrmatch`
since September 2026 (PRs for `docs/plans/2026-Q3/2026-09-11-postgres-tag-search-design.md`).
Meilisearch was removed from the codebase and the compose stacks. There is
no search index to sync or rebuild: a tag is searchable the moment its row
commits.

## Considered Options

- **Keeping Meilisearch and adding derived fields** for the tag list
  filters was rejected. Fields derived from other rows (child count, alias
  count, character↔source links) needed a re-sync at every neighbouring
  write plus a scheduled reindex, and dev already carried orphaned documents
  from out-of-band deletions.
- **Splitting the tag list page across engines** (Meilisearch for text,
  Postgres for structural filters) was rejected: filters and pagination do
  not compose across two result sets.
- **Postgres for everything** won on a measured proof of concept: 34 of 38
  problem queries returned the same top hit as Meilisearch, the other four
  favoured Postgres, and latency was lower on 33 of 38.

## Consequences

- Every future search filter is a `WHERE` clause; nothing to index.
- `total` is exact. Pagination counts no longer drift.
- Relevance is the tier/typo/position/usage order in
  `app/services/tag_search.py`. Changing it means changing SQL, not index
  settings; the acceptance corpus in
  `tests/integration/test_tag_search_corpus.py` guards it.
- `public.fold_search_text` is `IMMUTABLE` and sits inside four indexes;
  changing the unaccent dictionary means rebuilding them.
```

- [ ] **Step 2: Update `docs/ml-tag-suggestions.md`**

Delete the line `- Meilisearch unavailability is handled gracefully (warning, not crash).`

- [ ] **Step 3: Confirm the repo is clean of references**

Run: `git grep -il meili -- . ':!docs/adr' ':!docs/plans' ':!uv.lock'`
Expected: no output (`tests/unit/test_no_meilisearch.py` matches only its own name in the exclusion list; if it appears, that is fine).

- [ ] **Step 4: Commit and open PR 2**

```bash
git add docs/adr/0015-tag-search-runs-in-postgres.md docs/ml-tag-suggestions.md
git commit -m "docs: ADR-0015 tag search runs in Postgres"
git push -u origin chore/delete-meilisearch
gh pr create --title "chore: delete Meilisearch" --body "$(cat <<'BODY'
## Summary
- Removes the Meilisearch service module, 23 write-path sync calls, the reindex script and restore step, the worker and lifespan init, settings, compose service and volume, and the SDK dependency
- Adds a guard test that fails if a Meilisearch reference returns
- ADR-0015 records the decision

Follows PR 1 (Postgres search). Design: docs/plans/2026-Q3/2026-09-11-postgres-tag-search-design.md

## Deploy
After this deploys: `docker compose stop meilisearch && docker compose rm -f meilisearch && docker volume rm <project>_meilisearch_data` on production, and drop `MEILI_MASTER_KEY` / `MEILISEARCH_API_KEY` from the host `.env`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01MNoxNBcTrbQyAD55cVmfCh
BODY
)"
```

---

## After both PRs merge

- Run the `plan-close-out` skill for this plan: record the outcome, regenerate the plans index.
- Update memory: the `tag-list-filters-investigation` note's POC section is history once PR 1 ships.
- Next design: structural filters on the tag list page (both repos).
