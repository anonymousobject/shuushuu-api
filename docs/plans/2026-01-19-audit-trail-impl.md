# Audit Trail Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement comprehensive audit trail tracking for tag changes, image status changes, and review outcomes with public API endpoints.

**Architecture:** Two new database tables (`tag_audit_log`, `image_status_history`), wire up existing `tag_history` table, and 6 new API endpoints for querying audit data. Each audit write happens in the same transaction as the operation being tracked.

**Tech Stack:** FastAPI, SQLModel, Alembic migrations, MariaDB

---

## Task 1: Add TagAuditActionType Constants

**Files:**
- Modify: `app/config.py:223` (after `AdminActionType`)

**Step 1: Write the test**

Create test file `tests/unit/test_config.py` (or add to existing):

```python
"""Tests for config constants."""

from app.config import TagAuditActionType


class TestTagAuditActionType:
    """Tests for TagAuditActionType constants."""

    def test_all_action_types_defined(self) -> None:
        """Verify all expected action types are defined."""
        assert TagAuditActionType.RENAME == "rename"
        assert TagAuditActionType.TYPE_CHANGE == "type_change"
        assert TagAuditActionType.ALIAS_SET == "alias_set"
        assert TagAuditActionType.ALIAS_REMOVED == "alias_removed"
        assert TagAuditActionType.PARENT_SET == "parent_set"
        assert TagAuditActionType.PARENT_REMOVED == "parent_removed"
        assert TagAuditActionType.SOURCE_LINKED == "source_linked"
        assert TagAuditActionType.SOURCE_UNLINKED == "source_unlinked"

    def test_all_values_unique(self) -> None:
        """Ensure no duplicate action type values."""
        values = [
            TagAuditActionType.RENAME,
            TagAuditActionType.TYPE_CHANGE,
            TagAuditActionType.ALIAS_SET,
            TagAuditActionType.ALIAS_REMOVED,
            TagAuditActionType.PARENT_SET,
            TagAuditActionType.PARENT_REMOVED,
            TagAuditActionType.SOURCE_LINKED,
            TagAuditActionType.SOURCE_UNLINKED,
        ]
        assert len(values) == len(set(values))
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_config.py::TestTagAuditActionType -v`
Expected: FAIL with "cannot import name 'TagAuditActionType'"

**Step 3: Write minimal implementation**

In `app/config.py`, after line 223 (after `AdminActionType` class), add:

```python
class TagAuditActionType:
    """Action types for tag audit log."""

    RENAME = "rename"
    TYPE_CHANGE = "type_change"
    ALIAS_SET = "alias_set"
    ALIAS_REMOVED = "alias_removed"
    PARENT_SET = "parent_set"
    PARENT_REMOVED = "parent_removed"
    SOURCE_LINKED = "source_linked"
    SOURCE_UNLINKED = "source_unlinked"
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_config.py::TestTagAuditActionType -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/config.py tests/unit/test_config.py
git commit -m "feat: add TagAuditActionType constants for audit trail"
```

---

## Task 2: Create TagAuditLog Model

**Files:**
- Create: `app/models/tag_audit_log.py`
- Modify: `app/models/__init__.py:49` (add import and export)

**Step 1: Write the test**

Create `tests/unit/test_models_tag_audit_log.py`:

```python
"""Tests for TagAuditLog model."""

from datetime import datetime

import pytest
from sqlmodel import Session, select

from app.models.tag_audit_log import TagAuditLog


class TestTagAuditLogModel:
    """Tests for TagAuditLog model structure."""

    def test_model_has_required_fields(self) -> None:
        """Verify model has all required fields."""
        # Create instance without saving - just test structure
        log = TagAuditLog(
            tag_id=1,
            action_type="rename",
            old_title="Old Name",
            new_title="New Name",
            user_id=1,
        )
        assert log.tag_id == 1
        assert log.action_type == "rename"
        assert log.old_title == "Old Name"
        assert log.new_title == "New Name"
        assert log.user_id == 1

    def test_nullable_fields_default_to_none(self) -> None:
        """Verify nullable fields default to None."""
        log = TagAuditLog(tag_id=1, action_type="rename")
        assert log.old_title is None
        assert log.new_title is None
        assert log.old_type is None
        assert log.new_type is None
        assert log.old_alias_of is None
        assert log.new_alias_of is None
        assert log.old_parent_id is None
        assert log.new_parent_id is None
        assert log.character_tag_id is None
        assert log.source_tag_id is None
        assert log.user_id is None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_models_tag_audit_log.py -v`
Expected: FAIL with "No module named 'app.models.tag_audit_log'"

**Step 3: Write minimal implementation**

Create `app/models/tag_audit_log.py`:

```python
"""
SQLModel-based TagAuditLog model for tracking tag metadata changes.

This module tracks all changes to tag metadata including:
- Renames (title changes)
- Type changes
- Alias changes (setting/removing alias_of)
- Inheritance changes (setting/removing parent)
- Character-source link changes

Uses explicit columns per field type for type safety.
"""

from datetime import datetime

from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel


class TagAuditLogBase(SQLModel):
    """
    Base model with shared fields for TagAuditLog.

    These fields are safe to expose via the API.
    """

    tag_id: int
    action_type: str = Field(max_length=32)

    # Rename fields
    old_title: str | None = Field(default=None, max_length=128)
    new_title: str | None = Field(default=None, max_length=128)

    # Type change fields
    old_type: int | None = Field(default=None)
    new_type: int | None = Field(default=None)

    # Alias change fields (FK to tags.tag_id)
    old_alias_of: int | None = Field(default=None)
    new_alias_of: int | None = Field(default=None)

    # Parent/inheritance change fields (FK to tags.tag_id)
    old_parent_id: int | None = Field(default=None)
    new_parent_id: int | None = Field(default=None)

    # Character-source link fields (FK to tags.tag_id)
    character_tag_id: int | None = Field(default=None)
    source_tag_id: int | None = Field(default=None)


class TagAuditLog(TagAuditLogBase, table=True):
    """
    Database table for tag audit log.

    Tracks all metadata changes to tags for accountability and history.
    Each row represents a single change, with only the relevant columns
    populated for that action type.
    """

    __tablename__ = "tag_audit_log"

    __table_args__ = (
        ForeignKeyConstraint(
            ["tag_id"],
            ["tags.tag_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_tag_audit_log_tag_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tag_audit_log_user_id",
        ),
        ForeignKeyConstraint(
            ["old_alias_of"],
            ["tags.tag_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tag_audit_log_old_alias_of",
        ),
        ForeignKeyConstraint(
            ["new_alias_of"],
            ["tags.tag_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tag_audit_log_new_alias_of",
        ),
        ForeignKeyConstraint(
            ["old_parent_id"],
            ["tags.tag_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tag_audit_log_old_parent_id",
        ),
        ForeignKeyConstraint(
            ["new_parent_id"],
            ["tags.tag_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tag_audit_log_new_parent_id",
        ),
        ForeignKeyConstraint(
            ["character_tag_id"],
            ["tags.tag_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tag_audit_log_character_tag_id",
        ),
        ForeignKeyConstraint(
            ["source_tag_id"],
            ["tags.tag_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tag_audit_log_source_tag_id",
        ),
        Index("idx_tag_audit_log_tag_id", "tag_id"),
        Index("idx_tag_audit_log_user_id", "user_id"),
        Index("idx_tag_audit_log_action_type", "action_type"),
        Index("idx_tag_audit_log_created_at", "created_at"),
        Index("idx_tag_audit_log_character_tag_id", "character_tag_id"),
        Index("idx_tag_audit_log_source_tag_id", "source_tag_id"),
    )

    # Primary key
    id: int | None = Field(default=None, primary_key=True)

    # User who made the change
    user_id: int | None = Field(default=None)

    # Timestamp
    created_at: datetime | None = Field(
        default=None,
        sa_column_kwargs={"server_default": text("current_timestamp()")},
    )
```

**Step 4: Update models/__init__.py**

Add import after line 49 (after `from app.models.tag_history import TagHistory`):

```python
from app.models.tag_audit_log import TagAuditLog
```

Add to `__all__` list (around line 68):

```python
"TagAuditLog",
```

**Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_models_tag_audit_log.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add app/models/tag_audit_log.py app/models/__init__.py tests/unit/test_models_tag_audit_log.py
git commit -m "feat: add TagAuditLog model for tag metadata history"
```

---

## Task 3: Create ImageStatusHistory Model

**Files:**
- Create: `app/models/image_status_history.py`
- Modify: `app/models/__init__.py` (add import and export)

**Step 1: Write the test**

Create `tests/unit/test_models_image_status_history.py`:

```python
"""Tests for ImageStatusHistory model."""

from app.models.image_status_history import ImageStatusHistory


class TestImageStatusHistoryModel:
    """Tests for ImageStatusHistory model structure."""

    def test_model_has_required_fields(self) -> None:
        """Verify model has all required fields."""
        history = ImageStatusHistory(
            image_id=1,
            old_status=1,
            new_status=-1,
            user_id=123,
        )
        assert history.image_id == 1
        assert history.old_status == 1
        assert history.new_status == -1
        assert history.user_id == 123

    def test_user_id_nullable(self) -> None:
        """Verify user_id can be None (for system actions)."""
        history = ImageStatusHistory(
            image_id=1,
            old_status=1,
            new_status=-2,
        )
        assert history.user_id is None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_models_image_status_history.py -v`
Expected: FAIL with "No module named 'app.models.image_status_history'"

**Step 3: Write minimal implementation**

Create `app/models/image_status_history.py`:

```python
"""
SQLModel-based ImageStatusHistory model for tracking image status changes.

This is a public audit table (separate from AdminActions) that tracks
all image status changes for public visibility.

Visibility rules:
- User shown for: REPOST (-1), SPOILER (2), ACTIVE (1)
- User hidden for: REVIEW (-4), LOW_QUALITY (-3), INAPPROPRIATE (-2), OTHER (0)
"""

from datetime import datetime

from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel


class ImageStatusHistoryBase(SQLModel):
    """
    Base model with shared fields for ImageStatusHistory.
    """

    image_id: int
    old_status: int
    new_status: int


class ImageStatusHistory(ImageStatusHistoryBase, table=True):
    """
    Database table for image status history.

    Tracks all status changes for public audit trail.
    """

    __tablename__ = "image_status_history"

    __table_args__ = (
        ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_image_status_history_image_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_image_status_history_user_id",
        ),
        Index("idx_image_status_history_image_id", "image_id"),
        Index("idx_image_status_history_user_id", "user_id"),
        Index("idx_image_status_history_created_at", "created_at"),
    )

    # Primary key
    id: int | None = Field(default=None, primary_key=True)

    # User who made the change (nullable for system actions)
    user_id: int | None = Field(default=None)

    # Timestamp
    created_at: datetime | None = Field(
        default=None,
        sa_column_kwargs={"server_default": text("current_timestamp()")},
    )
```

**Step 4: Update models/__init__.py**

Add import:

```python
from app.models.image_status_history import ImageStatusHistory
```

Add to `__all__`:

```python
"ImageStatusHistory",
```

**Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_models_image_status_history.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add app/models/image_status_history.py app/models/__init__.py tests/unit/test_models_image_status_history.py
git commit -m "feat: add ImageStatusHistory model for public status audit"
```

---

## Task 4: Create Alembic Migration for New Tables

**Files:**
- Create: `alembic/versions/XXXX_add_audit_trail_tables.py`

**Step 1: Generate migration skeleton**

Run: `uv run alembic revision -m "add_audit_trail_tables"`

**Step 2: Write the migration**

Edit the generated file to contain:

```python
"""add_audit_trail_tables

Revision ID: [generated]
Revises: [latest]
Create Date: [generated]

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = '[generated]'
down_revision: str | Sequence[str] | None = '[latest]'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create audit trail tables."""
    # Create tag_audit_log table
    op.create_table(
        'tag_audit_log',
        sa.Column('id', mysql.INTEGER(unsigned=True), nullable=False, autoincrement=True),
        sa.Column('tag_id', mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column('action_type', sa.String(32), nullable=False),
        sa.Column('old_title', sa.String(128), nullable=True),
        sa.Column('new_title', sa.String(128), nullable=True),
        sa.Column('old_type', mysql.INTEGER(), nullable=True),
        sa.Column('new_type', mysql.INTEGER(), nullable=True),
        sa.Column('old_alias_of', mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column('new_alias_of', mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column('old_parent_id', mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column('new_parent_id', mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column('character_tag_id', mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column('source_tag_id', mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column('user_id', mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('current_timestamp()'), nullable=True),
        sa.ForeignKeyConstraint(['tag_id'], ['tags.tag_id'], name='fk_tag_audit_log_tag_id', ondelete='CASCADE', onupdate='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], name='fk_tag_audit_log_user_id', ondelete='SET NULL', onupdate='CASCADE'),
        sa.ForeignKeyConstraint(['old_alias_of'], ['tags.tag_id'], name='fk_tag_audit_log_old_alias_of', ondelete='SET NULL', onupdate='CASCADE'),
        sa.ForeignKeyConstraint(['new_alias_of'], ['tags.tag_id'], name='fk_tag_audit_log_new_alias_of', ondelete='SET NULL', onupdate='CASCADE'),
        sa.ForeignKeyConstraint(['old_parent_id'], ['tags.tag_id'], name='fk_tag_audit_log_old_parent_id', ondelete='SET NULL', onupdate='CASCADE'),
        sa.ForeignKeyConstraint(['new_parent_id'], ['tags.tag_id'], name='fk_tag_audit_log_new_parent_id', ondelete='SET NULL', onupdate='CASCADE'),
        sa.ForeignKeyConstraint(['character_tag_id'], ['tags.tag_id'], name='fk_tag_audit_log_character_tag_id', ondelete='SET NULL', onupdate='CASCADE'),
        sa.ForeignKeyConstraint(['source_tag_id'], ['tags.tag_id'], name='fk_tag_audit_log_source_tag_id', ondelete='SET NULL', onupdate='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_tag_audit_log_tag_id', 'tag_audit_log', ['tag_id'])
    op.create_index('idx_tag_audit_log_user_id', 'tag_audit_log', ['user_id'])
    op.create_index('idx_tag_audit_log_action_type', 'tag_audit_log', ['action_type'])
    op.create_index('idx_tag_audit_log_created_at', 'tag_audit_log', ['created_at'])
    op.create_index('idx_tag_audit_log_character_tag_id', 'tag_audit_log', ['character_tag_id'])
    op.create_index('idx_tag_audit_log_source_tag_id', 'tag_audit_log', ['source_tag_id'])

    # Create image_status_history table
    op.create_table(
        'image_status_history',
        sa.Column('id', mysql.INTEGER(unsigned=True), nullable=False, autoincrement=True),
        sa.Column('image_id', mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column('old_status', mysql.INTEGER(), nullable=False),
        sa.Column('new_status', mysql.INTEGER(), nullable=False),
        sa.Column('user_id', mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('current_timestamp()'), nullable=True),
        sa.ForeignKeyConstraint(['image_id'], ['images.image_id'], name='fk_image_status_history_image_id', ondelete='CASCADE', onupdate='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], name='fk_image_status_history_user_id', ondelete='SET NULL', onupdate='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_image_status_history_image_id', 'image_status_history', ['image_id'])
    op.create_index('idx_image_status_history_user_id', 'image_status_history', ['user_id'])
    op.create_index('idx_image_status_history_created_at', 'image_status_history', ['created_at'])


def downgrade() -> None:
    """Drop audit trail tables."""
    op.drop_index('idx_image_status_history_created_at', table_name='image_status_history')
    op.drop_index('idx_image_status_history_user_id', table_name='image_status_history')
    op.drop_index('idx_image_status_history_image_id', table_name='image_status_history')
    op.drop_table('image_status_history')

    op.drop_index('idx_tag_audit_log_source_tag_id', table_name='tag_audit_log')
    op.drop_index('idx_tag_audit_log_character_tag_id', table_name='tag_audit_log')
    op.drop_index('idx_tag_audit_log_created_at', table_name='tag_audit_log')
    op.drop_index('idx_tag_audit_log_action_type', table_name='tag_audit_log')
    op.drop_index('idx_tag_audit_log_user_id', table_name='tag_audit_log')
    op.drop_index('idx_tag_audit_log_tag_id', table_name='tag_audit_log')
    op.drop_table('tag_audit_log')
```

**Step 3: Run migration**

Run: `uv run alembic upgrade head`
Expected: Migration completes successfully

**Step 4: Verify tables exist**

Run: `uv run python -c "from app.models import TagAuditLog, ImageStatusHistory; print('Models imported successfully')"`
Expected: "Models imported successfully"

**Step 5: Commit**

```bash
git add alembic/versions/*_add_audit_trail_tables.py
git commit -m "feat: add migration for audit trail tables"
```

---

## Task 5: Create Audit Schemas

**Files:**
- Create: `app/schemas/audit.py`

**Step 1: Write the test**

Create `tests/unit/test_schemas_audit.py`:

```python
"""Tests for audit schemas."""

from datetime import datetime, UTC

from app.schemas.audit import (
    TagAuditLogResponse,
    ImageStatusHistoryResponse,
    TagHistoryResponse,
)


class TestTagAuditLogResponse:
    """Tests for TagAuditLogResponse schema."""

    def test_validates_rename_action(self) -> None:
        """Test schema validates rename action data."""
        data = {
            "id": 1,
            "tag_id": 100,
            "action_type": "rename",
            "old_title": "Old Name",
            "new_title": "New Name",
            "user": {"user_id": 1, "username": "test"},
            "created_at": datetime.now(UTC),
        }
        response = TagAuditLogResponse.model_validate(data)
        assert response.action_type == "rename"
        assert response.old_title == "Old Name"
        assert response.new_title == "New Name"

    def test_validates_source_linked_action(self) -> None:
        """Test schema validates source_linked action data."""
        data = {
            "id": 1,
            "tag_id": 100,
            "action_type": "source_linked",
            "character_tag": {"tag_id": 100, "title": "Cirno"},
            "source_tag": {"tag_id": 200, "title": "Touhou"},
            "user": {"user_id": 1, "username": "test"},
            "created_at": datetime.now(UTC),
        }
        response = TagAuditLogResponse.model_validate(data)
        assert response.action_type == "source_linked"
        assert response.character_tag is not None
        assert response.source_tag is not None


class TestImageStatusHistoryResponse:
    """Tests for ImageStatusHistoryResponse schema."""

    def test_validates_status_change(self) -> None:
        """Test schema validates status change data."""
        data = {
            "id": 1,
            "image_id": 1000,
            "old_status": 1,
            "old_status_label": "active",
            "new_status": -1,
            "new_status_label": "repost",
            "user": {"user_id": 1, "username": "mod"},
            "created_at": datetime.now(UTC),
        }
        response = ImageStatusHistoryResponse.model_validate(data)
        assert response.old_status == 1
        assert response.new_status == -1

    def test_user_can_be_none(self) -> None:
        """Test schema allows null user for hidden statuses."""
        data = {
            "id": 1,
            "image_id": 1000,
            "old_status": -4,
            "old_status_label": "review",
            "new_status": 1,
            "new_status_label": "active",
            "user": None,
            "created_at": datetime.now(UTC),
        }
        response = ImageStatusHistoryResponse.model_validate(data)
        assert response.user is None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_schemas_audit.py -v`
Expected: FAIL with "No module named 'app.schemas.audit'"

**Step 3: Write minimal implementation**

Create `app/schemas/audit.py`:

```python
"""
Pydantic schemas for audit trail endpoints.
"""

from pydantic import BaseModel

from app.schemas.base import UTCDatetime
from app.schemas.common import UserSummary
from app.schemas.tag import LinkedTag


class TagAuditLogResponse(BaseModel):
    """Schema for tag audit log response."""

    id: int
    tag_id: int
    action_type: str

    # Rename fields
    old_title: str | None = None
    new_title: str | None = None

    # Type change fields
    old_type: int | None = None
    new_type: int | None = None

    # Alias change fields
    old_alias_of: int | None = None
    new_alias_of: int | None = None

    # Parent change fields
    old_parent_id: int | None = None
    new_parent_id: int | None = None

    # Character-source link fields (enriched with tag info)
    character_tag: LinkedTag | None = None
    source_tag: LinkedTag | None = None

    # Who made the change
    user: UserSummary | None = None

    created_at: UTCDatetime

    model_config = {"from_attributes": True}


class TagAuditLogListResponse(BaseModel):
    """Schema for paginated tag audit log list."""

    total: int
    page: int
    per_page: int
    items: list[TagAuditLogResponse]


class TagHistoryResponse(BaseModel):
    """Schema for tag usage history (add/remove from images)."""

    image_id: int
    tag_id: int
    action: str  # 'added' or 'removed'
    user: UserSummary | None = None
    date: UTCDatetime


class TagHistoryListResponse(BaseModel):
    """Schema for paginated tag history list."""

    total: int
    page: int
    per_page: int
    items: list[TagHistoryResponse]


class ImageTagHistoryResponse(BaseModel):
    """Schema for image tag history (which tags were added/removed)."""

    tag: LinkedTag
    action: str  # 'added' or 'removed'
    user: UserSummary | None = None
    date: UTCDatetime


class ImageTagHistoryListResponse(BaseModel):
    """Schema for paginated image tag history list."""

    total: int
    page: int
    per_page: int
    items: list[ImageTagHistoryResponse]


class ImageStatusHistoryResponse(BaseModel):
    """Schema for image status history response."""

    id: int
    image_id: int
    old_status: int
    old_status_label: str
    new_status: int
    new_status_label: str
    user: UserSummary | None = None  # None for hidden statuses
    created_at: UTCDatetime

    model_config = {"from_attributes": True}


class ImageStatusHistoryListResponse(BaseModel):
    """Schema for paginated image status history list."""

    total: int
    page: int
    per_page: int
    items: list[ImageStatusHistoryResponse]


class ImageReviewPublicResponse(BaseModel):
    """Schema for public review outcome (hides votes and initiator)."""

    review_id: int
    review_type: int
    review_type_label: str
    outcome: int
    outcome_label: str
    created_at: UTCDatetime
    closed_at: UTCDatetime | None = None


class ImageReviewListResponse(BaseModel):
    """Schema for paginated review list."""

    total: int
    page: int
    per_page: int
    items: list[ImageReviewPublicResponse]


class UserHistoryItem(BaseModel):
    """Schema for a single item in user history (polymorphic)."""

    type: str  # 'tag_metadata', 'tag_usage', 'status_change'

    # For tag_metadata type
    action_type: str | None = None
    tag: LinkedTag | None = None
    old_title: str | None = None
    new_title: str | None = None

    # For tag_usage type
    action: str | None = None  # 'added' or 'removed'
    image_id: int | None = None

    # For status_change type
    old_status: int | None = None
    new_status: int | None = None
    new_status_label: str | None = None

    # Common
    created_at: UTCDatetime | None = None
    date: UTCDatetime | None = None  # For tag_usage (uses 'date' field)


class UserHistoryListResponse(BaseModel):
    """Schema for paginated user history list."""

    total: int
    page: int
    per_page: int
    items: list[UserHistoryItem]
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_schemas_audit.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/schemas/audit.py tests/unit/test_schemas_audit.py
git commit -m "feat: add audit trail response schemas"
```

---

## Task 6: Wire Up TagHistory for Tag Add/Remove on Images

**Files:**
- Modify: `app/api/v1/images.py:995-1000` (add_tag_to_image)
- Modify: `app/api/v1/images.py:1052-1057` (remove_tag_from_image)

**Step 1: Write the test**

Add to `tests/api/v1/test_images.py` or create `tests/api/v1/test_image_tag_history.py`:

```python
"""Tests for tag history tracking on image tagging."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag_history import TagHistory


class TestTagHistoryOnImageTagging:
    """Tests that TagHistory is written when tags are added/removed from images."""

    @pytest.mark.asyncio
    async def test_add_tag_creates_history_entry(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
        auth_headers_with_tag_permission: dict,
        test_image: int,
        test_tag: int,
    ) -> None:
        """Adding a tag to an image should create a TagHistory entry."""
        response = await async_client.post(
            f"/api/v1/images/{test_image}/tags/{test_tag}",
            headers=auth_headers_with_tag_permission,
        )
        assert response.status_code == 201

        # Verify TagHistory entry was created
        result = await db_session.execute(
            select(TagHistory).where(
                TagHistory.image_id == test_image,
                TagHistory.tag_id == test_tag,
                TagHistory.action == "a",
            )
        )
        history = result.scalar_one_or_none()
        assert history is not None
        assert history.user_id is not None

    @pytest.mark.asyncio
    async def test_remove_tag_creates_history_entry(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
        auth_headers_with_tag_permission: dict,
        test_image_with_tag: tuple[int, int],  # (image_id, tag_id)
    ) -> None:
        """Removing a tag from an image should create a TagHistory entry."""
        image_id, tag_id = test_image_with_tag

        response = await async_client.delete(
            f"/api/v1/images/{image_id}/tags/{tag_id}",
            headers=auth_headers_with_tag_permission,
        )
        assert response.status_code == 204

        # Verify TagHistory entry was created
        result = await db_session.execute(
            select(TagHistory).where(
                TagHistory.image_id == image_id,
                TagHistory.tag_id == tag_id,
                TagHistory.action == "r",
            )
        )
        history = result.scalar_one_or_none()
        assert history is not None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/v1/test_image_tag_history.py -v`
Expected: FAIL (history entry not created)

**Step 3: Write minimal implementation**

In `app/api/v1/images.py`, add import at top:

```python
from app.models.tag_history import TagHistory
```

In `add_tag_to_image` function, after line 1000 (`db.add(tag_link)`), add:

```python
    # Record in tag history
    history_entry = TagHistory(
        image_id=image_id,
        tag_id=resolved_tag_id,
        action="a",
        user_id=current_user.id,
    )
    db.add(history_entry)
```

In `remove_tag_from_image` function, before line 1052 (`await db.execute(delete(TagLinks)...`), add:

```python
    # Record in tag history
    history_entry = TagHistory(
        image_id=image_id,
        tag_id=tag_id,
        action="r",
        user_id=current_user.id,
    )
    db.add(history_entry)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/v1/test_image_tag_history.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/api/v1/images.py tests/api/v1/test_image_tag_history.py
git commit -m "feat: write to TagHistory on image tag add/remove"
```

---

## Task 7: Wire Up TagAuditLog for Tag Metadata Changes

**Files:**
- Modify: `app/api/v1/tags.py:762-807` (update_tag)

**Step 1: Write the test**

Create `tests/api/v1/test_tag_audit_log.py`:

```python
"""Tests for tag audit log tracking on tag metadata changes."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagAuditActionType
from app.models.tag_audit_log import TagAuditLog


class TestTagAuditLogOnUpdate:
    """Tests that TagAuditLog is written when tags are updated."""

    @pytest.mark.asyncio
    async def test_rename_creates_audit_entry(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
        auth_headers_admin: dict,
        test_tag: int,
    ) -> None:
        """Renaming a tag should create a TagAuditLog entry."""
        # Get original title
        from app.models import Tags
        tag_result = await db_session.execute(select(Tags).where(Tags.tag_id == test_tag))
        original_tag = tag_result.scalar_one()
        original_title = original_tag.title

        response = await async_client.put(
            f"/api/v1/tags/{test_tag}",
            headers=auth_headers_admin,
            json={"title": "New Title", "type": original_tag.type},
        )
        assert response.status_code == 200

        # Verify audit entry was created
        result = await db_session.execute(
            select(TagAuditLog).where(
                TagAuditLog.tag_id == test_tag,
                TagAuditLog.action_type == TagAuditActionType.RENAME,
            )
        )
        audit = result.scalar_one_or_none()
        assert audit is not None
        assert audit.old_title == original_title
        assert audit.new_title == "New Title"

    @pytest.mark.asyncio
    async def test_type_change_creates_audit_entry(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
        auth_headers_admin: dict,
        test_tag: int,
    ) -> None:
        """Changing tag type should create a TagAuditLog entry."""
        from app.models import Tags
        tag_result = await db_session.execute(select(Tags).where(Tags.tag_id == test_tag))
        original_tag = tag_result.scalar_one()
        original_type = original_tag.type
        new_type = 2 if original_type != 2 else 1

        response = await async_client.put(
            f"/api/v1/tags/{test_tag}",
            headers=auth_headers_admin,
            json={"title": original_tag.title, "type": new_type},
        )
        assert response.status_code == 200

        result = await db_session.execute(
            select(TagAuditLog).where(
                TagAuditLog.tag_id == test_tag,
                TagAuditLog.action_type == TagAuditActionType.TYPE_CHANGE,
            )
        )
        audit = result.scalar_one_or_none()
        assert audit is not None
        assert audit.old_type == original_type
        assert audit.new_type == new_type
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/v1/test_tag_audit_log.py -v`
Expected: FAIL (audit entry not created)

**Step 3: Write minimal implementation**

In `app/api/v1/tags.py`, add imports:

```python
from app.config import TagAuditActionType, TagType
from app.models.tag_audit_log import TagAuditLog
```

Replace the `update_tag` function (lines 762-807) with:

```python
@router.put("/{tag_id}", response_model=TagResponse)
async def update_tag(
    tag_id: Annotated[int, Path(description="Tag ID")],
    tag_data: TagCreate,
    current_user: Annotated[Users, Depends(get_current_user)],
    _: Annotated[None, Depends(require_permission(Permission.TAG_UPDATE))],
    db: AsyncSession = Depends(get_db),
) -> TagResponse:
    """
    Update an existing tag.
    """

    tag_result = await db.execute(select(Tags).where(Tags.tag_id == tag_id))  # type: ignore[arg-type]
    tag = tag_result.scalar_one_or_none()

    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")

    # Store original values for audit logging
    original_title = tag.title
    original_type = tag.type
    original_alias_of = tag.alias_of
    original_parent_id = tag.inheritedfrom_id

    update_data = tag_data.model_dump(exclude_unset=True)

    # Validate inheritedfrom_id and alias fields if present
    inheritedfrom_id = update_data.get("inheritedfrom_id")
    if inheritedfrom_id is not None:
        parent_result = await db.execute(select(Tags).where(Tags.tag_id == inheritedfrom_id))
        parent_tag = parent_result.scalar_one_or_none()
        if not parent_tag:
            raise HTTPException(
                status_code=400, detail=f"Parent tag with id {inheritedfrom_id} does not exist"
            )

    alias_id = update_data.get("alias_of")
    if alias_id is not None:
        alias_result = await db.execute(select(Tags).where(Tags.tag_id == alias_id))
        alias_tag = alias_result.scalar_one_or_none()
        if not alias_tag:
            raise HTTPException(
                status_code=400, detail=f"Alias of tag with id {alias_id} does not exist"
            )

    # Update fields
    for key, value in update_data.items():
        setattr(tag, key, value)

    # Create audit log entries for changes
    new_title = update_data.get("title")
    if new_title is not None and new_title != original_title:
        audit = TagAuditLog(
            tag_id=tag_id,
            action_type=TagAuditActionType.RENAME,
            old_title=original_title,
            new_title=new_title,
            user_id=current_user.user_id,
        )
        db.add(audit)

    new_type = update_data.get("type")
    if new_type is not None and new_type != original_type:
        audit = TagAuditLog(
            tag_id=tag_id,
            action_type=TagAuditActionType.TYPE_CHANGE,
            old_type=original_type,
            new_type=new_type,
            user_id=current_user.user_id,
        )
        db.add(audit)

    new_alias = update_data.get("alias_of")
    if "alias_of" in update_data and new_alias != original_alias_of:
        if original_alias_of is None and new_alias is not None:
            action_type = TagAuditActionType.ALIAS_SET
        elif original_alias_of is not None and new_alias is None:
            action_type = TagAuditActionType.ALIAS_REMOVED
        else:
            action_type = TagAuditActionType.ALIAS_SET  # Changed alias target
        audit = TagAuditLog(
            tag_id=tag_id,
            action_type=action_type,
            old_alias_of=original_alias_of,
            new_alias_of=new_alias,
            user_id=current_user.user_id,
        )
        db.add(audit)

    new_parent = update_data.get("inheritedfrom_id")
    if "inheritedfrom_id" in update_data and new_parent != original_parent_id:
        if original_parent_id is None and new_parent is not None:
            action_type = TagAuditActionType.PARENT_SET
        elif original_parent_id is not None and new_parent is None:
            action_type = TagAuditActionType.PARENT_REMOVED
        else:
            action_type = TagAuditActionType.PARENT_SET  # Changed parent
        audit = TagAuditLog(
            tag_id=tag_id,
            action_type=action_type,
            old_parent_id=original_parent_id,
            new_parent_id=new_parent,
            user_id=current_user.user_id,
        )
        db.add(audit)

    db.add(tag)
    await db.commit()
    await db.refresh(tag)

    return TagResponse.model_validate(tag)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/v1/test_tag_audit_log.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/api/v1/tags.py tests/api/v1/test_tag_audit_log.py
git commit -m "feat: write to TagAuditLog on tag metadata changes"
```

---

## Task 8: Wire Up TagAuditLog for Character-Source Link Changes

**Files:**
- Modify: `app/api/v1/tags.py:903-954` (create_character_source_link)
- Modify: `app/api/v1/tags.py:1002-1020` (delete_character_source_link)

**Step 1: Write the test**

Add to `tests/api/v1/test_tag_audit_log.py`:

```python
class TestTagAuditLogOnCharacterSourceLinks:
    """Tests that TagAuditLog is written for character-source link changes."""

    @pytest.mark.asyncio
    async def test_create_link_creates_audit_entry(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
        auth_headers_with_tag_permission: dict,
        test_character_tag: int,
        test_source_tag: int,
    ) -> None:
        """Creating a character-source link should create audit entries."""
        response = await async_client.post(
            "/api/v1/character-source-links",
            headers=auth_headers_with_tag_permission,
            json={
                "character_tag_id": test_character_tag,
                "source_tag_id": test_source_tag,
            },
        )
        assert response.status_code == 201

        # Check audit entry for character tag
        result = await db_session.execute(
            select(TagAuditLog).where(
                TagAuditLog.tag_id == test_character_tag,
                TagAuditLog.action_type == TagAuditActionType.SOURCE_LINKED,
            )
        )
        audit = result.scalar_one_or_none()
        assert audit is not None
        assert audit.character_tag_id == test_character_tag
        assert audit.source_tag_id == test_source_tag

    @pytest.mark.asyncio
    async def test_delete_link_creates_audit_entry(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
        auth_headers_with_tag_permission: dict,
        test_character_source_link: int,  # link_id
        test_character_tag: int,
        test_source_tag: int,
    ) -> None:
        """Deleting a character-source link should create audit entries."""
        response = await async_client.delete(
            f"/api/v1/character-source-links/{test_character_source_link}",
            headers=auth_headers_with_tag_permission,
        )
        assert response.status_code == 204

        # Check audit entry
        result = await db_session.execute(
            select(TagAuditLog).where(
                TagAuditLog.tag_id == test_character_tag,
                TagAuditLog.action_type == TagAuditActionType.SOURCE_UNLINKED,
            )
        )
        audit = result.scalar_one_or_none()
        assert audit is not None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/v1/test_tag_audit_log.py::TestTagAuditLogOnCharacterSourceLinks -v`
Expected: FAIL

**Step 3: Write minimal implementation**

In `create_character_source_link` function, after `await db.refresh(new_link)` (line 946), add:

```python
    # Log audit trail for character tag
    audit = TagAuditLog(
        tag_id=link_data.character_tag_id,
        action_type=TagAuditActionType.SOURCE_LINKED,
        character_tag_id=link_data.character_tag_id,
        source_tag_id=link_data.source_tag_id,
        user_id=current_user.user_id,
    )
    db.add(audit)
    await db.commit()
```

In `delete_character_source_link` function, before `await db.delete(link)` (line 1019), add:

```python
    # Log audit trail
    audit = TagAuditLog(
        tag_id=link.character_tag_id,
        action_type=TagAuditActionType.SOURCE_UNLINKED,
        character_tag_id=link.character_tag_id,
        source_tag_id=link.source_tag_id,
        user_id=current_user.user_id,
    )
    db.add(audit)
```

Note: Need to add `current_user` parameter to `delete_character_source_link`:

```python
@character_source_links_router.delete("/{link_id}", status_code=204)
async def delete_character_source_link(
    link_id: Annotated[int, Path(description="Link ID")],
    current_user: Annotated[Users, Depends(get_current_user)],
    _: Annotated[None, Depends(require_permission(Permission.TAG_CREATE))],
    db: AsyncSession = Depends(get_db),
) -> None:
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/v1/test_tag_audit_log.py::TestTagAuditLogOnCharacterSourceLinks -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/api/v1/tags.py
git commit -m "feat: write to TagAuditLog on character-source link changes"
```

---

## Task 9: Wire Up ImageStatusHistory for Status Changes

**Files:**
- Modify: `app/api/v1/admin.py:737-750` (change_image_status)
- Modify: `app/services/review_jobs.py:186-192` (_close_review)

**Step 1: Write the test**

Create `tests/api/v1/test_image_status_history.py`:

```python
"""Tests for image status history tracking."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus
from app.models.image_status_history import ImageStatusHistory


class TestImageStatusHistoryOnStatusChange:
    """Tests that ImageStatusHistory is written on status changes."""

    @pytest.mark.asyncio
    async def test_status_change_creates_history_entry(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
        auth_headers_admin: dict,
        test_image: int,
    ) -> None:
        """Changing image status should create ImageStatusHistory entry."""
        response = await async_client.post(
            f"/api/v1/admin/images/{test_image}/status",
            headers=auth_headers_admin,
            json={"status": ImageStatus.SPOILER},
        )
        assert response.status_code == 200

        # Verify history entry was created
        result = await db_session.execute(
            select(ImageStatusHistory).where(
                ImageStatusHistory.image_id == test_image,
                ImageStatusHistory.new_status == ImageStatus.SPOILER,
            )
        )
        history = result.scalar_one_or_none()
        assert history is not None
        assert history.old_status == ImageStatus.ACTIVE
        assert history.user_id is not None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/v1/test_image_status_history.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

In `app/api/v1/admin.py`, add import:

```python
from app.models.image_status_history import ImageStatusHistory
```

In `change_image_status` function, after the `AdminActions` logging (line 750), add:

```python
    # Log to public status history
    if status_data.status is not None and status_data.status != previous_status:
        status_history = ImageStatusHistory(
            image_id=image_id,
            old_status=previous_status,
            new_status=image.status,
            user_id=current_user.user_id,
        )
        db.add(status_history)
```

In `app/services/review_jobs.py`, add import:

```python
from app.models.image_status_history import ImageStatusHistory
```

In `_close_review` function, after updating the image status (around line 192), add:

```python
    # Log to public status history
    if image:
        status_history = ImageStatusHistory(
            image_id=review.image_id,
            old_status=ImageStatus.REVIEW,  # Was under review
            new_status=image.status,
            user_id=None,  # System action
        )
        db.add(status_history)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/v1/test_image_status_history.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/api/v1/admin.py app/services/review_jobs.py tests/api/v1/test_image_status_history.py
git commit -m "feat: write to ImageStatusHistory on status changes"
```

---

## Task 10: Create API Endpoint GET /tags/{tag_id}/history

**Files:**
- Modify: `app/api/v1/tags.py` (add new endpoint)

**Step 1: Write the test**

Add to `tests/api/v1/test_tag_audit_log.py`:

```python
class TestGetTagHistory:
    """Tests for GET /tags/{tag_id}/history endpoint."""

    @pytest.mark.asyncio
    async def test_get_tag_history_returns_audit_entries(
        self,
        async_client: AsyncClient,
        test_tag_with_history: int,  # Tag with audit entries
    ) -> None:
        """GET /tags/{tag_id}/history should return audit entries."""
        response = await async_client.get(f"/api/v1/tags/{test_tag_with_history}/history")
        assert response.status_code == 200

        data = response.json()
        assert "items" in data
        assert "total" in data
        assert len(data["items"]) > 0
        assert data["items"][0]["action_type"] is not None

    @pytest.mark.asyncio
    async def test_get_tag_history_includes_user_info(
        self,
        async_client: AsyncClient,
        test_tag_with_history: int,
    ) -> None:
        """History entries should include user info."""
        response = await async_client.get(f"/api/v1/tags/{test_tag_with_history}/history")
        data = response.json()

        assert data["items"][0]["user"] is not None
        assert "user_id" in data["items"][0]["user"]
        assert "username" in data["items"][0]["user"]

    @pytest.mark.asyncio
    async def test_get_tag_history_404_for_nonexistent_tag(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Should return 404 for nonexistent tag."""
        response = await async_client.get("/api/v1/tags/99999999/history")
        assert response.status_code == 404
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/v1/test_tag_audit_log.py::TestGetTagHistory -v`
Expected: FAIL with 404 (endpoint doesn't exist)

**Step 3: Write minimal implementation**

In `app/api/v1/tags.py`, add imports:

```python
from app.schemas.audit import TagAuditLogListResponse, TagAuditLogResponse
```

Add the endpoint after `get_tag` (around line 710):

```python
@router.get("/{tag_id}/history", response_model=TagAuditLogListResponse)
async def get_tag_history(
    tag_id: Annotated[int, Path(description="Tag ID")],
    pagination: Annotated[PaginationParams, Depends()],
    db: AsyncSession = Depends(get_db),
) -> TagAuditLogListResponse:
    """
    Get tag metadata change history.

    Returns a paginated list of all metadata changes (renames, type changes,
    alias changes, inheritance changes, character-source links).

    Sorted by most recent first.
    """
    # Verify tag exists
    tag_result = await db.execute(select(Tags).where(Tags.tag_id == tag_id))  # type: ignore[arg-type]
    if not tag_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Tag not found")

    # Query audit log entries for this tag
    # Include entries where tag_id matches OR character_tag_id/source_tag_id matches
    query = (
        select(TagAuditLog, Users)
        .outerjoin(Users, TagAuditLog.user_id == Users.user_id)
        .where(
            (TagAuditLog.tag_id == tag_id)
            | (TagAuditLog.character_tag_id == tag_id)
            | (TagAuditLog.source_tag_id == tag_id)
        )
    )

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Paginate and order
    query = (
        query.order_by(desc(TagAuditLog.created_at))
        .offset(pagination.offset)
        .limit(pagination.per_page)
    )

    result = await db.execute(query)
    rows = result.all()

    items = []
    for audit, user in rows:
        user_summary = None
        if user:
            user_summary = UserSummary(
                user_id=user.user_id or 0,
                username=user.username,
                avatar=user.avatar,
            )

        # Build response with optional tag info for char-source links
        response = TagAuditLogResponse(
            id=audit.id or 0,
            tag_id=audit.tag_id,
            action_type=audit.action_type,
            old_title=audit.old_title,
            new_title=audit.new_title,
            old_type=audit.old_type,
            new_type=audit.new_type,
            old_alias_of=audit.old_alias_of,
            new_alias_of=audit.new_alias_of,
            old_parent_id=audit.old_parent_id,
            new_parent_id=audit.new_parent_id,
            user=user_summary,
            created_at=audit.created_at,
        )

        # Enrich with tag titles for char-source links
        if audit.character_tag_id and audit.source_tag_id:
            char_result = await db.execute(
                select(Tags.tag_id, Tags.title).where(Tags.tag_id == audit.character_tag_id)
            )
            char_row = char_result.first()
            if char_row:
                response.character_tag = LinkedTag(tag_id=char_row[0], title=char_row[1])

            source_result = await db.execute(
                select(Tags.tag_id, Tags.title).where(Tags.tag_id == audit.source_tag_id)
            )
            source_row = source_result.first()
            if source_row:
                response.source_tag = LinkedTag(tag_id=source_row[0], title=source_row[1])

        items.append(response)

    return TagAuditLogListResponse(
        total=total or 0,
        page=pagination.page,
        per_page=pagination.per_page,
        items=items,
    )
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/v1/test_tag_audit_log.py::TestGetTagHistory -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/api/v1/tags.py
git commit -m "feat: add GET /tags/{tag_id}/history endpoint"
```

---

## Task 11: Create API Endpoint GET /tags/{tag_id}/usage-history

**Files:**
- Modify: `app/api/v1/tags.py` (add new endpoint)

This follows the same pattern as Task 10. Query TagHistory table, join with Users, return paginated list.

---

## Task 12: Create API Endpoint GET /images/{image_id}/tag-history

**Files:**
- Modify: `app/api/v1/images.py` (add new endpoint)

Same pattern. Query TagHistory where image_id matches, join with Tags for titles, Users for user info.

---

## Task 13: Create API Endpoint GET /images/{image_id}/status-history

**Files:**
- Modify: `app/api/v1/images.py` (add new endpoint)

Query ImageStatusHistory, apply user visibility rules (show user for REPOST/SPOILER/ACTIVE, hide for others).

---

## Task 14: Create API Endpoint GET /images/{image_id}/reviews

**Files:**
- Modify: `app/api/v1/images.py` (add new endpoint)

Query ImageReviews where status=CLOSED, return only public fields (outcome, timestamps).

---

## Task 15: Create API Endpoint GET /users/{user_id}/history

**Files:**
- Create: `app/api/v1/history.py` (new router)
- Modify: `app/api/v1/__init__.py` (add router)

Aggregate from TagAuditLog, TagHistory, ImageStatusHistory (with visibility rules), return unified sorted list.

---

## Summary

Tasks 1-9 set up the infrastructure (models, migrations, schemas, write hooks).
Tasks 10-15 create the API endpoints for reading audit data.

Each task follows TDD: write failing test, implement, verify pass, commit.
