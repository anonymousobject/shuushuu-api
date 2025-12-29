# Tag Suggestions for Missing Tags Reports - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow users to suggest specific tags when filing MISSING_TAGS reports, with admin review and contribution tracking.

**Architecture:** New `image_report_tag_suggestions` table stores suggested tags per report. Report creation validates and filters tags. Admins use a single endpoint to approve/reject suggestions, which immediately applies approved tags to the image.

**Tech Stack:** FastAPI, SQLModel, Alembic, MariaDB, pytest

---

## Task 1: Create the ImageReportTagSuggestions Model

**Files:**
- Create: `app/models/image_report_tag_suggestion.py`
- Modify: `app/models/__init__.py`

**Step 1: Write the model file**

Create `app/models/image_report_tag_suggestion.py`:

```python
"""
SQLModel-based ImageReportTagSuggestion model.

This table stores tag suggestions made by users when filing MISSING_TAGS reports.
Each suggestion tracks whether it was accepted/rejected by moderators for
contribution metrics and potential promotion to tagging roles.
"""

from datetime import datetime

from sqlalchemy import Column, ForeignKey, Index, Integer, text
from sqlmodel import Field, SQLModel


class ImageReportTagSuggestionBase(SQLModel):
    """
    Base model with shared fields for ImageReportTagSuggestions.
    """

    report_id: int
    tag_id: int
    accepted: bool | None = Field(default=None)  # NULL=pending, True=approved, False=rejected


class ImageReportTagSuggestions(ImageReportTagSuggestionBase, table=True):
    """
    Database table for tag suggestions in image reports.

    Stores tags suggested by users when filing MISSING_TAGS reports.
    Tracks acceptance/rejection for contribution metrics.
    """

    __tablename__ = "image_report_tag_suggestions"

    __table_args__ = (
        Index("idx_report_id", "report_id"),
        Index("idx_tag_id", "tag_id"),
        Index("idx_accepted", "accepted"),
    )

    suggestion_id: int | None = Field(default=None, primary_key=True)

    report_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("image_reports.report_id", ondelete="CASCADE", onupdate="CASCADE"),
            nullable=False,
        )
    )

    tag_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("tags.tag_id", ondelete="CASCADE", onupdate="CASCADE"),
            nullable=False,
        )
    )

    created_at: datetime | None = Field(
        default=None, sa_column_kwargs={"server_default": text("current_timestamp()")}
    )
```

**Step 2: Export the model**

Edit `app/models/__init__.py` - add import and export:

```python
# Add import after other imports
from app.models.image_report_tag_suggestion import ImageReportTagSuggestions

# Add to __all__ list after "ImageReports"
"ImageReportTagSuggestions",
```

**Step 3: Verify no syntax errors**

Run: `uv run python -c "from app.models import ImageReportTagSuggestions; print('OK')"`

Expected: `OK`

**Step 4: Commit**

```bash
git add app/models/image_report_tag_suggestion.py app/models/__init__.py
git commit -m "feat: add ImageReportTagSuggestions model"
```

---

## Task 2: Create the Alembic Migration

**Files:**
- Create: `alembic/versions/xxxx_add_tag_suggestions_table.py`

**Step 1: Create migration file**

Run: `uv run alembic revision -m "add tag suggestions table and admin notes"`

**Step 2: Edit the migration file**

Replace the generated content with:

```python
"""add tag suggestions table and admin notes

Revision ID: [auto-generated]
Revises: [auto-generated]
Create Date: [auto-generated]

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = '[auto-generated]'
down_revision: str | Sequence[str] | None = '[auto-generated]'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create image_report_tag_suggestions table
    op.create_table(
        'image_report_tag_suggestions',
        sa.Column('suggestion_id', mysql.INTEGER(unsigned=True), nullable=False, autoincrement=True),
        sa.Column('report_id', mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column('tag_id', mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column('accepted', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('current_timestamp()'), nullable=True),
        sa.ForeignKeyConstraint(['report_id'], ['image_reports.report_id'], name='fk_suggestions_report_id', ondelete='CASCADE', onupdate='CASCADE'),
        sa.ForeignKeyConstraint(['tag_id'], ['tags.tag_id'], name='fk_suggestions_tag_id', ondelete='CASCADE', onupdate='CASCADE'),
        sa.PrimaryKeyConstraint('suggestion_id'),
        sa.UniqueConstraint('report_id', 'tag_id', name='unique_report_tag')
    )
    op.create_index('idx_report_id', 'image_report_tag_suggestions', ['report_id'], unique=False)
    op.create_index('idx_tag_id', 'image_report_tag_suggestions', ['tag_id'], unique=False)
    op.create_index('idx_accepted', 'image_report_tag_suggestions', ['accepted'], unique=False)

    # Add admin_notes column to image_reports
    op.add_column('image_reports', sa.Column('admin_notes', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # Remove admin_notes column from image_reports
    op.drop_column('image_reports', 'admin_notes')

    # Drop image_report_tag_suggestions table
    op.drop_index('idx_accepted', table_name='image_report_tag_suggestions')
    op.drop_index('idx_tag_id', table_name='image_report_tag_suggestions')
    op.drop_index('idx_report_id', table_name='image_report_tag_suggestions')
    op.drop_table('image_report_tag_suggestions')
```

**Step 3: Run the migration**

Run: `uv run alembic upgrade head`

Expected: Migration applies successfully

**Step 4: Verify table exists**

Run: `uv run python -c "from app.models import ImageReportTagSuggestions; print('Table ready')"`

Expected: `Table ready`

**Step 5: Commit**

```bash
git add alembic/versions/
git commit -m "migration: add tag suggestions table and admin_notes column"
```

---

## Task 3: Update the ImageReports Model

**Files:**
- Modify: `app/models/image_report.py`

**Step 1: Add admin_notes field to ImageReportBase**

In `app/models/image_report.py`, add to `ImageReportBase` class:

```python
# Add after reason_text field
admin_notes: str | None = Field(default=None)
```

**Step 2: Verify model loads**

Run: `uv run python -c "from app.models import ImageReports; print(ImageReports.__table__.columns.keys())"`

Expected: Output includes `admin_notes`

**Step 3: Commit**

```bash
git add app/models/image_report.py
git commit -m "feat: add admin_notes field to ImageReports model"
```

---

## Task 4: Update Report Schemas

**Files:**
- Modify: `app/schemas/report.py`

**Step 1: Add new schema classes**

Add these classes to `app/schemas/report.py` after imports:

```python
from app.config import ReportCategory

# Add after existing imports, before ReportCreate class

class TagSuggestion(BaseModel):
    """Schema for a tag suggestion in a report response."""
    suggestion_id: int
    tag_id: int
    tag_name: str
    tag_type: int | None = None
    accepted: bool | None = None  # NULL=pending, True=approved, False=rejected

    model_config = {"from_attributes": True}


class SkippedTagsInfo(BaseModel):
    """Feedback about tags that were skipped during report creation."""
    already_on_image: list[int] = []  # Tag IDs already applied to image
    invalid_tag_ids: list[int] = []   # Tag IDs that don't exist
```

**Step 2: Update ReportCreate schema**

Modify `ReportCreate` class to add `suggested_tag_ids` field:

```python
class ReportCreate(BaseModel):
    """Schema for creating a new image report."""

    category: int = Field(
        ...,
        description="Report category (1=repost, 2=inappropriate, 3=spam, 4=missing_tags, 127=other)",
    )
    reason_text: str | None = Field(None, max_length=1000, description="Optional explanation")
    suggested_tag_ids: list[int] | None = Field(
        None,
        description="Tag IDs to suggest (only for MISSING_TAGS category)",
    )

    @field_validator("reason_text")
    @classmethod
    def sanitize_reason_text(cls, v: str | None) -> str | None:
        """Trim whitespace from report reason."""
        if v is None:
            return v
        return v.strip()

    @field_validator("suggested_tag_ids")
    @classmethod
    def dedupe_tag_ids(cls, v: list[int] | None) -> list[int] | None:
        """Remove duplicate tag IDs while preserving order."""
        if v is None:
            return v
        return list(dict.fromkeys(v))

    @model_validator(mode="after")
    def validate_tag_suggestions(self) -> "ReportCreate":
        """Validate tag suggestions are only for MISSING_TAGS category."""
        if self.suggested_tag_ids and self.category != ReportCategory.MISSING_TAGS:
            raise ValueError("Tag suggestions only allowed for MISSING_TAGS reports")
        return self
```

**Step 3: Update ReportResponse schema**

Add new fields to `ReportResponse` class:

```python
class ReportResponse(BaseModel):
    """Response schema for a report."""

    report_id: int
    image_id: int
    user_id: int
    username: str | None = None
    category: int | None
    category_label: str | None = None
    reason_text: str | None
    status: int
    status_label: str | None = None
    created_at: datetime | None
    reviewed_by: int | None
    reviewed_at: datetime | None
    admin_notes: str | None = None  # NEW
    suggested_tags: list[TagSuggestion] | None = None  # NEW
    skipped_tags: SkippedTagsInfo | None = None  # NEW (only in create response)

    model_config = {"from_attributes": True}

    def model_post_init(self, __context: object) -> None:
        """Set computed label fields."""
        # Category label
        if self.category is not None:
            self.category_label = ReportCategory.LABELS.get(self.category, "Unknown")
        # Status label
        status_labels = {0: "Pending", 1: "Reviewed", 2: "Dismissed"}
        self.status_label = status_labels.get(self.status, "Unknown")
```

**Step 4: Add model_validator import**

Add to imports at top:

```python
from pydantic import BaseModel, Field, field_validator, model_validator
```

**Step 5: Verify schemas load**

Run: `uv run python -c "from app.schemas.report import ReportCreate, ReportResponse, TagSuggestion, SkippedTagsInfo; print('OK')"`

Expected: `OK`

**Step 6: Commit**

```bash
git add app/schemas/report.py
git commit -m "feat: add tag suggestion schemas for reports"
```

---

## Task 5: Write Failing Tests for Report Creation with Tag Suggestions

**Files:**
- Modify: `tests/api/v1/test_reports.py`

**Step 1: Add test helper to create tags**

Add this function after existing helpers:

```python
async def create_test_tags(db_session: AsyncSession, count: int = 3) -> list[Tags]:
    """Create test tags and return them."""
    from app.models.tag import Tags

    tags = []
    for i in range(count):
        tag = Tags(
            title=f"test_tag_{i}",
            type=1,  # Theme
        )
        db_session.add(tag)
        tags.append(tag)
    await db_session.commit()
    for tag in tags:
        await db_session.refresh(tag)
    return tags


async def add_tag_to_image(db_session: AsyncSession, image_id: int, tag_id: int) -> None:
    """Add a tag to an image."""
    from app.models.tag_link import TagLinks

    tag_link = TagLinks(image_id=image_id, tag_id=tag_id)
    db_session.add(tag_link)
    await db_session.commit()
```

**Step 2: Add import for Tags model**

Add to imports:

```python
from app.models.tag import Tags
from app.models.tag_link import TagLinks
```

**Step 3: Add test class for tag suggestions**

Add new test class:

```python
@pytest.mark.api
class TestReportWithTagSuggestions:
    """Tests for MISSING_TAGS reports with tag suggestions."""

    async def test_report_with_valid_tag_suggestions(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test creating MISSING_TAGS report with valid tag suggestions."""
        user, password = await create_auth_user(db_session, username="taguser1")
        image = await create_test_image(db_session, user.user_id)
        tags = await create_test_tags(db_session, count=3)
        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/images/{image.image_id}/report",
            json={
                "category": 4,  # MISSING_TAGS
                "reason_text": "Missing character tags",
                "suggested_tag_ids": [t.tag_id for t in tags],
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["category"] == 4
        assert data["suggested_tags"] is not None
        assert len(data["suggested_tags"]) == 3

    async def test_report_skips_invalid_tag_ids(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that invalid tag IDs are skipped and reported."""
        user, password = await create_auth_user(db_session, username="taguser2")
        image = await create_test_image(db_session, user.user_id)
        tags = await create_test_tags(db_session, count=2)
        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/images/{image.image_id}/report",
            json={
                "category": 4,
                "suggested_tag_ids": [tags[0].tag_id, 999999, tags[1].tag_id],  # 999999 is invalid
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = response.json()
        assert len(data["suggested_tags"]) == 2
        assert data["skipped_tags"]["invalid_tag_ids"] == [999999]

    async def test_report_skips_tags_already_on_image(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that tags already on image are skipped and reported."""
        user, password = await create_auth_user(db_session, username="taguser3")
        image = await create_test_image(db_session, user.user_id)
        tags = await create_test_tags(db_session, count=3)
        # Add first tag to image
        await add_tag_to_image(db_session, image.image_id, tags[0].tag_id)
        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/images/{image.image_id}/report",
            json={
                "category": 4,
                "suggested_tag_ids": [t.tag_id for t in tags],
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = response.json()
        assert len(data["suggested_tags"]) == 2
        assert tags[0].tag_id in data["skipped_tags"]["already_on_image"]

    async def test_report_rejects_tag_suggestions_for_non_missing_tags(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that tag suggestions are rejected for non-MISSING_TAGS categories."""
        user, password = await create_auth_user(db_session, username="taguser4")
        image = await create_test_image(db_session, user.user_id)
        tags = await create_test_tags(db_session, count=2)
        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/images/{image.image_id}/report",
            json={
                "category": 1,  # REPOST, not MISSING_TAGS
                "suggested_tag_ids": [t.tag_id for t in tags],
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 422

    async def test_report_with_duplicate_tag_ids_dedupes(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that duplicate tag IDs are deduplicated."""
        user, password = await create_auth_user(db_session, username="taguser5")
        image = await create_test_image(db_session, user.user_id)
        tags = await create_test_tags(db_session, count=2)
        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/images/{image.image_id}/report",
            json={
                "category": 4,
                "suggested_tag_ids": [tags[0].tag_id, tags[0].tag_id, tags[1].tag_id],  # Duplicate
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = response.json()
        assert len(data["suggested_tags"]) == 2

    async def test_report_missing_tags_without_suggestions_still_works(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test MISSING_TAGS report without suggestions (just reason_text) still works."""
        user, password = await create_auth_user(db_session, username="taguser6")
        image = await create_test_image(db_session, user.user_id)
        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/images/{image.image_id}/report",
            json={
                "category": 4,
                "reason_text": "Missing some tags but I don't know which ones",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["suggested_tags"] is None or len(data["suggested_tags"]) == 0
```

**Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_reports.py::TestReportWithTagSuggestions -v`

Expected: All tests FAIL (features not implemented yet)

**Step 5: Commit**

```bash
git add tests/api/v1/test_reports.py
git commit -m "test: add failing tests for tag suggestions in reports"
```

---

## Task 6: Implement Report Creation with Tag Suggestions

**Files:**
- Modify: `app/api/v1/images.py`

**Step 1: Add imports**

Add these imports to `app/api/v1/images.py`:

```python
from app.models.image_report_tag_suggestion import ImageReportTagSuggestions
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.schemas.report import SkippedTagsInfo, TagSuggestion
```

**Step 2: Update report_image function**

Replace the `report_image` function (around line 1159) with:

```python
@router.post(
    "/{image_id}/report", response_model=ReportResponse, status_code=status.HTTP_201_CREATED
)
async def report_image(
    image_id: Annotated[int, Path(description="Image ID to report")],
    report_data: ReportCreate,
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> ReportResponse:
    """
    Report an image for review.

    Users can report images for various reasons:
    - 1: Repost (duplicate of another image)
    - 2: Inappropriate content
    - 3: Spam
    - 4: Missing tags (can include tag suggestions)
    - 5: Spoiler
    - 127: Other

    For MISSING_TAGS (category 4), users can optionally include a list of
    suggested_tag_ids. Invalid tags and tags already on the image are
    skipped and reported in the response.

    A user can only have one pending report per image. The report goes into
    a triage queue for admin review.

    Requires authentication.
    """
    # Verify image exists
    image_result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
    image = image_result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Check if user already has a pending report for this image
    existing_report = await db.execute(
        select(ImageReports).where(
            ImageReports.image_id == image_id,  # type: ignore[arg-type]
            ImageReports.user_id == current_user.id,  # type: ignore[arg-type]
            ImageReports.status == ReportStatus.PENDING,  # type: ignore[arg-type]
        )
    )
    if existing_report.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="You already have a pending report for this image",
        )

    # Process tag suggestions for MISSING_TAGS category
    skipped_tags = SkippedTagsInfo()
    valid_tag_ids: list[int] = []

    if (
        report_data.category == ReportCategory.MISSING_TAGS
        and report_data.suggested_tag_ids
    ):
        # Get existing tags on image
        existing_tags_result = await db.execute(
            select(TagLinks.tag_id).where(TagLinks.image_id == image_id)  # type: ignore[arg-type]
        )
        existing_tag_ids = set(existing_tags_result.scalars().all())

        # Validate which tag IDs exist
        valid_tags_result = await db.execute(
            select(Tags.tag_id).where(Tags.tag_id.in_(report_data.suggested_tag_ids))  # type: ignore[union-attr]
        )
        valid_db_tag_ids = set(valid_tags_result.scalars().all())

        for tag_id in report_data.suggested_tag_ids:
            if tag_id not in valid_db_tag_ids:
                skipped_tags.invalid_tag_ids.append(tag_id)
            elif tag_id in existing_tag_ids:
                skipped_tags.already_on_image.append(tag_id)
            else:
                valid_tag_ids.append(tag_id)

    # Create the report
    new_report = ImageReports(
        image_id=image_id,
        user_id=current_user.id,
        category=report_data.category,
        reason_text=report_data.reason_text,
        status=ReportStatus.PENDING,
    )
    db.add(new_report)
    await db.flush()  # Get report_id

    # Create tag suggestions
    suggestions: list[ImageReportTagSuggestions] = []
    for tag_id in valid_tag_ids:
        suggestion = ImageReportTagSuggestions(
            report_id=new_report.report_id,
            tag_id=tag_id,
        )
        db.add(suggestion)
        suggestions.append(suggestion)

    await db.commit()
    await db.refresh(new_report)
    for s in suggestions:
        await db.refresh(s)

    logger.info(
        "image_reported",
        report_id=new_report.report_id,
        image_id=image_id,
        user_id=current_user.id,
        category=report_data.category,
        tag_suggestions_count=len(suggestions),
    )

    # Build response
    response = ReportResponse.model_validate(new_report)
    response.username = current_user.username

    # Add tag suggestions to response
    if suggestions:
        # Fetch tag names for the suggestions
        tag_ids = [s.tag_id for s in suggestions]
        tags_result = await db.execute(
            select(Tags).where(Tags.tag_id.in_(tag_ids))  # type: ignore[union-attr]
        )
        tags_by_id = {t.tag_id: t for t in tags_result.scalars().all()}

        response.suggested_tags = [
            TagSuggestion(
                suggestion_id=s.suggestion_id or 0,
                tag_id=s.tag_id,
                tag_name=tags_by_id[s.tag_id].title or "",
                tag_type=tags_by_id[s.tag_id].type,
                accepted=s.accepted,
            )
            for s in suggestions
        ]

    # Include skipped tags info if any were skipped
    if skipped_tags.invalid_tag_ids or skipped_tags.already_on_image:
        response.skipped_tags = skipped_tags

    return response
```

**Step 3: Run tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_reports.py::TestReportWithTagSuggestions -v`

Expected: All tests PASS

**Step 4: Commit**

```bash
git add app/api/v1/images.py
git commit -m "feat: implement tag suggestions for MISSING_TAGS reports"
```

---

## Task 7: Write Failing Tests for Admin Apply Tag Suggestions

**Files:**
- Modify: `tests/api/v1/test_reports.py`

**Step 1: Add test class for apply endpoint**

Add new test class:

```python
@pytest.mark.api
class TestAdminApplyTagSuggestions:
    """Tests for POST /api/v1/admin/reports/{report_id}/apply-tag-suggestions endpoint."""

    async def test_apply_all_suggestions(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test applying all tag suggestions to an image."""
        admin, password = await create_auth_user(db_session, username="applytest1", admin=True)
        await grant_permission(db_session, admin.user_id, "report_manage")
        image = await create_test_image(db_session, admin.user_id)
        tags = await create_test_tags(db_session, count=3)
        token = await login_user(client, admin.username, password)

        # Create report with suggestions
        report = ImageReports(
            image_id=image.image_id,
            user_id=admin.user_id,
            category=4,
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.flush()

        suggestions = []
        for tag in tags:
            s = ImageReportTagSuggestions(report_id=report.report_id, tag_id=tag.tag_id)
            db_session.add(s)
            suggestions.append(s)
        await db_session.commit()
        for s in suggestions:
            await db_session.refresh(s)

        response = await client.post(
            f"/api/v1/admin/reports/{report.report_id}/apply-tag-suggestions",
            json={"approved_suggestion_ids": [s.suggestion_id for s in suggestions]},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["applied_tags"]) == 3

        # Verify tags were added to image
        from sqlalchemy import select as sql_select
        tag_links = await db_session.execute(
            sql_select(TagLinks).where(TagLinks.image_id == image.image_id)
        )
        assert len(tag_links.scalars().all()) == 3

    async def test_apply_partial_suggestions(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test applying only some tag suggestions."""
        admin, password = await create_auth_user(db_session, username="applytest2", admin=True)
        await grant_permission(db_session, admin.user_id, "report_manage")
        image = await create_test_image(db_session, admin.user_id)
        tags = await create_test_tags(db_session, count=3)
        token = await login_user(client, admin.username, password)

        report = ImageReports(
            image_id=image.image_id,
            user_id=admin.user_id,
            category=4,
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.flush()

        suggestions = []
        for tag in tags:
            s = ImageReportTagSuggestions(report_id=report.report_id, tag_id=tag.tag_id)
            db_session.add(s)
            suggestions.append(s)
        await db_session.commit()
        for s in suggestions:
            await db_session.refresh(s)

        # Only approve first 2 suggestions
        response = await client.post(
            f"/api/v1/admin/reports/{report.report_id}/apply-tag-suggestions",
            json={"approved_suggestion_ids": [suggestions[0].suggestion_id, suggestions[1].suggestion_id]},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["applied_tags"]) == 2

        # Verify suggestions were marked correctly
        await db_session.refresh(suggestions[0])
        await db_session.refresh(suggestions[1])
        await db_session.refresh(suggestions[2])
        assert suggestions[0].accepted is True
        assert suggestions[1].accepted is True
        assert suggestions[2].accepted is False

    async def test_apply_empty_list_rejects_all(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test applying empty list rejects all suggestions."""
        admin, password = await create_auth_user(db_session, username="applytest3", admin=True)
        await grant_permission(db_session, admin.user_id, "report_manage")
        image = await create_test_image(db_session, admin.user_id)
        tags = await create_test_tags(db_session, count=2)
        token = await login_user(client, admin.username, password)

        report = ImageReports(
            image_id=image.image_id,
            user_id=admin.user_id,
            category=4,
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.flush()

        suggestions = []
        for tag in tags:
            s = ImageReportTagSuggestions(report_id=report.report_id, tag_id=tag.tag_id)
            db_session.add(s)
            suggestions.append(s)
        await db_session.commit()
        for s in suggestions:
            await db_session.refresh(s)

        response = await client.post(
            f"/api/v1/admin/reports/{report.report_id}/apply-tag-suggestions",
            json={"approved_suggestion_ids": []},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200

        # Verify all suggestions rejected
        await db_session.refresh(suggestions[0])
        await db_session.refresh(suggestions[1])
        assert suggestions[0].accepted is False
        assert suggestions[1].accepted is False

    async def test_apply_with_admin_notes(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test applying suggestions with admin notes."""
        admin, password = await create_auth_user(db_session, username="applytest4", admin=True)
        await grant_permission(db_session, admin.user_id, "report_manage")
        image = await create_test_image(db_session, admin.user_id)
        tags = await create_test_tags(db_session, count=1)
        token = await login_user(client, admin.username, password)

        report = ImageReports(
            image_id=image.image_id,
            user_id=admin.user_id,
            category=4,
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.flush()

        s = ImageReportTagSuggestions(report_id=report.report_id, tag_id=tags[0].tag_id)
        db_session.add(s)
        await db_session.commit()
        await db_session.refresh(s)

        response = await client.post(
            f"/api/v1/admin/reports/{report.report_id}/apply-tag-suggestions",
            json={
                "approved_suggestion_ids": [s.suggestion_id],
                "admin_notes": "Good suggestions, approved all.",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200

        # Verify admin notes saved
        await db_session.refresh(report)
        assert report.admin_notes == "Good suggestions, approved all."

    async def test_apply_to_nonexistent_report_fails(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test applying to nonexistent report returns 404."""
        admin, password = await create_auth_user(db_session, username="applytest5", admin=True)
        await grant_permission(db_session, admin.user_id, "report_manage")
        token = await login_user(client, admin.username, password)

        response = await client.post(
            "/api/v1/admin/reports/999999/apply-tag-suggestions",
            json={"approved_suggestion_ids": []},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404

    async def test_apply_without_permission_fails(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test applying without REPORT_MANAGE permission fails."""
        user, password = await create_auth_user(db_session, username="noperm5")
        token = await login_user(client, user.username, password)

        response = await client.post(
            "/api/v1/admin/reports/1/apply-tag-suggestions",
            json={"approved_suggestion_ids": []},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403
```

**Step 2: Add import for ImageReportTagSuggestions**

Add to imports:

```python
from app.models.image_report_tag_suggestion import ImageReportTagSuggestions
```

**Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_reports.py::TestAdminApplyTagSuggestions -v`

Expected: All tests FAIL (endpoint not implemented yet)

**Step 4: Commit**

```bash
git add tests/api/v1/test_reports.py
git commit -m "test: add failing tests for apply tag suggestions endpoint"
```

---

## Task 8: Implement Admin Apply Tag Suggestions Endpoint

**Files:**
- Modify: `app/api/v1/admin.py`
- Modify: `app/schemas/report.py`

**Step 1: Add new schemas to report.py**

Add these schemas to `app/schemas/report.py`:

```python
class ApplyTagSuggestionsRequest(BaseModel):
    """Request schema for applying tag suggestions."""
    approved_suggestion_ids: list[int] = Field(
        ..., description="IDs of suggestions to approve"
    )
    admin_notes: str | None = Field(None, max_length=2000, description="Optional admin notes")


class ApplyTagSuggestionsResponse(BaseModel):
    """Response schema for apply tag suggestions endpoint."""
    message: str
    applied_tags: list[int]  # Tag IDs actually added to image
    already_present: list[int] = []  # Tag IDs that were already on image
```

**Step 2: Add imports to admin.py**

Add these imports:

```python
from app.models.image_report_tag_suggestion import ImageReportTagSuggestions
from app.models.tag_link import TagLinks
from app.schemas.report import ApplyTagSuggestionsRequest, ApplyTagSuggestionsResponse
```

**Step 3: Add endpoint to admin.py**

Add this endpoint after the `dismiss_report` function:

```python
@router.post(
    "/reports/{report_id}/apply-tag-suggestions",
    response_model=ApplyTagSuggestionsResponse,
)
async def apply_tag_suggestions(
    report_id: Annotated[int, Path(description="Report ID")],
    request_data: ApplyTagSuggestionsRequest,
    current_user: Annotated[Users, Depends(get_current_user)],
    _: Annotated[None, Depends(require_permission(Permission.REPORT_MANAGE))],
    db: AsyncSession = Depends(get_db),
) -> ApplyTagSuggestionsResponse:
    """
    Apply tag suggestions from a MISSING_TAGS report.

    Approves specified suggestions, rejects others, adds approved tags
    to the image, and marks the report as reviewed.

    Requires REPORT_MANAGE permission.
    """
    # Get the report
    result = await db.execute(
        select(ImageReports).where(ImageReports.report_id == report_id)  # type: ignore[arg-type]
    )
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if report.status != ReportStatus.PENDING:
        raise HTTPException(status_code=400, detail="Report has already been processed")

    # Get all suggestions for this report
    suggestions_result = await db.execute(
        select(ImageReportTagSuggestions).where(
            ImageReportTagSuggestions.report_id == report_id  # type: ignore[arg-type]
        )
    )
    suggestions = suggestions_result.scalars().all()

    if not suggestions:
        raise HTTPException(status_code=400, detail="This report has no tag suggestions")

    # Validate approved_suggestion_ids belong to this report
    suggestion_ids = {s.suggestion_id for s in suggestions}
    for sid in request_data.approved_suggestion_ids:
        if sid not in suggestion_ids:
            raise HTTPException(status_code=400, detail=f"Invalid suggestion ID: {sid}")

    # Get existing tags on image
    existing_tags_result = await db.execute(
        select(TagLinks.tag_id).where(TagLinks.image_id == report.image_id)  # type: ignore[arg-type]
    )
    existing_tag_ids = set(existing_tags_result.scalars().all())

    approved_ids = set(request_data.approved_suggestion_ids)
    applied_tags: list[int] = []
    already_present: list[int] = []

    for suggestion in suggestions:
        if suggestion.suggestion_id in approved_ids:
            suggestion.accepted = True
            # Add tag to image if not already present
            if suggestion.tag_id not in existing_tag_ids:
                tag_link = TagLinks(image_id=report.image_id, tag_id=suggestion.tag_id)
                db.add(tag_link)
                applied_tags.append(suggestion.tag_id)
                existing_tag_ids.add(suggestion.tag_id)  # Track to avoid duplicates
            else:
                already_present.append(suggestion.tag_id)
        else:
            suggestion.accepted = False

    # Update report
    report.status = ReportStatus.REVIEWED
    report.reviewed_by = current_user.user_id
    report.reviewed_at = datetime.now(UTC)
    if request_data.admin_notes:
        report.admin_notes = request_data.admin_notes

    # Log action
    action = AdminActions(
        user_id=current_user.user_id,
        action_type=AdminActionType.REPORT_ACTION,
        report_id=report_id,
        image_id=report.image_id,
        details={
            "action": "apply_tag_suggestions",
            "approved_count": len(approved_ids),
            "rejected_count": len(suggestions) - len(approved_ids),
            "applied_tags": applied_tags,
        },
    )
    db.add(action)

    await db.commit()

    return ApplyTagSuggestionsResponse(
        message=f"Applied {len(applied_tags)} tags to image",
        applied_tags=applied_tags,
        already_present=already_present,
    )
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_reports.py::TestAdminApplyTagSuggestions -v`

Expected: All tests PASS

**Step 5: Commit**

```bash
git add app/api/v1/admin.py app/schemas/report.py
git commit -m "feat: implement apply tag suggestions endpoint"
```

---

## Task 9: Update Dismiss Endpoint for Tag Suggestions

**Files:**
- Modify: `app/api/v1/admin.py`
- Modify: `app/schemas/report.py`

**Step 1: Add dismiss request schema**

Add to `app/schemas/report.py`:

```python
class ReportDismissRequest(BaseModel):
    """Request schema for dismissing a report."""
    admin_notes: str | None = Field(None, max_length=2000, description="Optional reason for dismissal")
```

**Step 2: Update dismiss_report function in admin.py**

Replace the `dismiss_report` function:

```python
@router.post("/reports/{report_id}/dismiss", response_model=MessageResponse)
async def dismiss_report(
    report_id: Annotated[int, Path(description="Report ID")],
    request_data: ReportDismissRequest | None = None,
    current_user: Annotated[Users, Depends(get_current_user)] = None,
    _: Annotated[None, Depends(require_permission(Permission.REPORT_MANAGE))] = None,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """
    Dismiss a report without taking action on the image.

    If the report has tag suggestions, they are all marked as rejected.

    Requires REPORT_MANAGE permission.
    """
    result = await db.execute(
        select(ImageReports).where(ImageReports.report_id == report_id)  # type: ignore[arg-type]
    )
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if report.status != ReportStatus.PENDING:
        raise HTTPException(status_code=400, detail="Report has already been processed")

    # Reject all tag suggestions if any exist
    await db.execute(
        ImageReportTagSuggestions.__table__.update()
        .where(ImageReportTagSuggestions.report_id == report_id)
        .values(accepted=False)
    )

    # Update report
    report.status = ReportStatus.DISMISSED
    report.reviewed_by = current_user.user_id
    report.reviewed_at = datetime.now(UTC)
    if request_data and request_data.admin_notes:
        report.admin_notes = request_data.admin_notes

    # Log action
    action = AdminActions(
        user_id=current_user.user_id,
        action_type=AdminActionType.REPORT_DISMISS,
        report_id=report_id,
        image_id=report.image_id,
        details={"admin_notes": request_data.admin_notes if request_data else None},
    )
    db.add(action)

    await db.commit()

    return MessageResponse(message="Report dismissed successfully")
```

**Step 3: Add import**

Add to imports in admin.py:

```python
from app.schemas.report import ReportDismissRequest
```

**Step 4: Run existing tests to verify no regression**

Run: `uv run pytest tests/api/v1/test_reports.py::TestAdminReportDismiss -v`

Expected: All tests PASS

**Step 5: Commit**

```bash
git add app/api/v1/admin.py app/schemas/report.py
git commit -m "feat: update dismiss endpoint to reject tag suggestions and accept admin notes"
```

---

## Task 10: Update Admin List Reports to Include Tag Suggestions

**Files:**
- Modify: `app/api/v1/admin.py`

**Step 1: Update list_reports function**

Update the `list_reports` function to include tag suggestions in the response:

After fetching reports, add logic to fetch suggestions:

```python
# After building items list, fetch suggestions for MISSING_TAGS reports
missing_tag_report_ids = [
    r.report_id for r in rows
    if r[0].category == ReportCategory.MISSING_TAGS
]

if missing_tag_report_ids:
    # Fetch all suggestions for these reports
    suggestions_result = await db.execute(
        select(ImageReportTagSuggestions, Tags.title, Tags.type)
        .join(Tags, ImageReportTagSuggestions.tag_id == Tags.tag_id)
        .where(ImageReportTagSuggestions.report_id.in_(missing_tag_report_ids))  # type: ignore[union-attr]
    )
    suggestions_rows = suggestions_result.all()

    # Group by report_id
    suggestions_by_report: dict[int, list[TagSuggestion]] = {}
    for sugg, tag_title, tag_type in suggestions_rows:
        if sugg.report_id not in suggestions_by_report:
            suggestions_by_report[sugg.report_id] = []
        suggestions_by_report[sugg.report_id].append(
            TagSuggestion(
                suggestion_id=sugg.suggestion_id or 0,
                tag_id=sugg.tag_id,
                tag_name=tag_title or "",
                tag_type=tag_type,
                accepted=sugg.accepted,
            )
        )

    # Attach suggestions to response items
    for item in items:
        if item.report_id in suggestions_by_report:
            item.suggested_tags = suggestions_by_report[item.report_id]
```

**Step 2: Add necessary imports**

```python
from app.config import ReportCategory
from app.models.tag import Tags
from app.schemas.report import TagSuggestion
```

**Step 3: Run tests**

Run: `uv run pytest tests/api/v1/test_reports.py -v`

Expected: All tests PASS

**Step 4: Commit**

```bash
git add app/api/v1/admin.py
git commit -m "feat: include tag suggestions in admin reports list"
```

---

## Task 11: Final Integration Tests and Cleanup

**Step 1: Run full test suite**

Run: `uv run pytest tests/api/v1/test_reports.py -v`

Expected: All tests PASS

**Step 2: Run linting**

Run: `uv run ruff check app/`

Expected: No errors

**Step 3: Run type checking**

Run: `uv run mypy app/ --ignore-missing-imports`

Expected: No blocking errors

**Step 4: Final commit**

```bash
git add -A
git commit -m "chore: cleanup and final integration"
```

---

## Summary

Tasks completed:
1. Created `ImageReportTagSuggestions` model
2. Created Alembic migration
3. Updated `ImageReports` model with `admin_notes`
4. Updated report schemas
5. Wrote tests for report creation with tag suggestions
6. Implemented report creation with tag suggestions
7. Wrote tests for apply endpoint
8. Implemented apply tag suggestions endpoint
9. Updated dismiss endpoint
10. Updated admin list reports
11. Final integration tests

Key endpoints:
- `POST /api/v1/images/{image_id}/report` - Now accepts `suggested_tag_ids` for MISSING_TAGS
- `POST /api/v1/admin/reports/{report_id}/apply-tag-suggestions` - New endpoint for approving suggestions
- `POST /api/v1/admin/reports/{report_id}/dismiss` - Now accepts `admin_notes` and rejects suggestions
