# RSS / Atom feed — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build two Atom 1.0 feed endpoints (`/api/v1/images.atom` and `/api/v1/tags/{id}/images.atom`) on the FastAPI backend, replacing the legacy PHP RSS 1.0 feed.

**Architecture:** Thin route handlers in `app/api/v1/feeds.py` delegate to pure helpers in `app/services/feeds.py`. ETag/Last-Modified derived from a cheap sentinel query; full rendering happens only when conditional-request checks miss. Per-tag feeds reuse the existing `resolve_tag_alias()` + `get_tag_hierarchy()` helpers from `app/api/v1/tags.py` so the image set matches the JSON tag-images endpoint.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy async, MariaDB, `feedgenerator` (PyPI, Django-extracted). Python 3.14+.

**Reference:** full design at `docs/plans/2026-04-24-rss-feed-design.md`. Read it before starting — this plan assumes the reader has seen the spec's feed-structure tables, ETag rationale, and edge-case list.

---

## File Structure

**Create:**
- `app/api/v1/feeds.py` — two route handlers (`list_images_atom`, `list_tag_images_atom`). Thin; dependency injection, conditional-request check, call service, return `Response`.
- `app/services/feeds.py` — pure helpers: MIME mapping, title composition, ETag/Last-Modified derivation, sentinel query, hydration query, Atom XML rendering.
- `tests/unit/test_feeds_service.py` — unit tests for pure helpers (MIME, title, ETag).
- `tests/api/v1/test_feeds.py` — API/integration tests hitting both endpoints.

**Modify:**
- `pyproject.toml` — add `feedgenerator>=2.2.1` to `dependencies`.
- `app/main.py` — register the feeds router.
- `app/schemas/image.py` — add `usage_count: int = 0` to `TagSummary` (used for title-composition representative selection). Non-breaking addition; existing JSON API responses gain the field.

**Do NOT modify:**
- `app/config.py` (reuse existing `FRONTEND_URL`).
- Any DB schema or Alembic migration — pure read-only feature.

---

## Chunk 1: Setup + pure helpers

Pure-function work. No DB, no HTTP. Every function in this chunk is unit-testable with plain Python input.

### Task 1: Dependency, router wiring, and empty modules

**Files:**
- Modify: `pyproject.toml` (dependencies)
- Create: `app/services/feeds.py` (empty module)
- Create: `app/api/v1/feeds.py` (empty router)
- Modify: `app/main.py` (register router)

- [ ] **Step 1: Add `feedgenerator` to dependencies**

Locate the `dependencies = [...]` block in `pyproject.toml` and append:

```toml
"feedgenerator>=2.2.1",
```

- [ ] **Step 2: Sync the environment**

Run: `uv sync`
Expected: no errors; `.venv/lib/.../feedgenerator/...` installed.

- [ ] **Step 3: Create `app/services/feeds.py` as an empty module**

```python
"""Atom feed rendering and query helpers."""
```

- [ ] **Step 4: Create `app/api/v1/feeds.py` with an empty router**

```python
"""Atom feed endpoints."""

from fastapi import APIRouter

router = APIRouter(tags=["feeds"])
```

- [ ] **Step 5: Register the router in `app/main.py`**

Find where other `app.include_router(...)` calls live. Add alongside them:

```python
from app.api.v1 import feeds as feeds_router
# ...
app.include_router(feeds_router.router, prefix="/api/v1")
```

- [ ] **Step 6: Verify the app still starts**

Run: `uv run python -c "from app.main import app; print(sorted(r.path for r in app.routes if hasattr(r, 'path')))"`
Expected: no import errors. No new routes yet — that's fine.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock app/services/feeds.py app/api/v1/feeds.py app/main.py
git commit -m "feat(feeds): scaffold feeds router and service module"
```

---

### Task 2: Extend `TagSummary` with `usage_count`

`compose_entry_title` (next task) picks one representative tag per category by `usage_count DESC`. The existing `TagSummary` schema (`app/schemas/image.py:24`) exposes `.tag`, `.tag_id`, `.type_id`, `.type_name` — but **not** `.usage_count`, even though the underlying `Tags.usage_count` column exists (`app/models/tag.py:122`). Add it so `TagSummary` instances reaching the feed carry the data the title composer needs. Pydantic's `from_attributes=True` (already set on `TagSummary`) pulls the value automatically.

**Files:**
- Modify: `app/schemas/image.py`
- Modify: `tests/unit/test_schemas.py` *(or similar — verify exact filename via `ls tests/unit/test_*schema*`; fall back to a new `tests/unit/test_tag_summary.py` if no tag-schema test file exists)*

- [ ] **Step 1: Write the failing test**

Add to the existing tag-summary tests (or create a new `tests/unit/test_tag_summary.py` with this content):

```python
"""TagSummary schema tests — usage_count field."""

from app.models.tag import Tags
from app.schemas.image import TagSummary


class TestTagSummaryUsageCount:
    def test_usage_count_populated_from_orm(self):
        tag = Tags(tag_id=1, title="t", type=1, usage_count=42)
        summary = TagSummary.model_validate(tag)
        assert summary.usage_count == 42

    def test_usage_count_defaults_to_zero(self):
        # Validate from a dict without usage_count — should default.
        summary = TagSummary.model_validate(
            {"tag_id": 1, "title": "t", "type": 1}
        )
        assert summary.usage_count == 0
```

- [ ] **Step 2: Run the test and confirm failure**

Run: `uv run pytest tests/unit/test_tag_summary.py -v`  *(or whichever path you used in Step 1)*
Expected: `AttributeError` or pydantic validation error — `usage_count` not on `TagSummary`.

- [ ] **Step 3: Add the field**

Edit `app/schemas/image.py`, in the `TagSummary` class definition, between `type_id` and the `model_config` line:

```python
    tag_id: int
    tag: str = Field(alias="title")  # Maps from Tags.title
    type_id: int = Field(alias="type")  # Maps from Tags.type
    usage_count: int = 0  # NEW — needed by feed title composer; non-breaking.
```

- [ ] **Step 4: Run the test and confirm pass**

Run: `uv run pytest tests/unit/test_tag_summary.py -v`
Expected: both tests pass.

- [ ] **Step 5: Run the full schema test suite for regressions**

Run: `uv run pytest tests/unit/ -k "schema" -v`
Expected: all pre-existing schema tests still pass (gaining a field is non-breaking).

- [ ] **Step 6: Commit**

```bash
git add app/schemas/image.py tests/unit/test_tag_summary.py
git commit -m "feat(schemas): expose usage_count on TagSummary for feed title composition"
```

---

### Task 3: MIME type mapping helper

**Files:**
- Modify: `app/services/feeds.py`
- Create: `tests/unit/test_feeds_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_feeds_service.py
"""Unit tests for app.services.feeds pure helpers."""

import pytest

from app.services.feeds import mime_type_for_ext


class TestMimeTypeForExt:
    @pytest.mark.parametrize(
        "ext,expected",
        [
            ("jpg", "image/jpeg"),
            ("jpeg", "image/jpeg"),
            ("JPG", "image/jpeg"),
            ("png", "image/png"),
            ("gif", "image/gif"),
            ("webp", "image/webp"),
        ],
    )
    def test_known_extensions(self, ext: str, expected: str):
        assert mime_type_for_ext(ext) == expected

    def test_unknown_extension_falls_back_to_octet_stream(self):
        assert mime_type_for_ext("xyz") == "application/octet-stream"

    def test_empty_string_falls_back(self):
        assert mime_type_for_ext("") == "application/octet-stream"
```

- [ ] **Step 2: Run the test and confirm failure**

Run: `uv run pytest tests/unit/test_feeds_service.py::TestMimeTypeForExt -v`
Expected: ImportError — `mime_type_for_ext` not defined yet.

- [ ] **Step 3: Implement**

Add to `app/services/feeds.py`:

```python
"""Atom feed rendering and query helpers."""

_MIME_BY_EXT: dict[str, str] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}


def mime_type_for_ext(ext: str) -> str:
    """Map a file extension (case-insensitive, no leading dot) to its MIME type.

    Returns 'application/octet-stream' for unknown or empty extensions — Atom
    validators accept this and feed readers handle it gracefully.
    """
    return _MIME_BY_EXT.get(ext.lower(), "application/octet-stream")
```

- [ ] **Step 4: Run the tests and confirm pass**

Run: `uv run pytest tests/unit/test_feeds_service.py::TestMimeTypeForExt -v`
Expected: all 8 parametrize cases + 2 fallback cases PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/feeds.py tests/unit/test_feeds_service.py
git commit -m "feat(feeds): MIME type mapping for enclosure links"
```

---

### Task 4: Title composition helper

Implements the spec's "Title composition" section. Picks one representative tag per category by `usage_count DESC` and formats `"{characters} ({sources}) by {artists}"` with empty sections skipped.

**Files:**
- Modify: `app/services/feeds.py`
- Modify: `tests/unit/test_feeds_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_feeds_service.py`:

```python
from app.config import TagType
from app.services.feeds import compose_entry_title


def _tag(tag_id: int, title: str, type_: int, usage_count: int):
    """Lightweight stand-in for TagSummary — matches its attribute names."""
    from types import SimpleNamespace

    return SimpleNamespace(
        tag_id=tag_id,
        tag=title,      # TagSummary's alias for Tags.title
        type_id=type_,  # TagSummary's alias for Tags.type
        usage_count=usage_count,
    )


class TestComposeEntryTitle:
    def test_all_three_sections(self):
        tags = [
            _tag(1, "hatsune miku", TagType.CHARACTER, 500),
            _tag(2, "kagamine rin", TagType.CHARACTER, 100),
            _tag(3, "vocaloid", TagType.SOURCE, 1000),
            _tag(4, "cutesexyrobutts", TagType.ARTIST, 50),
        ]
        assert (
            compose_entry_title(image_id=42, tags=tags)
            == "hatsune miku (vocaloid) by cutesexyrobutts"
        )

    def test_no_character_tags(self):
        tags = [
            _tag(3, "vocaloid", TagType.SOURCE, 1000),
            _tag(4, "cutesexyrobutts", TagType.ARTIST, 50),
        ]
        assert (
            compose_entry_title(image_id=42, tags=tags)
            == "(vocaloid) by cutesexyrobutts"
        )

    def test_no_source_tags(self):
        tags = [
            _tag(1, "hatsune miku", TagType.CHARACTER, 500),
            _tag(4, "cutesexyrobutts", TagType.ARTIST, 50),
        ]
        assert (
            compose_entry_title(image_id=42, tags=tags)
            == "hatsune miku by cutesexyrobutts"
        )

    def test_no_artist_tags(self):
        tags = [
            _tag(1, "hatsune miku", TagType.CHARACTER, 500),
            _tag(3, "vocaloid", TagType.SOURCE, 1000),
        ]
        assert (
            compose_entry_title(image_id=42, tags=tags)
            == "hatsune miku (vocaloid)"
        )

    def test_no_relevant_tags_falls_back(self):
        tags = [_tag(5, "solo", TagType.THEME, 999)]
        assert compose_entry_title(image_id=42, tags=tags) == "Image #42"

    def test_no_tags_at_all_falls_back(self):
        assert compose_entry_title(image_id=42, tags=[]) == "Image #42"

    def test_picks_highest_usage_count_per_category(self):
        tags = [
            _tag(1, "low usage char", TagType.CHARACTER, 1),
            _tag(2, "high usage char", TagType.CHARACTER, 9999),
            _tag(3, "low usage artist", TagType.ARTIST, 1),
            _tag(4, "high usage artist", TagType.ARTIST, 9999),
        ]
        assert (
            compose_entry_title(image_id=42, tags=tags)
            == "high usage char by high usage artist"
        )
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `uv run pytest tests/unit/test_feeds_service.py::TestComposeEntryTitle -v`
Expected: ImportError — `compose_entry_title` not defined.

- [ ] **Step 3: Implement**

Append to `app/services/feeds.py`:

```python
from typing import Any

from app.config import TagType


def _pick_representative(tags: list[Any], type_value: int) -> str | None:
    """Return the title of the highest-usage tag of the given type, or None."""
    candidates = [t for t in tags if t.type_id == type_value]
    if not candidates:
        return None
    # Stable sort: usage_count DESC, then tag_id ASC for determinism on ties.
    candidates.sort(key=lambda t: (-t.usage_count, t.tag_id))
    return candidates[0].tag


def compose_entry_title(image_id: int, tags: list[Any]) -> str:
    """Build the Atom entry <title> per the design spec.

    Format: '{characters} ({sources}) by {artists}' — single representative tag
    per category (highest usage_count). Empty sections are skipped. Falls back
    to 'Image #{image_id}' if no character, source, or artist tags are present.

    `tags` is a sequence of TagSummary-shaped objects with `.tag` (title),
    `.type_id` (int, matching TagType constants), `.tag_id`, and `.usage_count`.
    """
    char = _pick_representative(tags, TagType.CHARACTER)
    src = _pick_representative(tags, TagType.SOURCE)
    artist = _pick_representative(tags, TagType.ARTIST)

    parts: list[str] = []
    if char:
        parts.append(char)
    if src:
        parts.append(f"({src})")
    if artist:
        parts.append(f"by {artist}")

    if not parts:
        return f"Image #{image_id}"
    return " ".join(parts)
```

- [ ] **Step 4: Run tests and confirm pass**

Run: `uv run pytest tests/unit/test_feeds_service.py::TestComposeEntryTitle -v`
Expected: all 7 cases PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/feeds.py tests/unit/test_feeds_service.py
git commit -m "feat(feeds): compose Atom entry titles from image tags"
```

---

### Task 5: ETag and Last-Modified helpers

Pure functions operating on a list of `(image_id, date_added)` tuples (the sentinel result). No DB access here.

**Files:**
- Modify: `app/services/feeds.py`
- Modify: `tests/unit/test_feeds_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_feeds_service.py`:

```python
from datetime import UTC, datetime

from app.services.feeds import compute_feed_etag, newest_timestamp


class TestComputeFeedEtag:
    def _sentinel(self):
        return [
            (100, datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC)),
            (99, datetime(2026, 4, 23, 12, 0, 0, tzinfo=UTC)),
        ]

    def test_returns_weak_etag(self):
        etag = compute_feed_etag(self._sentinel())
        assert etag.startswith('W/"')
        assert etag.endswith('"')

    def test_deterministic_for_same_input(self):
        s = self._sentinel()
        assert compute_feed_etag(s) == compute_feed_etag(s)

    def test_changes_when_image_id_changes(self):
        a = self._sentinel()
        b = self._sentinel()
        b[0] = (101, b[0][1])
        assert compute_feed_etag(a) != compute_feed_etag(b)

    def test_changes_when_timestamp_changes(self):
        a = self._sentinel()
        b = self._sentinel()
        b[0] = (b[0][0], datetime(2026, 4, 24, 13, 0, 0, tzinfo=UTC))
        assert compute_feed_etag(a) != compute_feed_etag(b)

    def test_empty_sentinel_still_returns_valid_etag(self):
        etag = compute_feed_etag([])
        assert etag.startswith('W/"') and etag.endswith('"')

    def test_ignores_rows_with_null_date(self):
        a = [(100, datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC))]
        b = [
            (100, datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC)),
            (99, None),  # NULL date_added — defensive handling per spec
        ]
        # Null-date rows are excluded from the hash, so the result matches `a`.
        assert compute_feed_etag(a) == compute_feed_etag(b)


class TestNewestTimestamp:
    def test_picks_newest_from_sentinel(self):
        sentinel = [
            (100, datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC)),
            (99, datetime(2026, 4, 23, 12, 0, 0, tzinfo=UTC)),
        ]
        assert newest_timestamp(sentinel) == datetime(
            2026, 4, 24, 12, 0, 0, tzinfo=UTC
        )

    def test_empty_returns_none(self):
        assert newest_timestamp([]) is None

    def test_skips_none_timestamps(self):
        sentinel = [
            (100, None),
            (99, datetime(2026, 4, 23, 12, 0, 0, tzinfo=UTC)),
        ]
        assert newest_timestamp(sentinel) == datetime(
            2026, 4, 23, 12, 0, 0, tzinfo=UTC
        )

    def test_all_none_returns_none(self):
        assert newest_timestamp([(100, None), (99, None)]) is None

    def test_result_is_floored_to_whole_seconds(self):
        # Per spec: HTTP-date has 1-second resolution; we floor microseconds.
        sentinel = [
            (100, datetime(2026, 4, 24, 12, 0, 0, 999_999, tzinfo=UTC)),
        ]
        result = newest_timestamp(sentinel)
        assert result is not None
        assert result.microsecond == 0
```

- [ ] **Step 2: Run the tests and confirm failure**

Run: `uv run pytest tests/unit/test_feeds_service.py::TestComputeFeedEtag tests/unit/test_feeds_service.py::TestNewestTimestamp -v`
Expected: ImportError on `compute_feed_etag` / `newest_timestamp`.

- [ ] **Step 3: Implement**

Append to `app/services/feeds.py`:

```python
import hashlib
from datetime import UTC, datetime


SentinelRow = tuple[int, datetime | None]


def compute_feed_etag(sentinel: list[SentinelRow]) -> str:
    """Derive a weak ETag from the sentinel query result.

    Rows with NULL date_added are excluded from the hash (defensive per spec).
    The hash is stable for identical input, which is all a conditional request
    needs — we regenerate it from current DB state on every request and never
    store it.
    """
    payload = ",".join(
        f"{image_id}:{ts.isoformat()}"
        for image_id, ts in sentinel
        if ts is not None
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
    return f'W/"{digest}"'


def newest_timestamp(sentinel: list[SentinelRow]) -> datetime | None:
    """Return the newest non-NULL date_added from the sentinel, floored to the second.

    Returns None if the sentinel is empty or every row has NULL date_added —
    callers should omit the Last-Modified header in that case.
    """
    non_null = [ts for _, ts in sentinel if ts is not None]
    if not non_null:
        return None
    return max(non_null).replace(microsecond=0)
```

- [ ] **Step 4: Run tests and confirm pass**

Run: `uv run pytest tests/unit/test_feeds_service.py -v`
Expected: all tests in the file pass (MIME + title + ETag/LastMod = ~25 cases).

- [ ] **Step 5: Commit**

```bash
git add app/services/feeds.py tests/unit/test_feeds_service.py
git commit -m "feat(feeds): ETag and Last-Modified derivation from sentinel query"
```

---

## Chunk 2: DB query helpers

Everything in this chunk touches the database. Tests here use the live test DB via `async_db_session` (see `tests/conftest.py`).

### Task 6: Sentinel query (global + per-tag)

Cheap query used for ETag/Last-Modified derivation and to short-circuit the hydration query on conditional hits.

**Files:**
- Modify: `app/services/feeds.py`
- Modify: `tests/api/v1/test_feeds.py` (create if absent) — *integration-style* tests that exercise the query against a real DB.

- [ ] **Step 1: Create `tests/api/v1/test_feeds.py` with sentinel tests**

```python
"""Integration tests for Atom feed endpoints and query helpers."""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, TagType
from app.models import Images, Tags, TagLinks, Users
from app.services.feeds import fetch_feed_sentinel


async def _make_user(db: AsyncSession, username: str = "feeder") -> Users:
    user = Users(
        username=username,
        password="x",
        password_type="bcrypt",
        salt="",
        email=f"{username}@example.com",
        active=1,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _make_image(
    db: AsyncSession,
    user: Users,
    filename: str,
    status: int = ImageStatus.ACTIVE,
) -> Images:
    image = Images(
        filename=filename,
        ext="png",
        status=status,
        user_id=user.user_id,
        filesize=1024,
        date_added=datetime.now(UTC),
    )
    db.add(image)
    await db.commit()
    await db.refresh(image)
    return image


async def _make_tag(
    db: AsyncSession, title: str, type_: int = TagType.THEME
) -> Tags:
    tag = Tags(title=title, type=type_, user_id=None)
    db.add(tag)
    await db.commit()
    await db.refresh(tag)
    return tag


async def _link(db: AsyncSession, image: Images, tag: Tags) -> None:
    db.add(TagLinks(image_id=image.image_id, tag_id=tag.tag_id))
    await db.commit()


class TestFetchFeedSentinelGlobal:
    async def test_returns_only_active_images_newest_first(
        self, db_session: AsyncSession
    ):
        user = await _make_user(db_session)
        active_a = await _make_image(db_session, user, "a")
        hidden = await _make_image(db_session, user, "h", status=ImageStatus.INAPPROPRIATE)
        active_b = await _make_image(db_session, user, "b")
        await _make_image(db_session, user, "c", status=ImageStatus.REVIEW)

        sentinel = await fetch_feed_sentinel(db_session, tag_ids=None, limit=50)

        ids = [row[0] for row in sentinel]
        assert active_b.image_id in ids
        assert active_a.image_id in ids
        assert hidden.image_id not in ids
        # Newest first: active_b was inserted last, so should come first.
        assert ids.index(active_b.image_id) < ids.index(active_a.image_id)

    async def test_respects_limit(self, db_session: AsyncSession):
        user = await _make_user(db_session, "limituser")
        for i in range(10):
            await _make_image(db_session, user, f"lim{i}")

        sentinel = await fetch_feed_sentinel(db_session, tag_ids=None, limit=3)
        assert len(sentinel) == 3


class TestFetchFeedSentinelPerTag:
    async def test_filters_by_tag_id(self, db_session: AsyncSession):
        user = await _make_user(db_session, "tagfilter")
        tag = await _make_tag(db_session, "filtertag")
        img_with = await _make_image(db_session, user, "with")
        img_without = await _make_image(db_session, user, "without")
        await _link(db_session, img_with, tag)

        sentinel = await fetch_feed_sentinel(
            db_session, tag_ids=[tag.tag_id], limit=50
        )

        ids = [row[0] for row in sentinel]
        assert img_with.image_id in ids
        assert img_without.image_id not in ids

    async def test_multiple_tag_ids_union(self, db_session: AsyncSession):
        """tag_ids represents the already-expanded hierarchy set; any match qualifies."""
        user = await _make_user(db_session, "multitag")
        t1 = await _make_tag(db_session, "t1")
        t2 = await _make_tag(db_session, "t2")
        img_a = await _make_image(db_session, user, "a_t1")
        img_b = await _make_image(db_session, user, "b_t2")
        await _link(db_session, img_a, t1)
        await _link(db_session, img_b, t2)

        sentinel = await fetch_feed_sentinel(
            db_session, tag_ids=[t1.tag_id, t2.tag_id], limit=50
        )

        ids = [row[0] for row in sentinel]
        assert img_a.image_id in ids
        assert img_b.image_id in ids

    async def test_empty_tag_list_returns_no_rows(self, db_session: AsyncSession):
        # Caller should never pass an empty list (should pass None for global),
        # but guard the helper anyway.
        sentinel = await fetch_feed_sentinel(db_session, tag_ids=[], limit=50)
        assert sentinel == []
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `uv run pytest tests/api/v1/test_feeds.py -v`
Expected: ImportError on `fetch_feed_sentinel`.

- [ ] **Step 3: Implement the sentinel query**

Append to `app/services/feeds.py`:

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus
from app.models.image import Images
from app.models.tag_link import TagLinks


async def fetch_feed_sentinel(
    db: AsyncSession,
    tag_ids: list[int] | None,
    limit: int = 50,
) -> list[SentinelRow]:
    """Return [(image_id, date_added), ...] for the feed window.

    Cheap query — indexed scan on (status, image_id DESC) only, no joins beyond
    an optional IN subquery for per-tag filtering. Used for ETag derivation and
    to short-circuit the full hydration query on conditional-request hits.

    Args:
        tag_ids: None for the global feed; a non-empty list (the already-resolved
            alias + hierarchy-expanded tag IDs) for per-tag. An empty list returns
            no rows.
    """
    if tag_ids == []:
        return []

    query = (
        select(Images.image_id, Images.date_added)
        .where(Images.status == ImageStatus.ACTIVE)
        .order_by(Images.image_id.desc())
        .limit(limit)
    )

    if tag_ids is not None:
        tag_subquery = (
            select(TagLinks.image_id)
            .where(TagLinks.tag_id.in_(tag_ids))
            .distinct()
            .subquery()
        )
        query = query.where(Images.image_id.in_(select(tag_subquery)))

    result = await db.execute(query)
    return [(row.image_id, row.date_added) for row in result]
```

- [ ] **Step 4: Run the tests and confirm pass**

Run: `uv run pytest tests/api/v1/test_feeds.py -v`
Expected: all sentinel tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/feeds.py tests/api/v1/test_feeds.py
git commit -m "feat(feeds): sentinel query for ETag/Last-Modified derivation"
```

---

### Task 7: Hydration query + schema conversion

Full query with `selectinload` for user and tag relationships, converted to `ImageDetailedResponse` via `from_db_model` (see spec for why `model_validate` won't work here).

**Files:**
- Modify: `app/services/feeds.py`
- Modify: `tests/api/v1/test_feeds.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/v1/test_feeds.py`:

```python
from app.services.feeds import fetch_feed_entries


class TestFetchFeedEntriesGlobal:
    async def test_returns_image_detailed_responses(self, db_session: AsyncSession):
        user = await _make_user(db_session, "hydrator")
        tag = await _make_tag(db_session, "hydrator_tag", type_=TagType.ARTIST)
        image = await _make_image(db_session, user, "h1")
        await _link(db_session, image, tag)

        entries = await fetch_feed_entries(db_session, tag_ids=None, limit=50)

        assert len(entries) >= 1
        entry = next(e for e in entries if e.image_id == image.image_id)
        assert entry.user is not None
        assert entry.user.username == "hydrator"
        assert entry.tags is not None
        assert any(t.tag == "hydrator_tag" for t in entry.tags)

    async def test_only_active_images(self, db_session: AsyncSession):
        user = await _make_user(db_session, "activeonly")
        active = await _make_image(db_session, user, "a")
        hidden = await _make_image(db_session, user, "h", status=ImageStatus.INAPPROPRIATE)

        entries = await fetch_feed_entries(db_session, tag_ids=None, limit=50)
        ids = [e.image_id for e in entries]
        assert active.image_id in ids
        assert hidden.image_id not in ids

    async def test_ordered_newest_first(self, db_session: AsyncSession):
        user = await _make_user(db_session, "orderer")
        first = await _make_image(db_session, user, "o1")
        second = await _make_image(db_session, user, "o2")

        entries = await fetch_feed_entries(db_session, tag_ids=None, limit=50)
        ids = [e.image_id for e in entries]
        assert ids.index(second.image_id) < ids.index(first.image_id)
```

- [ ] **Step 2: Run the tests and confirm failure**

Run: `uv run pytest tests/api/v1/test_feeds.py::TestFetchFeedEntriesGlobal -v`
Expected: ImportError on `fetch_feed_entries`.

- [ ] **Step 3: Implement**

Append to `app/services/feeds.py`:

```python
from sqlalchemy.orm import selectinload

from app.schemas.image import ImageDetailedResponse


async def fetch_feed_entries(
    db: AsyncSession,
    tag_ids: list[int] | None,
    limit: int = 50,
) -> list[ImageDetailedResponse]:
    """Full hydration query for feed rendering.

    Eager-loads the uploader and every linked tag (plus the tag row itself for
    title/type). Converts results via ImageDetailedResponse.from_db_model, which
    handles the tag_links -> tags mapping that from_attributes cannot do.
    """
    if tag_ids == []:
        return []

    query = (
        select(Images)
        .options(
            selectinload(Images.user),
            selectinload(Images.tag_links).selectinload(TagLinks.tag),
        )
        .where(Images.status == ImageStatus.ACTIVE)
        .order_by(Images.image_id.desc())
        .limit(limit)
    )

    if tag_ids is not None:
        tag_subquery = (
            select(TagLinks.image_id)
            .where(TagLinks.tag_id.in_(tag_ids))
            .distinct()
            .subquery()
        )
        query = query.where(Images.image_id.in_(select(tag_subquery)))

    result = await db.execute(query)
    images = result.scalars().all()
    return [ImageDetailedResponse.from_db_model(image) for image in images]
```

- [ ] **Step 4: Run tests and confirm pass**

Run: `uv run pytest tests/api/v1/test_feeds.py -v`
Expected: all sentinel + hydration tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/feeds.py tests/api/v1/test_feeds.py
git commit -m "feat(feeds): hydration query returning ImageDetailedResponse list"
```

---

## Chunk 3: Atom rendering + HTTP handlers

Renders the feed XML and exposes the two routes with full conditional-request handling.

### Task 8: Atom feed builder

Uses `feedgenerator.Atom1Feed`. Takes a pre-computed `FeedMeta` dataclass (feed-level values) and the list of `ImageDetailedResponse` entries.

**Files:**
- Modify: `app/services/feeds.py`
- Modify: `tests/unit/test_feeds_service.py`
- Modify: `tests/api/v1/test_feeds.py`

- [ ] **Step 1: Write unit tests for builder invariants**

Append to `tests/unit/test_feeds_service.py`:

```python
from datetime import UTC, datetime
import xml.etree.ElementTree as ET

from app.services.feeds import FeedMeta, build_atom_feed


ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _feed_meta() -> "FeedMeta":
    return FeedMeta(
        feed_id="tag:e-shuushuu.net,2005:feed:images",
        title="Shuushuu — latest images",
        self_url="https://e-shuushuu.net/api/v1/images.atom",
        alternate_url="https://e-shuushuu.net/",
        updated=datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC),
    )


class TestBuildAtomFeedEmpty:
    def test_empty_feed_is_valid_atom(self):
        xml = build_atom_feed(_feed_meta(), entries=[])
        root = ET.fromstring(xml)
        assert root.tag == f"{ATOM_NS}feed"

    def test_empty_feed_has_id_title_self_link_updated(self):
        xml = build_atom_feed(_feed_meta(), entries=[])
        root = ET.fromstring(xml)
        assert root.find(f"{ATOM_NS}id").text == (
            "tag:e-shuushuu.net,2005:feed:images"
        )
        assert root.find(f"{ATOM_NS}title").text == "Shuushuu — latest images"
        self_link = root.find(f"{ATOM_NS}link[@rel='self']")
        assert self_link is not None
        assert self_link.get("href") == (
            "https://e-shuushuu.net/api/v1/images.atom"
        )
        assert root.find(f"{ATOM_NS}updated") is not None

    def test_empty_feed_has_no_entries(self):
        xml = build_atom_feed(_feed_meta(), entries=[])
        root = ET.fromstring(xml)
        assert root.findall(f"{ATOM_NS}entry") == []
```

- [ ] **Step 2: Run the tests and confirm failure**

Run: `uv run pytest tests/unit/test_feeds_service.py -v -k "BuildAtomFeedEmpty"`
Expected: ImportError on `FeedMeta` / `build_atom_feed`.

- [ ] **Step 3: Implement the builder**

**API context (verified against feedgenerator 2.2.1 source):**
- `add_item(...)` accepts `description` (emits `<summary type="html">`) **and** `content` (emits `<content type="html">`) as separate kwargs. We want `<content>`, not `<summary>`, so pass `content=...` and `description=None`.
- `enclosures` kwarg iterates with attribute access (`enclosure.url`, `enclosure.length`, `enclosure.mime_type`). Must be a list of `Enclosure` instances, not dicts.
- `categories` kwarg is stringified via `str(c)` and emitted as `<category term="...">` with no `scheme`. To get `<category term="..." scheme="...">` per our design spec, we subclass `Atom1Feed` and override `add_item_elements`. Extra item-level data rides through `add_item`'s `**kwargs` and is stored in the item dict.
- `feed.feed["id"]`, `feed.feed["author_name"]` are read by `add_root_elements` directly — setting them on the feed dict works. `feed.feed["updated"]` is **not** read; `<updated>` is derived from `self.latest_post_date()` (newest `pubdate`/`updateddate` across items; falls back to `datetime.now()` on empty feeds, which matches our spec for the empty-feed case).

Append to `app/services/feeds.py`:

```python
from dataclasses import dataclass
from html import escape

from feedgenerator import Atom1Feed, Enclosure  # type: ignore[import-untyped]

from app.config import TagType, settings

TAG_TYPE_NAME: dict[int, str] = {
    TagType.THEME: "Theme",
    TagType.SOURCE: "Source",
    TagType.ARTIST: "Artist",
    TagType.CHARACTER: "Character",
    # TagType.ALL is a filter pseudo-type; never on actual rows.
}


class _ShuushuuAtom1Feed(Atom1Feed):
    """Atom1Feed variant that emits <category term=... scheme=...> per RFC 4287.

    The stock Atom1Feed.add_item_elements drops the scheme attribute, so we
    append our own category elements from a 'shuu_categories' item attribute
    (list of (term, scheme) tuples) passed through add_item's **kwargs.
    """

    def add_item_elements(self, handler, item):  # type: ignore[no-untyped-def]
        super().add_item_elements(handler, item)
        for term, scheme in item.get("shuu_categories", ()):
            handler.addQuickElement(
                "category", "", {"term": term, "scheme": scheme}
            )


@dataclass(frozen=True)
class FeedMeta:
    feed_id: str
    title: str
    self_url: str
    alternate_url: str


def _category_scheme(tag_type_id: int) -> str:
    return (
        f"{settings.FRONTEND_URL.rstrip('/')}/tag-type/"
        f"{TAG_TYPE_NAME.get(tag_type_id, 'Unknown')}"
    )


def build_atom_feed(
    meta: FeedMeta,
    entries: list[ImageDetailedResponse],
) -> str:
    """Render an Atom 1.0 XML document.

    Uses our _ShuushuuAtom1Feed subclass to emit scheme-carrying <category>
    elements. Item <content type="html"> comes from the image caption;
    enclosure links use ImageResponse.url (CDN-aware).
    """
    feed = _ShuushuuAtom1Feed(
        title=meta.title,
        link=meta.alternate_url,
        description="",  # empty → no <subtitle>
        feed_url=meta.self_url,
        language="en",
    )
    # Override <id> and feed-level <author>. <updated> is auto-derived.
    feed.feed["id"] = meta.feed_id
    feed.feed["author_name"] = "Shuushuu"

    frontend = settings.FRONTEND_URL.rstrip("/")

    for image in entries:
        author_name = (
            image.user.username
            if image.user and image.user.username
            else "[deleted user]"
        )
        entry_dt = image.date_added or datetime.now(UTC)

        content_html: str | None = escape(image.caption) if image.caption else None

        shuu_categories = [
            (t.tag, _category_scheme(t.type_id)) for t in (image.tags or [])
        ]

        enclosure = Enclosure(
            url=image.url,  # CDN-aware computed field on ImageResponse.
            length=str(image.filesize or 0),
            mime_type=mime_type_for_ext(image.ext),
        )

        feed.add_item(
            title=compose_entry_title(
                image_id=image.image_id, tags=image.tags or []
            ),
            link=f"{frontend}/images/{image.image_id}",
            description=None,  # skip <summary>; we emit <content> instead.
            content=content_html,
            unique_id=f"tag:e-shuushuu.net,2005:image:{image.image_id}",
            unique_id_is_permalink=False,
            pubdate=entry_dt,
            updateddate=entry_dt,
            author_name=author_name,
            enclosures=[enclosure],
            shuu_categories=shuu_categories,  # via **kwargs → item dict
        )

    return feed.writeString("utf-8")
```

- [ ] **Step 4: Run the tests and confirm pass**

Run: `uv run pytest tests/unit/test_feeds_service.py -v -k "BuildAtomFeedEmpty"`
Expected: all empty-feed tests pass. If enclosure-related attribute errors appear at this step, adjust the enclosure-passing shape — the empty-feed test has no entries so won't hit that path yet.

- [ ] **Step 5: Add tests for a non-empty feed**

Rather than construct `ImageDetailedResponse` by hand (brittle — lots of required fields), build a minimal `Images` ORM instance with eager-loaded relationships set directly on the Python object, and funnel it through `from_db_model`. This exercises the exact code path the handler uses.

Append to `tests/unit/test_feeds_service.py`:

```python
from types import SimpleNamespace

from app.models.image import Images
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.user import Users
from app.schemas.image import ImageDetailedResponse


def _orm_image(
    image_id: int = 42,
    filename: str = "abc42",
    ext: str = "png",
    caption: str | None = None,
    filesize: int = 1024,
    date_added: datetime | None = None,
    username: str | None = "alice",
    tags: list[Tags] | None = None,
) -> Images:
    """Build an Images row with eager-loaded .user and .tag_links set in memory.

    The schema's from_db_model reads image.user and iterates image.tag_links,
    so we populate both directly. Returned object is ready for
    ImageDetailedResponse.from_db_model(image).
    """
    user = None
    if username is not None:
        user = Users(
            user_id=1,
            username=username,
            password="x",
            password_type="bcrypt",
            salt="",
            email="a@b.c",
            active=1,
        )

    img = Images(
        image_id=image_id,
        filename=filename,
        ext=ext,
        caption=caption,
        filesize=filesize,
        user_id=1 if username else None,
        status=1,
        date_added=date_added or datetime(2026, 4, 24, 10, 0, 0, tzinfo=UTC),
    )
    # Directly attach relationships — avoids a DB round-trip for unit tests.
    img.user = user  # type: ignore[assignment]
    img.tag_links = [  # type: ignore[assignment]
        SimpleNamespace(tag=t) for t in (tags or [])
    ]
    return img


def _entry(**overrides) -> ImageDetailedResponse:
    return ImageDetailedResponse.from_db_model(_orm_image(**overrides))


class TestBuildAtomFeedWithEntries:
    def test_entry_has_tag_uri_id(self):
        xml = build_atom_feed(_feed_meta(), entries=[_entry(image_id=42)])
        root = ET.fromstring(xml)
        entry_node = root.find(f"{ATOM_NS}entry")
        assert entry_node.find(f"{ATOM_NS}id").text == (
            "tag:e-shuushuu.net,2005:image:42"
        )

    def test_entry_alternate_link_points_to_detail_page(self):
        xml = build_atom_feed(_feed_meta(), entries=[_entry(image_id=42)])
        root = ET.fromstring(xml)
        alt = root.find(f"{ATOM_NS}entry/{ATOM_NS}link[@rel='alternate']")
        assert alt is not None
        assert alt.get("href", "").endswith("/images/42")

    def test_entry_enclosure_has_mime_and_length(self):
        xml = build_atom_feed(
            _feed_meta(), entries=[_entry(image_id=42, ext="png", filesize=1024)]
        )
        root = ET.fromstring(xml)
        enc = root.find(f"{ATOM_NS}entry/{ATOM_NS}link[@rel='enclosure']")
        assert enc is not None
        assert enc.get("type") == "image/png"
        assert enc.get("length") == "1024"

    def test_entry_author_is_uploader(self):
        xml = build_atom_feed(
            _feed_meta(), entries=[_entry(username="alice")]
        )
        root = ET.fromstring(xml)
        assert (
            root.find(
                f"{ATOM_NS}entry/{ATOM_NS}author/{ATOM_NS}name"
            ).text
            == "alice"
        )

    def test_entry_author_falls_back_for_deleted_uploader(self):
        xml = build_atom_feed(
            _feed_meta(), entries=[_entry(username=None)]
        )
        root = ET.fromstring(xml)
        assert (
            root.find(
                f"{ATOM_NS}entry/{ATOM_NS}author/{ATOM_NS}name"
            ).text
            == "[deleted user]"
        )

    def test_entry_content_is_html_escaped_caption(self):
        xml = build_atom_feed(
            _feed_meta(),
            entries=[_entry(caption="a <b> caption & stuff")],
        )
        root = ET.fromstring(xml)
        content = root.find(f"{ATOM_NS}entry/{ATOM_NS}content")
        assert content is not None
        assert content.get("type") == "html"
        # Two escape layers apply: html.escape (our code, so <b> is treated
        # as plain text inside type="html") + XML-attribute escape (library).
        # ET.fromstring reverses only the outer XML layer, so .text still
        # shows the HTML-escaped form.
        assert content.text == "a &lt;b&gt; caption &amp; stuff"

    def test_entry_with_no_caption_omits_content(self):
        xml = build_atom_feed(_feed_meta(), entries=[_entry(caption=None)])
        root = ET.fromstring(xml)
        assert root.find(f"{ATOM_NS}entry/{ATOM_NS}content") is None

    def test_entry_categories_carry_scheme(self):
        tags = [
            Tags(tag_id=1, title="hatsune miku", type=TagType.CHARACTER, usage_count=500),
            Tags(tag_id=2, title="vocaloid", type=TagType.SOURCE, usage_count=1000),
        ]
        xml = build_atom_feed(_feed_meta(), entries=[_entry(tags=tags)])
        root = ET.fromstring(xml)
        cats = root.findall(f"{ATOM_NS}entry/{ATOM_NS}category")
        assert len(cats) == 2
        by_term = {c.get("term"): c.get("scheme") for c in cats}
        assert by_term["hatsune miku"].endswith("/tag-type/Character")
        assert by_term["vocaloid"].endswith("/tag-type/Source")
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/unit/test_feeds_service.py::TestBuildAtomFeedWithEntries -v`
Expected: all 8 tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/services/feeds.py tests/unit/test_feeds_service.py
git commit -m "feat(feeds): Atom XML builder using feedgenerator"
```

---

### Task 9: Global feed handler

Route: `GET /api/v1/images.atom`. Includes full conditional-request handling.

**Files:**
- Modify: `app/api/v1/feeds.py`
- Modify: `tests/api/v1/test_feeds.py`

- [ ] **Step 1: Write the handler tests**

Append to `tests/api/v1/test_feeds.py`:

```python
class TestGlobalImagesFeed:
    async def test_returns_atom_content_type(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user = await _make_user(db_session, "ctuser")
        await _make_image(db_session, user, "ct1")

        response = await client.get("/api/v1/images.atom")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith(
            "application/atom+xml"
        )

    async def test_includes_only_active_images(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user = await _make_user(db_session, "actuser")
        active = await _make_image(db_session, user, "a1")
        hidden = await _make_image(
            db_session, user, "h1", status=ImageStatus.INAPPROPRIATE
        )

        response = await client.get("/api/v1/images.atom")

        body = response.text
        assert f"image:{active.image_id}" in body
        assert f"image:{hidden.image_id}" not in body

    async def test_caps_at_50_entries(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user = await _make_user(db_session, "capuser")
        for i in range(55):
            await _make_image(db_session, user, f"cap{i}")

        response = await client.get("/api/v1/images.atom")
        assert response.status_code == 200
        assert response.text.count("<entry") <= 50

    async def test_sets_cache_control_header(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user = await _make_user(db_session, "cacheuser")
        await _make_image(db_session, user, "c1")

        response = await client.get("/api/v1/images.atom")

        assert "max-age=300" in response.headers["cache-control"]

    async def test_sets_etag_and_last_modified(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user = await _make_user(db_session, "etaguser")
        await _make_image(db_session, user, "e1")

        response = await client.get("/api/v1/images.atom")

        assert response.headers.get("etag", "").startswith('W/"')
        assert "last-modified" in response.headers

    async def test_conditional_if_none_match_returns_304(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user = await _make_user(db_session, "condetag")
        await _make_image(db_session, user, "ce1")

        first = await client.get("/api/v1/images.atom")
        assert first.status_code == 200
        etag = first.headers["etag"]

        second = await client.get(
            "/api/v1/images.atom", headers={"If-None-Match": etag}
        )
        assert second.status_code == 304
        assert second.text == ""

    async def test_new_image_busts_etag(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user = await _make_user(db_session, "bustetag")
        await _make_image(db_session, user, "be1")

        first = await client.get("/api/v1/images.atom")
        first_etag = first.headers["etag"]

        await _make_image(db_session, user, "be2")

        second = await client.get(
            "/api/v1/images.atom", headers={"If-None-Match": first_etag}
        )
        assert second.status_code == 200
        assert second.headers["etag"] != first_etag

    async def test_if_modified_since_returns_304(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user = await _make_user(db_session, "imsuser")
        await _make_image(db_session, user, "ims1")

        first = await client.get("/api/v1/images.atom")
        assert first.status_code == 200
        last_mod = first.headers["last-modified"]

        second = await client.get(
            "/api/v1/images.atom",
            headers={"If-Modified-Since": last_mod},
        )
        assert second.status_code == 304

    async def test_empty_feed_is_200(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        # Note: db_session may share rows across tests; we only assert feed
        # validity and 200 status, not that there are zero entries.
        response = await client.get("/api/v1/images.atom")

        assert response.status_code == 200
        assert "<feed" in response.text
```

- [ ] **Step 2: Run the tests and confirm failure**

Run: `uv run pytest tests/api/v1/test_feeds.py::TestGlobalImagesFeed -v`
Expected: 404 on every request — handler not registered yet.

- [ ] **Step 3: Implement the handler**

Replace `app/api/v1/feeds.py` with:

```python
"""Atom feed endpoints."""

from datetime import datetime
from email.utils import format_datetime, parsedate_to_datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.tags import get_tag_hierarchy, resolve_tag_alias
from app.config import settings
from app.core.database import get_db
from app.services.feeds import (
    FeedMeta,
    build_atom_feed,
    compute_feed_etag,
    fetch_feed_entries,
    fetch_feed_sentinel,
    newest_timestamp,
)

router = APIRouter(tags=["feeds"])

CACHE_CONTROL = "public, max-age=300"
ATOM_CONTENT_TYPE = "application/atom+xml; charset=utf-8"


def _frontend(*parts: str) -> str:
    base = settings.FRONTEND_URL.rstrip("/")
    return "/".join([base, *parts])


def _self_url(request: Request) -> str:
    """Absolute URL of the current request — used for feed <link rel='self'>."""
    return str(request.url).split("?")[0]


def _is_not_modified(
    request: Request, etag: str, last_mod: datetime | None
) -> bool:
    """Conditional-request evaluation.

    A request is 'not modified' if either:
    - Its If-None-Match matches our ETag (exact or '*'), or
    - We know a Last-Modified and the request's If-Modified-Since is at/after it.

    Both comparisons are best-effort: we accept weak-ETag equality and ignore
    list/quote edge cases. Feed readers send simple values in practice.
    """
    inm = request.headers.get("if-none-match", "").strip()
    if inm == "*" or (inm and inm == etag):
        return True

    if last_mod is not None:
        ims = request.headers.get("if-modified-since")
        if ims:
            try:
                ims_dt = parsedate_to_datetime(ims)
            except (TypeError, ValueError):
                ims_dt = None
            if ims_dt is not None and ims_dt >= last_mod:
                return True

    return False


def _cacheable_headers(etag: str, last_mod: datetime | None) -> dict[str, str]:
    headers = {"Cache-Control": CACHE_CONTROL, "ETag": etag}
    if last_mod is not None:
        headers["Last-Modified"] = format_datetime(last_mod, usegmt=True)
    return headers


@router.get("/images.atom")
async def list_images_atom(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Latest 50 active images, newest first."""
    sentinel = await fetch_feed_sentinel(db, tag_ids=None, limit=50)
    etag = compute_feed_etag(sentinel)
    last_mod = newest_timestamp(sentinel)
    headers = _cacheable_headers(etag, last_mod)

    if _is_not_modified(request, etag, last_mod):
        return Response(status_code=304, headers=headers)

    entries = await fetch_feed_entries(db, tag_ids=None, limit=50)
    meta = FeedMeta(
        feed_id="tag:e-shuushuu.net,2005:feed:images",
        title="Shuushuu — latest images",
        self_url=_self_url(request),
        alternate_url=_frontend(),
    )
    xml = build_atom_feed(meta, entries)

    return Response(content=xml, media_type=ATOM_CONTENT_TYPE, headers=headers)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/api/v1/test_feeds.py::TestGlobalImagesFeed -v`
Expected: all 8 tests pass. If conditional-request tests fail, verify `request.headers.get("if-none-match")` returns the exact same string we handed out (case-insensitive header match is FastAPI/Starlette default).

- [ ] **Step 5: Verify manually with a real request**

Run: `uv run uvicorn app.main:app --port 8000 --reload` (in one terminal)
In another: `curl -iD- http://localhost:8000/api/v1/images.atom | head -40`
Expected: `200 OK`, `Content-Type: application/atom+xml; charset=utf-8`, valid XML body.
Kill uvicorn when done.

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/feeds.py tests/api/v1/test_feeds.py
git commit -m "feat(feeds): global images Atom feed handler with conditional requests"
```

---

### Task 10: Per-tag feed handler

Route: `GET /api/v1/tags/{tag_id}/images.atom`. Reuses `resolve_tag_alias()` and `get_tag_hierarchy()` from the tags module.

**Files:**
- Modify: `app/api/v1/feeds.py`
- Modify: `tests/api/v1/test_feeds.py`

- [ ] **Step 1: Write the handler tests**

Append to `tests/api/v1/test_feeds.py`:

```python
class TestPerTagImagesFeed:
    async def test_returns_only_images_with_tag(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user = await _make_user(db_session, "ptuser")
        tag = await _make_tag(db_session, "pt_tag")
        with_tag = await _make_image(db_session, user, "pt_w")
        without_tag = await _make_image(db_session, user, "pt_wo")
        await _link(db_session, with_tag, tag)

        response = await client.get(f"/api/v1/tags/{tag.tag_id}/images.atom")

        assert response.status_code == 200
        body = response.text
        assert f"image:{with_tag.image_id}" in body
        assert f"image:{without_tag.image_id}" not in body

    async def test_unknown_tag_id_returns_404(self, client: AsyncClient):
        response = await client.get("/api/v1/tags/999999999/images.atom")
        assert response.status_code == 404

    async def test_alias_tag_serves_canonical_image_set(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """An alias tag should return the same images as its canonical target."""
        user = await _make_user(db_session, "aliasuser")
        canonical = await _make_tag(db_session, "canonical_tag")
        # Build alias row by setting alias_of.
        alias = Tags(
            title="alias_tag",
            type=TagType.THEME,
            user_id=None,
            alias_of=canonical.tag_id,
        )
        db_session.add(alias)
        await db_session.commit()
        await db_session.refresh(alias)

        image = await _make_image(db_session, user, "aliasimg")
        await _link(db_session, image, canonical)

        # Fetch via alias ID — should still surface the image linked to canonical.
        response = await client.get(f"/api/v1/tags/{alias.tag_id}/images.atom")

        assert response.status_code == 200
        assert f"image:{image.image_id}" in response.text

    async def test_conditional_request_returns_304(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user = await _make_user(db_session, "pt_etag_user")
        tag = await _make_tag(db_session, "pt_etag_tag")
        image = await _make_image(db_session, user, "pt_e1")
        await _link(db_session, image, tag)

        first = await client.get(f"/api/v1/tags/{tag.tag_id}/images.atom")
        assert first.status_code == 200
        etag = first.headers["etag"]

        second = await client.get(
            f"/api/v1/tags/{tag.tag_id}/images.atom",
            headers={"If-None-Match": etag},
        )
        assert second.status_code == 304
```

- [ ] **Step 2: Run the tests and confirm failure**

Run: `uv run pytest tests/api/v1/test_feeds.py::TestPerTagImagesFeed -v`
Expected: 404 on all (handler not registered yet).

- [ ] **Step 3: Implement the per-tag handler**

Add to `app/api/v1/feeds.py` immediately below the global handler (imports are already at the top of the file from Task 9):

```python
@router.get("/tags/{tag_id}/images.atom")
async def list_tag_images_atom(
    request: Request,
    tag_id: Annotated[int, Path(ge=1)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Latest 50 active images tagged with `tag_id`.

    Resolves aliases and expands the tag hierarchy, matching the behavior of
    GET /api/v1/tags/{id}/images so readers see the same image set as the
    frontend tag page.
    """
    tag, resolved_id = await resolve_tag_alias(db, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")

    effective_ids = await get_tag_hierarchy(db, resolved_id)

    sentinel = await fetch_feed_sentinel(db, tag_ids=effective_ids, limit=50)
    etag = compute_feed_etag(sentinel)
    last_mod = newest_timestamp(sentinel)
    headers = _cacheable_headers(etag, last_mod)

    if _is_not_modified(request, etag, last_mod):
        return Response(status_code=304, headers=headers)

    entries = await fetch_feed_entries(db, tag_ids=effective_ids, limit=50)
    meta = FeedMeta(
        feed_id=f"tag:e-shuushuu.net,2005:feed:tags:{resolved_id}",
        title=f"Shuushuu — tag: {tag.title}",
        self_url=_self_url(request),
        alternate_url=_frontend("tags", str(resolved_id)),
    )
    xml = build_atom_feed(meta, entries)

    return Response(content=xml, media_type=ATOM_CONTENT_TYPE, headers=headers)
```

**Verify `resolve_tag_alias`'s exact return contract before running.** It's defined at `app/api/v1/tags.py:167` and returns `tuple[Tags | None, int]` per existing call sites (e.g. `app/api/v1/tags.py:695`). `tag is None` on unknown id → our 404 branch fires. If the behavior differs in this repo's version, adjust the branch.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/api/v1/test_feeds.py::TestPerTagImagesFeed -v`
Expected: 4 tests pass. If the alias test fails, inspect `resolve_tag_alias` to confirm it follows alias chains and adjust test data accordingly.

- [ ] **Step 5: Run the full feeds test suite for regressions**

Run: `uv run pytest tests/api/v1/test_feeds.py tests/unit/test_feeds_service.py -v`
Expected: every feeds test passes.

- [ ] **Step 6: Run the full project test suite once**

Run: `uv run pytest --tb=short`
Expected: green. Any failure in a non-feeds test is a regression to fix before committing.

- [ ] **Step 7: Manual smoke test**

Run uvicorn as in Task 9, then:
```bash
curl -sD- http://localhost:8000/api/v1/tags/1/images.atom | head -40   # valid feed or 404
curl -si http://localhost:8000/api/v1/tags/999999999/images.atom | head -5  # 404
```

Kill uvicorn.

- [ ] **Step 8: Commit**

```bash
git add app/api/v1/feeds.py tests/api/v1/test_feeds.py
git commit -m "feat(feeds): per-tag images Atom feed with alias and hierarchy support"
```

---

## Wrap-up

- [ ] **Verify no TODO/FIXME/`pass` stubs left in new files**

Run: `grep -rnE "TODO|FIXME|^\s*pass\s*$" app/services/feeds.py app/api/v1/feeds.py`
Expected: no output.

- [ ] **Check formatting and lint**

Run: `uv run ruff check app/services/feeds.py app/api/v1/feeds.py tests/unit/test_feeds_service.py tests/api/v1/test_feeds.py`
Expected: no violations.

Run: `uv run ruff format --check app/services/feeds.py app/api/v1/feeds.py tests/unit/test_feeds_service.py tests/api/v1/test_feeds.py`
Expected: all files formatted.

- [ ] **Type-check**

Run: `uv run mypy app/services/feeds.py app/api/v1/feeds.py`
Expected: no errors. If feedgenerator lacks type stubs, the `# type: ignore[import-untyped]` on its import is sufficient.

- [ ] **Open PR**

```bash
git push -u origin HEAD
gh pr create --title "feat: Atom feeds for images and tags" --body-file docs/plans/2026-04-24-rss-feed-design.md
```

---

## Out of scope (deferred to later tasks)

- Per-user / authenticated feeds (`/me/favorites.atom`).
- Redis cache on the rendered XML.
- Frontend HTML `<link rel="alternate" type="application/atom+xml">` tags — frontend's concern.
- Retag-in-window cache busting — accepted 5-minute staleness per design doc.
