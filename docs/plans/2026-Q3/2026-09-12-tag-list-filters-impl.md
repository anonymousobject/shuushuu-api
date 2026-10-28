# Tag List Filters (API) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `GET /api/v1/search` accepts the tag-list filters (aliases tri-state, minimum usage, added-date range, has alias, is child, has children, source linked) as `WHERE` predicates on the existing Postgres search, in both the page of ids and the exact total.

**Architecture:** A frozen `SearchFilters` dataclass replaces the two loose filter kwargs of `build_search`; one pure `_filter_clauses` function turns it into bound SQL predicates that every query path (empty, prefix-only, general) appends to its `WHERE`. The route validates the parameters, builds the dataclass, and skips the artist-identity prepend whenever a structural filter is set.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy `text()` + asyncpg, Postgres 18; pytest via `./run-tests.sh`; `uv run ruff`, `uv run mypy`.

**Spec:** `<shuushuu-frontend-repo>/docs/plans/2026-Q3/2026-09-12-tag-list-filters-design.md` — the one design for both repos; read its "API contract" and "Predicates" sections first. This plan covers the API half only; the frontend half is `<shuushuu-frontend-repo>/docs/plans/2026-Q3/2026-09-12-tag-list-filters-impl.md`.

## Global Constraints

- Work in `/home/dtaylor/shuu/shuushuu-api`; read `AGENTS.md` first. Never `git add -A`; stage files by name.
- Branch `feat/tag-list-filters` off `chore/delete-meilisearch` (head 355fb7e7). Do NOT open a PR at the end: the branch is third in a stack and its PR opens against main after #393 and the deletion PR merge. Push the branch.
- Parameter names and values, verbatim from the spec: `aliases` ∈ `hide|only|all` (API default `all`); `min_usage` integer ≥ 0 on the effective count `COALESCE(parent.usage_count, t.usage_count)`; `added_from`/`added_to` as `YYYY-MM-DD`, inclusive, UTC; `has_alias`, `is_child`, `has_children`, `source_linked` ∈ `yes|no`; `source_linked` requires `type` 2 (source) or 4 (character), else 422; `added_from` after `added_to` is 422.
- `exclude_aliases` is removed outright (no shim). Only the frontend tags page sends it, and its frontend plan switches to `aliases`.
- `date_added` is `timestamp without time zone` holding UTC. Bind NAIVE `datetime` values (no tzinfo) or asyncpg raises. Bounds: `>=` start-date midnight, `<` the day after the end date at midnight.
- Every user value is a bind parameter; never interpolate; never `:param::type`.
- The identity prepend layer runs only when no filter beyond `type` and `aliases` is set.
- Tests: `./run-tests.sh <path>` for a file, `./run-tests.sh` for the suite. Lint: `uv run ruff check app tests && uv run ruff format --check app tests && uv run mypy app`. Test output must be pristine apart from the suite's 80 pre-existing warnings in unrelated files.
- Every commit ends with:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01MNoxNBcTrbQyAD55cVmfCh
  ```

## File structure

- `app/services/tag_search.py` — gains `Aliases`, `YesNo`, `SearchFilters`, `_filter_clauses`; `build_search` and `search_tags` take `filters: SearchFilters` instead of `type_filter`/`exclude_aliases`.
- `app/api/v1/search.py` — new query parameters, validation, `SearchFilters` construction, identity-layer gate.
- `tests/unit/test_tag_search_sql.py` — builder tests for every filter clause.
- `tests/integration/test_tag_search_corpus.py` — fixture gains hierarchy and source links; filter tests with exact totals.
- `tests/api/v1/test_search.py` — parameter round-trips, 422s, identity gate.

---

### Task 1: `SearchFilters` and the predicates in the builder

**Files:**
- Modify: `app/services/tag_search.py`
- Test: `tests/unit/test_tag_search_sql.py`

**Interfaces:**
- Produces:
  ```python
  Aliases = Literal["hide", "only", "all"]
  YesNo = Literal["yes", "no"]

  @dataclass(frozen=True)
  class SearchFilters:
      type_filter: int | None = None
      aliases: Aliases = "all"
      min_usage: int | None = None
      added_from: date | None = None
      added_to: date | None = None
      has_alias: YesNo | None = None
      is_child: YesNo | None = None
      has_children: YesNo | None = None
      source_linked: YesNo | None = None

      @property
      def is_structural(self) -> bool: ...   # True when any field beyond type_filter/aliases is set

  def build_search(query, *, limit, offset, filters: SearchFilters, sort) -> SearchStatements
  async def search_tags(db, query, *, limit=20, offset=0, filters: SearchFilters = SearchFilters(), sort=None) -> TagSearchResult
  ```
  `build_search` raises `ValueError` when `source_linked` is set and `type_filter` is not 2 or 4.

- [ ] **Step 1: Rewrite the unit test helper and add the filter tests**

Replace the `_build` helper at the top of `tests/unit/test_tag_search_sql.py` and update its two existing callers that pass `type_filter`/`exclude_aliases` (`test_filters_apply_to_ids_and_count` passes `filters=SearchFilters(type_filter=4, aliases="hide")`):

```python
from datetime import date, datetime

import pytest

from app.services.tag_search import SearchFilters, build_search


def _build(query: str, filters: SearchFilters | None = None, **overrides):
    kwargs = {"limit": 10, "offset": 0, "filters": filters or SearchFilters(), "sort": None}
    kwargs.update(overrides)
    return build_search(query, **kwargs)
```

Append to `TestBuildSearch`:

```python
    @pytest.mark.parametrize("query", ["", "sa", "sakura"])
    def test_aliases_tri_state_reaches_ids_and_count(self, query: str):
        hide = _build(query, SearchFilters(aliases="hide"))
        only = _build(query, SearchFilters(aliases="only"))
        every = _build(query, SearchFilters(aliases="all"))
        for sql in (hide.ids_sql, hide.count_sql):
            assert "t.alias_of IS NULL" in sql
        for sql in (only.ids_sql, only.count_sql):
            assert "t.alias_of IS NOT NULL" in sql
        for sql in (every.ids_sql, every.count_sql):
            assert "alias_of IS" not in sql

    @pytest.mark.parametrize("query", ["", "sa", "sakura"])
    def test_min_usage_uses_effective_count_and_joins_parent_in_count(self, query: str):
        st = _build(query, SearchFilters(min_usage=50))
        for sql in (st.ids_sql, st.count_sql):
            assert "COALESCE(parent.usage_count, t.usage_count) >= :min_usage" in sql
        assert "LEFT JOIN tags parent ON parent.tag_id = t.alias_of" in st.count_sql
        assert st.params["min_usage"] == 50

    def test_count_skips_parent_join_without_min_usage(self):
        st = _build("sakura", SearchFilters(aliases="hide"))
        assert "parent" not in st.count_sql

    def test_added_range_binds_naive_utc_midnights(self):
        st = _build("", SearchFilters(added_from=date(2020, 6, 1), added_to=date(2020, 6, 30)))
        assert "t.date_added >= :added_from_ts" in st.ids_sql
        assert "t.date_added < :added_to_next_ts" in st.count_sql
        assert st.params["added_from_ts"] == datetime(2020, 6, 1)
        assert st.params["added_to_next_ts"] == datetime(2020, 7, 1)
        assert st.params["added_from_ts"].tzinfo is None

    def test_added_from_alone(self):
        st = _build("", SearchFilters(added_from=date(2021, 1, 1)))
        assert "added_from_ts" in st.ids_sql
        assert "added_to_next_ts" not in st.ids_sql

    def test_has_alias_yes_and_no(self):
        yes = _build("", SearchFilters(has_alias="yes"))
        no = _build("", SearchFilters(has_alias="no"))
        assert "EXISTS (SELECT 1 FROM tags a WHERE a.alias_of = t.tag_id)" in yes.ids_sql
        assert "NOT EXISTS (SELECT 1 FROM tags a WHERE a.alias_of = t.tag_id)" in no.count_sql
        assert "NOT EXISTS" not in yes.ids_sql

    def test_is_child_yes_and_no(self):
        assert "t.inheritedfrom_id IS NOT NULL" in _build("", SearchFilters(is_child="yes")).count_sql
        assert "t.inheritedfrom_id IS NULL" in _build("", SearchFilters(is_child="no")).ids_sql

    def test_has_children_yes_and_no(self):
        yes = _build("", SearchFilters(has_children="yes"))
        no = _build("", SearchFilters(has_children="no"))
        assert "EXISTS (SELECT 1 FROM tags c WHERE c.inheritedfrom_id = t.tag_id)" in yes.ids_sql
        assert "NOT EXISTS (SELECT 1 FROM tags c WHERE c.inheritedfrom_id = t.tag_id)" in no.ids_sql

    def test_source_linked_picks_the_column_by_type(self):
        character = _build("", SearchFilters(type_filter=4, source_linked="yes"))
        source = _build("", SearchFilters(type_filter=2, source_linked="no"))
        assert (
            "EXISTS (SELECT 1 FROM character_source_links l WHERE l.character_tag_id = t.tag_id)"
            in character.ids_sql
        )
        assert (
            "NOT EXISTS (SELECT 1 FROM character_source_links l WHERE l.source_tag_id = t.tag_id)"
            in source.count_sql
        )

    @pytest.mark.parametrize("type_filter", [None, 1, 3])
    def test_source_linked_without_character_or_source_type_is_rejected(self, type_filter):
        with pytest.raises(ValueError, match="source_linked"):
            _build("", SearchFilters(type_filter=type_filter, source_linked="yes"))

    def test_is_structural_flag(self):
        assert not SearchFilters().is_structural
        assert not SearchFilters(type_filter=4, aliases="hide").is_structural
        assert SearchFilters(min_usage=0).is_structural
        assert SearchFilters(added_to=date(2020, 1, 1)).is_structural
        assert SearchFilters(has_children="no").is_structural

    def test_filters_combine_with_query_and_type(self):
        st = _build("sakura", SearchFilters(type_filter=4, aliases="hide", min_usage=10, has_alias="no"))
        for sql in (st.ids_sql, st.count_sql):
            assert "t.type = :type_filter" in sql
            assert "t.alias_of IS NULL" in sql
            assert ">= :min_usage" in sql
            assert "NOT EXISTS (SELECT 1 FROM tags a WHERE a.alias_of = t.tag_id)" in sql
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./run-tests.sh tests/unit/test_tag_search_sql.py`
Expected: FAIL — `ImportError: cannot import name 'SearchFilters'`.

- [ ] **Step 3: Add the filter model and clauses to `app/services/tag_search.py`**

Add to the imports: `from datetime import date, datetime, time, timedelta` and `from typing import Any, Literal`. After `SearchGates` add:

```python
Aliases = Literal["hide", "only", "all"]
YesNo = Literal["yes", "no"]

# Tag types that carry character_source_links rows, and the column each side
# of the link uses (app.config.TagType values; imported here as literals to
# keep this module free of app.config).
_SOURCE_LINK_COLUMN = {4: "character_tag_id", 2: "source_tag_id"}


@dataclass(frozen=True)
class SearchFilters:
    """Structural filters applied to both the page of ids and the exact count.

    `type_filter` and `aliases` narrow the corpus the way the old kwargs did;
    the rest are the tag-list filters. Field names match the API parameters.
    """

    type_filter: int | None = None
    aliases: Aliases = "all"
    min_usage: int | None = None
    added_from: date | None = None
    added_to: date | None = None
    has_alias: YesNo | None = None
    is_child: YesNo | None = None
    has_children: YesNo | None = None
    source_linked: YesNo | None = None

    @property
    def is_structural(self) -> bool:
        """True when any filter beyond type and aliases is set."""
        return any(
            value is not None
            for value in (
                self.min_usage,
                self.added_from,
                self.added_to,
                self.has_alias,
                self.is_child,
                self.has_children,
                self.source_linked,
            )
        )


def _exists(subquery: str, value: YesNo) -> str:
    prefix = "" if value == "yes" else "NOT "
    return f"{prefix}EXISTS ({subquery})"


def _filter_clauses(filters: SearchFilters, params: dict[str, Any]) -> list[str]:
    """WHERE predicates for `filters`, binding their values into `params`."""
    clauses: list[str] = []
    if filters.type_filter is not None:
        clauses.append("t.type = :type_filter")
        params["type_filter"] = filters.type_filter
    if filters.aliases == "hide":
        clauses.append("t.alias_of IS NULL")
    elif filters.aliases == "only":
        clauses.append("t.alias_of IS NOT NULL")
    if filters.min_usage is not None:
        clauses.append(f"{_EFFECTIVE_USAGE} >= :min_usage")
        params["min_usage"] = filters.min_usage
    # date_added is a naive UTC timestamp: bind naive midnights, half-open at
    # the far end so the column stays bare for a future index.
    if filters.added_from is not None:
        clauses.append("t.date_added >= :added_from_ts")
        params["added_from_ts"] = datetime.combine(filters.added_from, time.min)
    if filters.added_to is not None:
        clauses.append("t.date_added < :added_to_next_ts")
        params["added_to_next_ts"] = datetime.combine(filters.added_to + timedelta(days=1), time.min)
    if filters.has_alias is not None:
        clauses.append(_exists("SELECT 1 FROM tags a WHERE a.alias_of = t.tag_id", filters.has_alias))
    if filters.is_child is not None:
        clauses.append(
            "t.inheritedfrom_id IS NOT NULL" if filters.is_child == "yes" else "t.inheritedfrom_id IS NULL"
        )
    if filters.has_children is not None:
        clauses.append(
            _exists("SELECT 1 FROM tags c WHERE c.inheritedfrom_id = t.tag_id", filters.has_children)
        )
    if filters.source_linked is not None:
        column = _SOURCE_LINK_COLUMN.get(filters.type_filter or 0)
        if column is None:
            raise ValueError("source_linked requires type_filter 2 (source) or 4 (character)")
        clauses.append(
            _exists(
                f"SELECT 1 FROM character_source_links l WHERE l.{column} = t.tag_id",
                filters.source_linked,
            )
        )
    return clauses
```

`_EFFECTIVE_USAGE` is defined later in the file today; move the `_EFFECTIVE_USAGE`/`_SORT_COLUMNS` block above `SearchFilters` so the name exists when `_filter_clauses` is defined (functions resolve globals at call time, so order does not matter for correctness, but keep related definitions together).

Then change `build_search`:

```python
def build_search(
    query: str,
    *,
    limit: int,
    offset: int,
    filters: SearchFilters,
    sort: list[str] | None,
) -> SearchStatements:
    """Build the ids and count statements for one search. Pure."""
    query = query.strip()
    tokens = query.split()
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    filters_sql = _filter_clauses(filters, params)
    # The count needs the parent join only when the effective count is filtered.
    count_from = _BASE_FROM if filters.min_usage is not None else "FROM tags t"
    order = _order_clause(sort)
```

and replace every later use: the old `filters` list becomes `filters_sql` (three `where = ...` sites, the prefix path's `filters.insert(0, ...)`, and the numeric-token `filters.append(...)` loop); the three count statements become `f"SELECT count(*) {count_from} {where}"` (empty and prefix paths) and `f"WITH {candidates} SELECT count(*) {count_from} JOIN candidates c ON c.tag_id = t.tag_id {where}"` (general path). Delete the old `if type_filter is not None:` / `if exclude_aliases:` block.

Change `search_tags`:

```python
async def search_tags(
    db: AsyncSession,
    query: str,
    *,
    limit: int = 20,
    offset: int = 0,
    filters: SearchFilters = SearchFilters(),
    sort: list[str] | None = None,
) -> TagSearchResult:
```

with the docstring's `type_filter`/`exclude_aliases` lines replaced by `filters: Type, alias, and structural filters; see SearchFilters.` and the `build_search(...)` call passing `filters=filters`.

- [ ] **Step 4: Run the unit tests and the other callers**

Run: `./run-tests.sh tests/unit/test_tag_search_sql.py`
Expected: PASS (13 existing + 18 new cases).

The corpus and route tests still pass `type_filter=`/`exclude_aliases=`; they are updated in Tasks 2 and 3, so expect them to fail with `TypeError` until then. Do not run them now.

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff check app tests/unit && uv run ruff format app/services/tag_search.py tests/unit/test_tag_search_sql.py && uv run mypy app`

```bash
git add app/services/tag_search.py tests/unit/test_tag_search_sql.py
git commit -m "feat(search): SearchFilters with alias, usage, date, hierarchy, and source-link predicates"
```

---

### Task 2: Route parameters, validation, identity gate

**Files:**
- Modify: `app/api/v1/search.py`
- Modify: `tests/api/v1/test_search.py`

**Interfaces:**
- Consumes: `SearchFilters`, `Aliases`, `YesNo`, `search_tags(db, q, *, limit, offset, filters, sort)` from Task 1.
- Produces: the query parameters listed in Global Constraints on `GET /api/v1/search`; `exclude_aliases` gone.

- [ ] **Step 1: Update the route tests**

In `tests/api/v1/test_search.py`, change `test_search_with_exclude_aliases` to send `{"q": "test", "aliases": "hide"}` and rename it `test_search_with_aliases_hide`; change `test_exclude_aliases_blocks_an_alias_owner` to send `"aliases": "hide"`. Then add a new class:

```python
@pytest.mark.api
class TestTagListFilters:
    async def _seed_family(self, db_session: AsyncSession) -> dict[str, Tags]:
        (canonical,) = await _seed(db_session, Tags(title="feline", type=TagType.THEME, usage_count=500))
        (alias,) = await _seed(db_session, Tags(title="feline alias", type=TagType.THEME, alias_of=canonical.tag_id))
        (parent,) = await _seed(db_session, Tags(title="feline parent", type=TagType.THEME, usage_count=40))
        (child,) = await _seed(
            db_session, Tags(title="feline child", type=TagType.THEME, usage_count=3, inheritedfrom_id=parent.tag_id)
        )
        (character,) = await _seed(db_session, Tags(title="feline girl", type=TagType.CHARACTER, usage_count=20))
        (loner,) = await _seed(db_session, Tags(title="feline loner", type=TagType.CHARACTER, usage_count=7))
        (source,) = await _seed(db_session, Tags(title="feline show", type=TagType.SOURCE, usage_count=90))
        db_session.add(CharacterSourceLinks(character_tag_id=character.tag_id, source_tag_id=source.tag_id))
        await db_session.commit()
        return {"canonical": canonical, "alias": alias, "parent": parent, "child": child,
                "character": character, "loner": loner, "source": source}

    async def _titles(self, client: AsyncClient, **params) -> tuple[list[str], int]:
        response = await client.get("/api/v1/search", params={"q": "feline", **params})
        assert response.status_code == 200, response.text
        data = response.json()
        return [hit["title"] for hit in data["hits"]], data["total"]

    async def test_aliases_only_and_all(self, client: AsyncClient, db_session: AsyncSession):
        await self._seed_family(db_session)
        only, only_total = await self._titles(client, aliases="only")
        assert only == ["feline alias"] and only_total == 1
        every, every_total = await self._titles(client, aliases="all")
        assert "feline alias" in every and every_total == 7

    async def test_min_usage_counts_the_alias_by_its_parent(self, client: AsyncClient, db_session: AsyncSession):
        await self._seed_family(db_session)
        titles, total = await self._titles(client, aliases="all", min_usage=100)
        assert set(titles) == {"feline", "feline alias"} and total == 2

    async def test_added_range(self, client: AsyncClient, db_session: AsyncSession):
        family = await self._seed_family(db_session)
        family["loner"].date_added = datetime(2020, 6, 15, 12, 0, tzinfo=UTC)
        await db_session.commit()
        titles, total = await self._titles(client, added_from="2020-01-01", added_to="2020-12-31")
        assert titles == ["feline loner"] and total == 1
        titles, total = await self._titles(client, added_to="2019-12-31")
        assert titles == [] and total == 0

    async def test_added_from_after_added_to_is_422(self, client: AsyncClient):
        response = await client.get("/api/v1/search", params={"q": "", "added_from": "2021-01-01", "added_to": "2020-01-01"})
        assert response.status_code == 422

    async def test_has_alias_is_child_has_children(self, client: AsyncClient, db_session: AsyncSession):
        await self._seed_family(db_session)
        assert (await self._titles(client, has_alias="yes"))[0] == ["feline"]
        assert (await self._titles(client, is_child="yes"))[0] == ["feline child"]
        assert (await self._titles(client, has_children="yes"))[0] == ["feline parent"]
        titles, _ = await self._titles(client, has_children="no", is_child="no", has_alias="no")
        assert "feline parent" not in titles and "feline child" not in titles and "feline" not in titles

    async def test_source_linked_by_type(self, client: AsyncClient, db_session: AsyncSession):
        await self._seed_family(db_session)
        assert (await self._titles(client, type=TagType.CHARACTER, source_linked="yes"))[0] == ["feline girl"]
        assert (await self._titles(client, type=TagType.CHARACTER, source_linked="no"))[0] == ["feline loner"]
        assert (await self._titles(client, type=TagType.SOURCE, source_linked="yes"))[0] == ["feline show"]

    @pytest.mark.parametrize("params", [{}, {"type": TagType.THEME}, {"type": TagType.ARTIST}])
    async def test_source_linked_needs_character_or_source_type(self, client: AsyncClient, params):
        response = await client.get("/api/v1/search", params={"q": "", "source_linked": "yes", **params})
        assert response.status_code == 422

    @pytest.mark.parametrize("params", [{"aliases": "sometimes"}, {"min_usage": -1}, {"has_alias": "maybe"}, {"added_from": "2020-13-01"}])
    async def test_invalid_values_are_422(self, client: AsyncClient, params):
        response = await client.get("/api/v1/search", params={"q": "", **params})
        assert response.status_code == 422

    async def test_identity_prepend_skips_under_a_structural_filter(self, client: AsyncClient, db_session: AsyncSession):
        owner, _ = await _seed_identity_owner(db_session)
        response = await client.get("/api/v1/search", params={"q": "21412050", "min_usage": 0})
        data = response.json()
        assert all(hit["matched_identity"] is None for hit in data["hits"])
        assert owner.tag_id in [hit["tag_id"] for hit in data["hits"]]  # still a plain text hit via its URL
```

Add the imports the class needs at the top of the file: `from datetime import UTC, datetime` and `from app.models.character_source_link import CharacterSourceLinks`.

- [ ] **Step 2: Run it to verify it fails**

Run: `./run-tests.sh tests/api/v1/test_search.py`
Expected: FAIL — the filter tests get 200s that ignore the unknown parameters (wrong titles/totals) and the 422 tests get 200; the existing tests fail with `TypeError` from the route's old `search_tags(..., type_filter=..., exclude_aliases=...)` call.

- [ ] **Step 3: Rewrite the route parameters and the engine call**

In `app/api/v1/search.py` add imports `from datetime import date` and `from app.services.tag_search import Aliases, SearchFilters, YesNo, search_tags`, and `from fastapi import HTTPException` if not present. Replace the `exclude_aliases` parameter with:

```python
    aliases: Annotated[
        Aliases, Query(description="hide alias rows, show only alias rows, or all (default)")
    ] = "all",
    min_usage: Annotated[
        int | None, Query(ge=0, description="Minimum effective usage count (the parent's for aliases)")
    ] = None,
    added_from: Annotated[date | None, Query(description="Added on or after this UTC date")] = None,
    added_to: Annotated[date | None, Query(description="Added on or before this UTC date")] = None,
    has_alias: Annotated[YesNo | None, Query(description="Has at least one alias pointing at it")] = None,
    is_child: Annotated[YesNo | None, Query(description="Has a parent tag")] = None,
    has_children: Annotated[YesNo | None, Query(description="Is the parent of at least one tag")] = None,
    source_linked: Annotated[
        YesNo | None,
        Query(description="Characters: has a source link. Sources: has a linked character. Needs type 2 or 4."),
    ] = None,
```

Then, before the engine call:

```python
    if added_from is not None and added_to is not None and added_from > added_to:
        raise HTTPException(status_code=422, detail="added_from must not be after added_to")
    if source_linked is not None and type_id not in (TagType.SOURCE, TagType.CHARACTER):
        raise HTTPException(
            status_code=422, detail="source_linked requires type 2 (source) or 4 (character)"
        )
    filters = SearchFilters(
        type_filter=type_id,
        aliases=aliases,
        min_usage=min_usage,
        added_from=added_from,
        added_to=added_to,
        has_alias=has_alias,
        is_child=is_child,
        has_children=has_children,
        source_linked=source_linked,
    )
    sort = [f"{sort_by}:{sort_order.lower()}"] if sort_by is not None else None
    result = await search_tags(db, q, limit=limit, offset=offset, filters=filters, sort=sort)
```

(`TagType` comes from `app.config`; import it.) Change `_identity_hit_already_on_first_page` to take `filters: SearchFilters` instead of `type_id`/`exclude_aliases` and pass `filters=filters` to `search_tags`; update its one call site. Change the identity guard to:

```python
    identity = parse_identity_query(q) if q and not filters.is_structural else None
    if identity is not None:
        exact_tag = await resolve_identity(db, identity)
        if (
            exact_tag is not None
            and (type_id is None or exact_tag.type == type_id)
            and _passes_alias_filter(exact_tag, aliases)
        ):
```

with the helper, placed above the route:

```python
def _passes_alias_filter(tag: Tags, aliases: Aliases) -> bool:
    """Whether the identity owner may be injected under the request's alias filter."""
    if aliases == "hide":
        return tag.alias_of is None
    if aliases == "only":
        return tag.alias_of is not None
    return True
```

Update the comment above the identity block: it now says the layer runs only when no structural filter is set, and why (identity queries are bare ids typed into typeaheads).

- [ ] **Step 4: Run the route tests**

Run: `./run-tests.sh tests/api/v1/test_search.py`
Expected: PASS (20 existing, two renamed, + 10 new).

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff check app tests && uv run ruff format app/api/v1/search.py tests/api/v1/test_search.py && uv run mypy app`

```bash
git add app/api/v1/search.py tests/api/v1/test_search.py
git commit -m "feat(search): tag-list filter parameters on GET /search; aliases replaces exclude_aliases"
```

---

### Task 3: Corpus fixture hierarchy and links; integration tests

**Files:**
- Modify: `tests/integration/test_tag_search_corpus.py`

**Interfaces:**
- Consumes: `search_tags(db, q, *, filters=SearchFilters(...))` from Task 1.

- [ ] **Step 1: Extend the fixture**

Add to `CORPUS` (keep the tuple shape):

```python
    ("sailor uniform", TagType.THEME, 30000, "", None, []),
    ("blazer", TagType.THEME, 12000, "", None, []),
    ("Cardcaptor Sakura", TagType.SOURCE, 6000, "", None, []),
```

Add two module-level lists after `CORPUS`:

```python
# (parent_title, child_title): inheritedfrom_id wiring
HIERARCHY = [
    ("school uniform", "sailor uniform"),
    ("school uniform", "blazer"),
    ("Pokémon", "Pokémon Adventures"),
]
# (character_title, source_title): character_source_links rows
SOURCE_LINKS = [
    ("Kinomoto Sakura", "Cardcaptor Sakura"),
    ("Kinomoto Touya", "Cardcaptor Sakura"),
]
```

In `seed_corpus`, after the alias/url loop and before the commit:

```python
    for parent_title, child_title in HIERARCHY:
        by_title[child_title].inheritedfrom_id = by_title[parent_title].tag_id
    for character_title, source_title in SOURCE_LINKS:
        db_session.add(
            CharacterSourceLinks(
                character_tag_id=by_title[character_title].tag_id,
                source_tag_id=by_title[source_title].tag_id,
            )
        )
```

with `from app.models.character_source_link import CharacterSourceLinks` and `from app.services.tag_search import SearchFilters, search_tags` at the top.

Update the two existing tests that use the old kwargs: `test_type_filter_and_exclude_aliases` becomes `filters=SearchFilters(type_filter=TagType.CHARACTER)` and `filters=SearchFilters(aliases="hide")`. "Cardcaptor Sakura" now sorts first under `title:asc` for "sakura", so `test_explicit_sort_overrides_relevance` changes its assertion to `assert found[0] == "Cardcaptor Sakura"` and its comment to name that title.

- [ ] **Step 2: Run the corpus file to verify the top-1 corpus still holds**

Run: `./run-tests.sh tests/integration/test_tag_search_corpus.py`
Expected: PASS. Every `EXPECTED_TOP1` row still holds: "Cardcaptor Sakura" is a tier-2 word match for "sakura" and ranks below the exact alias row; the two new theme children do not contain "school".

- [ ] **Step 3: Add the filter integration tests**

Append to `TestSearchCorpus`:

```python
    async def test_min_usage_filters_on_effective_count(self, db_session: AsyncSession):
        by_title = await seed_corpus(db_session)
        result = await search_tags(db_session, "sakura", filters=SearchFilters(aliases="all", min_usage=20000), limit=50)
        titles = titles_for(by_title, result)
        assert "sakura" in titles  # alias of cherry blossoms (21,476)
        assert "Sakura" not in titles  # the character has 797
        assert result.total == len(result.tag_ids)

    async def test_aliases_only(self, db_session: AsyncSession):
        by_title = await seed_corpus(db_session)
        result = await search_tags(db_session, "", filters=SearchFilters(aliases="only"), limit=100)
        titles = titles_for(by_title, result)
        assert set(titles) == {t for t, _, _, _, alias, _ in CORPUS if alias}
        assert result.total == len(titles)

    async def test_added_range_uses_the_utc_date(self, db_session: AsyncSession):
        by_title = await seed_corpus(db_session)
        by_title["blazer"].date_added = datetime(2020, 6, 30, 23, 59, tzinfo=UTC)
        await db_session.commit()
        inside = await search_tags(db_session, "", filters=SearchFilters(added_from=date(2020, 6, 30), added_to=date(2020, 6, 30)))
        assert titles_for(by_title, inside) == ["blazer"] and inside.total == 1
        outside = await search_tags(db_session, "", filters=SearchFilters(added_to=date(2020, 6, 29)))
        assert outside.tag_ids == [] and outside.total == 0

    async def test_hierarchy_filters(self, db_session: AsyncSession):
        by_title = await seed_corpus(db_session)
        parents = titles_for(by_title, await search_tags(db_session, "", filters=SearchFilters(has_children="yes"), limit=100))
        assert set(parents) == {"school uniform", "Pokémon"}
        children = titles_for(by_title, await search_tags(db_session, "", filters=SearchFilters(is_child="yes"), limit=100))
        assert set(children) == {"sailor uniform", "blazer", "Pokémon Adventures"}
        no_parent = await search_tags(db_session, "", filters=SearchFilters(is_child="no"), limit=100)
        assert no_parent.total == len(CORPUS) - 3

    async def test_has_alias(self, db_session: AsyncSession):
        by_title = await seed_corpus(db_session)
        result = await search_tags(db_session, "", filters=SearchFilters(has_alias="yes"), limit=100)
        assert set(titles_for(by_title, result)) == {"cherry blossoms", "cat", "swimsuit", "bikini", "TKennshou"}

    async def test_source_linked_both_sides(self, db_session: AsyncSession):
        by_title = await seed_corpus(db_session)
        linked_chars = await search_tags(db_session, "", filters=SearchFilters(type_filter=TagType.CHARACTER, source_linked="yes"), limit=100)
        assert set(titles_for(by_title, linked_chars)) == {"Kinomoto Sakura", "Kinomoto Touya"}
        unlinked_chars = await search_tags(db_session, "kinomoto", filters=SearchFilters(type_filter=TagType.CHARACTER, source_linked="no"), limit=100)
        assert "Kinomoto Sakura" not in titles_for(by_title, unlinked_chars)
        assert "Kinomoto Nadeshiko" in titles_for(by_title, unlinked_chars)
        linked_sources = await search_tags(db_session, "", filters=SearchFilters(type_filter=TagType.SOURCE, source_linked="yes"), limit=100)
        assert titles_for(by_title, linked_sources) == ["Cardcaptor Sakura"]

    async def test_filters_apply_on_the_prefix_path_too(self, db_session: AsyncSession):
        by_title = await seed_corpus(db_session)
        result = await search_tags(db_session, "sa", filters=SearchFilters(aliases="only"), limit=100)
        titles = titles_for(by_title, result)
        assert titles and all(by_title[t].alias_of is not None for t in titles)
        assert result.total == len(titles)
```

Add `from datetime import UTC, date, datetime` to the imports.

- [ ] **Step 4: Run the corpus file**

Run: `./run-tests.sh tests/integration/test_tag_search_corpus.py`
Expected: PASS (46 existing + 7 new). If `test_has_alias` disagrees on the set, list the alias rows in `CORPUS` (`sakura`→cherry blossoms, `neko`→cat, `mizugi`→swimsuit, `two-piece swimsuit`→bikini, `Pixiv 21412050`→TKennshou) and fix the expectation only if the fixture differs from that list.

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff check tests && uv run ruff format tests/integration/test_tag_search_corpus.py`

```bash
git add tests/integration/test_tag_search_corpus.py
git commit -m "test(search): corpus hierarchy and source links exercise every tag-list filter"
```

---

### Task 4: Full suite, dev check, push

**Files:** none new.

- [ ] **Step 1: Full suite and lint**

Run: `./run-tests.sh && uv run ruff check app scripts tests && uv run ruff format --check app scripts tests && uv run mypy app`
Expected: green apart from the 80 pre-existing warnings.

- [ ] **Step 2: Confirm the dev API serves the filters**

The dev container hot-reloads this checkout. Run:

```bash
curl -s "http://localhost:8000/api/v1/search?q=&type=4&source_linked=no&limit=2" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['total'], [h['title'] for h in d['hits']])"
curl -s -o /dev/null -w "%{http_code}\n" "http://localhost:8000/api/v1/search?q=&source_linked=no"
curl -s "http://localhost:8000/api/v1/search?q=kinomto&aliases=hide&min_usage=100&limit=3" | python3 -c "import json,sys; d=json.load(sys.stdin); print([h['title'] for h in d['hits']])"
```
Expected: a total in the tens of thousands with two character titles; `422`; Kinomoto Sakura first.

- [ ] **Step 3: Push the branch (no PR)**

```bash
git push -u origin feat/tag-list-filters
```

Leave the frontend checkout on its own branch; the frontend plan's tasks run against this dev API.
