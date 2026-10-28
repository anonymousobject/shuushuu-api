# Bundled Feed Comments (API) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `GET /api/v1/images/` gains an opt-in `include_comments` param that returns all comments for the returned page's images, grouped by image id, so the frontend can drop its serial second fetch.

**Architecture:** A new service module `app/services/comments.py` owns the batched comment query and the comment-author eager-load chain (currently copy-pasted four times in `app/api/v1/comments.py`). The endpoint calls the service when the param is set and attaches the result as a new optional field on `ImageDetailedListResponse`. Existing `/comments` endpoints keep their own query paths.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy (async), Pydantic v2, pytest (real MariaDB, no mocks).

**Spec:** `docs/plans/2026-Q3/2026-08-17-bundled-feed-comments-design.md` (this repo). Frontend counterpart plan: `../shuushuu-frontend/docs/plans/2026-Q3/2026-08-17-bundled-feed-comments-impl.md`.

## Global Constraints

- Branch: `feat/bundled-feed-comments` (already exists; the design doc is its first commit).
- Never `git add -A` in this repo — stage explicit paths only.
- Smallest reasonable change; match surrounding style, including the existing `# type: ignore[...]` / `# noqa: E712` comment idioms.
- Test through the real API and DB (repo rule: no mocks, no testing of mocked behavior).
- Run tests with `uv run pytest …`. Pre-commit runs ruff on staged app code automatically.
- The param defaults to off; when off, the response must be byte-for-byte what it is today except for a `"comments": null` field. Do NOT add `response_model_exclude_none` to the route — that would strip legitimately-null fields elsewhere in the response.

---

### Task 1: Extract the comment-author eager-load chain (pure refactor)

The chain `selectinload(Comments.user).selectinload(Users.user_groups).selectinload(UserGroups.group)` appears four times in `app/api/v1/comments.py` (in `list_comments`, `get_comment`, `get_image_comments`, `get_user_comments`). Move it to one helper that Task 2's service also uses. No behavior change; existing tests prove it.

**Files:**
- Create: `app/services/comments.py`
- Modify: `app/api/v1/comments.py` (4 `.options(...)` sites + imports)

**Interfaces:**
- Produces: `comment_user_eager_load() -> _AbstractLoad` in `app/services/comments.py` — returns the selectinload chain for a comment's author. Task 2 consumes it.

- [ ] **Step 1: Confirm the current tests pass (baseline)**

Run: `uv run pytest tests/api/v1/test_comments.py -q`
Expected: PASS (all green). If not, STOP — do not refactor on a broken baseline; raise the failure.

- [ ] **Step 2: Create the service module with the helper**

Create `app/services/comments.py`:

```python
"""
Comment query services shared by the comments and images endpoints.
"""

from sqlalchemy.orm import selectinload
from sqlalchemy.orm.strategy_options import _AbstractLoad

from app.models import Comments, Users
from app.models.permissions import UserGroups


def comment_user_eager_load() -> _AbstractLoad:
    """Eager-load chain for a comment's author (user -> user_groups -> group)."""
    return (
        selectinload(Comments.user)  # type: ignore[arg-type]
        .selectinload(Users.user_groups)  # type: ignore[arg-type]
        .selectinload(UserGroups.group)  # type: ignore[arg-type]
    )
```

If mypy rejects the `_AbstractLoad` import or return type (Step 4 checks), fall back to annotating the return as `Load` from `sqlalchemy.orm`, and if that also fails, drop the annotation — the four call sites are the contract, not the annotation.

- [ ] **Step 3: Replace the four inline chains in `app/api/v1/comments.py`**

Add to the imports block (it already imports from `app.models` and `app.schemas.comment`):

```python
from app.services.comments import comment_user_eager_load
```

Replace each of the four occurrences of

```python
        selectinload(Comments.user)  # type: ignore[arg-type]
        .selectinload(Users.user_groups)  # type: ignore[arg-type]
        .selectinload(UserGroups.group)  # type: ignore[arg-type]
```

(inside `query.options(...)` in `list_comments` ~line 158, `get_comment` ~line 188, `get_image_comments` ~line 249, `get_user_comments` ~line 311) with `comment_user_eager_load()`, e.g.:

```python
    query = query.options(comment_user_eager_load())
```

Then remove the now-unused `selectinload` import from `app/api/v1/comments.py` **only if** nothing else in the file uses it (check with grep before deleting; ruff will also flag it).

- [ ] **Step 4: Verify — tests, lint, types**

Run: `uv run pytest tests/api/v1/test_comments.py -q`
Expected: PASS, same tests as Step 1.

Run: `uv run ruff check app/services/comments.py app/api/v1/comments.py && uv run mypy app/services/comments.py app/api/v1/comments.py`
Expected: clean. Fix per Step 2's fallback note if mypy objects to the annotation.

- [ ] **Step 5: Commit**

```bash
git add app/services/comments.py app/api/v1/comments.py
git commit -m "refactor(comments): extract the comment-author eager-load chain

The selectinload chain user -> user_groups -> group was copy-pasted
four times in the comments endpoints. One helper in the new
app/services/comments.py now owns it; the bundled-feed-comments
service will be its fifth caller."
```

---

### Task 2: `include_comments` param, service query, and response field (TDD)

**Files:**
- Create: `tests/api/v1/test_images_include_comments.py`
- Modify: `app/services/comments.py` (add the service function)
- Modify: `app/schemas/image.py:294-300` (`ImageDetailedListResponse`)
- Modify: `app/api/v1/images.py` (`list_images` signature ~line 404-514 and response construction ~line 1085-1091)

**Interfaces:**
- Consumes: `comment_user_eager_load()` from Task 1.
- Produces:
  - `async def comments_for_images(db: AsyncSession, image_ids: Sequence[int]) -> dict[int, list[CommentResponse]]` in `app/services/comments.py`.
  - `ImageDetailedListResponse.comments: dict[int, list[CommentResponse]] | None = None`.
  - Query param `include_comments: bool = False` on `GET /api/v1/images/`. The frontend plan consumes all three via the regenerated OpenAPI types.

- [ ] **Step 1: Write the failing tests**

Create `tests/api/v1/test_images_include_comments.py`:

```python
"""
Tests for include_comments on GET /api/v1/images/ (bundled feed comments).

Design: docs/plans/2026-Q3/2026-08-17-bundled-feed-comments-design.md
"""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.comment import Comments
from app.models.image import Images


async def _make_image(
    db_session: AsyncSession, sample_image_data: dict, filename: str
) -> Images:
    image = Images(**{**sample_image_data, "filename": filename})
    db_session.add(image)
    await db_session.commit()
    await db_session.refresh(image)
    return image


@pytest.mark.api
class TestListImagesIncludeComments:
    """GET /api/v1/images/?include_comments=true bundles the page's comments."""

    async def test_comments_field_null_without_param(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        await _make_image(db_session, sample_image_data, "bundle-off-001")
        response = await client.get("/api/v1/images/")
        assert response.status_code == 200
        assert response.json()["comments"] is None

    async def test_comments_field_null_when_param_false(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        await _make_image(db_session, sample_image_data, "bundle-false-001")
        response = await client.get("/api/v1/images/", params={"include_comments": "false"})
        assert response.status_code == 200
        assert response.json()["comments"] is None

    async def test_bundles_page_comments_grouped_and_ordered(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        image_a = await _make_image(db_session, sample_image_data, "bundle-a-001")
        image_b = await _make_image(db_session, sample_image_data, "bundle-b-001")

        # Distinct explicit dates: the column server-defaults to now(), and
        # same-second inserts would make the order assertion nondeterministic.
        first = Comments(
            image_id=image_a.image_id,
            user_id=1,
            post_text="first",
            date=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        )
        db_session.add(first)
        await db_session.commit()
        await db_session.refresh(first)

        db_session.add_all(
            [
                Comments(
                    image_id=image_a.image_id,
                    user_id=1,
                    post_text="reply to first",
                    parent_comment_id=first.post_id,
                    date=datetime(2026, 1, 1, 12, 0, 1, tzinfo=UTC),
                ),
                Comments(
                    image_id=image_a.image_id,
                    user_id=1,
                    post_text="second",
                    date=datetime(2026, 1, 1, 12, 0, 2, tzinfo=UTC),
                ),
                Comments(
                    image_id=image_a.image_id,
                    user_id=1,
                    post_text="deleted, must not appear",
                    deleted=True,
                    date=datetime(2026, 1, 1, 12, 0, 3, tzinfo=UTC),
                ),
            ]
        )
        await db_session.commit()

        # Default sort is image_id DESC, so the two fresh images lead page 1.
        response = await client.get(
            "/api/v1/images/", params={"include_comments": "true", "per_page": 100}
        )
        assert response.status_code == 200
        data = response.json()

        page_ids = {img["image_id"] for img in data["images"]}
        assert image_a.image_id in page_ids
        assert image_b.image_id in page_ids

        comments_map = data["comments"]
        assert comments_map is not None

        # JSON object keys are strings.
        bundled = comments_map[str(image_a.image_id)]
        assert [c["post_text"] for c in bundled] == ["first", "reply to first", "second"]

        # Thread linkage and the embedded author survive the bundling.
        assert bundled[1]["parent_comment_id"] == first.post_id
        assert all(c["user"]["username"] for c in bundled)

        # Comment-less images do not appear in the map.
        assert str(image_b.image_id) not in comments_map

    async def test_empty_map_when_page_has_no_comments(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        image = await _make_image(db_session, sample_image_data, "bundle-empty-001")
        # per_page=1 with the default image_id DESC sort pins the page to the
        # image just created, so other tests' commented images can't leak in.
        response = await client.get(
            "/api/v1/images/", params={"include_comments": "true", "per_page": 1}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["images"][0]["image_id"] == image.image_id
        assert data["comments"] == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_images_include_comments.py -v`
Expected: all four FAIL with `KeyError: 'comments'` (the field does not exist yet). Any other failure mode (fixture error, 422) means the test itself is wrong — fix the test before touching app code.

- [ ] **Step 3: Add the response field**

In `app/schemas/image.py`, `ImageDetailedListResponse` (~line 294) becomes:

```python
class ImageDetailedListResponse(BaseModel):
    """Schema for paginated image list with detailed image data (includes relationships)"""

    total: int
    page: int
    per_page: int
    images: list[ImageDetailedResponse]
    # Populated only when the request sets include_comments=true: every
    # non-deleted comment for the returned images, oldest first, keyed by
    # image id. Images without comments are absent from the map.
    comments: dict[int, list[CommentResponse]] | None = None
```

Add the import at the top of `app/schemas/image.py`:

```python
from app.schemas.comment import CommentResponse
```

(`app/schemas/comment.py` imports only models and `app.schemas.base`/`common`, so this creates no import cycle.)

- [ ] **Step 4: Add the service function**

Append to `app/services/comments.py`:

```python
async def comments_for_images(
    db: AsyncSession, image_ids: Sequence[int]
) -> dict[int, list[CommentResponse]]:
    """
    Every non-deleted comment on the given images, oldest first, grouped by
    image id. Feeds include_comments=true on the images list; images without
    comments are absent from the result.
    """
    if not image_ids:
        return {}
    result = await db.execute(
        select(Comments)
        .where(
            Comments.deleted == False,  # type: ignore[arg-type]  # noqa: E712
            Comments.image_id.in_(image_ids),  # type: ignore[union-attr]
        )
        .order_by(asc(Comments.date))
        .options(comment_user_eager_load())
    )
    grouped: dict[int, list[CommentResponse]] = {}
    for comment in result.scalars().all():
        if comment.image_id is None:
            continue
        grouped.setdefault(comment.image_id, []).append(
            CommentResponse.model_validate(comment)
        )
    return grouped
```

Extend the module's imports to:

```python
from collections.abc import Sequence

from sqlalchemy import asc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.strategy_options import _AbstractLoad

from app.models import Comments, Users
from app.models.permissions import UserGroups
from app.schemas.comment import CommentResponse
```

- [ ] **Step 5: Wire the endpoint**

In `app/api/v1/images.py`, `list_images`:

1. Add the param at the end of the query-param block, directly after the `reported` param (~line 509-514) and before `db: AsyncSession = Depends(get_db)`:

```python
    include_comments: Annotated[
        bool,
        Query(
            description="Bundle every comment on the returned images into the "
            "response, grouped by image id (comments map). Default false."
        ),
    ] = False,
```

2. Add the imports (the file already imports from `app.schemas` and `app.services` siblings):

```python
from app.schemas.comment import CommentResponse
from app.services.comments import comments_for_images
```

3. Immediately before the final `return ImageDetailedListResponse(...)` (~line 1086, after `await stamp_context_sources(db, response_items)`):

```python
    comments_map: dict[int, list[CommentResponse]] | None = None
    if include_comments:
        comments_map = await comments_for_images(db, [img.image_id for img in images])
```

4. Add the field to the return:

```python
    return ImageDetailedListResponse(
        total=total or 0,
        page=pagination.page,
        per_page=pagination.per_page,
        images=response_items,
        comments=comments_map,
    )
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_images_include_comments.py -v`
Expected: 4 passed.

- [ ] **Step 7: Run the neighboring suites**

Run: `uv run pytest tests/api/v1/test_comments.py tests/api/v1/test_images.py -q`
Expected: PASS — the default-off path must not change any existing images/comments behavior.

Run: `uv run ruff check app/ && uv run mypy app/services/comments.py app/schemas/image.py app/api/v1/images.py`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add app/services/comments.py app/schemas/image.py app/api/v1/images.py tests/api/v1/test_images_include_comments.py
git commit -m "feat(images): include_comments bundles the page's comments (fe#357 part 2)

GET /api/v1/images/?include_comments=true returns every non-deleted
comment on the returned images as a comments map on the list response
(oldest first, keyed by image id, author eager-loaded). Default off:
the field serializes as null and nothing else changes. Kills the
frontend's serial second fetch per feed page.

Design: docs/plans/2026-Q3/2026-08-17-bundled-feed-comments-design.md"
```

---

### Task 3: Full-suite verification and PR

**Files:** none (verification only).

**Interfaces:**
- Produces: the merged param the frontend plan's Task 1 regenerates types from.

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest tests/ -q`
Expected: PASS. If anything unrelated fails, first re-run main's last green CI (`gh run list --branch main`, `gh run rerun <id>`) before blaming this branch — CI-only anyio loop clashes have burned us before (api#323).

- [ ] **Step 2: Confirm the OpenAPI schema carries the param and field**

Run: `curl -s localhost:8000/api/openapi.json | python3 -c "import json,sys; s=json.load(sys.stdin); params=[p['name'] for p in s['paths']['/api/v1/images/']['get']['parameters']]; print('include_comments' in params, 'comments' in s['components']['schemas']['ImageDetailedListResponse']['properties'])"`
Expected: `True True`. (Requires the local dev API to be running this branch; restart the `shuushuu-api` container if its reload hasn't picked the branch up.)

- [ ] **Step 3: Push and open the PR**

```bash
git push -u origin feat/bundled-feed-comments
gh pr create --title "feat(images): include_comments bundles feed comments (fe#357 part 2)" --body "$(cat <<'EOF'
Part 2 of anonymousobject/shuushuu-frontend#357. Design doc:
docs/plans/2026-Q3/2026-08-17-bundled-feed-comments-design.md (first commit of this branch).

- `GET /api/v1/images/?include_comments=true` returns every non-deleted comment on the returned page's images as a `comments` map (oldest first, keyed by image id), replacing the frontend's serial `/comments?image_ids=` round trip.
- Default off — the field serializes as `null` and existing consumers are untouched. No coordinated deploy: this merges and deploys first, the frontend PR follows.
- Refactor bonus: the comment-author eager-load chain, previously copy-pasted four times in the comments endpoints, now lives in `app/services/comments.py`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Do NOT use a closing keyword for fe#357 — the frontend PR closes it once both halves are in.
