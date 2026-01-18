# Tag Removal Suggestions Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extend tag suggestions to support both additions and removals, allowing users to suggest tags that should be removed from images.

**Architecture:** Add a `suggestion_type` column to the existing `image_report_tag_suggestions` table (1=add, 2=remove). Update API schemas to accept separate add/remove lists. Modify admin apply endpoint to delete TagLinks for approved removal suggestions.

**Tech Stack:** FastAPI, SQLModel, Alembic, Pydantic, pytest

---

## Task 1: Database Migration

**Files:**
- Create: `alembic/versions/XXXXXX_add_suggestion_type_column.py`

**Step 1: Generate migration**

Run: `cd /home/dtaylor/shuu/shuushuu-api && alembic revision -m "add suggestion_type column"`

**Step 2: Edit migration file**

```python
"""add suggestion_type column

Revision ID: <generated>
Revises: <previous>
Create Date: <generated>

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "<generated>"
down_revision: str | Sequence[str] | None = "<previous>"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add suggestion_type column to image_report_tag_suggestions."""
    op.add_column(
        "image_report_tag_suggestions",
        sa.Column(
            "suggestion_type",
            mysql.TINYINT(unsigned=True),
            nullable=False,
            server_default="1",
        ),
    )
    # Add index for filtering by type
    op.create_index(
        "idx_suggestion_type",
        "image_report_tag_suggestions",
        ["suggestion_type"],
        unique=False,
    )


def downgrade() -> None:
    """Remove suggestion_type column."""
    op.drop_index("idx_suggestion_type", table_name="image_report_tag_suggestions")
    op.drop_column("image_report_tag_suggestions", "suggestion_type")
```

**Step 3: Run migration**

Run: `cd /home/dtaylor/shuu/shuushuu-api && alembic upgrade head`
Expected: Migration applies successfully

**Step 4: Commit**

```bash
git add alembic/versions/*_add_suggestion_type_column.py
git commit -m "feat: add suggestion_type column to tag suggestions table"
```

---

## Task 2: Update Model

**Files:**
- Modify: `app/models/image_report_tag_suggestion.py`

**Step 1: Add suggestion_type field to model**

In `app/models/image_report_tag_suggestion.py`, add to `ImageReportTagSuggestionBase`:

```python
suggestion_type: int = Field(default=1)  # 1=add, 2=remove
```

**Step 2: Run tests to verify no regressions**

Run: `cd /home/dtaylor/shuu/shuushuu-api && pytest tests/api/v1/test_reports.py -v --tb=short`
Expected: All existing tests pass

**Step 3: Commit**

```bash
git add app/models/image_report_tag_suggestion.py
git commit -m "feat: add suggestion_type field to ImageReportTagSuggestions model"
```

---

## Task 3: Update Config Category Name

**Files:**
- Modify: `app/config.py:236-253`

**Step 1: Rename MISSING_TAGS to TAG_SUGGESTIONS**

Change the class and labels:

```python
class ReportCategory:
    """Image report category constants"""

    REPOST = 1
    INAPPROPRIATE = 2
    SPAM = 3
    TAG_SUGGESTIONS = 4  # Renamed from MISSING_TAGS
    SPOILER = 5
    OTHER = 127

    LABELS = {
        REPOST: "Repost",
        INAPPROPRIATE: "Inappropriate Image",
        SPAM: "Spam",
        TAG_SUGGESTIONS: "Tag Suggestions",  # Updated label
        SPOILER: "Spoiler",
        OTHER: "Other",
    }
```

**Step 2: Update all references to MISSING_TAGS**

Search and replace `ReportCategory.MISSING_TAGS` with `ReportCategory.TAG_SUGGESTIONS` in:
- `app/schemas/report.py`
- `app/api/v1/images.py`
- `app/api/v1/admin.py`

**Step 3: Run tests to verify**

Run: `cd /home/dtaylor/shuu/shuushuu-api && pytest tests/api/v1/test_reports.py -v --tb=short`
Expected: All tests pass (tests use numeric category 4 directly)

**Step 4: Commit**

```bash
git add app/config.py app/schemas/report.py app/api/v1/images.py app/api/v1/admin.py
git commit -m "refactor: rename MISSING_TAGS to TAG_SUGGESTIONS"
```

---

## Task 4: Update Request Schema

**Files:**
- Modify: `app/schemas/report.py:41-83`

**Step 1: Update ReportCreate schema**

Replace the current `suggested_tag_ids` field with two separate fields:

```python
class ReportCreate(BaseModel):
    """Schema for creating a new image report."""

    category: int = Field(
        ...,
        description="Report category (1=repost, 2=inappropriate, 3=spam, 4=tag_suggestions, 127=other)",
    )
    reason_text: str | None = Field(None, max_length=1000, description="Optional explanation")
    suggested_tag_ids_add: list[int] | None = Field(
        None,
        description="Tag IDs to suggest adding (only for TAG_SUGGESTIONS category)",
    )
    suggested_tag_ids_remove: list[int] | None = Field(
        None,
        description="Tag IDs to suggest removing (only for TAG_SUGGESTIONS category)",
    )

    @field_validator("reason_text")
    @classmethod
    def sanitize_reason_text(cls, v: str | None) -> str | None:
        """Sanitize report reason."""
        if v is None:
            return v
        return v.strip()

    @field_validator("suggested_tag_ids_add", "suggested_tag_ids_remove")
    @classmethod
    def dedupe_tag_ids(cls, v: list[int] | None) -> list[int] | None:
        """Remove duplicate tag IDs while preserving order."""
        if not v:
            return None
        return list(dict.fromkeys(v))

    @model_validator(mode="after")
    def validate_tag_suggestions(self) -> "ReportCreate":
        """Validate tag suggestions are only for TAG_SUGGESTIONS category."""
        has_suggestions = self.suggested_tag_ids_add or self.suggested_tag_ids_remove
        if has_suggestions and self.category != ReportCategory.TAG_SUGGESTIONS:
            raise ValueError("Tag suggestions only allowed for TAG_SUGGESTIONS reports")
        return self
```

**Step 2: Update SkippedTagsInfo schema**

Add the `not_on_image` field:

```python
class SkippedTagsInfo(BaseModel):
    """Feedback about tags that were skipped during report creation."""

    already_on_image: list[int] = []  # Addition skipped: tag already present
    not_on_image: list[int] = []      # Removal skipped: tag not on image
    invalid_tag_ids: list[int] = []   # Tag ID doesn't exist
```

**Step 3: Commit**

```bash
git add app/schemas/report.py
git commit -m "feat: update ReportCreate schema for add/remove suggestions"
```

---

## Task 5: Update Response Schema

**Files:**
- Modify: `app/schemas/report.py:19-28`

**Step 1: Add suggestion_type to TagSuggestion**

```python
class TagSuggestion(BaseModel):
    """Schema for a tag suggestion in a report response."""

    suggestion_id: int
    tag_id: int
    tag_name: str
    tag_type: int | None = None
    suggestion_type: int = 1  # 1=add, 2=remove
    accepted: bool | None = None

    model_config = {"from_attributes": True}
```

**Step 2: Update ApplyTagSuggestionsResponse**

```python
class ApplyTagSuggestionsResponse(BaseModel):
    """Response schema for apply tag suggestions endpoint."""

    message: str
    applied_tags: list[int]     # Tag IDs added to image
    removed_tags: list[int] = []  # Tag IDs removed from image
    already_present: list[int] = []  # Additions skipped (already on image)
    already_absent: list[int] = []   # Removals skipped (not on image)
```

**Step 3: Commit**

```bash
git add app/schemas/report.py
git commit -m "feat: add suggestion_type to response schemas"
```

---

## Task 6: Write Tests for Tag Removal Suggestions (Report Creation)

**Files:**
- Modify: `tests/api/v1/test_reports.py`

**Step 1: Add test for creating report with removal suggestions**

Add to `TestReportWithTagSuggestions` class:

```python
async def test_report_with_removal_suggestions(
    self, client: AsyncClient, db_session: AsyncSession
):
    """Test creating TAG_SUGGESTIONS report with removal suggestions."""
    user, password = await create_auth_user(db_session, username="removeuser1")
    image = await create_test_image(db_session, user.user_id)
    tags = await create_test_tags(db_session, count=3)
    # Add tags to image so they can be suggested for removal
    for tag in tags:
        await add_tag_to_image(db_session, image.image_id, tag.tag_id)
    token = await login_user(client, user.username, password)

    response = await client.post(
        f"/api/v1/images/{image.image_id}/report",
        json={
            "category": 4,  # TAG_SUGGESTIONS
            "reason_text": "These tags don't belong",
            "suggested_tag_ids_remove": [t.tag_id for t in tags],
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["category"] == 4
    assert data["suggested_tags"] is not None
    assert len(data["suggested_tags"]) == 3
    # Verify all are removal suggestions
    for suggestion in data["suggested_tags"]:
        assert suggestion["suggestion_type"] == 2

async def test_report_with_mixed_add_and_remove_suggestions(
    self, client: AsyncClient, db_session: AsyncSession
):
    """Test creating report with both add and remove suggestions."""
    user, password = await create_auth_user(db_session, username="mixeduser1")
    image = await create_test_image(db_session, user.user_id)
    tags = await create_test_tags(db_session, count=4)
    # Add first 2 tags to image (for removal suggestions)
    await add_tag_to_image(db_session, image.image_id, tags[0].tag_id)
    await add_tag_to_image(db_session, image.image_id, tags[1].tag_id)
    # tags[2] and tags[3] are not on image (for addition suggestions)
    token = await login_user(client, user.username, password)

    response = await client.post(
        f"/api/v1/images/{image.image_id}/report",
        json={
            "category": 4,
            "suggested_tag_ids_add": [tags[2].tag_id, tags[3].tag_id],
            "suggested_tag_ids_remove": [tags[0].tag_id, tags[1].tag_id],
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    data = response.json()
    assert len(data["suggested_tags"]) == 4

    add_suggestions = [s for s in data["suggested_tags"] if s["suggestion_type"] == 1]
    remove_suggestions = [s for s in data["suggested_tags"] if s["suggestion_type"] == 2]
    assert len(add_suggestions) == 2
    assert len(remove_suggestions) == 2

async def test_report_skips_removal_for_tags_not_on_image(
    self, client: AsyncClient, db_session: AsyncSession
):
    """Test that removal suggestions for tags not on image are skipped."""
    user, password = await create_auth_user(db_session, username="removeuser2")
    image = await create_test_image(db_session, user.user_id)
    tags = await create_test_tags(db_session, count=3)
    # Only add first tag to image
    await add_tag_to_image(db_session, image.image_id, tags[0].tag_id)
    token = await login_user(client, user.username, password)

    response = await client.post(
        f"/api/v1/images/{image.image_id}/report",
        json={
            "category": 4,
            "suggested_tag_ids_remove": [t.tag_id for t in tags],  # Only tags[0] is on image
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    data = response.json()
    assert len(data["suggested_tags"]) == 1  # Only tags[0]
    assert data["skipped_tags"]["not_on_image"] == [tags[1].tag_id, tags[2].tag_id]
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/dtaylor/shuu/shuushuu-api && pytest tests/api/v1/test_reports.py::TestReportWithTagSuggestions::test_report_with_removal_suggestions -v`
Expected: FAIL (feature not implemented yet)

**Step 3: Commit**

```bash
git add tests/api/v1/test_reports.py
git commit -m "test: add tests for tag removal suggestions in report creation"
```

---

## Task 7: Update Report Creation Endpoint

**Files:**
- Modify: `app/api/v1/images.py:1564-1659`

**Step 1: Update the tag suggestion processing logic**

Replace the existing tag suggestion processing (lines ~1564-1588) with:

```python
    # Process tag suggestions for TAG_SUGGESTIONS category
    skipped_tags = SkippedTagsInfo()
    valid_add_tags: list[int] = []
    valid_remove_tags: list[int] = []

    if report_data.category == ReportCategory.TAG_SUGGESTIONS:
        # Get existing tags on image
        existing_tags_result = await db.execute(
            select(TagLinks.tag_id).where(TagLinks.image_id == image_id)
        )
        existing_tag_ids = set(existing_tags_result.scalars().all())

        # Process addition suggestions
        if report_data.suggested_tag_ids_add:
            valid_add_result = await db.execute(
                select(Tags.tag_id).where(Tags.tag_id.in_(report_data.suggested_tag_ids_add))
            )
            valid_add_db_ids = set(valid_add_result.scalars().all())

            for tag_id in report_data.suggested_tag_ids_add:
                if tag_id not in valid_add_db_ids:
                    skipped_tags.invalid_tag_ids.append(tag_id)
                elif tag_id in existing_tag_ids:
                    skipped_tags.already_on_image.append(tag_id)
                else:
                    valid_add_tags.append(tag_id)

        # Process removal suggestions
        if report_data.suggested_tag_ids_remove:
            valid_remove_result = await db.execute(
                select(Tags.tag_id).where(Tags.tag_id.in_(report_data.suggested_tag_ids_remove))
            )
            valid_remove_db_ids = set(valid_remove_result.scalars().all())

            for tag_id in report_data.suggested_tag_ids_remove:
                if tag_id not in valid_remove_db_ids:
                    skipped_tags.invalid_tag_ids.append(tag_id)
                elif tag_id not in existing_tag_ids:
                    skipped_tags.not_on_image.append(tag_id)
                else:
                    valid_remove_tags.append(tag_id)
```

**Step 2: Update suggestion creation (lines ~1600-1608)**

```python
    # Create tag suggestions
    suggestions: list[ImageReportTagSuggestions] = []

    # Addition suggestions (type=1)
    for tag_id in valid_add_tags:
        suggestion = ImageReportTagSuggestions(
            report_id=new_report.report_id,
            tag_id=tag_id,
            suggestion_type=1,
        )
        db.add(suggestion)
        suggestions.append(suggestion)

    # Removal suggestions (type=2)
    for tag_id in valid_remove_tags:
        suggestion = ImageReportTagSuggestions(
            report_id=new_report.report_id,
            tag_id=tag_id,
            suggestion_type=2,
        )
        db.add(suggestion)
        suggestions.append(suggestion)
```

**Step 3: Update response building (lines ~1647-1655)**

Add `suggestion_type` to TagSuggestion:

```python
            response.suggested_tags.append(
                TagSuggestion(
                    suggestion_id=s.suggestion_id or 0,
                    tag_id=s.tag_id,
                    tag_name=tag.title or "",
                    tag_type=tag.type,
                    suggestion_type=s.suggestion_type,
                    accepted=s.accepted,
                )
            )
```

**Step 4: Update skipped_tags condition (line ~1658)**

```python
    # Include skipped tags info if any were skipped
    if (skipped_tags.invalid_tag_ids or skipped_tags.already_on_image
            or skipped_tags.not_on_image):
        response.skipped_tags = skipped_tags
```

**Step 5: Run tests**

Run: `cd /home/dtaylor/shuu/shuushuu-api && pytest tests/api/v1/test_reports.py::TestReportWithTagSuggestions -v`
Expected: All tests pass including new removal tests

**Step 6: Commit**

```bash
git add app/api/v1/images.py
git commit -m "feat: implement tag removal suggestions in report creation"
```

---

## Task 8: Write Tests for Admin Apply Removal Suggestions

**Files:**
- Modify: `tests/api/v1/test_reports.py`

**Step 1: Add tests for approving removal suggestions**

Add to `TestAdminApplyTagSuggestions` class:

```python
async def test_apply_removal_suggestions(
    self, client: AsyncClient, db_session: AsyncSession
):
    """Test applying removal suggestions removes tags from image."""
    admin, password = await create_auth_user(db_session, username="removeapply1", admin=True)
    await grant_permission(db_session, admin.user_id, "report_manage")
    image = await create_test_image(db_session, admin.user_id)
    tags = await create_test_tags(db_session, count=3)
    # Add all tags to image
    for tag in tags:
        await add_tag_to_image(db_session, image.image_id, tag.tag_id)
    token = await login_user(client, admin.username, password)

    # Create report with removal suggestions
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
        s = ImageReportTagSuggestions(
            report_id=report.report_id,
            tag_id=tag.tag_id,
            suggestion_type=2,  # removal
        )
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
    assert len(data["removed_tags"]) == 3
    assert len(data["applied_tags"]) == 0

    # Verify tags were removed from image
    from sqlalchemy import select as sql_select
    tag_links = await db_session.execute(
        sql_select(TagLinks).where(TagLinks.image_id == image.image_id)
    )
    assert len(tag_links.scalars().all()) == 0

async def test_apply_mixed_add_and_remove_suggestions(
    self, client: AsyncClient, db_session: AsyncSession
):
    """Test applying both add and remove suggestions in one action."""
    admin, password = await create_auth_user(db_session, username="mixedapply1", admin=True)
    await grant_permission(db_session, admin.user_id, "report_manage")
    image = await create_test_image(db_session, admin.user_id)
    tags = await create_test_tags(db_session, count=4)
    # Add first 2 tags (will be removed)
    await add_tag_to_image(db_session, image.image_id, tags[0].tag_id)
    await add_tag_to_image(db_session, image.image_id, tags[1].tag_id)
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
    # Add suggestions for tags[2] and tags[3]
    for tag in tags[2:4]:
        s = ImageReportTagSuggestions(
            report_id=report.report_id,
            tag_id=tag.tag_id,
            suggestion_type=1,  # add
        )
        db_session.add(s)
        suggestions.append(s)
    # Remove suggestions for tags[0] and tags[1]
    for tag in tags[0:2]:
        s = ImageReportTagSuggestions(
            report_id=report.report_id,
            tag_id=tag.tag_id,
            suggestion_type=2,  # remove
        )
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
    assert len(data["applied_tags"]) == 2  # tags[2], tags[3] added
    assert len(data["removed_tags"]) == 2  # tags[0], tags[1] removed

    # Verify final state: only tags[2] and tags[3] on image
    from sqlalchemy import select as sql_select
    tag_links = await db_session.execute(
        sql_select(TagLinks.tag_id).where(TagLinks.image_id == image.image_id)
    )
    final_tag_ids = set(tag_links.scalars().all())
    assert tags[2].tag_id in final_tag_ids
    assert tags[3].tag_id in final_tag_ids
    assert tags[0].tag_id not in final_tag_ids
    assert tags[1].tag_id not in final_tag_ids

async def test_removal_already_absent_at_review_time(
    self, client: AsyncClient, db_session: AsyncSession
):
    """Test handling when tag to remove is already absent at review time."""
    admin, password = await create_auth_user(db_session, username="absenttest1", admin=True)
    await grant_permission(db_session, admin.user_id, "report_manage")
    image = await create_test_image(db_session, admin.user_id)
    tags = await create_test_tags(db_session, count=2)
    # Add tags to image initially
    await add_tag_to_image(db_session, image.image_id, tags[0].tag_id)
    await add_tag_to_image(db_session, image.image_id, tags[1].tag_id)
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
        s = ImageReportTagSuggestions(
            report_id=report.report_id,
            tag_id=tag.tag_id,
            suggestion_type=2,
        )
        db_session.add(s)
        suggestions.append(s)
    await db_session.commit()
    for s in suggestions:
        await db_session.refresh(s)

    # Simulate tag being removed between report creation and review
    from sqlalchemy import delete
    await db_session.execute(
        delete(TagLinks).where(
            TagLinks.image_id == image.image_id,
            TagLinks.tag_id == tags[0].tag_id,
        )
    )
    await db_session.commit()

    response = await client.post(
        f"/api/v1/admin/reports/{report.report_id}/apply-tag-suggestions",
        json={"approved_suggestion_ids": [s.suggestion_id for s in suggestions]},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["removed_tags"]) == 1  # Only tags[1] was actually removed
    assert len(data["already_absent"]) == 1
    assert tags[0].tag_id in data["already_absent"]
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/dtaylor/shuu/shuushuu-api && pytest tests/api/v1/test_reports.py::TestAdminApplyTagSuggestions::test_apply_removal_suggestions -v`
Expected: FAIL

**Step 3: Commit**

```bash
git add tests/api/v1/test_reports.py
git commit -m "test: add tests for admin applying removal suggestions"
```

---

## Task 9: Update Admin Apply Tag Suggestions Endpoint

**Files:**
- Modify: `app/api/v1/admin.py:903-1009`

**Step 1: Update the apply logic**

Replace the processing loop (lines ~963-979) with:

```python
    # Get existing tags on image
    existing_tags_result = await db.execute(
        select(TagLinks.tag_id).where(TagLinks.image_id == report.image_id)
    )
    existing_tag_ids = set(existing_tags_result.scalars().all())

    approved_ids = set(request_data.approved_suggestion_ids)
    applied_tags: list[int] = []
    removed_tags: list[int] = []
    already_present: list[int] = []
    already_absent: list[int] = []

    for suggestion in suggestions:
        if suggestion.suggestion_id in approved_ids:
            suggestion.accepted = True

            if suggestion.suggestion_type == 1:  # Add
                if suggestion.tag_id not in existing_tag_ids:
                    tag_link = TagLinks(image_id=report.image_id, tag_id=suggestion.tag_id)
                    db.add(tag_link)
                    applied_tags.append(suggestion.tag_id)
                    existing_tag_ids.add(suggestion.tag_id)
                else:
                    already_present.append(suggestion.tag_id)

            elif suggestion.suggestion_type == 2:  # Remove
                if suggestion.tag_id in existing_tag_ids:
                    await db.execute(
                        delete(TagLinks).where(
                            TagLinks.image_id == report.image_id,
                            TagLinks.tag_id == suggestion.tag_id,
                        )
                    )
                    removed_tags.append(suggestion.tag_id)
                    existing_tag_ids.discard(suggestion.tag_id)
                else:
                    already_absent.append(suggestion.tag_id)
        else:
            suggestion.accepted = False
```

**Step 2: Add delete import at top of file**

```python
from sqlalchemy import delete, desc, func, select
```

**Step 3: Update response (lines ~1005-1009)**

```python
    return ApplyTagSuggestionsResponse(
        message=f"Applied {len(applied_tags)} tags, removed {len(removed_tags)} tags",
        applied_tags=applied_tags,
        removed_tags=removed_tags,
        already_present=already_present,
        already_absent=already_absent,
    )
```

**Step 4: Update audit log details (lines ~994-999)**

```python
        details={
            "action": "apply_tag_suggestions",
            "approved_count": len(approved_ids),
            "rejected_count": len(suggestions) - len(approved_ids),
            "applied_tags": applied_tags,
            "removed_tags": removed_tags,
        },
```

**Step 5: Run tests**

Run: `cd /home/dtaylor/shuu/shuushuu-api && pytest tests/api/v1/test_reports.py::TestAdminApplyTagSuggestions -v`
Expected: All tests pass

**Step 6: Commit**

```bash
git add app/api/v1/admin.py
git commit -m "feat: implement tag removal in apply-tag-suggestions endpoint"
```

---

## Task 10: Update Admin List Reports Endpoint

**Files:**
- Modify: `app/api/v1/admin.py:806-824`

**Step 1: Add suggestion_type to TagSuggestion in list response**

Update the suggestion building (lines ~816-824):

```python
            suggestions_by_report[suggestion.report_id].append(
                TagSuggestion(
                    suggestion_id=suggestion.suggestion_id or 0,
                    tag_id=suggestion.tag_id,
                    tag_name=tag.title or "",
                    tag_type=tag.type,
                    suggestion_type=suggestion.suggestion_type,
                    accepted=suggestion.accepted,
                )
            )
```

**Step 2: Run full test suite**

Run: `cd /home/dtaylor/shuu/shuushuu-api && pytest tests/api/v1/test_reports.py -v`
Expected: All tests pass

**Step 3: Commit**

```bash
git add app/api/v1/admin.py
git commit -m "feat: include suggestion_type in admin reports list response"
```

---

## Task 11: Update Existing Tests for Backward Compatibility

**Files:**
- Modify: `tests/api/v1/test_reports.py`

**Step 1: Update tests using old field name**

Search for `suggested_tag_ids` in tests and update to `suggested_tag_ids_add`:

- `test_report_with_valid_tag_suggestions`: Change `suggested_tag_ids` to `suggested_tag_ids_add`
- `test_report_skips_invalid_tag_ids`: Change `suggested_tag_ids` to `suggested_tag_ids_add`
- `test_report_skips_tags_already_on_image`: Change `suggested_tag_ids` to `suggested_tag_ids_add`
- `test_report_rejects_tag_suggestions_for_non_missing_tags`: Change `suggested_tag_ids` to `suggested_tag_ids_add`
- `test_report_with_duplicate_tag_ids_dedupes`: Change `suggested_tag_ids` to `suggested_tag_ids_add`

**Step 2: Run all tests**

Run: `cd /home/dtaylor/shuu/shuushuu-api && pytest tests/api/v1/test_reports.py -v`
Expected: All tests pass

**Step 3: Commit**

```bash
git add tests/api/v1/test_reports.py
git commit -m "test: update tests to use new suggested_tag_ids_add field"
```

---

## Task 12: Final Integration Test

**Step 1: Run full test suite**

Run: `cd /home/dtaylor/shuu/shuushuu-api && pytest tests/ -v --tb=short`
Expected: All tests pass

**Step 2: Manual verification (optional)**

Start the dev server and manually test:
1. Create a report with removal suggestions via API
2. Verify suggestions appear in admin list with correct `suggestion_type`
3. Apply removal suggestions and verify tags are removed

**Step 3: Final commit**

```bash
git add -A
git commit -m "feat: complete tag removal suggestions implementation"
```

---

## Summary

| Task | Description |
|------|-------------|
| 1 | Database migration - add `suggestion_type` column |
| 2 | Update model with `suggestion_type` field |
| 3 | Rename `MISSING_TAGS` to `TAG_SUGGESTIONS` |
| 4 | Update request schema with add/remove fields |
| 5 | Update response schema with `suggestion_type` |
| 6 | Write tests for removal suggestions (report creation) |
| 7 | Implement removal suggestions in report creation |
| 8 | Write tests for admin applying removals |
| 9 | Implement removal handling in apply endpoint |
| 10 | Update admin list to include `suggestion_type` |
| 11 | Update existing tests for backward compatibility |
| 12 | Final integration test |
