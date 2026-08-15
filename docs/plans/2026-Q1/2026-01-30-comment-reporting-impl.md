# Comment Reporting Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add comment reporting functionality that integrates with the existing image reporting admin queue.

**Architecture:** New `CommentReports` model mirrors `ImageReports`. User endpoint added to `comments.py`. Admin endpoints added to `admin.py` with `report_type` filter to unify queues.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy async, Alembic migrations, pytest

---

## Task 1: Add CommentReportCategory Constants

**Files:**
- Modify: `app/config.py:282-300` (after ReportCategory class)

**Step 1: Add the constants**

Add after the existing `ReportCategory` class (around line 300):

```python
class CommentReportCategory:
    """Comment report category constants"""

    RULE_VIOLATION = 1
    SPAM = 2
    OTHER = 127

    LABELS = {
        RULE_VIOLATION: "Rule Violation",
        SPAM: "Spam",
        OTHER: "Other",
    }
```

**Step 2: Verify no syntax errors**

Run: `uv run python -c "from app.config import CommentReportCategory; print(CommentReportCategory.LABELS)"`

Expected: `{1: 'Rule Violation', 2: 'Spam', 127: 'Other'}`

**Step 3: Commit**

```bash
git add app/config.py
git commit -m "feat: add CommentReportCategory constants"
```

---

## Task 2: Create CommentReports Model

**Files:**
- Create: `app/models/comment_report.py`
- Modify: `app/models/__init__.py` (add import)

**Step 1: Create the model file**

Create `app/models/comment_report.py`:

```python
"""
SQLModel-based CommentReport models with inheritance for security

This module defines the CommentReports database model using SQLModel.
"""

from datetime import datetime

from sqlalchemy import Column, ForeignKey, Index, Integer, text
from sqlmodel import Field, SQLModel

from app.config import ReportStatus


class CommentReportBase(SQLModel):
    """
    Base model with shared public fields for CommentReports.

    These fields are safe to expose via the API.
    """

    comment_id: int
    user_id: int

    category: int | None = Field(default=None)
    reason_text: str | None = Field(default=None, max_length=1000)

    status: int = Field(default=ReportStatus.PENDING)

    admin_notes: str | None = Field(default=None)


class CommentReports(CommentReportBase, table=True):
    """
    Database table for comment reports.

    Extends CommentReportBase with:
    - Primary key
    - Foreign key relationships
    - Timestamps
    - Review tracking fields
    """

    __tablename__ = "comment_reports"

    __table_args__ = (
        Index("idx_comment_reports_comment_id", "comment_id"),
        Index("idx_comment_reports_user_id", "user_id"),
        Index("idx_comment_reports_reviewed_by", "reviewed_by"),
        Index("idx_comment_reports_status_category", "status", "category"),
        Index(
            "idx_comment_reports_pending_per_user",
            "comment_id",
            "user_id",
            "status",
        ),
    )

    report_id: int | None = Field(default=None, primary_key=True)

    comment_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("posts.post_id", ondelete="CASCADE", onupdate="CASCADE"),
            nullable=False,
        )
    )
    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("users.user_id", ondelete="CASCADE", onupdate="CASCADE"),
            nullable=False,
        )
    )

    created_at: datetime | None = Field(
        default=None, sa_column_kwargs={"server_default": text("current_timestamp()")}
    )

    reviewed_by: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("users.user_id", ondelete="SET NULL", onupdate="CASCADE"),
            nullable=True,
        ),
    )
    reviewed_at: datetime | None = Field(default=None)
```

**Step 2: Add import to models/__init__.py**

Add to `app/models/__init__.py`:

```python
from app.models.comment_report import CommentReports
```

And add `"CommentReports"` to the `__all__` list.

**Step 3: Verify model loads**

Run: `uv run python -c "from app.models import CommentReports; print(CommentReports.__tablename__)"`

Expected: `comment_reports`

**Step 4: Commit**

```bash
git add app/models/comment_report.py app/models/__init__.py
git commit -m "feat: add CommentReports model"
```

---

## Task 3: Create Alembic Migration

**Files:**
- Create: `alembic/versions/xxxx_add_comment_reports.py`

**Step 1: Generate migration**

Run: `uv run alembic revision -m "add_comment_reports"`

**Step 2: Edit the migration file**

Replace the generated `upgrade()` and `downgrade()` functions:

```python
"""add_comment_reports

Revision ID: [auto-generated]
Revises: [auto-generated]
Create Date: [auto-generated]

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "[auto-generated]"
down_revision: Union[str, None] = "[auto-generated]"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "comment_reports",
        sa.Column("report_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("comment_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.Integer(), nullable=True),
        sa.Column("reason_text", sa.String(1000), nullable=True),
        sa.Column("status", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("admin_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("current_timestamp()"),
            nullable=True,
        ),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("report_id"),
        sa.ForeignKeyConstraint(
            ["comment_id"],
            ["posts.post_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
    )
    op.create_index(
        "idx_comment_reports_comment_id", "comment_reports", ["comment_id"]
    )
    op.create_index("idx_comment_reports_user_id", "comment_reports", ["user_id"])
    op.create_index(
        "idx_comment_reports_reviewed_by", "comment_reports", ["reviewed_by"]
    )
    op.create_index(
        "idx_comment_reports_status_category", "comment_reports", ["status", "category"]
    )
    op.create_index(
        "idx_comment_reports_pending_per_user",
        "comment_reports",
        ["comment_id", "user_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("idx_comment_reports_pending_per_user", table_name="comment_reports")
    op.drop_index("idx_comment_reports_status_category", table_name="comment_reports")
    op.drop_index("idx_comment_reports_reviewed_by", table_name="comment_reports")
    op.drop_index("idx_comment_reports_user_id", table_name="comment_reports")
    op.drop_index("idx_comment_reports_comment_id", table_name="comment_reports")
    op.drop_table("comment_reports")
```

**Step 3: Run migration**

Run: `uv run alembic upgrade head`

Expected: Migration applies successfully

**Step 4: Verify table exists**

Run: `uv run python -c "from app.core.database import engine; import asyncio; asyncio.run(engine.dispose())"`

Or check via database client that `comment_reports` table exists.

**Step 5: Commit**

```bash
git add alembic/versions/*_add_comment_reports.py
git commit -m "feat: add comment_reports migration"
```

---

## Task 4: Create CommentReport Schemas

**Files:**
- Create: `app/schemas/comment_report.py`

**Step 1: Create the schema file**

Create `app/schemas/comment_report.py`:

```python
"""
Pydantic schemas for comment reporting.
"""

from pydantic import BaseModel, Field, field_validator

from app.config import CommentReportCategory
from app.schemas.base import UTCDatetime, UTCDatetimeOptional
from app.schemas.common import UserSummary


class CommentReportCreate(BaseModel):
    """Schema for creating a new comment report."""

    category: int = Field(
        ...,
        description="Report category (1=rule_violation, 2=spam, 127=other)",
    )
    reason_text: str | None = Field(None, max_length=1000, description="Optional explanation")

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: int) -> int:
        """Validate category is a valid CommentReportCategory."""
        valid = {
            CommentReportCategory.RULE_VIOLATION,
            CommentReportCategory.SPAM,
            CommentReportCategory.OTHER,
        }
        if v not in valid:
            raise ValueError(f"Invalid category. Must be one of: {valid}")
        return v

    @field_validator("reason_text")
    @classmethod
    def sanitize_reason_text(cls, v: str | None) -> str | None:
        """Sanitize report reason."""
        if v is None:
            return v
        return v.strip()


class CommentReportResponse(BaseModel):
    """Response schema for a comment report."""

    report_id: int
    comment_id: int
    image_id: int  # Denormalized for convenience
    user_id: int
    username: str | None = None
    category: int | None
    category_label: str | None = None
    reason_text: str | None
    status: int
    status_label: str | None = None
    created_at: UTCDatetime
    reviewed_by: int | None = None
    reviewed_at: UTCDatetimeOptional = None
    admin_notes: str | None = None

    model_config = {"from_attributes": True}

    def model_post_init(self, __context: object) -> None:
        """Set computed label fields."""
        if self.category is not None:
            self.category_label = CommentReportCategory.LABELS.get(self.category, "Unknown")
        status_labels = {0: "Pending", 1: "Reviewed", 2: "Dismissed"}
        self.status_label = status_labels.get(self.status, "Unknown")


class CommentReportListItem(CommentReportResponse):
    """Extended response for admin listing."""

    comment_author: UserSummary | None = None
    comment_preview: str | None = None  # First 100 chars of comment


class CommentReportListResponse(BaseModel):
    """Response schema for listing comment reports."""

    total: int
    page: int
    per_page: int
    items: list[CommentReportListItem]


class CommentReportDismissRequest(BaseModel):
    """Schema for dismissing a comment report."""

    admin_notes: str | None = Field(None, max_length=2000, description="Optional admin notes")


class CommentReportDeleteRequest(BaseModel):
    """Schema for deleting a reported comment."""

    admin_notes: str | None = Field(None, max_length=2000, description="Optional admin notes")
```

**Step 2: Verify schema loads**

Run: `uv run python -c "from app.schemas.comment_report import CommentReportCreate; print(CommentReportCreate.model_fields.keys())"`

Expected: `dict_keys(['category', 'reason_text'])`

**Step 3: Commit**

```bash
git add app/schemas/comment_report.py
git commit -m "feat: add comment report schemas"
```

---

## Task 5: Write Failing Tests for User Report Endpoint

**Files:**
- Create: `tests/api/v1/test_comment_reports.py`

**Step 1: Create test file with user endpoint tests**

Create `tests/api/v1/test_comment_reports.py`:

```python
"""
API tests for the comment reporting system.

Tests cover:
- User report endpoint (POST /comments/{comment_id}/report)
- Admin triage endpoints (list, dismiss, delete)
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import CommentReportCategory, ImageStatus, ReportStatus
from app.core.security import get_password_hash
from app.models import Comments, CommentReports, Images, Users
from app.models.permissions import GroupPerms, Groups, Perms, UserGroups


async def create_auth_user(
    db_session: AsyncSession,
    username: str = "reportuser",
    email: str = "report@example.com",
) -> tuple[Users, str]:
    """Create a user and return the user object and password."""
    password = "TestPassword123!"
    user = Users(
        username=username,
        password=get_password_hash(password),
        password_type="bcrypt",
        salt="",
        email=email,
        active=1,
        admin=0,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user, password


async def login_user(client: AsyncClient, username: str, password: str) -> str:
    """Login and return the access token."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


async def create_test_image(db_session: AsyncSession, user_id: int) -> Images:
    """Create a test image."""
    image = Images(
        filename="test-comment-report-image",
        ext="jpg",
        original_filename="test.jpg",
        md5_hash="commentreporttest00001",
        filesize=123456,
        width=1920,
        height=1080,
        user_id=user_id,
        status=ImageStatus.ACTIVE,
        locked=0,
    )
    db_session.add(image)
    await db_session.commit()
    await db_session.refresh(image)
    return image


async def create_test_comment(
    db_session: AsyncSession, user_id: int, image_id: int, text: str = "Test comment"
) -> Comments:
    """Create a test comment."""
    comment = Comments(
        user_id=user_id,
        image_id=image_id,
        post_text=text,
        deleted=False,
    )
    db_session.add(comment)
    await db_session.commit()
    await db_session.refresh(comment)
    return comment


async def grant_permission(db_session: AsyncSession, user_id: int, perm_title: str):
    """Grant a permission to a user via a group."""
    result = await db_session.execute(select(Perms).where(Perms.title == perm_title))
    perm = result.scalar_one_or_none()
    if not perm:
        perm = Perms(title=perm_title, desc=f"Test permission {perm_title}")
        db_session.add(perm)
        await db_session.flush()

    result = await db_session.execute(select(Groups).where(Groups.title == "test_mod"))
    group = result.scalar_one_or_none()
    if not group:
        group = Groups(title="test_mod", desc="Test mod group")
        db_session.add(group)
        await db_session.flush()

    result = await db_session.execute(
        select(GroupPerms).where(
            GroupPerms.group_id == group.group_id,
            GroupPerms.perm_id == perm.perm_id,
        )
    )
    if not result.scalar_one_or_none():
        group_perm = GroupPerms(group_id=group.group_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(group_perm)

    result = await db_session.execute(
        select(UserGroups).where(
            UserGroups.user_id == user_id,
            UserGroups.group_id == group.group_id,
        )
    )
    if not result.scalar_one_or_none():
        user_group = UserGroups(user_id=user_id, group_id=group.group_id)
        db_session.add(user_group)

    await db_session.commit()


@pytest.mark.api
class TestUserCommentReportEndpoint:
    """Tests for POST /api/v1/comments/{comment_id}/report endpoint."""

    async def test_report_comment_success(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test successfully reporting a comment."""
        user, password = await create_auth_user(db_session)
        image = await create_test_image(db_session, user.user_id)
        comment = await create_test_comment(db_session, user.user_id, image.image_id)
        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/comments/{comment.post_id}/report",
            json={
                "category": CommentReportCategory.SPAM,
                "reason_text": "This is spam",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["comment_id"] == comment.post_id
        assert data["category"] == CommentReportCategory.SPAM
        assert data["status"] == ReportStatus.PENDING

    async def test_report_comment_requires_auth(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that reporting requires authentication."""
        user, _ = await create_auth_user(db_session)
        image = await create_test_image(db_session, user.user_id)
        comment = await create_test_comment(db_session, user.user_id, image.image_id)

        response = await client.post(
            f"/api/v1/comments/{comment.post_id}/report",
            json={"category": CommentReportCategory.SPAM},
        )

        assert response.status_code == 401

    async def test_report_nonexistent_comment(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test reporting a comment that doesn't exist."""
        user, password = await create_auth_user(db_session)
        token = await login_user(client, user.username, password)

        response = await client.post(
            "/api/v1/comments/999999/report",
            json={"category": CommentReportCategory.SPAM},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    async def test_report_deleted_comment(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that deleted comments cannot be reported."""
        user, password = await create_auth_user(db_session)
        image = await create_test_image(db_session, user.user_id)
        comment = await create_test_comment(db_session, user.user_id, image.image_id)
        comment.deleted = True
        await db_session.commit()

        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/comments/{comment.post_id}/report",
            json={"category": CommentReportCategory.SPAM},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400
        assert "deleted" in response.json()["detail"].lower()

    async def test_duplicate_pending_report(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that a user cannot have multiple pending reports on the same comment."""
        user, password = await create_auth_user(db_session)
        image = await create_test_image(db_session, user.user_id)
        comment = await create_test_comment(db_session, user.user_id, image.image_id)
        token = await login_user(client, user.username, password)

        # First report succeeds
        response = await client.post(
            f"/api/v1/comments/{comment.post_id}/report",
            json={"category": CommentReportCategory.SPAM},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 201

        # Second report fails
        response = await client.post(
            f"/api/v1/comments/{comment.post_id}/report",
            json={"category": CommentReportCategory.RULE_VIOLATION},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 409
        assert "pending report" in response.json()["detail"].lower()

    async def test_invalid_category(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that invalid categories are rejected."""
        user, password = await create_auth_user(db_session)
        image = await create_test_image(db_session, user.user_id)
        comment = await create_test_comment(db_session, user.user_id, image.image_id)
        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/comments/{comment.post_id}/report",
            json={"category": 999},  # Invalid category
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 422
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_comment_reports.py::TestUserCommentReportEndpoint -v`

Expected: All tests FAIL (endpoint doesn't exist yet)

**Step 3: Commit**

```bash
git add tests/api/v1/test_comment_reports.py
git commit -m "test: add failing tests for comment report user endpoint"
```

---

## Task 6: Implement User Report Endpoint

**Files:**
- Modify: `app/api/v1/comments.py`

**Step 1: Add imports at top of file**

Add to imports in `app/api/v1/comments.py`:

```python
from app.config import CommentReportCategory, ReportStatus
from app.models.comment_report import CommentReports
from app.schemas.comment_report import CommentReportCreate, CommentReportResponse
```

**Step 2: Add the endpoint**

Add at the end of `app/api/v1/comments.py` (before the file ends):

```python
@router.post("/{comment_id}/report", response_model=CommentReportResponse, status_code=201)
async def report_comment(
    comment_id: int,
    report_data: CommentReportCreate,
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> CommentReportResponse:
    """
    Report a comment for rule violations.

    Categories:
    - 1: Rule Violation (harassment, illegal content, etc.)
    - 2: Spam
    - 127: Other

    Rate limit: One pending report per user per comment.
    """
    # Check comment exists and is not deleted
    result = await db.execute(
        select(Comments).where(Comments.post_id == comment_id)  # type: ignore[arg-type]
    )
    comment = result.scalar_one_or_none()

    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    if comment.deleted:
        raise HTTPException(status_code=400, detail="Cannot report a deleted comment")

    # Check for existing pending report from this user
    result = await db.execute(
        select(CommentReports).where(
            CommentReports.comment_id == comment_id,  # type: ignore[arg-type]
            CommentReports.user_id == current_user.user_id,  # type: ignore[arg-type]
            CommentReports.status == ReportStatus.PENDING,  # type: ignore[arg-type]
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="You already have a pending report on this comment",
        )

    # Create the report
    report = CommentReports(
        comment_id=comment_id,
        user_id=current_user.user_id,  # type: ignore[arg-type]
        category=report_data.category,
        reason_text=report_data.reason_text,
        status=ReportStatus.PENDING,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)

    return CommentReportResponse(
        report_id=report.report_id or 0,
        comment_id=report.comment_id,
        image_id=comment.image_id,
        user_id=report.user_id,
        category=report.category,
        reason_text=report.reason_text,
        status=report.status,
        created_at=report.created_at,  # type: ignore[arg-type]
    )
```

**Step 3: Run tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_comment_reports.py::TestUserCommentReportEndpoint -v`

Expected: All tests PASS

**Step 4: Commit**

```bash
git add app/api/v1/comments.py
git commit -m "feat: add POST /comments/{id}/report endpoint"
```

---

## Task 7: Write Failing Tests for Admin Endpoints

**Files:**
- Modify: `tests/api/v1/test_comment_reports.py`

**Step 1: Add admin endpoint tests**

Append to `tests/api/v1/test_comment_reports.py`:

```python
@pytest.mark.api
class TestAdminCommentReportEndpoints:
    """Tests for admin comment report endpoints."""

    async def test_list_comment_reports(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test listing comment reports with report_type=comment filter."""
        user, password = await create_auth_user(db_session, "admin1", "admin1@test.com")
        await grant_permission(db_session, user.user_id, "report_view")
        image = await create_test_image(db_session, user.user_id)
        comment = await create_test_comment(db_session, user.user_id, image.image_id)

        # Create a comment report
        report = CommentReports(
            comment_id=comment.post_id,
            user_id=user.user_id,
            category=CommentReportCategory.SPAM,
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.commit()

        token = await login_user(client, user.username, password)

        response = await client.get(
            "/api/v1/admin/reports?report_type=comment",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "comment_reports" in data
        assert len(data["comment_reports"]) >= 1

    async def test_dismiss_comment_report(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test dismissing a comment report."""
        user, password = await create_auth_user(db_session, "admin2", "admin2@test.com")
        await grant_permission(db_session, user.user_id, "report_manage")
        image = await create_test_image(db_session, user.user_id)
        comment = await create_test_comment(db_session, user.user_id, image.image_id)

        report = CommentReports(
            comment_id=comment.post_id,
            user_id=user.user_id,
            category=CommentReportCategory.SPAM,
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.commit()
        await db_session.refresh(report)

        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/admin/reports/comments/{report.report_id}/dismiss",
            json={"admin_notes": "Not a valid report"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200

        # Verify report was dismissed
        await db_session.refresh(report)
        assert report.status == ReportStatus.DISMISSED

    async def test_delete_comment_via_report(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test deleting a comment via the report action."""
        user, password = await create_auth_user(db_session, "admin3", "admin3@test.com")
        await grant_permission(db_session, user.user_id, "report_manage")
        image = await create_test_image(db_session, user.user_id)
        comment = await create_test_comment(db_session, user.user_id, image.image_id)

        report = CommentReports(
            comment_id=comment.post_id,
            user_id=user.user_id,
            category=CommentReportCategory.RULE_VIOLATION,
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.commit()
        await db_session.refresh(report)

        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/admin/reports/comments/{report.report_id}/delete",
            json={"admin_notes": "Violates rules"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200

        # Verify comment was soft-deleted
        await db_session.refresh(comment)
        assert comment.deleted is True

        # Verify report was marked reviewed
        await db_session.refresh(report)
        assert report.status == ReportStatus.REVIEWED

    async def test_dismiss_requires_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that dismiss requires REPORT_MANAGE permission."""
        user, password = await create_auth_user(db_session, "nonadmin", "nonadmin@test.com")
        # No permission granted
        image = await create_test_image(db_session, user.user_id)
        comment = await create_test_comment(db_session, user.user_id, image.image_id)

        report = CommentReports(
            comment_id=comment.post_id,
            user_id=user.user_id,
            category=CommentReportCategory.SPAM,
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.commit()
        await db_session.refresh(report)

        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/admin/reports/comments/{report.report_id}/dismiss",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_comment_reports.py::TestAdminCommentReportEndpoints -v`

Expected: All tests FAIL (endpoints don't exist yet)

**Step 3: Commit**

```bash
git add tests/api/v1/test_comment_reports.py
git commit -m "test: add failing tests for admin comment report endpoints"
```

---

## Task 8: Implement Admin List Endpoint with report_type Filter

**Files:**
- Modify: `app/api/v1/admin.py`

**Step 1: Add imports**

Add to imports at top of `app/api/v1/admin.py`:

```python
from app.config import CommentReportCategory
from app.models.comment_report import CommentReports
from app.schemas.comment_report import (
    CommentReportListItem,
    CommentReportListResponse,
)
```

**Step 2: Modify the list_reports endpoint**

Find the `list_reports` function (around line 760) and add `report_type` parameter. Modify the function to handle the new parameter and return unified response.

Add parameter to function signature:

```python
async def list_reports(
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    status_filter: Annotated[int | None, Query(alias="status")] = None,
    category: Annotated[int | None, Query()] = None,
    report_type: Annotated[str | None, Query(pattern="^(image|comment|all)$")] = "all",  # NEW
    current_user: Users = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> dict:  # Change return type to dict for flexibility
```

Add comment reports query logic after image reports (before the return statement). Return a unified response structure.

Due to complexity, this requires careful integration. The key changes are:
1. Query CommentReports when `report_type` is "comment" or "all"
2. Return `{"image_reports": [...], "comment_reports": [...], "total": int}`

**Step 3: Run tests**

Run: `uv run pytest tests/api/v1/test_comment_reports.py::TestAdminCommentReportEndpoints::test_list_comment_reports -v`

Expected: PASS

**Step 4: Commit**

```bash
git add app/api/v1/admin.py
git commit -m "feat: add report_type filter to admin reports listing"
```

---

## Task 9: Implement Admin Dismiss Endpoint

**Files:**
- Modify: `app/api/v1/admin.py`

**Step 1: Add the dismiss endpoint**

Add after the existing image report dismiss endpoint:

```python
@router.post("/reports/comments/{report_id}/dismiss", response_model=MessageResponse)
async def dismiss_comment_report(
    report_id: Annotated[int, Path(description="Comment Report ID")],
    current_user: Annotated[Users, Depends(get_current_user)],
    _: Annotated[None, Depends(require_permission(Permission.REPORT_MANAGE))],
    request_data: CommentReportDismissRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """
    Dismiss a comment report without taking action.

    Requires REPORT_MANAGE permission.
    """
    from app.schemas.comment_report import CommentReportDismissRequest

    result = await db.execute(
        select(CommentReports).where(CommentReports.report_id == report_id)  # type: ignore[arg-type]
    )
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if report.status != ReportStatus.PENDING:
        raise HTTPException(status_code=400, detail="Report has already been processed")

    report.status = ReportStatus.DISMISSED
    report.reviewed_by = current_user.user_id
    report.reviewed_at = datetime.now(UTC)
    if request_data and request_data.admin_notes:
        report.admin_notes = request_data.admin_notes

    await db.commit()

    return MessageResponse(message="Comment report dismissed successfully")
```

**Step 2: Run tests**

Run: `uv run pytest tests/api/v1/test_comment_reports.py::TestAdminCommentReportEndpoints::test_dismiss_comment_report -v`

Expected: PASS

**Step 3: Commit**

```bash
git add app/api/v1/admin.py
git commit -m "feat: add POST /admin/reports/comments/{id}/dismiss endpoint"
```

---

## Task 10: Implement Admin Delete Endpoint

**Files:**
- Modify: `app/api/v1/admin.py`

**Step 1: Add the delete endpoint**

Add after the dismiss endpoint:

```python
@router.post("/reports/comments/{report_id}/delete", response_model=MessageResponse)
async def delete_comment_via_report(
    report_id: Annotated[int, Path(description="Comment Report ID")],
    current_user: Annotated[Users, Depends(get_current_user)],
    _: Annotated[None, Depends(require_permission(Permission.REPORT_MANAGE))],
    request_data: CommentReportDeleteRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """
    Delete a reported comment and mark the report as reviewed.

    Soft-deletes the comment (sets deleted=True) and creates an audit log entry.

    Requires REPORT_MANAGE permission.
    """
    from app.schemas.comment_report import CommentReportDeleteRequest

    result = await db.execute(
        select(CommentReports).where(CommentReports.report_id == report_id)  # type: ignore[arg-type]
    )
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if report.status != ReportStatus.PENDING:
        raise HTTPException(status_code=400, detail="Report has already been processed")

    # Get the comment
    result = await db.execute(
        select(Comments).where(Comments.post_id == report.comment_id)  # type: ignore[arg-type]
    )
    comment = result.scalar_one_or_none()

    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    if comment.deleted:
        raise HTTPException(status_code=400, detail="Comment has already been deleted")

    # Soft-delete the comment
    comment.deleted = True

    # Update report
    report.status = ReportStatus.REVIEWED
    report.reviewed_by = current_user.user_id
    report.reviewed_at = datetime.now(UTC)
    if request_data and request_data.admin_notes:
        report.admin_notes = request_data.admin_notes

    # Create audit log
    action = AdminActions(
        user_id=current_user.user_id,
        action_type=AdminActionType.COMMENT_DELETE,
        details={
            "comment_id": comment.post_id,
            "report_id": report_id,
            "admin_notes": request_data.admin_notes if request_data else None,
        },
    )
    db.add(action)

    await db.commit()

    return MessageResponse(message="Comment deleted successfully")
```

**Step 2: Run all admin tests**

Run: `uv run pytest tests/api/v1/test_comment_reports.py::TestAdminCommentReportEndpoints -v`

Expected: All tests PASS

**Step 3: Commit**

```bash
git add app/api/v1/admin.py
git commit -m "feat: add POST /admin/reports/comments/{id}/delete endpoint"
```

---

## Task 11: Run Full Test Suite and Fix Issues

**Step 1: Run all comment report tests**

Run: `uv run pytest tests/api/v1/test_comment_reports.py -v`

Expected: All tests PASS

**Step 2: Run mypy**

Run: `uv run mypy app/models/comment_report.py app/schemas/comment_report.py app/api/v1/comments.py app/api/v1/admin.py`

Expected: No errors (or only pre-existing ones)

**Step 3: Run full test suite to check for regressions**

Run: `uv run pytest tests/api/v1/test_reports.py tests/api/v1/test_comments.py -v`

Expected: All tests PASS

**Step 4: Final commit**

```bash
git add -A
git commit -m "chore: fix any remaining issues from testing"
```

---

## Summary

After completing all tasks:

- **Config:** `CommentReportCategory` constants added
- **Model:** `CommentReports` table created with proper indexes
- **Schemas:** Request/response schemas for comment reports
- **User endpoint:** `POST /comments/{id}/report`
- **Admin endpoints:**
  - `GET /admin/reports?report_type=comment` - List with filtering
  - `POST /admin/reports/comments/{id}/dismiss` - Dismiss report
  - `POST /admin/reports/comments/{id}/delete` - Delete comment
- **Tests:** Comprehensive test coverage for all endpoints
