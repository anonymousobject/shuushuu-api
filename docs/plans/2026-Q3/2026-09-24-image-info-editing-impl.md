# Image Info Editing (API) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `PATCH /images/{id}` accepts `source_url`, validates `miscmeta` properly, and records every change to either field in a new public `image_metadata_history` table. The table is exposed per image (`GET /images/{id}/metadata-history`) and in the editor's profile history (`GET /users/{id}/history`).

**Architecture:** One normalizer in `app/schemas/image.py` validates `source_url` for both upload and PATCH. A pure service function builds history rows by diffing the PATCH's validated fields against the loaded image, and the handler adds them in the same transaction. The new endpoint copies `status-history`. The user-history union gains a fifth branch shaped like the status branch.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy async, Alembic, Postgres, pytest (httpx `AsyncClient`).

**Spec:** `<shuushuu-frontend-repo>/docs/plans/2026-Q3/2026-09-24-image-info-editing-design.md`. Read its "Decisions" and "API" sections first. The frontend half is `<shuushuu-frontend-repo>/docs/plans/2026-Q3/2026-09-24-image-info-editing-impl.md` and depends on this plan being merged or running on the dev API.

## Global Constraints

- Work in `/home/dtaylor/shuu/shuushuu-api`; read `AGENTS.md` first. Branch `feat/image-metadata-history` off `main` (pre-commit blocks commits to `main`).
- Tracked fields, verbatim: `miscmeta`, `source_url`. Caption is NOT tracked.
- Table name `image_metadata_history`; columns `id`, `image_id`, `user_id`, `field` (varchar 32), `old_value`, `new_value` (varchar 2000, nullable), `created_at`. FK names `fk_image_metadata_history_image_id` (CASCADE) and `fk_image_metadata_history_user_id` (SET NULL). Indexes `idx_image_metadata_history_image_id`, `idx_image_metadata_history_user_id`.
- `source_url` rules: trim; blank becomes null; must start with `http://` or `https://`; max 2000. Error message, verbatim: `source_url must start with http:// or https://`.
- `miscmeta` rules: trim; blank becomes null; max 255.
- History is public: both endpoints need no auth and always return values and editor.
- User-history kind number `5`, type string `"image_metadata"`.
- Verification commands: `uv run pytest <path> -q`, `uv run mypy app/`, `uv run ruff check`, `uv run ruff format --check`. Schema sync: `uv run pytest tests/integration/test_schema_sync.py --schema-sync -q`. Test output must be pristine.
- Commit messages end with the session's attribution trailer lines.

## Review Focus

- A legacy row stores `""` rather than NULL, and an editor saves a blank value. Expect no history row: `""` → null is not a change a person can see, and a "(none) → (none)" entry is noise. Pinned in Task 2.
- A 2000-character source URL is accepted and a 2001-character one is rejected. Pinned in Task 1.
- A double submit (Enter plus click) sends the same value twice. Expect exactly one history row. Pinned in Task 2 by the unchanged-value test.
- A rejected PATCH (403 or 422) leaves no history row. Pinned in Task 2.
- A metadata edit and a tag audit row land in the same second in one user's history. Expect a deterministic order and lossless pagination across that tie. Pinned in Task 4.

---

### Task 1: Validate `source_url` and `miscmeta` on PATCH with a shared normalizer

**Files:**
- Modify: `app/schemas/image.py` (new `normalize_source_url`; `ImageUpdate` fields and validators)
- Modify: `app/api/v1/images.py` (upload calls the normalizer; `update_image` docstring)
- Test: `tests/api/v1/test_image_edit.py`, `tests/api/v1/test_upload.py`

**Interfaces:**
- Produces: `normalize_source_url(value: str | None) -> str | None` in `app/schemas/image.py` (raises `ValueError` with the verbatim message); `SOURCE_URL_SCHEME_ERROR: str` constant beside it; `ImageUpdate.source_url: str | None`, `ImageUpdate.miscmeta: str | None` (both normalized).

- [ ] **Step 1: Create the branch**

```bash
cd /home/dtaylor/shuu/shuushuu-api
git switch -c feat/image-metadata-history main
uv run python scripts/gen_plans_index.py
git add docs/plans/2026-Q3/2026-09-24-image-info-editing-impl.md docs/plans/README.md
git commit -m "docs(plans): API implementation plan for image info editing"
```

- [ ] **Step 2: Let the test helper create an image with a source**

In `tests/api/v1/test_image_edit.py`, extend `create_image`:

```python
async def create_image(
    db_session: AsyncSession,
    user_id: int,
    caption: str = "original caption",
    miscmeta: str | None = None,
    source_url: str | None = None,
) -> Images:
```

and pass `source_url=source_url,` into the `Images(...)` constructor, after `miscmeta=miscmeta,`.

- [ ] **Step 3: Write the failing PATCH tests**

Append to `tests/api/v1/test_image_edit.py`:

```python
class TestImageEditSourceAndMiscmeta:
    """PATCH /api/v1/images/{image_id}: source_url and miscmeta validation."""

    @pytest.mark.asyncio
    async def test_owner_can_set_source_url(self, client: AsyncClient, db_session: AsyncSession):
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id)

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"source_url": "  https://www.pixiv.net/artworks/1  "},
            headers=auth_header(owner),
        )

        assert response.status_code == 200, response.text
        assert response.json()["source_url"] == "https://www.pixiv.net/artworks/1"

    @pytest.mark.asyncio
    async def test_mod_with_image_edit_meta_can_set_source_url(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await create_user(db_session, username="srcowner", email="srcowner@test.com")
        mod = await create_user(db_session, username="srcmod", email="srcmod@test.com")
        image = await create_image(db_session, owner.user_id)
        await grant_permission(db_session, mod.user_id, "image_edit_meta")

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"source_url": "https://example.com/art"},
            headers=auth_header(mod),
        )

        assert response.status_code == 200, response.text
        assert response.json()["source_url"] == "https://example.com/art"

    @pytest.mark.asyncio
    async def test_blank_source_url_clears_it(self, client: AsyncClient, db_session: AsyncSession):
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id, source_url="https://example.com/a")

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"source_url": "   "},
            headers=auth_header(owner),
        )

        assert response.status_code == 200, response.text
        assert response.json()["source_url"] is None

    @pytest.mark.asyncio
    async def test_non_http_source_url_rejected(self, client: AsyncClient, db_session: AsyncSession):
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id, source_url="https://example.com/a")

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"source_url": "javascript:alert(1)"},
            headers=auth_header(owner),
        )

        assert response.status_code == 422, response.text
        # No "Value error, " prefix: the frontend shows this message verbatim.
        assert response.json()["detail"][0]["msg"] == "source_url must start with http:// or https://"
        await db_session.refresh(image)
        assert image.source_url == "https://example.com/a"

    @pytest.mark.asyncio
    async def test_source_url_length_boundary(self, client: AsyncClient, db_session: AsyncSession):
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id)
        prefix = "https://example.com/"
        at_limit = prefix + "a" * (2000 - len(prefix))

        ok = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"source_url": at_limit},
            headers=auth_header(owner),
        )
        too_long = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"source_url": at_limit + "a"},
            headers=auth_header(owner),
        )

        assert ok.status_code == 200, ok.text
        assert ok.json()["source_url"] == at_limit
        assert too_long.status_code == 422, too_long.text

    @pytest.mark.asyncio
    async def test_non_editor_cannot_set_source_url(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await create_user(db_session, username="srcowner2", email="srcowner2@test.com")
        other = await create_user(db_session, username="srcother", email="srcother@test.com")
        image = await create_image(db_session, owner.user_id)

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"source_url": "https://example.com/art"},
            headers=auth_header(other),
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_miscmeta_trimmed_and_blank_clears(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id, miscmeta="old")

        trimmed = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"miscmeta": "  circle: foo  "},
            headers=auth_header(owner),
        )
        cleared = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"miscmeta": "   "},
            headers=auth_header(owner),
        )

        assert trimmed.status_code == 200, trimmed.text
        assert trimmed.json()["miscmeta"] == "circle: foo"
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["miscmeta"] is None

    @pytest.mark.asyncio
    async def test_overlong_miscmeta_rejected(self, client: AsyncClient, db_session: AsyncSession):
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id)

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"miscmeta": "a" * 256},
            headers=auth_header(owner),
        )

        assert response.status_code == 422, response.text
```

In `tests/api/v1/test_upload.py`, `test_upload_rejects_non_http_source_url`, add after the status assertion:

```python
        assert response.json()["detail"] == "source_url must start with http:// or https://"
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_image_edit.py tests/api/v1/test_upload.py -q`
Expected: the new source_url tests FAIL (`source_url` is not an `ImageUpdate` field, so PATCH returns 400 "No fields to update" or leaves it unchanged); `test_overlong_miscmeta_rejected` FAILs because the value reaches the varchar(255) column (a 500 response, or the database error raised straight through the test client); the upload assertion PASSes already (it pins existing behaviour through the refactor).

- [ ] **Step 5: Implement the normalizer and the `ImageUpdate` fields**

In `app/schemas/image.py`, add `from pydantic_core import PydanticCustomError` to the imports, then above `class ImageUpdate`:

```python
SOURCE_URL_SCHEME_ERROR = "source_url must start with http:// or https://"


def normalize_source_url(value: str | None) -> str | None:
    """Trim a source URL; blank becomes None; only http(s) is accepted.

    Shared by the upload form and PATCH /images/{id}. The scheme check blocks
    javascript:/data: URLs and similar, since the value renders as a link.
    Raises ValueError with SOURCE_URL_SCHEME_ERROR on any other scheme.
    """
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if not value.startswith(("http://", "https://")):
        raise ValueError(SOURCE_URL_SCHEME_ERROR)
    return value
```

Replace the `ImageUpdate` field block (keep `sanitize_caption` as is):

```python
class ImageUpdate(BaseModel):
    """Schema for updating image metadata and owner status — all fields optional."""

    caption: str | None = None
    miscmeta: str | None = Field(default=None, max_length=255)
    source_url: str | None = Field(default=None, max_length=2000)
    status: int | None = None
    replacement_id: int | None = None

    @field_validator("caption")
    ...  # unchanged

    @field_validator("miscmeta")
    @classmethod
    def normalize_miscmeta(cls, v: str | None) -> str | None:
        """Trim miscmeta; a blank value clears the field."""
        if v is None:
            return None
        return v.strip() or None

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, v: str | None) -> str | None:
        """Apply the shared source URL rules (see normalize_source_url)."""
        try:
            return normalize_source_url(v)
        except ValueError as exc:
            # A PydanticCustomError keeps the message free of pydantic's
            # "Value error, " prefix, so the frontend can show it verbatim.
            raise PydanticCustomError("source_url_scheme", str(exc)) from exc
```

- [ ] **Step 6: Route the upload through the normalizer**

In `app/api/v1/images.py`, add `normalize_source_url` to the `from app.schemas.image import (...)` block. Replace the upload's inline check (the block under "Validate source_url before touching storage") with:

```python
        # Validate source_url before touching storage (see normalize_source_url).
        try:
            source_url = normalize_source_url(source_url)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
```

In `update_image`'s docstring, change "Update image metadata (caption, miscmeta)" to "Update image metadata (caption, miscmeta, source_url)".

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_image_edit.py tests/api/v1/test_upload.py -q`
Expected: all PASS.

- [ ] **Step 8: Type-check, lint, commit**

```bash
uv run mypy app/ && uv run ruff check && uv run ruff format --check
git add app/schemas/image.py app/api/v1/images.py tests/api/v1/test_image_edit.py tests/api/v1/test_upload.py
git commit -m "feat(images): accept and validate source_url on PATCH; bound miscmeta"
```

---

### Task 2: `image_metadata_history` table, written on every PATCH that changes a tracked field

**Files:**
- Modify: `app/config.py` (new `ImageMetadataField` below `TagAuditActionType`)
- Create: `app/models/image_metadata_history.py`
- Modify: `app/models/__init__.py` (import and `__all__`)
- Create: `alembic/versions/0005_image_metadata_history.py`
- Create: `app/services/image_metadata_history.py`
- Modify: `app/api/v1/images.py` (`update_image` adds the rows)
- Test: `tests/api/v1/test_image_edit.py`

**Interfaces:**
- Consumes: Task 1's normalized `ImageUpdate` values.
- Produces: `ImageMetadataField.MISCMETA = "miscmeta"`, `ImageMetadataField.SOURCE_URL = "source_url"`, `ImageMetadataField.ALL: tuple[str, ...]`; model `ImageMetadataHistory` (fields `id`, `image_id`, `user_id`, `field`, `old_value`, `new_value`, `created_at`); `build_metadata_history(image_id: int, current: Images, update_fields: dict[str, Any], user_id: int) -> list[ImageMetadataHistory]`.

- [ ] **Step 1: Write the failing history-write tests**

Append to `tests/api/v1/test_image_edit.py` (add `from app.models.image_metadata_history import ImageMetadataHistory` to the imports):

```python
async def history_rows(db_session: AsyncSession, image_id: int) -> list[ImageMetadataHistory]:
    result = await db_session.execute(
        select(ImageMetadataHistory)
        .where(ImageMetadataHistory.image_id == image_id)
        .order_by(ImageMetadataHistory.id)
    )
    return list(result.scalars().all())


class TestImageEditHistory:
    """PATCH /api/v1/images/{image_id} records miscmeta/source_url changes."""

    @pytest.mark.asyncio
    async def test_setting_source_url_writes_one_row(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id)

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"source_url": "https://example.com/art"},
            headers=auth_header(owner),
        )

        assert response.status_code == 200, response.text
        rows = await history_rows(db_session, image.image_id)
        assert [(r.field, r.old_value, r.new_value, r.user_id) for r in rows] == [
            ("source_url", None, "https://example.com/art", owner.user_id)
        ]
        assert rows[0].created_at is not None

    @pytest.mark.asyncio
    async def test_changing_both_fields_writes_two_rows(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await create_user(db_session)
        image = await create_image(
            db_session, owner.user_id, miscmeta="old info", source_url="https://example.com/old"
        )

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"miscmeta": "new info", "source_url": "https://example.com/new"},
            headers=auth_header(owner),
        )

        assert response.status_code == 200, response.text
        rows = await history_rows(db_session, image.image_id)
        assert sorted((r.field, r.old_value, r.new_value) for r in rows) == [
            ("miscmeta", "old info", "new info"),
            ("source_url", "https://example.com/old", "https://example.com/new"),
        ]

    @pytest.mark.asyncio
    async def test_clearing_records_the_old_value(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id, source_url="https://example.com/a")

        await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"source_url": ""},
            headers=auth_header(owner),
        )

        rows = await history_rows(db_session, image.image_id)
        assert [(r.field, r.old_value, r.new_value) for r in rows] == [
            ("source_url", "https://example.com/a", None)
        ]

    @pytest.mark.asyncio
    async def test_unchanged_value_writes_no_row(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Covers a double submit, and a value that differs only by whitespace."""
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id, miscmeta="same")

        first = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"miscmeta": "same"},
            headers=auth_header(owner),
        )
        second = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"miscmeta": "  same  "},
            headers=auth_header(owner),
        )

        assert first.status_code == 200 and second.status_code == 200
        assert await history_rows(db_session, image.image_id) == []

    @pytest.mark.asyncio
    async def test_legacy_empty_string_to_blank_writes_no_row(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """A legacy '' and a cleared null both read as "none"; no visible change."""
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id, miscmeta="")

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"miscmeta": ""},
            headers=auth_header(owner),
        )

        assert response.status_code == 200, response.text
        assert await history_rows(db_session, image.image_id) == []

    @pytest.mark.asyncio
    async def test_legacy_empty_string_old_value_recorded_as_null(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id, miscmeta="")

        await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"miscmeta": "now set"},
            headers=auth_header(owner),
        )

        rows = await history_rows(db_session, image.image_id)
        assert [(r.old_value, r.new_value) for r in rows] == [(None, "now set")]

    @pytest.mark.asyncio
    async def test_caption_change_writes_no_row(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id)

        await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"caption": "new caption"},
            headers=auth_header(owner),
        )

        assert await history_rows(db_session, image.image_id) == []

    @pytest.mark.asyncio
    async def test_rejected_edits_write_no_row(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await create_user(db_session, username="histowner", email="histowner@test.com")
        other = await create_user(db_session, username="histother", email="histother@test.com")
        image = await create_image(db_session, owner.user_id)

        forbidden = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"source_url": "https://example.com/art"},
            headers=auth_header(other),
        )
        invalid = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"source_url": "ftp://example.com/art"},
            headers=auth_header(owner),
        )

        assert forbidden.status_code == 403
        assert invalid.status_code == 422
        assert await history_rows(db_session, image.image_id) == []

    @pytest.mark.asyncio
    async def test_mod_edit_records_the_mod(self, client: AsyncClient, db_session: AsyncSession):
        owner = await create_user(db_session, username="histowner2", email="histowner2@test.com")
        mod = await create_user(db_session, username="histmod", email="histmod@test.com")
        image = await create_image(db_session, owner.user_id)
        await grant_permission(db_session, mod.user_id, "image_edit_meta")

        await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"miscmeta": "credited"},
            headers=auth_header(mod),
        )

        rows = await history_rows(db_session, image.image_id)
        assert [r.user_id for r in rows] == [mod.user_id]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_image_edit.py -q`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'app.models.image_metadata_history'`.

- [ ] **Step 3: Add the field constants**

In `app/config.py`, directly below the `TagAuditActionType` class:

```python
class ImageMetadataField:
    """Image fields whose edits image_metadata_history records."""

    MISCMETA = "miscmeta"
    SOURCE_URL = "source_url"

    ALL = (MISCMETA, SOURCE_URL)
```

- [ ] **Step 4: Add the model**

Create `app/models/image_metadata_history.py`:

```python
"""
SQLModel-based ImageMetadataHistory model for tracking edits to an image's
free-text metadata (miscmeta, source_url).

A public audit table, like image_status_history and deliberately separate
from AdminActions (the private moderation log): every row is shown, values
and editor included, on the image's history page and on the editor's profile
history. One row per changed field.
"""

from datetime import datetime

from sqlalchemy import Column, ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel

from app.models.types import UtcDateTime


class ImageMetadataHistoryBase(SQLModel):
    """
    Base model with shared fields for ImageMetadataHistory.
    """

    image_id: int
    # An ImageMetadataField value
    field: str = Field(max_length=32)
    # None means unset (old_value) or cleared (new_value). 2000 fits the wider
    # of the two tracked columns, source_url.
    old_value: str | None = Field(default=None, max_length=2000)
    new_value: str | None = Field(default=None, max_length=2000)


class ImageMetadataHistory(ImageMetadataHistoryBase, table=True):
    """
    Database table for image metadata history.
    """

    __tablename__ = "image_metadata_history"

    __table_args__ = (
        ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_image_metadata_history_image_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_image_metadata_history_user_id",
        ),
        Index("idx_image_metadata_history_image_id", "image_id"),
        Index("idx_image_metadata_history_user_id", "user_id"),
    )

    # Primary key
    id: int | None = Field(default=None, primary_key=True)

    # Editor (nullable: the account may be deleted later)
    user_id: int | None = Field(default=None)

    # Timestamp
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(UtcDateTime, nullable=True, server_default=text("CURRENT_TIMESTAMP")),
    )
```

In `app/models/__init__.py`, add `from app.models.image_metadata_history import ImageMetadataHistory` next to the `ImageStatusHistory` import, and `"ImageMetadataHistory",` next to `"ImageStatusHistory",` in `__all__`.

- [ ] **Step 5: Add the migration**

Create `alembic/versions/0005_image_metadata_history.py`:

```python
"""image_metadata_history: public audit of miscmeta/source_url edits

Revision ID: 0005_image_metadata_history
Revises: 0004_tag_search_fold
Create Date: 2026-09-24

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_image_metadata_history"
down_revision: str | Sequence[str] | None = "0004_tag_search_fold"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "image_metadata_history",
        sa.Column("image_id", sa.Integer(), nullable=False),
        sa.Column("field", sa.String(length=32), nullable=False),
        sa.Column("old_value", sa.String(length=2000), nullable=True),
        sa.Column("new_value", sa.String(length=2000), nullable=True),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            name="fk_image_metadata_history_image_id",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            name="fk_image_metadata_history_user_id",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
    )
    op.create_index(
        "idx_image_metadata_history_image_id", "image_metadata_history", ["image_id"]
    )
    op.create_index("idx_image_metadata_history_user_id", "image_metadata_history", ["user_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("image_metadata_history")
```

- [ ] **Step 6: Prove the model and migration agree**

Run: `uv run pytest tests/integration/test_schema_sync.py tests/integration/test_fk_constraint_names.py --schema-sync -q`
Expected: PASS. If `test_models_match_migration_chain` fails, its diff names the mismatched column, index, or constraint: change the migration to match the model (the model is the contract), and re-run until it passes.

- [ ] **Step 7: Add the service function**

Create `app/services/image_metadata_history.py`:

```python
"""History rows for edits to an image's free-text metadata (miscmeta, source_url)."""

from typing import Any

from app.config import ImageMetadataField
from app.models.image import Images
from app.models.image_metadata_history import ImageMetadataHistory


def build_metadata_history(
    image_id: int, current: Images, update_fields: dict[str, Any], user_id: int
) -> list[ImageMetadataHistory]:
    """One history row per tracked field whose value the update changes.

    Call before applying the update: old values are read from `current`.
    `update_fields` holds validated ImageUpdate values (already trimmed, blank
    as None). A legacy '' in the column counts as None, so clearing an
    already-empty field records nothing. Untracked fields (caption) and
    unchanged values produce no row.
    """
    rows = []
    for field in ImageMetadataField.ALL:
        if field not in update_fields:
            continue
        old_value = getattr(current, field) or None
        new_value = update_fields[field]
        if old_value != new_value:
            rows.append(
                ImageMetadataHistory(
                    image_id=image_id,
                    user_id=user_id,
                    field=field,
                    old_value=old_value,
                    new_value=new_value,
                )
            )
    return rows
```

- [ ] **Step 8: Write the rows from the PATCH handler**

In `app/api/v1/images.py`, import `from app.services.image_metadata_history import build_metadata_history` (keep the services imports alphabetical). In `update_image`, immediately above the `# Apply metadata updates` loop:

```python
    # Record miscmeta/source_url changes before applying them: the old values
    # are read off the loaded image. Same transaction as the update.
    db.add_all(build_metadata_history(image_id, image, update_fields, current_user.id))
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_image_edit.py -q`
Expected: all PASS.

- [ ] **Step 10: Type-check, lint, commit**

```bash
uv run mypy app/ && uv run ruff check && uv run ruff format --check
git add app/config.py app/models/image_metadata_history.py app/models/__init__.py \
  alembic/versions/0005_image_metadata_history.py app/services/image_metadata_history.py \
  app/api/v1/images.py tests/api/v1/test_image_edit.py
git commit -m "feat(images): record miscmeta/source_url edits in image_metadata_history"
```

---

### Task 3: `GET /images/{image_id}/metadata-history`

**Files:**
- Modify: `app/schemas/audit.py` (two response schemas after `ImageStatusHistoryListResponse`)
- Modify: `app/api/v1/images.py` (new route directly after `get_image_status_history`)
- Create: `tests/api/v1/test_image_metadata_history_endpoint.py`

**Interfaces:**
- Consumes: `ImageMetadataHistory` (Task 2).
- Produces: `ImageMetadataHistoryResponse { id: int, image_id: int, field: Literal["miscmeta", "source_url"], old_value: str | None, new_value: str | None, user: UserSummary | None, created_at: UTCDatetime }`, `ImageMetadataHistoryListResponse { total, page, per_page, items }`. The frontend reads these names from the generated types.

- [ ] **Step 1: Write the failing endpoint tests**

Create `tests/api/v1/test_image_metadata_history_endpoint.py`:

```python
"""Tests for GET /api/v1/images/{image_id}/metadata-history."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.image import Images
from app.models.image_metadata_history import ImageMetadataHistory
from app.models.user import Users


async def _make_user(db_session: AsyncSession, username: str) -> Users:
    user = Users(
        username=username,
        password="hashed",
        password_type="bcrypt",
        salt="",
        email=f"{username}@example.com",
        active=1,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _make_image(db_session: AsyncSession, owner: Users, filename: str) -> Images:
    image = Images(
        filename=filename,
        ext="jpg",
        md5_hash=hashlib.md5(filename.encode()).hexdigest(),
        user_id=owner.user_id,
        width=100,
        height=100,
        filesize=1000,
    )
    db_session.add(image)
    await db_session.commit()
    await db_session.refresh(image)
    return image


@pytest.mark.api
class TestImageMetadataHistoryEndpoint:
    async def test_returns_entries_newest_first_with_editor(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await _make_user(db_session, "mdhistuser")
        image = await _make_image(db_session, user, "mdhist1")
        base = datetime(2026, 1, 1, tzinfo=UTC)
        db_session.add_all(
            [
                ImageMetadataHistory(
                    image_id=image.image_id, user_id=user.user_id, field="source_url",
                    old_value=None, new_value="https://example.com/a", created_at=base,
                ),
                ImageMetadataHistory(
                    image_id=image.image_id, user_id=user.user_id, field="miscmeta",
                    old_value="old", new_value=None, created_at=base + timedelta(days=1),
                ),
            ]
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/images/{image.image_id}/metadata-history")

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["total"] == 2
        assert [(i["field"], i["old_value"], i["new_value"]) for i in data["items"]] == [
            ("miscmeta", "old", None),
            ("source_url", None, "https://example.com/a"),
        ]
        assert data["items"][0]["user"]["username"] == "mdhistuser"
        assert data["items"][0]["image_id"] == image.image_id
        assert data["items"][0]["created_at"] is not None

    async def test_same_timestamp_orders_by_id_desc(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await _make_user(db_session, "mdhisttie")
        image = await _make_image(db_session, user, "mdhisttie")
        same = datetime(2026, 1, 1, tzinfo=UTC)
        first = ImageMetadataHistory(
            image_id=image.image_id, user_id=user.user_id, field="miscmeta",
            old_value=None, new_value="one", created_at=same,
        )
        db_session.add(first)
        await db_session.commit()
        second = ImageMetadataHistory(
            image_id=image.image_id, user_id=user.user_id, field="miscmeta",
            old_value="one", new_value="two", created_at=same,
        )
        db_session.add(second)
        await db_session.commit()

        response = await client.get(f"/api/v1/images/{image.image_id}/metadata-history")

        assert [i["id"] for i in response.json()["items"]] == [second.id, first.id]

    async def test_paginates(self, client: AsyncClient, db_session: AsyncSession) -> None:
        user = await _make_user(db_session, "mdhistpage")
        image = await _make_image(db_session, user, "mdhistpage")
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for day in range(3):
            db_session.add(
                ImageMetadataHistory(
                    image_id=image.image_id, user_id=user.user_id, field="miscmeta",
                    old_value=None, new_value=f"v{day}", created_at=base + timedelta(days=day),
                )
            )
        await db_session.commit()

        response = await client.get(
            f"/api/v1/images/{image.image_id}/metadata-history?page=2&per_page=2"
        )

        data = response.json()
        assert data["total"] == 3
        assert data["page"] == 2
        assert data["per_page"] == 2
        assert [i["new_value"] for i in data["items"]] == ["v0"]

    async def test_only_this_images_entries(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await _make_user(db_session, "mdhistscope")
        image = await _make_image(db_session, user, "mdhistscope1")
        other = await _make_image(db_session, user, "mdhistscope2")
        db_session.add(
            ImageMetadataHistory(
                image_id=other.image_id, user_id=user.user_id, field="miscmeta",
                old_value=None, new_value="elsewhere",
            )
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/images/{image.image_id}/metadata-history")

        assert response.json()["total"] == 0
        assert response.json()["items"] == []

    async def test_deleted_editor_shows_null_user(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        owner = await _make_user(db_session, "mdhistnull")
        image = await _make_image(db_session, owner, "mdhistnull")
        db_session.add(
            ImageMetadataHistory(
                image_id=image.image_id, user_id=None, field="miscmeta",
                old_value=None, new_value="orphaned",
            )
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/images/{image.image_id}/metadata-history")

        assert response.json()["items"][0]["user"] is None

    async def test_unknown_image_returns_404(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/images/999999999/metadata-history")
        assert response.status_code == 404

    async def test_patch_then_read_back(self, client: AsyncClient, db_session: AsyncSession) -> None:
        """End to end: an owner's PATCH shows up here, anonymously readable."""
        owner = await _make_user(db_session, "mdhistpatch")
        image = await _make_image(db_session, owner, "mdhistpatch")

        patch = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"source_url": "https://example.com/src"},
            headers={"Authorization": f"Bearer {create_access_token(owner.user_id)}"},
        )
        assert patch.status_code == 200, patch.text

        response = await client.get(f"/api/v1/images/{image.image_id}/metadata-history")

        items = response.json()["items"]
        assert [(i["field"], i["new_value"], i["user"]["user_id"]) for i in items] == [
            ("source_url", "https://example.com/src", owner.user_id)
        ]
```

(`ruff format` will reflow the one-line keyword arguments; that is fine.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_image_metadata_history_endpoint.py -q`
Expected: FAIL, every test gets 404 (route does not exist), except `test_unknown_image_returns_404` which passes for the wrong reason.

- [ ] **Step 3: Add the response schemas**

In `app/schemas/audit.py`, directly after `ImageStatusHistoryListResponse` (make sure `Literal` is imported from `typing`; the file already uses it for `UserHistoryItem`):

```python
# =============================================================================
# Image Metadata History
# =============================================================================


class ImageMetadataHistoryResponse(BaseModel):
    """
    One edit to an image's miscmeta or source_url.

    Public: the editor and both values are always shown. None on either side
    means unset (old_value) or cleared (new_value).
    """

    id: int
    image_id: int
    field: Literal["miscmeta", "source_url"]
    old_value: str | None = None
    new_value: str | None = None

    # Who made the edit; null only when that account no longer exists
    user: UserSummary | None = None

    created_at: UTCDatetime

    model_config = {"from_attributes": True}


class ImageMetadataHistoryListResponse(BaseModel):
    """Paginated list of image metadata history entries."""

    total: int
    page: int
    per_page: int
    items: list[ImageMetadataHistoryResponse]
```

- [ ] **Step 4: Add the route**

In `app/api/v1/images.py`, add the two schemas to the `from app.schemas.audit import (...)` block and `from app.models.image_metadata_history import ImageMetadataHistory` beside the `ImageStatusHistory` import. Directly after `get_image_status_history`:

```python
@router.get("/{image_id}/metadata-history", response_model=ImageMetadataHistoryListResponse)
async def get_image_metadata_history(
    image_id: Annotated[int, Path(description="Image ID")],
    pagination: Annotated[PaginationParams, Depends()],
    db: AsyncSession = Depends(get_db),
) -> ImageMetadataHistoryListResponse:
    """
    Get the edit history of an image's miscmeta and source_url.

    Public: every entry shows its editor and both values. Newest first.
    """
    image_result = await db.execute(select(Images.image_id).where(Images.image_id == image_id))  # type: ignore[call-overload]
    if image_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Image not found")

    total = (
        await db.execute(
            select(func.count())
            .select_from(ImageMetadataHistory)
            .where(ImageMetadataHistory.image_id == image_id)  # type: ignore[arg-type]
        )
    ).scalar() or 0

    query = (
        select(ImageMetadataHistory, Users)
        .outerjoin(Users, ImageMetadataHistory.user_id == Users.user_id)  # type: ignore[arg-type]
        .options(
            selectinload(Users.user_groups).selectinload(UserGroups.group)  # type: ignore[arg-type]
        )
        .where(ImageMetadataHistory.image_id == image_id)  # type: ignore[arg-type]
        .order_by(
            desc(ImageMetadataHistory.created_at),  # type: ignore[arg-type]
            desc(ImageMetadataHistory.id),  # type: ignore[arg-type]
        )
        .offset(pagination.offset)
        .limit(pagination.per_page)
    )
    rows = (await db.execute(query)).all()

    items = [
        ImageMetadataHistoryResponse(
            id=history.id,
            image_id=history.image_id,
            field=history.field,
            old_value=history.old_value,
            new_value=history.new_value,
            user=UserSummary(
                user_id=user.user_id,
                username=user.username,
                avatar=user.avatar,
                avatar_in_r2=user.avatar_in_r2,
                user_title=user.user_title,
                groups=user.groups,
            )
            if user
            else None,
            created_at=history.created_at,
        )
        for history, user in rows
    ]

    return ImageMetadataHistoryListResponse(
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        items=items,
    )
```

The `# type: ignore` codes mirror `get_image_status_history`; drop or change any that mypy reports as unused or wrong. Do not add ignores mypy does not ask for.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_image_metadata_history_endpoint.py -q`
Expected: all PASS.

- [ ] **Step 6: Type-check, lint, commit**

```bash
uv run mypy app/ && uv run ruff check && uv run ruff format --check
git add app/schemas/audit.py app/api/v1/images.py tests/api/v1/test_image_metadata_history_endpoint.py
git commit -m "feat(images): GET /images/{id}/metadata-history"
```

---

### Task 4: Metadata edits in `GET /users/{user_id}/history`

**Files:**
- Modify: `app/schemas/audit.py` (`UserHistoryItem`)
- Modify: `app/api/v1/history.py` (union branch, total, hydration, dispatch, docstrings)
- Test: `tests/api/v1/test_user_history_endpoint.py` (new tests inside `TestGetUserHistory`, after `test_event_id_unique_across_all_four_kinds`, reusing its `_make_user` / `_make_image` helpers)

**Interfaces:**
- Consumes: `ImageMetadataHistory` (Task 2).
- Produces: `UserHistoryItem.type` gains `"image_metadata"`; new optional fields `field: Literal["miscmeta", "source_url"] | None`, `old_value: str | None`, `new_value: str | None`; items of this type set `image_id`, `created_at`, and `event_id = "5-{id}-0"`.

- [ ] **Step 1: Write the failing tests**

Add `from app.models.image_metadata_history import ImageMetadataHistory` to the imports of `tests/api/v1/test_user_history_endpoint.py`, then add inside `TestGetUserHistory`:

```python
    async def test_returns_image_metadata_items_correctly(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await self._make_user(db_session, "userhistmetaedit")
        image = await self._make_image(db_session, user, "userhistmetaedit")
        db_session.add(
            ImageMetadataHistory(
                image_id=image.image_id,
                user_id=user.user_id,
                field="source_url",
                old_value=None,
                new_value="https://example.com/src",
            )
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/users/{user.user_id}/history")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        item = data["items"][0]
        assert item["type"] == "image_metadata"
        assert item["image_id"] == image.image_id
        assert item["field"] == "source_url"
        assert item["old_value"] is None
        assert item["new_value"] == "https://example.com/src"
        assert item["created_at"] is not None
        assert item["event_id"].startswith("5-")

    async def test_other_users_metadata_edits_absent(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await self._make_user(db_session, "userhistmetaself")
        other = await self._make_user(db_session, "userhistmetaother")
        image = await self._make_image(db_session, user, "userhistmetaother")
        db_session.add(
            ImageMetadataHistory(
                image_id=image.image_id,
                user_id=other.user_id,
                field="miscmeta",
                old_value=None,
                new_value="not mine",
            )
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/users/{user.user_id}/history")

        assert response.json()["total"] == 0
        assert response.json()["items"] == []

    async def test_image_metadata_interleaves_and_breaks_ties(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """By date first; on an exact tie, status (prio 3) beats both prio-1
        kinds, and image_metadata (kind 5) beats tag audit (kind 1)."""
        user = await self._make_user(db_session, "userhistmetaorder")
        tag = Tags(title="metadata order tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)
        image = await self._make_image(db_session, user, "userhistmetaorder")

        base_date = datetime(2026, 1, 1, tzinfo=UTC)
        tie = base_date + timedelta(days=1)
        db_session.add(
            ImageMetadataHistory(
                image_id=image.image_id, user_id=user.user_id, field="miscmeta",
                old_value=None, new_value="oldest", created_at=base_date,
            )
        )
        db_session.add(
            TagAuditLog(
                tag_id=tag.tag_id, user_id=user.user_id,
                action_type=TagAuditActionType.RENAME, old_title="old",
                new_title="metadata order tag", created_at=tie,
            )
        )
        db_session.add(
            ImageMetadataHistory(
                image_id=image.image_id, user_id=user.user_id, field="source_url",
                old_value=None, new_value="https://example.com/tie", created_at=tie,
            )
        )
        db_session.add(
            ImageStatusHistory(
                image_id=image.image_id, user_id=user.user_id,
                old_status=ImageStatus.ACTIVE, new_status=ImageStatus.SPOILER, created_at=tie,
            )
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/users/{user.user_id}/history")

        items = response.json()["items"]
        assert [(i["type"], i.get("field")) for i in items] == [
            ("status_change", None),
            ("image_metadata", "source_url"),
            ("tag_metadata", None),
            ("image_metadata", "miscmeta"),
        ]

    async def test_pagination_lossless_across_metadata_ties(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Walking one item per page yields exactly the one-page order: no
        duplicates or gaps where metadata edits tie with tag audit rows."""
        user = await self._make_user(db_session, "userhistmetapage")
        tag = Tags(title="metadata page tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)
        image = await self._make_image(db_session, user, "userhistmetapage")

        base_date = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(4):
            same_second = base_date + timedelta(days=i)
            db_session.add(
                ImageMetadataHistory(
                    image_id=image.image_id, user_id=user.user_id, field="miscmeta",
                    old_value=None, new_value=f"v{i}", created_at=same_second,
                )
            )
            db_session.add(
                TagAuditLog(
                    tag_id=tag.tag_id, user_id=user.user_id,
                    action_type=TagAuditActionType.RENAME, old_title=f"t{i}",
                    new_title="metadata page tag", created_at=same_second,
                )
            )
        await db_session.commit()

        full = await client.get(f"/api/v1/users/{user.user_id}/history?per_page=100")
        expected = [item["event_id"] for item in full.json()["items"]]
        assert full.json()["total"] == 8
        assert len(expected) == 8

        walked = []
        for page in range(1, 9):
            response = await client.get(
                f"/api/v1/users/{user.user_id}/history?page={page}&per_page=1"
            )
            walked.extend(item["event_id"] for item in response.json()["items"])

        assert walked == expected
```

(`ruff format` will reflow the compact keyword arguments.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_user_history_endpoint.py -q -k "metadata_items or metadata_edits_absent or metadata_interleaves or metadata_ties"`
Expected: `test_returns_image_metadata_items_correctly` FAILs (`total == 0`), the ordering and pagination tests FAIL (metadata rows missing), `test_other_users_metadata_edits_absent` passes for the wrong reason.

- [ ] **Step 3: Extend `UserHistoryItem`**

In `app/schemas/audit.py`:
- In the class docstring's type list, add the line `- image_metadata: image_id, field, old_value, new_value, created_at`.
- Change `type: Literal["tag_metadata", "tag_usage", "status_change"]` to `type: Literal["tag_metadata", "tag_usage", "status_change", "image_metadata"]`.
- Change the `created_at` comment to `# For tag_metadata, status_change and image_metadata`.
- After the `new_status_label` field, add:

```python
    # For image_metadata: which image field changed, and its values (None
    # means unset before the edit, or cleared by it)
    field: Literal["miscmeta", "source_url"] | None = None
    old_value: str | None = None
    new_value: str | None = None
```

- [ ] **Step 4: Add the union branch, total, and hydration**

In `app/api/v1/history.py`, import `ImageMetadataHistory` from `app.models.image_metadata_history`.

In `_user_history_union`, after `status_branch`:

```python
    metadata_branch = (
        select(
            literal(5).label("kind"),
            ImageMetadataHistory.id.label("id_a"),  # type: ignore[union-attr]
            literal(0).label("id_b"),
            ImageMetadataHistory.created_at.label("ts"),  # type: ignore[union-attr]
            literal(1).label("prio"),
            ImageMetadataHistory.id.label("tiebreak"),  # type: ignore[union-attr]
        )
        .where(ImageMetadataHistory.user_id == user_id)  # type: ignore[arg-type]
        .order_by(desc(ImageMetadataHistory.created_at), desc(ImageMetadataHistory.id))  # type: ignore[arg-type]
        .limit(branch_limit)
    )
```

and add it to the union: `union_all(audit_branch, history_branch, link_branch, status_branch, metadata_branch)`.

Update the union's docstring:
- First line: "Paginated UNION ALL of a user's five history sources."
- The kind list: "(1=audit, 2=tag_history, 3=tag_links, 4=status_history, 5=image_metadata_history)".
- The prio sentence: "`prio` reproduces the per-type ordering (status > tag usage > tag audit = image metadata on a timestamp tie)".
- The `kind` sentence: after "because kinds 2 and 3 share prio 2 but draw tiebreaks from unrelated id spaces (tag_history_id vs image_id)", add "and kinds 1 and 5 share prio 1 with unrelated id spaces (tag_audit_log.id vs image_metadata_history.id)".
- Where it says the kind-1/2/4 branches have `id_a` equal to `tiebreak`, make it "kinds 1/2/4/5", and change "the other three branches" to "the other four branches".

In `_user_history_total`, before the `return`:

```python
    metadata_total = (
        await db.execute(
            select(func.count())
            .select_from(ImageMetadataHistory)
            .where(ImageMetadataHistory.user_id == user_id)  # type: ignore[arg-type]
        )
    ).scalar() or 0
```

return `audit_total + history_total + link_total + status_total + metadata_total`, and change the docstring's first line to "Total history events for a user: sum of five plain COUNTs."

After `_hydrate_status_change_items`:

```python
async def _hydrate_image_metadata_items(
    db: AsyncSession, ids: list[int]
) -> dict[int, UserHistoryItem]:
    """Load kind-5 (image_metadata) rows for the given ImageMetadataHistory ids."""
    if not ids:
        return {}
    rows = (
        (
            await db.execute(
                select(ImageMetadataHistory).where(ImageMetadataHistory.id.in_(ids))  # type: ignore[union-attr]
            )
        )
        .scalars()
        .all()
    )
    return {
        row.id or 0: UserHistoryItem(
            type="image_metadata",
            image_id=row.image_id,
            field=row.field,  # type: ignore[arg-type]
            old_value=row.old_value,
            new_value=row.new_value,
            created_at=row.created_at,
        )
        for row in rows
    }
```

(`row.field` is a plain `str` on the model and a `Literal` on the schema; pydantic validates it at runtime. Keep the ignore only if mypy asks for it.)

In `get_user_history`:
- after the `status_items = ...` line add `image_metadata_items = await _hydrate_image_metadata_items(db, [r.id_a for r in rows if r.kind == 5])`
- change the final `else: item = status_items[row.id_a]` to:

```python
        elif row.kind == 4:
            item = status_items[row.id_a]
        else:
            item = image_metadata_items[row.id_a]
```

- in the endpoint docstring's "Aggregates history from:" list add `- Image metadata history (miscmeta/source_url edits; always public)`.

- [ ] **Step 5: Run the whole user-history file**

Run: `uv run pytest tests/api/v1/test_user_history_endpoint.py -q`
Expected: all PASS, the pre-existing tests included (they pin the other four kinds' order and must not move).

- [ ] **Step 6: Type-check, lint, commit**

```bash
uv run mypy app/ && uv run ruff check && uv run ruff format --check
git add app/schemas/audit.py app/api/v1/history.py tests/api/v1/test_user_history_endpoint.py
git commit -m "feat(history): image metadata edits in the user history feed"
```

---

### Task 5: Full verification and PR

**Files:** none new.

- [ ] **Step 1: Run the full suite the way CI does**

Run: `uv run pytest tests/ -q --tb=short --schema-sync -n auto --dist loadgroup`
Expected: all PASS, no warnings or errors in the output. A failure is this branch's to explain: if one looks unrelated, run the same test on `main` before calling it pre-existing, and report it either way.

- [ ] **Step 2: Static checks**

Run: `uv run mypy app/ && uv run ruff check && uv run ruff format --check`
Expected: clean.

- [ ] **Step 3: Migrate the dev database**

The frontend plan's e2e tests run against the dev API. The `shuushuu-api` container bind-mounts `./app` and `./alembic` from this checkout and reloads on change, so it is already serving this branch. Apply the migration inside it (`uv run` is broken in that container; call the venv directly):

```bash
docker exec shuushuu-api /app/.venv/bin/alembic upgrade head
docker exec shuushuu-api /app/.venv/bin/alembic current   # expect 0005_image_metadata_history (head)
curl -s http://localhost:8000/api/v1/images/1/metadata-history
```

The curl must return JSON with `total` and `items`, or `{"detail":"Image not found"}` if image 1 does not exist. `{"detail":"Not Found"}` means the route is missing: the container is not serving this branch.

- [ ] **Step 4: Push and open the PR**

Push `feat/image-metadata-history` and open a PR against `main` with `gh pr create`. The body summarizes the four behaviour changes (PATCH `source_url`, `miscmeta` bounds, the history table and endpoint, the user-history kind) and the deploy note: **run the migration before deploying the frontend.** End the body with the session's PR attribution lines.
