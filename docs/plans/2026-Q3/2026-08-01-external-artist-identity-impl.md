# External Artist Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Structured `(site, external_id)` artist identity parsed from tag external-link URLs — pixiv-only POC — powering exact ID search, upload auto-suggest, and a duplicate-artist guard, per `docs/plans/2026-Q3/2026-08-01-external-artist-identity-design.md`.

**Architecture:** New pure-function parser module (`app/services/artist_identity.py`) + two nullable columns on `tag_external_links` populated on link write and by a three-source backfill. Search prepends an exact-lookup row; url-import resolves its already-extracted pixiv `artist_id` to a tag. Everything is additive; alias retirement is a separate, mod-gated follow-up and **not in this plan**.

**Tech Stack:** FastAPI + SQLModel + Alembic + MariaDB (API repo `~/shuu/shuushuu-api`), SvelteKit 5 (frontend repo `~/shuu/shuushuu-frontend`). Meilisearch needs **no changes** (URL tokens already searchable; api#301 handles numeric exactness).

## Global Constraints

- API repo has heavy uncommitted WIP: **never `git add -A` / `git add .`** — stage explicit files only.
- Run everything through `uv run ...` in the API repo; full suite via `make pytest` (isolated DB).
- Schema changes: `make pytest` silently skips schema-sync unless `ARGS="--schema-sync"` is passed. Any task touching models/migrations must run it.
- Verify the Alembic head with `uv run alembic heads` before writing a migration (never infer from files; `down_revision` can be a tuple).
- API pre-commit excludes `tests|docs|scripts` from ruff — match the existing file's style there by hand; never reformat whole test files.
- Frontend: after API schema changes, run `npm run generate:api` (stale generated types hide type errors); run BOTH `npm run check` and `npm run lint` (they disagree in opposite directions on rune patterns); never `npm run format`.
- Branch names: `feat/artist-identity` in each repo. Merge order: API PR first, then frontend.
- Site string is `"pixiv"` everywhere (`SITE_PIXIV` constant); external IDs are stored as strings.

---

### Task 1: Identity parser module (pure functions)

**Files:**
- Create: `app/services/artist_identity.py`
- Test: `tests/unit/test_artist_identity.py`

**Interfaces:**
- Produces: `ArtistIdentity(site: str, external_id: str)` frozen dataclass; `SITE_PIXIV = "pixiv"`; `parse_identity_url(url: str) -> ArtistIdentity | None`; `parse_identity_query(q: str) -> ArtistIdentity | None`; `canonical_profile_url(identity: ArtistIdentity) -> str`. All later tasks import from `app.services.artist_identity`.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for artist identity URL/query parsing."""

import pytest

from app.services.artist_identity import (
    SITE_PIXIV,
    ArtistIdentity,
    canonical_profile_url,
    parse_identity_query,
    parse_identity_url,
)


class TestParseIdentityUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.pixiv.net/users/21412050",
            "https://pixiv.net/users/21412050",
            "http://www.pixiv.net/users/21412050",
            "https://www.pixiv.net/en/users/21412050",
            "https://touch.pixiv.net/users/21412050",
            "https://www.pixiv.net/member.php?id=21412050",
            "https://www.pixiv.net/member_illust.php?mode=medium&id=21412050",
            "  https://www.pixiv.net/users/21412050  ",
        ],
    )
    def test_parses_pixiv_profile_urls(self, url):
        assert parse_identity_url(url) == ArtistIdentity(site=SITE_PIXIV, external_id="21412050")

    @pytest.mark.parametrize(
        "url",
        [
            "https://twitter.com/someone",  # no parser registered in v1
            "https://www.pixiv.net/artworks/12345",  # artwork, not a profile
            "https://example.com/users/123",
            "not a url",
            "",
        ],
    )
    def test_rejects_non_identity_urls(self, url):
        assert parse_identity_url(url) is None

    def test_trailing_path_segments_allowed(self):
        url = "https://www.pixiv.net/users/21412050/illustrations"
        assert parse_identity_url(url) == ArtistIdentity(site=SITE_PIXIV, external_id="21412050")


class TestParseIdentityQuery:
    def test_bare_digits_are_a_pixiv_id(self):
        assert parse_identity_query("21412050") == ArtistIdentity(
            site=SITE_PIXIV, external_id="21412050"
        )

    @pytest.mark.parametrize("q", ["pixiv 21412050", "Pixiv 21412050", "pixiv:21412050"])
    def test_pixiv_prefixed_id(self, q):
        assert parse_identity_query(q) == ArtistIdentity(site=SITE_PIXIV, external_id="21412050")

    def test_full_url_query(self):
        assert parse_identity_query("https://www.pixiv.net/users/21412050") == ArtistIdentity(
            site=SITE_PIXIV, external_id="21412050"
        )

    @pytest.mark.parametrize("q", ["sakura", "pixiv", "21412050 extra words", ""])
    def test_non_identity_queries_return_none(self, q):
        assert parse_identity_query(q) is None


class TestCanonicalProfileUrl:
    def test_pixiv(self):
        identity = ArtistIdentity(site=SITE_PIXIV, external_id="21412050")
        assert canonical_profile_url(identity) == "https://www.pixiv.net/users/21412050"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_artist_identity.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.artist_identity'`

- [ ] **Step 3: Write the module**

```python
"""Parse artist-identity URLs and search queries into (site, external_id) pairs.

v1 registers pixiv only (POC). To add a site, append its URL patterns and
site constant — see docs/plans/2026-Q3/2026-08-01-external-artist-identity-design.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SITE_PIXIV = "pixiv"

# Every pixiv profile-URL form in the wild. Old descs/links use the legacy
# member.php forms; modern URLs may carry a language prefix (/en/users/...).
_PIXIV_URL_PATTERNS = [
    re.compile(
        r"^https?://(?:www\.|touch\.)?pixiv\.net/(?:[a-z]{2}/)?users/(\d+)", re.IGNORECASE
    ),
    re.compile(
        r"^https?://(?:www\.|touch\.)?pixiv\.net/member(?:_illust)?\.php\?(?:[^#]*&)?id=(\d+)",
        re.IGNORECASE,
    ),
]

_BARE_ID = re.compile(r"^\d{1,12}$")
_PIXIV_PREFIXED = re.compile(r"^pixiv[\s:]+(\d{1,12})$", re.IGNORECASE)


@dataclass(frozen=True)
class ArtistIdentity:
    site: str
    external_id: str


def parse_identity_url(url: str) -> ArtistIdentity | None:
    """Return the identity a profile URL encodes, or None for any other URL."""
    stripped = url.strip()
    for pattern in _PIXIV_URL_PATTERNS:
        match = pattern.match(stripped)
        if match:
            return ArtistIdentity(site=SITE_PIXIV, external_id=match.group(1))
    return None


def parse_identity_query(q: str) -> ArtistIdentity | None:
    """Return the identity a search query names: bare ID, 'pixiv <id>', or URL."""
    stripped = q.strip()
    if not stripped:
        return None
    if _BARE_ID.match(stripped):
        return ArtistIdentity(site=SITE_PIXIV, external_id=stripped)
    prefixed = _PIXIV_PREFIXED.match(stripped)
    if prefixed:
        return ArtistIdentity(site=SITE_PIXIV, external_id=prefixed.group(1))
    return parse_identity_url(stripped)


def canonical_profile_url(identity: ArtistIdentity) -> str:
    """The URL to create when backfilling an identity that has no link yet."""
    return f"https://www.pixiv.net/users/{identity.external_id}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_artist_identity.py -q`
Expected: all PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check app/services/artist_identity.py
uv run mypy app/services/artist_identity.py
git add app/services/artist_identity.py tests/unit/test_artist_identity.py
git commit -m "feat(tags): add artist identity parser (pixiv POC)"
```

---

### Task 2: `site`/`external_id` columns — model, migration, response schema

**Files:**
- Modify: `app/models/tag_external_link.py` (table class `TagExternalLinks`, `__table_args__` around line 49)
- Modify: `app/schemas/tag.py:194` (`TagExternalLinkResponse`)
- Create: `alembic/versions/<generated>_add_identity_columns_to_tag_external_links.py`

**Interfaces:**
- Produces: `TagExternalLinks.site: str | None`, `TagExternalLinks.external_id: str | None` (max 32/128); non-unique index `idx_tag_external_links_site_external_id`; both fields on `TagExternalLinkResponse`. The UNIQUE index is a separate post-backfill migration, deliberately **not** in this plan (design §2: adding it before conflicts are hand-resolved would abort the backfill).

- [ ] **Step 1: Add the fields to the model first (this is the failing "test" driver)**

In `TagExternalLinks`, after the `archive_url` field:

```python
    # Structured identity parsed from url by app/services/artist_identity.py.
    # NULL for URLs no registered parser recognizes. The unique guard on
    # (site, external_id) is added by a later migration, after backfill
    # conflicts are hand-resolved (see the design doc).
    site: str | None = Field(default=None, max_length=32)
    external_id: str | None = Field(default=None, max_length=128)
```

And in `__table_args__`, after `Index("unique_tag_url", ...)`:

```python
        Index("idx_tag_external_links_site_external_id", "site", "external_id"),
```

- [ ] **Step 2: Run schema-sync to verify it fails (model ahead of DB)**

Run: `make pytest ARGS="--schema-sync tests/integration/test_schema_sync.py"`
Expected: FAIL — model columns `site`/`external_id` missing from database

- [ ] **Step 3: Write the migration**

First: `uv run alembic heads` — confirm the single head (was `6dda18b955d8` at planning time; use whatever it is now). Then `uv run alembic revision -m "add identity columns to tag_external_links"` and fill in:

```python
def upgrade() -> None:
    op.add_column(
        "tag_external_links", sa.Column("site", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "tag_external_links", sa.Column("external_id", sa.String(length=128), nullable=True)
    )
    op.create_index(
        "idx_tag_external_links_site_external_id",
        "tag_external_links",
        ["site", "external_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_tag_external_links_site_external_id", table_name="tag_external_links"
    )
    op.drop_column("tag_external_links", "external_id")
    op.drop_column("tag_external_links", "site")
```

- [ ] **Step 4: Expose the fields on the response schema**

In `TagExternalLinkResponse` (`app/schemas/tag.py:194`), after the existing fields:

```python
    site: str | None = None
    external_id: str | None = None
```

- [ ] **Step 5: Run schema-sync to verify it passes**

Run: `make pytest ARGS="--schema-sync tests/integration/test_schema_sync.py"`
Expected: PASS (the isolated test DB runs all migrations)

- [ ] **Step 6: Commit**

```bash
git add app/models/tag_external_link.py app/schemas/tag.py alembic/versions/*add_identity_columns*
git commit -m "feat(tags): add site/external_id identity columns to tag_external_links"
```

---

### Task 3: Identity resolution + parse-on-write in `add_tag_link` + duplicate guard

**Files:**
- Modify: `app/services/artist_identity.py` (add `resolve_identity`)
- Modify: `app/api/v1/tags.py` (`add_tag_link`, around line 1987 `new_link = TagExternalLinks(...)`)
- Test: `tests/api/v1/test_tags.py` (add to the existing tag-link test class — grep `add_tag_link` or `links` in the file for its name and fixture pattern; reuse its auth/tag fixtures)

**Interfaces:**
- Consumes: Task 1 parser, Task 2 columns.
- Produces: `async def resolve_identity(db: AsyncSession, identity: ArtistIdentity) -> Tags | None` in `app/services/artist_identity.py` — used by Tasks 4, 5, and 6.

- [ ] **Step 1: Write the failing API tests**

Follow the existing tag-link tests' fixture style exactly (admin auth client + created tag). The three behaviors:

```python
    async def test_pixiv_link_populates_identity(self, ...existing fixtures...):
        """Adding a pixiv profile URL parses site/external_id onto the link."""
        resp = await client.post(
            f"/api/v1/tags/{tag_id}/links",
            json={"url": "https://www.pixiv.net/users/21412050"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["site"] == "pixiv"
        assert body["external_id"] == "21412050"

    async def test_non_identity_link_leaves_identity_null(self, ...):
        """A URL no parser recognizes stores NULL site/external_id."""
        resp = await client.post(
            f"/api/v1/tags/{tag_id}/links",
            json={"url": "https://example.com/gallery"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["site"] is None
        assert resp.json()["external_id"] is None

    async def test_duplicate_identity_on_other_tag_is_409(self, ...):
        """The same pixiv ID cannot be claimed by a second artist tag."""
        # first tag claims the ID
        await client.post(
            f"/api/v1/tags/{tag_id}/links",
            json={"url": "https://www.pixiv.net/users/21412050"},
            headers=auth_headers,
        )
        # second tag tries the same ID via a *different* URL form
        resp = await client.post(
            f"/api/v1/tags/{other_tag_id}/links",
            json={"url": "https://www.pixiv.net/member.php?id=21412050"},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert "21412050" in resp.json()["detail"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `make pytest ARGS="tests/api/v1/test_tags.py -k identity"`
Expected: FAIL — response lacks `site` values (fields exist from Task 2 but are never populated), and the second POST returns 200, not 409

- [ ] **Step 3: Implement `resolve_identity`**

Append to `app/services/artist_identity.py`:

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import Tags
from app.models.tag_external_link import TagExternalLinks


async def resolve_identity(db: AsyncSession, identity: ArtistIdentity) -> Tags | None:
    """Exact lookup: which tag owns this (site, external_id)? None if unclaimed."""
    result = await db.execute(
        select(Tags)
        .join(TagExternalLinks, TagExternalLinks.tag_id == Tags.tag_id)  # type: ignore[arg-type]
        .where(TagExternalLinks.site == identity.site)  # type: ignore[arg-type]
        .where(TagExternalLinks.external_id == identity.external_id)  # type: ignore[arg-type]
        .limit(1)
    )
    return result.scalars().first()
```

(Match the file's existing `# type: ignore` idiom only where mypy actually complains — check with `uv run mypy`.)

- [ ] **Step 4: Hook the parser into `add_tag_link`**

In `app/api/v1/tags.py`, immediately after `new_link = TagExternalLinks(tag_id=tag_id, url=link_data.url)`:

```python
    identity = parse_identity_url(link_data.url)
    if identity is not None:
        claimed_by = await resolve_identity(db, identity)
        if claimed_by is not None and claimed_by.tag_id != tag_id:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{identity.site} ID {identity.external_id} already belongs to "
                    f"tag '{claimed_by.title}' (id {claimed_by.tag_id})"
                ),
            )
        new_link.site = identity.site
        new_link.external_id = identity.external_id
```

Add the imports next to the file's other service imports:

```python
from app.services.artist_identity import parse_identity_url, resolve_identity
```

This is an application-level guard (SELECT-then-insert, same TOCTOU shape as the existing duplicate-tag check); the DB unique index lands post-backfill as the backstop.

- [ ] **Step 5: Run tests to verify they pass, then the file's full test set**

Run: `make pytest ARGS="tests/api/v1/test_tags.py"`
Expected: all PASS (existing link tests must stay green — the audit/409-URL behaviors are untouched)

- [ ] **Step 6: Commit**

```bash
git add app/services/artist_identity.py app/api/v1/tags.py tests/api/v1/test_tags.py
git commit -m "feat(tags): parse identity on link add, guard duplicate artist IDs"
```

---

### Task 4: Search exact-match layer

**Files:**
- Modify: `app/schemas/search.py` (`TagSearchHit`)
- Modify: `app/api/v1/search.py` (the `search` endpoint, after the MySQL hydrate block that builds `hits`)
- Test: `tests/api/v1/test_search.py` (follow its existing fixture/mocking pattern for the meilisearch dependency)

**Interfaces:**
- Consumes: `parse_identity_query`, `resolve_identity` (Tasks 1, 3).
- Produces: `TagSearchHit.matched_identity: str | None` — the frontend (Task 7) renders a hit with this set as "`{matched_identity}` → `{title}`".

- [ ] **Step 1: Write the failing API tests**

Read `tests/api/v1/test_search.py` first and copy its approach to providing search results (it must already inject/mock the meilisearch service dependency since the suite runs without meilisearch). Behaviors to pin:

```python
    async def test_exact_identity_query_prepends_artist_hit(self, ...):
        """A bare pixiv ID returns the owning artist first, flagged with matched_identity."""
        # fixture: artist tag + TagExternalLinks row with site="pixiv",
        # external_id="21412050" inserted directly via the db session fixture
        resp = await client.get("/api/v1/search", params={"q": "21412050"})
        assert resp.status_code == 200
        hits = resp.json()["hits"]
        assert hits[0]["tag_id"] == artist_tag_id
        assert hits[0]["matched_identity"] == "pixiv 21412050"

    async def test_exact_hit_not_duplicated_when_meili_also_returns_it(self, ...):
        """If the fuzzy layer already found the artist, it appears once, flagged."""
        # same fixture; meili stub returns [artist_tag_id]
        resp = await client.get("/api/v1/search", params={"q": "21412050"})
        hits = resp.json()["hits"]
        assert [h["tag_id"] for h in hits].count(artist_tag_id) == 1
        assert hits[0]["matched_identity"] == "pixiv 21412050"

    async def test_text_query_has_no_matched_identity(self, ...):
        resp = await client.get("/api/v1/search", params={"q": "sakura"})
        assert all(h["matched_identity"] is None for h in resp.json()["hits"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `make pytest ARGS="tests/api/v1/test_search.py -k identity or matched"`
Expected: FAIL — `matched_identity` key absent / exact hit not prepended

- [ ] **Step 3: Add the schema field**

In `TagSearchHit` (`app/schemas/search.py`):

```python
    matched_identity: str | None = None
```

- [ ] **Step 4: Implement the exact layer in the endpoint**

In `app/api/v1/search.py`, after the hydrate block finishes building `hits` (and before the `SearchResponse` return). Reuse the hydrate block's own field mapping when constructing the hit — the exact attribute names are visible in that block (`title`, `desc`, `type`, `usage_count`, `alias_of`, `date_added`, `is_alias`, ...):

```python
    identity = parse_identity_query(q) if q else None
    if identity is not None:
        exact_tag = await resolve_identity(db, identity)
        if exact_tag is not None:
            label = f"{identity.site} {identity.external_id}"
            existing = next((h for h in hits if h.tag_id == exact_tag.tag_id), None)
            if existing is not None:
                hits.remove(existing)
                existing.matched_identity = label
                hits.insert(0, existing)
            else:
                hits.insert(
                    0,
                    TagSearchHit(
                        # copy the field mapping used by the hydrate block above,
                        # sourced from exact_tag, with:
                        matched_identity=label,
                    ),
                )
                total += 1
```

Import `parse_identity_query`/`resolve_identity` alongside the file's other imports. Note the placement is deliberate: after the meilisearch call, so meilisearch downtime still 503s exactly as today (no behavior change for the fuzzy layer).

While in this endpoint, also resolve the design-doc open item: dev queries showed `total: 2` with only 1 hit for an ID query — find what counts a document that the hydrate step then drops (likely a tag deleted from MySQL but not meilisearch, or a type filter). Understand it and note the cause in the PR description; fix it here only if it's a one-liner in this endpoint, otherwise file an issue.

- [ ] **Step 5: Run tests to verify they pass, then the whole file**

Run: `make pytest ARGS="tests/api/v1/test_search.py"`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add app/schemas/search.py app/api/v1/search.py tests/api/v1/test_search.py
git commit -m "feat(search): exact artist-identity lookup ranked above fuzzy hits"
```

---

### Task 5: url-import resolves the artist tag

**Files:**
- Modify: `app/schemas/url_import.py` (`UrlResolveResponse`, line ~27)
- Modify: `app/api/v1/url_import.py` (the resolve endpoint that constructs `UrlResolveResponse`, line ~117)
- Test: `tests/api/v1/test_url_import.py` (follow its existing upstream-stubbing pattern)

**Interfaces:**
- Consumes: `resolve_identity`, `ArtistIdentity`, `SITE_PIXIV` (Tasks 1, 3). `ImportResult.artist_id` already exists (`app/services/url_import/base.py:60`).
- Produces: `UrlResolveResponse.artist_id: str | None`, `.artist_tag_id: int | None`, `.artist_tag_title: str | None` — consumed by frontend Task 8.

- [ ] **Step 1: Write the failing API test**

Copy the file's existing resolve-endpoint test setup (stubbed upstream pixiv response). New behaviors:

```python
    async def test_resolve_returns_known_artist_tag(self, ...):
        """When the pixiv artist_id maps to a tag, the response names it."""
        # fixture: artist tag with a pixiv link (site="pixiv",
        # external_id matching the stubbed pixiv userId)
        resp = await client.post(..., json={"url": pixiv_artwork_url}, ...)
        body = resp.json()
        assert body["artist_id"] == "21412050"
        assert body["artist_tag_id"] == artist_tag_id
        assert body["artist_tag_title"] == "TKennshou"

    async def test_resolve_unknown_artist_id_returns_null_tag(self, ...):
        """Unknown pixiv ID → artist_id present, tag fields null (no false match)."""
        resp = await client.post(..., json={"url": pixiv_artwork_url}, ...)
        body = resp.json()
        assert body["artist_id"] == "21412050"
        assert body["artist_tag_id"] is None
        assert body["artist_tag_title"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `make pytest ARGS="tests/api/v1/test_url_import.py -k artist_tag"`
Expected: FAIL — `artist_id` / `artist_tag_id` keys absent from the response

- [ ] **Step 3: Extend the schema**

In `UrlResolveResponse` (`app/schemas/url_import.py`):

```python
    artist_id: str | None = None
    artist_tag_id: int | None = None
    artist_tag_title: str | None = None
```

- [ ] **Step 4: Resolve in the endpoint**

In `app/api/v1/url_import.py`, the resolve handler already has the `ImportResult` as `post`. Confirm the handler has a DB session dependency; if not, add `db: AsyncSession = Depends(get_db)` matching the file's other endpoints. Before constructing `UrlResolveResponse`:

```python
    artist_tag_id: int | None = None
    artist_tag_title: str | None = None
    if post.artist_id and post.site == SITE_PIXIV:
        artist_tag = await resolve_identity(
            db, ArtistIdentity(site=SITE_PIXIV, external_id=post.artist_id)
        )
        if artist_tag is not None:
            artist_tag_id = artist_tag.tag_id
            artist_tag_title = artist_tag.title
```

then pass `artist_id=post.artist_id, artist_tag_id=artist_tag_id, artist_tag_title=artist_tag_title` into the `UrlResolveResponse(...)` constructor. First verify the pixiv resolver's `.site` string equals `"pixiv"` (`grep -n "site" app/services/url_import/pixiv.py`); if it differs, compare against that value instead of `SITE_PIXIV` and say so in the commit message.

- [ ] **Step 5: Run tests to verify they pass, then the whole file**

Run: `make pytest ARGS="tests/api/v1/test_url_import.py"`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add app/schemas/url_import.py app/api/v1/url_import.py tests/api/v1/test_url_import.py
git commit -m "feat(upload): resolve known artist tag from imported pixiv URL"
```

---

### Task 6: Backfill service + thin script

**Files:**
- Create: `app/services/artist_identity_backfill.py`
- Create: `scripts/backfill_artist_identity.py` (thin wrapper, same convention as `scripts/ml_remap.py` — no separate script test)
- Test: `tests/integration/test_artist_identity_backfill.py`

**Interfaces:**
- Consumes: parser + `resolve_identity` (Tasks 1, 3), Task 2 columns.
- Produces: `async def run_backfill(db: AsyncSession, *, apply: bool) -> BackfillReport` with `BackfillReport(links_parsed, links_created_from_aliases, links_created_from_desc, anomalies: list[str], artist_tags_without_identity: int)`.

**Order within the run matters:** existing links first (they're ground truth), then alias titles, then desc — later sources consult identities established by earlier ones.

- [ ] **Step 1: Write the failing integration tests**

Use the integration suite's DB session fixture pattern (see `tests/integration/test_import_tag_mappings.py` for the create-rows-then-run-service shape):

```python
"""Integration tests for the artist-identity backfill."""

import pytest
from sqlalchemy import select

from app.config import TagType
from app.models.tag import Tags
from app.models.tag_external_link import TagExternalLinks
from app.services.artist_identity_backfill import run_backfill


@pytest.mark.integration
class TestBackfillExistingLinks:
    async def test_parses_identity_onto_existing_pixiv_link(self, db):
        artist = Tags(title="TKennshou", type=TagType.ARTIST, usage_count=1)
        db.add(artist)
        await db.flush()
        db.add(TagExternalLinks(tag_id=artist.tag_id, url="https://www.pixiv.net/users/21412050"))
        await db.commit()

        report = await run_backfill(db, apply=True)

        link = (await db.execute(
            select(TagExternalLinks).where(TagExternalLinks.tag_id == artist.tag_id)
        )).scalar_one()
        assert (link.site, link.external_id) == ("pixiv", "21412050")
        assert report.links_parsed == 1

    async def test_dry_run_changes_nothing(self, db):
        # same setup; apply=False
        report = await run_backfill(db, apply=False)
        assert report.links_parsed == 1
        link = ...  # re-fetch
        assert link.site is None


class TestBackfillFromAliases:
    async def test_alias_title_creates_link_on_canonical(self, db):
        artist = Tags(title="SomeArtist", type=TagType.ARTIST, usage_count=1)
        db.add(artist)
        await db.flush()
        db.add(Tags(title="Pixiv 21412050", type=TagType.ARTIST, alias_of=artist.tag_id))
        await db.commit()

        report = await run_backfill(db, apply=True)

        link = ...  # fetch artist's links
        assert link.url == "https://www.pixiv.net/users/21412050"
        assert (link.site, link.external_id) == ("pixiv", "21412050")
        assert report.links_created_from_aliases == 1

    async def test_conflicting_alias_is_an_anomaly_not_a_write(self, db):
        # artist A owns pixiv 111 via a link; artist B has alias "Pixiv 111"
        report = await run_backfill(db, apply=True)
        assert any("111" in a for a in report.anomalies)
        # artist B gained no link
        assert (await db.execute(
            select(TagExternalLinks).where(TagExternalLinks.tag_id == artist_b.tag_id)
        )).first() is None


class TestBackfillFromDesc:
    async def test_desc_url_creates_link(self, db):
        artist = Tags(
            title="OldArtist",
            type=TagType.ARTIST,
            desc="profile: https://www.pixiv.net/member.php?id=555",
        )
        # run, assert link (canonical /users/555 form) + report.links_created_from_desc == 1
        # and desc is unchanged

    async def test_multiple_distinct_desc_ids_is_an_anomaly(self, db):
        # desc contains member.php?id=1 and users/2 → anomaly, no link created
```

Fill each `...` with the same select/fixture code as the first test — no shortcuts; every test must be runnable as written.

- [ ] **Step 2: Run tests to verify they fail**

Run: `make pytest ARGS="tests/integration/test_artist_identity_backfill.py"`
Expected: FAIL — `ModuleNotFoundError: app.services.artist_identity_backfill`

- [ ] **Step 3: Implement the service**

```python
"""Backfill (site, external_id) identity from links, alias titles, and descs.

Three sources in confidence order; see the design doc §5. Dry-run by default —
`apply=False` computes the full report without writing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.models.tag import Tags
from app.models.tag_external_link import TagExternalLinks
from app.services.artist_identity import (
    SITE_PIXIV,
    ArtistIdentity,
    canonical_profile_url,
    parse_identity_url,
)

_ALIAS_TITLE = re.compile(r"^pixiv\s+(\d+)$", re.IGNORECASE)
# Non-anchored forms of the URL patterns, for scanning desc text.
_DESC_URL = re.compile(
    r"https?://(?:www\.|touch\.)?pixiv\.net/"
    r"(?:(?:[a-z]{2}/)?users/(\d+)|member(?:_illust)?\.php\?(?:[^\s#]*&)?id=(\d+))",
    re.IGNORECASE,
)


@dataclass
class BackfillReport:
    links_parsed: int = 0
    links_created_from_aliases: int = 0
    links_created_from_desc: int = 0
    artist_tags_without_identity: int = 0
    anomalies: list[str] = field(default_factory=list)


async def _identity_owners(db: AsyncSession) -> dict[tuple[str, str], int]:
    """Map (site, external_id) -> owning tag_id for every populated link."""
    rows = await db.execute(
        select(TagExternalLinks.tag_id, TagExternalLinks.site, TagExternalLinks.external_id)
        .where(TagExternalLinks.site.is_not(None))  # type: ignore[union-attr]
    )
    return {(r.site, r.external_id): r.tag_id for r in rows.all()}


async def run_backfill(db: AsyncSession, *, apply: bool) -> BackfillReport:
    report = BackfillReport()

    # --- Source 1: parse existing link URLs in place ---
    links = (
        (await db.execute(
            select(TagExternalLinks).where(TagExternalLinks.site.is_(None))  # type: ignore[union-attr]
        )).scalars().all()
    )
    owners = await _identity_owners(db)
    for link in links:
        identity = parse_identity_url(link.url)
        if identity is None:
            continue
        key = (identity.site, identity.external_id)
        owner = owners.get(key)
        if owner is not None and owner != link.tag_id:
            report.anomalies.append(
                f"link {link.link_id} (tag {link.tag_id}): {identity.site} "
                f"{identity.external_id} already owned by tag {owner}"
            )
            continue
        report.links_parsed += 1
        owners[key] = link.tag_id
        if apply:
            link.site = identity.site
            link.external_id = identity.external_id

    # --- Source 2: "Pixiv <id>" alias titles ---
    aliases = (
        (await db.execute(
            select(Tags).where(Tags.alias_of.is_not(None))  # type: ignore[union-attr]
        )).scalars().all()
    )
    for alias in aliases:
        match = _ALIAS_TITLE.match(alias.title)
        if not match:
            continue
        identity = ArtistIdentity(site=SITE_PIXIV, external_id=match.group(1))
        key = (identity.site, identity.external_id)
        owner = owners.get(key)
        if owner == alias.alias_of:
            continue  # canonical already has it
        if owner is not None:
            report.anomalies.append(
                f"alias tag {alias.tag_id} '{alias.title}' -> tag {alias.alias_of}, "
                f"but identity owned by tag {owner}"
            )
            continue
        report.links_created_from_aliases += 1
        owners[key] = alias.alias_of  # type: ignore[assignment]
        if apply:
            db.add(
                TagExternalLinks(
                    tag_id=alias.alias_of,
                    url=canonical_profile_url(identity),
                    site=identity.site,
                    external_id=identity.external_id,
                )
            )

    # --- Source 3: pixiv URLs embedded in artist descs ---
    artists = (
        (await db.execute(
            select(Tags)
            .where(Tags.type == TagType.ARTIST)  # type: ignore[arg-type]
            .where(Tags.alias_of.is_(None))  # type: ignore[union-attr]
            .where(Tags.desc.is_not(None))  # type: ignore[union-attr]
        )).scalars().all()
    )
    claimed_tags = {tag_id for (_site, _eid), tag_id in owners.items()}
    for artist in artists:
        if artist.tag_id in claimed_tags:
            continue
        ids = {m.group(1) or m.group(2) for m in _DESC_URL.finditer(artist.desc or "")}
        if not ids:
            continue
        if len(ids) > 1:
            report.anomalies.append(
                f"tag {artist.tag_id} '{artist.title}': desc names multiple pixiv "
                f"ids {sorted(ids)}"
            )
            continue
        identity = ArtistIdentity(site=SITE_PIXIV, external_id=ids.pop())
        key = (identity.site, identity.external_id)
        owner = owners.get(key)
        if owner is not None:
            report.anomalies.append(
                f"tag {artist.tag_id} '{artist.title}': desc pixiv id "
                f"{identity.external_id} already owned by tag {owner}"
            )
            continue
        report.links_created_from_desc += 1
        owners[key] = artist.tag_id  # type: ignore[assignment]
        if apply:
            db.add(
                TagExternalLinks(
                    tag_id=artist.tag_id,
                    url=canonical_profile_url(identity),
                    site=identity.site,
                    external_id=identity.external_id,
                )
            )

    # --- Coverage figure for the report ---
    claimed_tags = {tag_id for (_s, _e), tag_id in owners.items()}
    all_artists = (
        (await db.execute(
            select(Tags.tag_id)
            .where(Tags.type == TagType.ARTIST)  # type: ignore[arg-type]
            .where(Tags.alias_of.is_(None))  # type: ignore[union-attr]
        )).scalars().all()
    )
    report.artist_tags_without_identity = len([t for t in all_artists if t not in claimed_tags])

    if apply:
        await db.commit()
    return report
```

Adapt `# type: ignore` comments to what mypy actually reports, per the codebase idiom.

- [ ] **Step 4: Run tests to verify they pass**

Run: `make pytest ARGS="tests/integration/test_artist_identity_backfill.py"`
Expected: all PASS

- [ ] **Step 5: Write the thin script**

`scripts/backfill_artist_identity.py`, modeled on `scripts/ml_remap.py`'s session/arg handling:

```python
#!/usr/bin/env python3
"""Backfill artist identity (site/external_id) on tag_external_links.

Dry-run by default; --apply to write. See
docs/plans/2026-Q3/2026-08-01-external-artist-identity-design.md §5.

Usage:
    uv run python scripts/backfill_artist_identity.py            # dry run
    uv run python scripts/backfill_artist_identity.py --apply
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.database import get_async_session
from app.services.artist_identity_backfill import run_backfill


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = parser.parse_args()

    async with get_async_session() as db:
        report = await run_backfill(db, apply=args.apply)

    mode = "APPLIED" if args.apply else "DRY RUN"
    print(f"[{mode}] links parsed in place:      {report.links_parsed}")
    print(f"[{mode}] links created from aliases: {report.links_created_from_aliases}")
    print(f"[{mode}] links created from descs:   {report.links_created_from_desc}")
    print(f"artist tags still without identity:  {report.artist_tags_without_identity}")
    print(f"\nanomalies ({len(report.anomalies)}):")
    for line in report.anomalies:
        print(f"  - {line}")


if __name__ == "__main__":
    asyncio.run(main())
```

(Confirm `get_async_session`'s exact import/usage against `scripts/ml_remap.py` and copy that pattern if it differs.)

- [ ] **Step 6: Full suite + commit**

Run: `make pytest` then `make pytest ARGS="--schema-sync tests/integration/test_schema_sync.py"`
Expected: all PASS

```bash
git add app/services/artist_identity_backfill.py scripts/backfill_artist_identity.py tests/integration/test_artist_identity_backfill.py
git commit -m "feat(tags): three-source artist-identity backfill with dry-run report"
```

Then push and open the API PR (base `main`, title `feat(tags): structured artist identity (pixiv POC)`).

---

### Task 7 (frontend repo): generated types + identity suggestion row

**Repo:** `~/shuu/shuushuu-frontend`, branch `feat/artist-identity`.

**Files:**
- Regenerate: `npm run generate:api` (dev API must be running the API branch)
- Modify: `src/lib/components/features/TagSuggestionDropdown.svelte` (alias row template, lines ~54-67)
- Test: `tests/e2e/artist-identity-search.spec.ts` (new)

**Interfaces:**
- Consumes: `TagSearchHit.matched_identity` from Task 4 (via regenerated types).

- [ ] **Step 1: Regenerate API types**

Run: `npm run generate:api` — verify the generated search-hit type now includes `matched_identity`. Commit the regenerated file separately if the repo's convention is to check it in (check `git log --oneline -3 -- <generated path>`).

- [ ] **Step 2: Write the failing e2e test**

Follow the authenticated-e2e conventions (login helper, `.env.local` creds, `set -a; source .env.local` run pattern) and the fresh-tag-per-spec pattern from the existing tag e2e specs. No mocks — real dev API:

```typescript
import { test, expect } from '@playwright/test';
// reuse the repo's login + api-request helpers (see existing tag link e2e spec)

test('bare pixiv ID search shows identity row resolving to the artist', async ({ page, request }) => {
  // setup via API as admin: create artist tag, add link
  // https://www.pixiv.net/users/<unique 9-digit id> (unique per run to
  // avoid cross-run identity conflicts, e.g. from Date.now())
  // then:
  await page.goto('/');
  const searchInput = page.getByPlaceholder(/search/i); // match existing specs' selector
  await searchInput.fill(uniqueId);
  const dropdown = page.locator('.suggestion-dropdown'); // match component's actual class
  await expect(dropdown).toContainText(`pixiv ${uniqueId}`);
  await expect(dropdown).toContainText(tagTitle);
});
```

Copy selectors from an existing spec that already exercises `TagSuggestionDropdown` (grep `tests/e2e` for `TagSuggestionDropdown` or `suggestion`), don't invent them.

- [ ] **Step 3: Run e2e to verify it fails**

Run: `set -a; source .env.local; set +a; npx playwright test tests/e2e/artist-identity-search.spec.ts --workers=1`
Expected: FAIL — dropdown never shows the `pixiv <id> →` row (API returns `matched_identity` but the component ignores it)

- [ ] **Step 4: Render the identity row**

In `TagSuggestionDropdown.svelte`, the hit row currently renders (lines ~54-67) an alias form `{title} → {alias_of_name}`. Add the identity form ahead of it, reusing the same classes:

```svelte
{#if hit.matched_identity}
	<span class="suggestion-title">{hit.matched_identity}</span>
	<span class="alias-arrow" aria-label="pixiv ID of">→</span>
	<span class="suggestion-title">{hit.title}</span>
{:else if hit.is_alias && hit.alias_of_name}
	<!-- existing alias rendering, unchanged -->
```

Match the component's actual class names (`class:alias-source` etc. are visible at lines 54-67) rather than the names sketched here; the visual result must be indistinguishable in weight from today's alias rows.

- [ ] **Step 5: Run e2e to verify it passes, then the checks**

Run: the same playwright command, then `npm run check && npm run lint`
Expected: PASS on all three

- [ ] **Step 6: Commit**

```bash
git add src/lib/components/features/TagSuggestionDropdown.svelte tests/e2e/artist-identity-search.spec.ts
git commit -m "feat(search): render exact pixiv-identity suggestion rows"
```

---

### Task 8 (frontend repo): upload artist auto-suggest

**Files:**
- Modify: `src/lib/components/features/UrlImportPanel.svelte` (meta emit, lines ~18, ~63)
- Modify: `src/routes/upload/+page.svelte` (`importedMeta` state line ~54, meta display block lines ~617-622, `artistTags` state line ~96)

**Interfaces:**
- Consumes: `artist_tag_id` / `artist_tag_title` from Task 5 (via regenerated types).

**Testing note (decided at planning):** an e2e would need a live pixiv fetch (no mocks allowed in e2e), which is not reliable in CI or repeatable. The resolution logic is API-tested in Task 5; the wiring below is verified manually on dev during the rollout checklist. If a Vitest component tier exists by execution time, add a component test for the add-artist button instead.

- [ ] **Step 1: Extend the meta payload**

In `UrlImportPanel.svelte`: extend the emitted meta type (line ~18) and its construction (line ~63):

```typescript
meta: {
	sourceUrl: string;
	title?: string;
	artistName?: string;
	artistTag?: { tagId: number; title: string };
}
// ...
artistTag: result.artist_tag_id != null
	? { tagId: result.artist_tag_id, title: result.artist_tag_title ?? '' }
	: undefined
```

- [ ] **Step 2: Offer the artist in the upload form**

In `src/routes/upload/+page.svelte`: widen `importedMeta` (line ~54) with the same `artistTag` shape. In the imported-meta display block (lines ~617-622), add after the existing title/artistName line:

```svelte
{#if importedMeta?.artistTag && !artistTags.some((t) => t.tag_id === importedMeta.artistTag.tagId)}
	<button
		type="button"
		class="add-artist-suggestion"
		onclick={() => {
			artistTags = [
				...artistTags,
				{ tag_id: importedMeta.artistTag.tagId, title: importedMeta.artistTag.title, type: TAG_TYPE_ARTIST }
			];
		}}
	>
		+ Add artist: {importedMeta.artistTag.title}
	</button>
{/if}
```

Match the `TagWithType` shape used by `artistTags` (line ~96) and the `TAG_TYPE_ARTIST` constant already imported at line ~241; style the button consistently with the block's existing classes.

- [ ] **Step 3: Verify checks pass**

Run: `npm run check && npm run lint`
Expected: PASS. Then manually on dev: import a pixiv artwork URL for a linked artist, confirm the button appears and adds the chip once.

- [ ] **Step 4: Commit and open the frontend PR**

```bash
git add src/lib/components/features/UrlImportPanel.svelte src/routes/upload/+page.svelte
git commit -m "feat(upload): suggest known artist tag from imported pixiv URL"
```

Open the frontend PR noting it depends on the API PR (merge API first).

---

### Task 9: Dev rollout checklist (ops, after both PRs merge)

- [ ] `uv run alembic upgrade head` on dev
- [ ] `uv run python scripts/backfill_artist_identity.py` (dry run) — save the anomaly report for the user/mods to hand-review
- [ ] After the user approves the clean cases: re-run with `--apply`
- [ ] `uv run python scripts/reindex_search.py` — the backfill creates new link rows (alias/desc sources) that must reach meilisearch's `external_urls` for fuzzy bare-ID search (design §5)
- [ ] Manual smoke: bare-ID search shows the identity row; upload import suggests the artist
- [ ] Hand the anomaly report + dev instance to the mods for the parity evaluation (design §6 step 2)
- [ ] **STOP.** Unique-index migration and alias retirement are follow-up work, gated on mod sign-off.
