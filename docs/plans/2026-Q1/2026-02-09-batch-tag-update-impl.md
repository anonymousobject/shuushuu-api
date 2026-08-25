# Batch Tag Update Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `POST /api/v1/tags/batch` endpoint for applying multiple tags to multiple images in one call.

**Architecture:** Three new components: Pydantic schemas for request/response validation, a service module with the batch logic (bulk queries, skip-and-report), and a thin route handler wiring auth + service. All inserts are atomic via a single commit.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy async, Pydantic v2, pytest with httpx AsyncClient

---

### Task 1: Add request/response schemas

**Files:**
- Modify: `app/schemas/tag.py` (append to end of file)

**Step 1: Write the failing test**

Create `tests/api/v1/test_batch_tag.py`:

```python
"""Tests for POST /api/v1/tags/batch endpoint."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.core.security import create_access_token
from app.models.image import Images
from app.models.permissions import Perms, UserPerms
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.user import Users


async def _create_user_with_tag_permission(db_session: AsyncSession) -> Users:
    """Create a user with IMAGE_TAG_ADD permission."""
    user = Users(
        username="batch_tagger",
        password="hashed_password_here",
        password_type="bcrypt",
        salt="saltsalt12345678",
        email="tagger@example.com",
        active=1,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    perm = Perms(title="image_tag_add", desc="Add tags to images")
    db_session.add(perm)
    await db_session.commit()
    await db_session.refresh(perm)

    user_perm = UserPerms(
        user_id=user.user_id,
        perm_id=perm.perm_id,
        permvalue=1,
    )
    db_session.add(user_perm)
    await db_session.commit()

    return user


async def _create_test_images(db_session: AsyncSession, user: Users, count: int) -> list[Images]:
    """Create test images owned by user."""
    images = []
    for i in range(count):
        image = Images(
            filename=f"batch-test-{i:03d}",
            ext="jpg",
            original_filename=f"batch{i}.jpg",
            md5_hash=f"batch{i:028x}",
            filesize=100000,
            width=800,
            height=600,
            caption=f"Batch test image {i}",
            rating=0.0,
            user_id=user.user_id,
            status=1,
            locked=False,
        )
        db_session.add(image)
        images.append(image)
    await db_session.commit()
    for img in images:
        await db_session.refresh(img)
    return images


async def _create_test_tags(db_session: AsyncSession, count: int) -> list[Tags]:
    """Create test tags."""
    tags = []
    for i in range(count):
        tag = Tags(title=f"Batch Tag {i}", type=TagType.THEME)
        db_session.add(tag)
        tags.append(tag)
    await db_session.commit()
    for t in tags:
        await db_session.refresh(t)
    return tags


@pytest.mark.api
class TestBatchTagValidation:
    """Tests for request validation on POST /api/v1/tags/batch."""

    async def test_rejects_unauthenticated(self, client: AsyncClient):
        """Batch tag endpoint requires authentication."""
        response = await client.post(
            "/api/v1/tags/batch",
            json={"action": "add", "tag_ids": [1], "image_ids": [1]},
        )
        assert response.status_code == 401

    async def test_rejects_empty_tag_ids(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """tag_ids must have at least 1 item."""
        user = await _create_user_with_tag_permission(db_session)
        token = create_access_token(user.id)
        response = await client.post(
            "/api/v1/tags/batch",
            json={"action": "add", "tag_ids": [], "image_ids": [1]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422

    async def test_rejects_too_many_tag_ids(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """tag_ids must have at most 5 items."""
        user = await _create_user_with_tag_permission(db_session)
        token = create_access_token(user.id)
        response = await client.post(
            "/api/v1/tags/batch",
            json={"action": "add", "tag_ids": [1, 2, 3, 4, 5, 6], "image_ids": [1]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422

    async def test_rejects_empty_image_ids(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """image_ids must have at least 1 item."""
        user = await _create_user_with_tag_permission(db_session)
        token = create_access_token(user.id)
        response = await client.post(
            "/api/v1/tags/batch",
            json={"action": "add", "tag_ids": [1], "image_ids": []},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422

    async def test_rejects_too_many_image_ids(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """image_ids must have at most 100 items."""
        user = await _create_user_with_tag_permission(db_session)
        token = create_access_token(user.id)
        response = await client.post(
            "/api/v1/tags/batch",
            json={"action": "add", "tag_ids": [1], "image_ids": list(range(1, 102))},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422

    async def test_rejects_invalid_action(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Only 'add' action is supported."""
        user = await _create_user_with_tag_permission(db_session)
        token = create_access_token(user.id)
        response = await client.post(
            "/api/v1/tags/batch",
            json={"action": "remove", "tag_ids": [1], "image_ids": [1]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/v1/test_batch_tag.py::TestBatchTagValidation -v`
Expected: FAIL (import errors — schemas and endpoint don't exist yet)

**Step 3: Write the schemas**

Append to `app/schemas/tag.py`:

```python
from enum import Enum as StdEnum


class BatchTagAction(str, StdEnum):
    """Supported batch tag actions."""
    ADD = "add"


class BatchTagRequest(BaseModel):
    """Request schema for batch tag operations."""
    action: BatchTagAction
    tag_ids: list[int] = Field(min_length=1, max_length=5)
    image_ids: list[int] = Field(min_length=1, max_length=100)


class BatchTagResultItem(BaseModel):
    """A single successful tag-image pair."""
    image_id: int
    tag_id: int


class BatchTagSkippedItem(BaseModel):
    """A single skipped tag-image pair with reason."""
    image_id: int
    tag_id: int
    reason: str


class BatchTagResponse(BaseModel):
    """Response schema for batch tag operations."""
    added: list[BatchTagResultItem]
    skipped: list[BatchTagSkippedItem]
```

**Step 4: Run tests — still fails (no endpoint yet)**

Run: `uv run pytest tests/api/v1/test_batch_tag.py::TestBatchTagValidation::test_rejects_unauthenticated -v`
Expected: FAIL (404 — endpoint not registered)

**Step 5: Commit schemas**

```bash
git add app/schemas/tag.py tests/api/v1/test_batch_tag.py
git commit -m "feat: add batch tag request/response schemas and validation tests"
```

---

### Task 2: Add service module with batch logic

**Files:**
- Create: `app/services/batch_tag.py`

**Step 1: Write the failing test**

Append to `tests/api/v1/test_batch_tag.py`:

```python
@pytest.mark.api
class TestBatchTagAdd:
    """Tests for the happy path and skip-and-report behavior."""

    async def test_add_tags_to_images(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Successfully add multiple tags to multiple images."""
        user = await _create_user_with_tag_permission(db_session)
        token = create_access_token(user.id)
        images = await _create_test_images(db_session, user, 3)
        tags = await _create_test_tags(db_session, 2)

        response = await client.post(
            "/api/v1/tags/batch",
            json={
                "action": "add",
                "tag_ids": [t.tag_id for t in tags],
                "image_ids": [img.image_id for img in images],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["added"]) == 6  # 3 images * 2 tags
        assert len(data["skipped"]) == 0

    async def test_skips_nonexistent_images(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Missing image IDs are reported as skipped."""
        user = await _create_user_with_tag_permission(db_session)
        token = create_access_token(user.id)
        images = await _create_test_images(db_session, user, 1)
        tags = await _create_test_tags(db_session, 1)

        response = await client.post(
            "/api/v1/tags/batch",
            json={
                "action": "add",
                "tag_ids": [tags[0].tag_id],
                "image_ids": [images[0].image_id, 999999],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["added"]) == 1
        skipped = data["skipped"]
        assert len(skipped) == 1
        assert skipped[0]["image_id"] == 999999
        assert skipped[0]["reason"] == "image_not_found"

    async def test_skips_nonexistent_tags(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Missing tag IDs are reported as skipped."""
        user = await _create_user_with_tag_permission(db_session)
        token = create_access_token(user.id)
        images = await _create_test_images(db_session, user, 1)

        response = await client.post(
            "/api/v1/tags/batch",
            json={
                "action": "add",
                "tag_ids": [999999],
                "image_ids": [images[0].image_id],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["added"]) == 0
        skipped = data["skipped"]
        assert len(skipped) == 1
        assert skipped[0]["reason"] == "tag_not_found"

    async def test_skips_already_tagged(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Already-existing tag links are reported as skipped."""
        user = await _create_user_with_tag_permission(db_session)
        token = create_access_token(user.id)
        images = await _create_test_images(db_session, user, 1)
        tags = await _create_test_tags(db_session, 1)

        # Pre-link the tag
        existing_link = TagLinks(
            image_id=images[0].image_id,
            tag_id=tags[0].tag_id,
            user_id=user.user_id,
        )
        db_session.add(existing_link)
        await db_session.commit()

        response = await client.post(
            "/api/v1/tags/batch",
            json={
                "action": "add",
                "tag_ids": [tags[0].tag_id],
                "image_ids": [images[0].image_id],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["added"]) == 0
        assert len(data["skipped"]) == 1
        assert data["skipped"][0]["reason"] == "already_tagged"

    async def test_resolves_alias_tags(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Alias tags resolve to their canonical tag."""
        user = await _create_user_with_tag_permission(db_session)
        token = create_access_token(user.id)
        images = await _create_test_images(db_session, user, 1)

        # Create canonical tag and alias
        canonical = Tags(title="Canonical Tag", type=TagType.THEME)
        db_session.add(canonical)
        await db_session.commit()
        await db_session.refresh(canonical)

        alias = Tags(title="Alias Tag", type=TagType.THEME, alias_of=canonical.tag_id)
        db_session.add(alias)
        await db_session.commit()
        await db_session.refresh(alias)

        response = await client.post(
            "/api/v1/tags/batch",
            json={
                "action": "add",
                "tag_ids": [alias.tag_id],
                "image_ids": [images[0].image_id],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["added"]) == 1
        # The added pair should use the canonical tag_id
        assert data["added"][0]["tag_id"] == canonical.tag_id

    async def test_creates_tag_history(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Each new tag link creates a tag history entry."""
        from app.models.tag_history import TagHistory
        from sqlalchemy import select, func

        user = await _create_user_with_tag_permission(db_session)
        token = create_access_token(user.id)
        images = await _create_test_images(db_session, user, 2)
        tags = await _create_test_tags(db_session, 1)

        response = await client.post(
            "/api/v1/tags/batch",
            json={
                "action": "add",
                "tag_ids": [tags[0].tag_id],
                "image_ids": [img.image_id for img in images],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

        # Verify history entries were created
        result = await db_session.execute(
            select(func.count(TagHistory.tag_history_id)).where(
                TagHistory.tag_id == tags[0].tag_id,
                TagHistory.action == "a",
            )
        )
        count = result.scalar()
        assert count == 2

    async def test_requires_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Users without IMAGE_TAG_ADD permission get 403."""
        # Create user WITHOUT the permission
        user = Users(
            username="no_perms_user",
            password="hashed_password_here",
            password_type="bcrypt",
            salt="saltsalt12345678",
            email="noperms@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        token = create_access_token(user.id)
        response = await client.post(
            "/api/v1/tags/batch",
            json={"action": "add", "tag_ids": [1], "image_ids": [1]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_batch_tag.py::TestBatchTagAdd -v`
Expected: FAIL (endpoint returns 404 or 405)

**Step 3: Write the service module**

Create `app/services/batch_tag.py`:

```python
"""Batch tag operations service."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.tags import resolve_tag_alias
from app.models.image import Images
from app.models.tag import Tags
from app.models.tag_history import TagHistory
from app.models.tag_link import TagLinks
from app.schemas.tag import (
    BatchTagResponse,
    BatchTagResultItem,
    BatchTagSkippedItem,
)


async def batch_add_tags(
    tag_ids: list[int],
    image_ids: list[int],
    user_id: int,
    db: AsyncSession,
) -> BatchTagResponse:
    """
    Add multiple tags to multiple images, skipping invalid or duplicate pairs.

    Returns a response listing which pairs were added and which were skipped.
    """
    added: list[BatchTagResultItem] = []
    skipped: list[BatchTagSkippedItem] = []

    # 1. Resolve tags: fetch all, resolve aliases, collect missing
    resolved_tags: dict[int, int] = {}  # original_tag_id -> resolved_tag_id
    for tag_id in tag_ids:
        tag_result = await db.execute(
            select(Tags).where(Tags.tag_id == tag_id)  # type: ignore[arg-type]
        )
        tag = tag_result.scalar_one_or_none()
        if not tag:
            # Tag doesn't exist — will skip for all images later
            resolved_tags[tag_id] = -1  # sentinel
            continue
        _, resolved_id = await resolve_tag_alias(db, tag_id, tag)
        resolved_tags[tag_id] = resolved_id

    # Collect tag_not_found entries (for all images)
    missing_tag_ids = {tid for tid, rid in resolved_tags.items() if rid == -1}

    # 2. Fetch existing images in one query
    valid_resolved_tag_ids = {rid for rid in resolved_tags.values() if rid != -1}
    existing_image_result = await db.execute(
        select(Images.image_id).where(  # type: ignore[call-overload]
            Images.image_id.in_(image_ids)  # type: ignore[union-attr]
        )
    )
    existing_image_ids = {row[0] for row in existing_image_result.all()}

    # 3. Fetch existing tag links in one query
    existing_links: set[tuple[int, int]] = set()  # (image_id, tag_id)
    if existing_image_ids and valid_resolved_tag_ids:
        links_result = await db.execute(
            select(TagLinks.image_id, TagLinks.tag_id).where(  # type: ignore[call-overload]
                TagLinks.image_id.in_(existing_image_ids),  # type: ignore[union-attr]
                TagLinks.tag_id.in_(valid_resolved_tag_ids),  # type: ignore[union-attr]
            )
        )
        existing_links = {(row[0], row[1]) for row in links_result.all()}

    # 4. Process each image-tag pair
    for image_id in image_ids:
        for original_tag_id in tag_ids:
            resolved_tag_id = resolved_tags[original_tag_id]

            if original_tag_id in missing_tag_ids:
                skipped.append(BatchTagSkippedItem(
                    image_id=image_id,
                    tag_id=original_tag_id,
                    reason="tag_not_found",
                ))
                continue

            if image_id not in existing_image_ids:
                skipped.append(BatchTagSkippedItem(
                    image_id=image_id,
                    tag_id=resolved_tag_id,
                    reason="image_not_found",
                ))
                continue

            if (image_id, resolved_tag_id) in existing_links:
                skipped.append(BatchTagSkippedItem(
                    image_id=image_id,
                    tag_id=resolved_tag_id,
                    reason="already_tagged",
                ))
                continue

            # Add tag link
            db.add(TagLinks(
                image_id=image_id,
                tag_id=resolved_tag_id,
                user_id=user_id,
            ))

            # Record history
            db.add(TagHistory(
                image_id=image_id,
                tag_id=resolved_tag_id,
                action="a",
                user_id=user_id,
            ))

            added.append(BatchTagResultItem(
                image_id=image_id,
                tag_id=resolved_tag_id,
            ))

            # Track as existing to prevent duplicates within same batch
            existing_links.add((image_id, resolved_tag_id))

    await db.commit()

    return BatchTagResponse(added=added, skipped=skipped)
```

**Step 4: Run tests — still fails (no endpoint route yet)**

Run: `uv run pytest tests/api/v1/test_batch_tag.py::TestBatchTagAdd::test_add_tags_to_images -v`
Expected: FAIL (404)

**Step 5: Commit service**

```bash
git add app/services/batch_tag.py
git commit -m "feat: add batch tag service with skip-and-report logic"
```

---

### Task 3: Add route handler and wire everything together

**Files:**
- Modify: `app/api/v1/tags.py` (add endpoint + import)

**Step 1: Add the endpoint**

Add import at top of `app/api/v1/tags.py`:

```python
from app.schemas.tag import (
    # ... existing imports ...
    BatchTagRequest,
    BatchTagResponse,
)
```

Add endpoint (after the existing tag CRUD endpoints, before the character-source links section):

```python
@router.post("/batch", response_model=BatchTagResponse)
async def batch_tag_operation(
    request: BatchTagRequest,
    current_user: Annotated[Users, Depends(get_current_user)],
    _: Annotated[None, Depends(require_permission(Permission.IMAGE_TAG_ADD))],
    db: AsyncSession = Depends(get_db),
) -> BatchTagResponse:
    """
    Batch add tags to multiple images.

    Applies the specified tags to the specified images. Skips invalid
    pairs (missing image, missing tag, already tagged) and reports them
    in the response.

    Requires IMAGE_TAG_ADD permission.
    """
    from app.services.batch_tag import batch_add_tags

    return await batch_add_tags(
        tag_ids=request.tag_ids,
        image_ids=request.image_ids,
        user_id=current_user.id,
        db=db,
    )
```

**Step 2: Run all tests**

Run: `uv run pytest tests/api/v1/test_batch_tag.py -v`
Expected: ALL PASS

**Step 3: Run existing tag tests to verify no regressions**

Run: `uv run pytest tests/api/v1/test_tags.py -v`
Expected: ALL PASS

**Step 4: Commit route handler**

```bash
git add app/api/v1/tags.py
git commit -m "feat: add POST /api/v1/tags/batch endpoint"
```

---

### Task 4: Run full test suite and verify

**Step 1: Run entire test suite**

Run: `uv run pytest -v`
Expected: ALL PASS

**Step 2: Run linting**

Run: `uv run ruff check app/services/batch_tag.py app/schemas/tag.py app/api/v1/tags.py tests/api/v1/test_batch_tag.py`
Expected: No errors

**Step 3: Manual smoke test (if docker is up)**

```bash
# Test validation (should 422)
curl -s -X POST http://localhost:8000/api/v1/tags/batch \
  -H "Content-Type: application/json" \
  -d '{"action": "add", "tag_ids": [], "image_ids": [1]}' | jq .

# Test auth required (should 401)
curl -s -X POST http://localhost:8000/api/v1/tags/batch \
  -H "Content-Type: application/json" \
  -d '{"action": "add", "tag_ids": [1], "image_ids": [1]}' | jq .
```

**Step 4: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix: address lint/test issues from batch tag implementation"
```
