# Character-Source Links Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create a system to link character tags to their source tags for discovery, suggestions, and soft warnings.

**Architecture:** New `character_source_links` junction table with CRUD endpoints under `/api/v1/tags/`. Extends existing tag responses to include linked sources/characters. Uses existing TAG_CREATE/TAG_UPDATE permissions.

**Tech Stack:** SQLModel, FastAPI, Alembic, pytest

---

## Task 1: Create CharacterSourceLinks Model

**Files:**
- Create: `app/models/character_source_link.py`
- Modify: `app/models/__init__.py`

**Step 1: Write the model file**

Create `app/models/character_source_link.py`:

```python
"""
SQLModel-based CharacterSourceLink model for linking character tags to source tags.

This module defines the CharacterSourceLinks database model using SQLModel.
The inheritance structure is:

CharacterSourceLinkBase (shared public fields)
    ├─> CharacterSourceLinks (database table)
    └─> API schemas (defined in app/schemas)
"""

from datetime import UTC, datetime

from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel


class CharacterSourceLinkBase(SQLModel):
    """
    Base model with shared public fields for character-source links.

    These fields are safe to expose via the API.
    """

    character_tag_id: int = Field(foreign_key="tags.tag_id", index=True)
    source_tag_id: int = Field(foreign_key="tags.tag_id", index=True)


class CharacterSourceLinks(CharacterSourceLinkBase, table=True):
    """
    Database table for character-source links.

    Links character tags (type=4) to their source tags (type=2).
    Many-to-many: A character can have multiple sources, a source can have many characters.
    """

    __tablename__ = "character_source_links"

    __table_args__ = (
        ForeignKeyConstraint(
            ["character_tag_id"],
            ["tags.tag_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_character_source_links_character_tag_id",
        ),
        ForeignKeyConstraint(
            ["source_tag_id"],
            ["tags.tag_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_character_source_links_source_tag_id",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_character_source_links_user_id",
        ),
        Index("idx_character_tag_id", "character_tag_id"),
        Index("idx_source_tag_id", "source_tag_id"),
        Index("unique_character_source", "character_tag_id", "source_tag_id", unique=True),
    )

    # Primary key
    id: int | None = Field(default=None, primary_key=True)

    # Timestamp
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column_kwargs={"server_default": text("current_timestamp()")},
    )

    # Creator user (optional, for audit trail)
    created_by_user_id: int | None = Field(default=None, foreign_key="users.user_id")
```

**Step 2: Add to models/__init__.py**

Add import and export for CharacterSourceLinks:

```python
from app.models.character_source_link import CharacterSourceLinks

# In __all__ list, add:
"CharacterSourceLinks",
```

**Step 3: Run type checker to verify**

Run: `uv run mypy app/models/character_source_link.py`
Expected: No errors

**Step 4: Commit**

```bash
git add app/models/character_source_link.py app/models/__init__.py
git commit -m "feat: add CharacterSourceLinks model"
```

---

## Task 2: Create Alembic Migration

**Files:**
- Create: `alembic/versions/XXXX_add_character_source_links_table.py`

**Step 1: Create migration file**

Run: `uv run alembic revision -m "add_character_source_links_table"`

**Step 2: Edit the migration file**

Replace the generated upgrade/downgrade with:

```python
"""add_character_source_links_table

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
    op.create_table(
        'character_source_links',
        sa.Column('character_tag_id', mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column('source_tag_id', mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column('id', mysql.INTEGER(unsigned=True), nullable=False, autoincrement=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('current_timestamp()'), nullable=False),
        sa.Column('created_by_user_id', mysql.INTEGER(unsigned=True), nullable=True),
        sa.ForeignKeyConstraint(['character_tag_id'], ['tags.tag_id'], name='fk_character_source_links_character_tag_id', ondelete='CASCADE', onupdate='CASCADE'),
        sa.ForeignKeyConstraint(['source_tag_id'], ['tags.tag_id'], name='fk_character_source_links_source_tag_id', ondelete='CASCADE', onupdate='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.user_id'], name='fk_character_source_links_user_id', ondelete='SET NULL', onupdate='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('character_tag_id', 'source_tag_id', name='unique_character_source')
    )
    op.create_index('idx_character_tag_id', 'character_source_links', ['character_tag_id'], unique=False)
    op.create_index('idx_source_tag_id', 'character_source_links', ['source_tag_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_source_tag_id', table_name='character_source_links')
    op.drop_index('idx_character_tag_id', table_name='character_source_links')
    op.drop_table('character_source_links')
```

**Step 3: Run migration**

Run: `uv run alembic upgrade head`
Expected: Migration applies successfully

**Step 4: Verify table exists**

Run: `docker exec -it shuushuu-api-db-1 mariadb -u shuushuu -pshuushuu shuushuu -e "DESCRIBE character_source_links;"`
Expected: Table structure displayed

**Step 5: Commit**

```bash
git add alembic/versions/*_add_character_source_links_table.py
git commit -m "feat: add character_source_links migration"
```

---

## Task 3: Create API Schemas

**Files:**
- Modify: `app/schemas/tag.py`

**Step 1: Add new schemas to tag.py**

Add after `TagExternalLinkResponse`:

```python
class CharacterSourceLinkCreate(BaseModel):
    """Schema for creating a character-source link"""

    character_tag_id: int
    source_tag_id: int


class CharacterSourceLinkResponse(BaseModel):
    """Schema for character-source link response"""

    id: int
    character_tag_id: int
    source_tag_id: int
    created_at: datetime
    created_by_user_id: int | None = None

    model_config = {"from_attributes": True}


class CharacterSourceLinkListResponse(BaseModel):
    """Schema for paginated character-source link list"""

    total: int
    page: int
    per_page: int
    links: list[CharacterSourceLinkResponse]


class LinkedTag(BaseModel):
    """Minimal tag info for linked sources/characters"""

    tag_id: int
    title: str | None


class CharacterSourceLinkWithTitles(CharacterSourceLinkResponse):
    """Link response with tag titles included"""

    character_title: str | None = None
    source_title: str | None = None
```

**Step 2: Run type checker**

Run: `uv run mypy app/schemas/tag.py`
Expected: No errors

**Step 3: Commit**

```bash
git add app/schemas/tag.py
git commit -m "feat: add character-source link schemas"
```

---

## Task 4: Write Failing Tests for CRUD Endpoints

**Files:**
- Create: `tests/api/v1/test_character_source_links.py`

**Step 1: Write test file**

```python
"""
Tests for character-source links API endpoints.

These tests cover the /api/v1/tags/character-source-links endpoints including:
- List character-source links
- Create link (admin only)
- Delete link (admin only)
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.core.security import get_password_hash
from app.models.character_source_link import CharacterSourceLinks
from app.models.permissions import Perms, UserPerms
from app.models.tag import Tags
from app.models.user import Users


@pytest.fixture
async def character_tag(db_session: AsyncSession) -> Tags:
    """Create a character tag for testing."""
    tag = Tags(title="Hakurei Reimu", desc="Shrine maiden", type=TagType.CHARACTER)
    db_session.add(tag)
    await db_session.commit()
    await db_session.refresh(tag)
    return tag


@pytest.fixture
async def source_tag(db_session: AsyncSession) -> Tags:
    """Create a source tag for testing."""
    tag = Tags(title="Touhou", desc="Touhou Project", type=TagType.SOURCE)
    db_session.add(tag)
    await db_session.commit()
    await db_session.refresh(tag)
    return tag


@pytest.fixture
async def admin_user_with_tag_create(db_session: AsyncSession) -> tuple[Users, str]:
    """Create admin user with TAG_CREATE permission, return user and password."""
    # Create TAG_CREATE permission
    perm = Perms(title="tag_create", desc="Create tags")
    db_session.add(perm)
    await db_session.commit()
    await db_session.refresh(perm)

    # Create admin user
    password = "AdminPassword123!"
    admin = Users(
        username="admincslink",
        password=get_password_hash(password),
        password_type="bcrypt",
        salt="",
        email="admincslink@example.com",
        active=1,
        admin=1,
    )
    db_session.add(admin)
    await db_session.commit()
    await db_session.refresh(admin)

    # Grant TAG_CREATE permission
    user_perm = UserPerms(user_id=admin.user_id, perm_id=perm.perm_id, permvalue=1)
    db_session.add(user_perm)
    await db_session.commit()

    return admin, password


@pytest.mark.api
class TestCreateCharacterSourceLink:
    """Tests for POST /api/v1/tags/character-source-links endpoint."""

    async def test_create_link_as_admin(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        character_tag: Tags,
        source_tag: Tags,
        admin_user_with_tag_create: tuple[Users, str],
    ):
        """Test creating a character-source link as admin."""
        admin, password = admin_user_with_tag_create

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": admin.username, "password": password},
        )
        access_token = login_response.json()["access_token"]

        # Create link
        response = await client.post(
            "/api/v1/tags/character-source-links",
            json={
                "character_tag_id": character_tag.tag_id,
                "source_tag_id": source_tag.tag_id,
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["character_tag_id"] == character_tag.tag_id
        assert data["source_tag_id"] == source_tag.tag_id
        assert "id" in data
        assert data["created_by_user_id"] == admin.user_id

    async def test_create_link_rejects_non_character_tag(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        source_tag: Tags,
        admin_user_with_tag_create: tuple[Users, str],
    ):
        """Test that creating link with non-character tag fails."""
        admin, password = admin_user_with_tag_create

        # Create a theme tag (not character)
        theme_tag = Tags(title="school uniform", type=TagType.THEME)
        db_session.add(theme_tag)
        await db_session.commit()
        await db_session.refresh(theme_tag)

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": admin.username, "password": password},
        )
        access_token = login_response.json()["access_token"]

        # Try to create link with theme tag as character
        response = await client.post(
            "/api/v1/tags/character-source-links",
            json={
                "character_tag_id": theme_tag.tag_id,
                "source_tag_id": source_tag.tag_id,
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 400
        assert "character" in response.json()["detail"].lower()

    async def test_create_link_rejects_non_source_tag(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        character_tag: Tags,
        admin_user_with_tag_create: tuple[Users, str],
    ):
        """Test that creating link with non-source tag fails."""
        admin, password = admin_user_with_tag_create

        # Create a theme tag (not source)
        theme_tag = Tags(title="maid outfit", type=TagType.THEME)
        db_session.add(theme_tag)
        await db_session.commit()
        await db_session.refresh(theme_tag)

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": admin.username, "password": password},
        )
        access_token = login_response.json()["access_token"]

        # Try to create link with theme tag as source
        response = await client.post(
            "/api/v1/tags/character-source-links",
            json={
                "character_tag_id": character_tag.tag_id,
                "source_tag_id": theme_tag.tag_id,
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 400
        assert "source" in response.json()["detail"].lower()

    async def test_create_duplicate_link_fails(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        character_tag: Tags,
        source_tag: Tags,
        admin_user_with_tag_create: tuple[Users, str],
    ):
        """Test that creating duplicate link returns 409."""
        admin, password = admin_user_with_tag_create

        # Create existing link
        link = CharacterSourceLinks(
            character_tag_id=character_tag.tag_id,
            source_tag_id=source_tag.tag_id,
        )
        db_session.add(link)
        await db_session.commit()

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": admin.username, "password": password},
        )
        access_token = login_response.json()["access_token"]

        # Try to create duplicate link
        response = await client.post(
            "/api/v1/tags/character-source-links",
            json={
                "character_tag_id": character_tag.tag_id,
                "source_tag_id": source_tag.tag_id,
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 409

    async def test_create_link_without_permission(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        character_tag: Tags,
        source_tag: Tags,
    ):
        """Test that users without permission cannot create links."""
        # Create regular user
        user = Users(
            username="regularcslink",
            password=get_password_hash("Password123!"),
            password_type="bcrypt",
            salt="",
            email="regularcslink@example.com",
            active=1,
            admin=0,
        )
        db_session.add(user)
        await db_session.commit()

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "regularcslink", "password": "Password123!"},
        )
        access_token = login_response.json()["access_token"]

        # Try to create link
        response = await client.post(
            "/api/v1/tags/character-source-links",
            json={
                "character_tag_id": character_tag.tag_id,
                "source_tag_id": source_tag.tag_id,
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 403


@pytest.mark.api
class TestListCharacterSourceLinks:
    """Tests for GET /api/v1/tags/character-source-links endpoint."""

    async def test_list_links(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        character_tag: Tags,
        source_tag: Tags,
    ):
        """Test listing character-source links."""
        # Create some links
        link = CharacterSourceLinks(
            character_tag_id=character_tag.tag_id,
            source_tag_id=source_tag.tag_id,
        )
        db_session.add(link)
        await db_session.commit()

        response = await client.get("/api/v1/tags/character-source-links")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert "links" in data

    async def test_filter_by_character_tag_id(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        character_tag: Tags,
        source_tag: Tags,
    ):
        """Test filtering links by character_tag_id."""
        # Create link
        link = CharacterSourceLinks(
            character_tag_id=character_tag.tag_id,
            source_tag_id=source_tag.tag_id,
        )
        db_session.add(link)
        await db_session.commit()

        response = await client.get(
            f"/api/v1/tags/character-source-links?character_tag_id={character_tag.tag_id}"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["links"][0]["character_tag_id"] == character_tag.tag_id

    async def test_filter_by_source_tag_id(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        character_tag: Tags,
        source_tag: Tags,
    ):
        """Test filtering links by source_tag_id."""
        # Create link
        link = CharacterSourceLinks(
            character_tag_id=character_tag.tag_id,
            source_tag_id=source_tag.tag_id,
        )
        db_session.add(link)
        await db_session.commit()

        response = await client.get(
            f"/api/v1/tags/character-source-links?source_tag_id={source_tag.tag_id}"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["links"][0]["source_tag_id"] == source_tag.tag_id


@pytest.mark.api
class TestDeleteCharacterSourceLink:
    """Tests for DELETE /api/v1/tags/character-source-links/{id} endpoint."""

    async def test_delete_link_as_admin(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        character_tag: Tags,
        source_tag: Tags,
        admin_user_with_tag_create: tuple[Users, str],
    ):
        """Test deleting a character-source link as admin."""
        admin, password = admin_user_with_tag_create

        # Create link
        link = CharacterSourceLinks(
            character_tag_id=character_tag.tag_id,
            source_tag_id=source_tag.tag_id,
        )
        db_session.add(link)
        await db_session.commit()
        await db_session.refresh(link)

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": admin.username, "password": password},
        )
        access_token = login_response.json()["access_token"]

        # Delete link
        response = await client.delete(
            f"/api/v1/tags/character-source-links/{link.id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 204

    async def test_delete_nonexistent_link(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        admin_user_with_tag_create: tuple[Users, str],
    ):
        """Test deleting non-existent link returns 404."""
        admin, password = admin_user_with_tag_create

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": admin.username, "password": password},
        )
        access_token = login_response.json()["access_token"]

        # Try to delete non-existent link
        response = await client.delete(
            "/api/v1/tags/character-source-links/999999",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 404

    async def test_delete_link_without_permission(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        character_tag: Tags,
        source_tag: Tags,
    ):
        """Test that users without permission cannot delete links."""
        # Create link
        link = CharacterSourceLinks(
            character_tag_id=character_tag.tag_id,
            source_tag_id=source_tag.tag_id,
        )
        db_session.add(link)
        await db_session.commit()
        await db_session.refresh(link)

        # Create regular user
        user = Users(
            username="regulardelcslink",
            password=get_password_hash("Password123!"),
            password_type="bcrypt",
            salt="",
            email="regulardelcslink@example.com",
            active=1,
            admin=0,
        )
        db_session.add(user)
        await db_session.commit()

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "regulardelcslink", "password": "Password123!"},
        )
        access_token = login_response.json()["access_token"]

        # Try to delete link
        response = await client.delete(
            f"/api/v1/tags/character-source-links/{link.id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 403
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_character_source_links.py -v`
Expected: Tests fail with 404 (endpoints don't exist yet)

**Step 3: Commit failing tests**

```bash
git add tests/api/v1/test_character_source_links.py
git commit -m "test: add failing tests for character-source links CRUD"
```

---

## Task 5: Implement CRUD Endpoints

**Files:**
- Modify: `app/api/v1/tags.py`

**Step 1: Add imports**

At the top of `app/api/v1/tags.py`, add:

```python
from app.models.character_source_link import CharacterSourceLinks
from app.schemas.tag import (
    # ... existing imports ...
    CharacterSourceLinkCreate,
    CharacterSourceLinkListResponse,
    CharacterSourceLinkResponse,
)
```

**Step 2: Add POST endpoint**

Add after the `delete_tag_link` endpoint:

```python
@router.post("/character-source-links", response_model=CharacterSourceLinkResponse, status_code=201)
async def create_character_source_link(
    link_data: CharacterSourceLinkCreate,
    current_user: Annotated[Users, Depends(get_current_user)],
    _: Annotated[None, Depends(require_permission(Permission.TAG_CREATE))],
    db: AsyncSession = Depends(get_db),
) -> CharacterSourceLinkResponse:
    """
    Create a character-source link.

    Links a character tag to a source tag. Requires TAG_CREATE permission.
    Returns 400 if character_tag_id is not a character tag or source_tag_id is not a source tag.
    Returns 409 if link already exists.
    """
    from app.config import TagType

    # Verify character tag exists and is type CHARACTER
    char_result = await db.execute(
        select(Tags).where(Tags.tag_id == link_data.character_tag_id)
    )
    char_tag = char_result.scalar_one_or_none()
    if not char_tag:
        raise HTTPException(status_code=404, detail="Character tag not found")
    if char_tag.type != TagType.CHARACTER:
        raise HTTPException(
            status_code=400,
            detail=f"character_tag_id must be a Character tag (type={TagType.CHARACTER}), got type={char_tag.type}",
        )

    # Verify source tag exists and is type SOURCE
    source_result = await db.execute(
        select(Tags).where(Tags.tag_id == link_data.source_tag_id)
    )
    source_tag = source_result.scalar_one_or_none()
    if not source_tag:
        raise HTTPException(status_code=404, detail="Source tag not found")
    if source_tag.type != TagType.SOURCE:
        raise HTTPException(
            status_code=400,
            detail=f"source_tag_id must be a Source tag (type={TagType.SOURCE}), got type={source_tag.type}",
        )

    # Create link
    new_link = CharacterSourceLinks(
        character_tag_id=link_data.character_tag_id,
        source_tag_id=link_data.source_tag_id,
        created_by_user_id=current_user.user_id,
    )
    db.add(new_link)

    try:
        await db.commit()
        await db.refresh(new_link)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Link between this character and source already exists",
        ) from None

    return CharacterSourceLinkResponse.model_validate(new_link)
```

**Step 3: Add GET (list) endpoint**

```python
@router.get("/character-source-links", response_model=CharacterSourceLinkListResponse)
async def list_character_source_links(
    pagination: Annotated[PaginationParams, Depends()],
    character_tag_id: Annotated[int | None, Query(description="Filter by character tag ID")] = None,
    source_tag_id: Annotated[int | None, Query(description="Filter by source tag ID")] = None,
    db: AsyncSession = Depends(get_db),
) -> CharacterSourceLinkListResponse:
    """
    List character-source links with optional filtering.

    Can filter by character_tag_id or source_tag_id to find all sources for a character
    or all characters for a source.
    """
    query = select(CharacterSourceLinks)

    if character_tag_id is not None:
        query = query.where(CharacterSourceLinks.character_tag_id == character_tag_id)
    if source_tag_id is not None:
        query = query.where(CharacterSourceLinks.source_tag_id == source_tag_id)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Paginate and order
    query = query.order_by(desc(CharacterSourceLinks.created_at)).offset(pagination.offset).limit(pagination.per_page)

    result = await db.execute(query)
    links = result.scalars().all()

    return CharacterSourceLinkListResponse(
        total=total or 0,
        page=pagination.page,
        per_page=pagination.per_page,
        links=[CharacterSourceLinkResponse.model_validate(link) for link in links],
    )
```

**Step 4: Add DELETE endpoint**

```python
@router.delete("/character-source-links/{link_id}", status_code=204)
async def delete_character_source_link(
    link_id: Annotated[int, Path(description="Link ID")],
    _: Annotated[None, Depends(require_permission(Permission.TAG_CREATE))],
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Delete a character-source link.

    Requires TAG_CREATE permission.
    Returns 404 if link doesn't exist.
    """
    result = await db.execute(
        select(CharacterSourceLinks).where(CharacterSourceLinks.id == link_id)
    )
    link = result.scalar_one_or_none()

    if not link:
        raise HTTPException(status_code=404, detail="Link not found")

    await db.delete(link)
    await db.commit()
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_character_source_links.py -v`
Expected: All tests pass

**Step 6: Run full test suite to check for regressions**

Run: `uv run pytest -x`
Expected: All tests pass

**Step 7: Commit**

```bash
git add app/api/v1/tags.py
git commit -m "feat: implement character-source links CRUD endpoints"
```

---

## Task 6: Write Tests for Extended Tag Responses

**Files:**
- Modify: `tests/api/v1/test_character_source_links.py`

**Step 1: Add tests for GET /tags/{tag_id} including sources/characters**

Add new test class:

```python
@pytest.mark.api
class TestTagResponseWithLinks:
    """Tests for GET /api/v1/tags/{tag_id} including linked sources/characters."""

    async def test_character_tag_includes_sources(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        character_tag: Tags,
        source_tag: Tags,
    ):
        """Test that character tag response includes linked sources."""
        # Create link
        link = CharacterSourceLinks(
            character_tag_id=character_tag.tag_id,
            source_tag_id=source_tag.tag_id,
        )
        db_session.add(link)
        await db_session.commit()

        # Get character tag
        response = await client.get(f"/api/v1/tags/{character_tag.tag_id}")
        assert response.status_code == 200
        data = response.json()
        assert "sources" in data
        assert len(data["sources"]) == 1
        assert data["sources"][0]["tag_id"] == source_tag.tag_id
        assert data["sources"][0]["title"] == "Touhou"

    async def test_source_tag_includes_characters(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        character_tag: Tags,
        source_tag: Tags,
    ):
        """Test that source tag response includes linked characters."""
        # Create link
        link = CharacterSourceLinks(
            character_tag_id=character_tag.tag_id,
            source_tag_id=source_tag.tag_id,
        )
        db_session.add(link)
        await db_session.commit()

        # Get source tag
        response = await client.get(f"/api/v1/tags/{source_tag.tag_id}")
        assert response.status_code == 200
        data = response.json()
        assert "characters" in data
        assert len(data["characters"]) == 1
        assert data["characters"][0]["tag_id"] == character_tag.tag_id
        assert data["characters"][0]["title"] == "Hakurei Reimu"

    async def test_character_with_multiple_sources(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        character_tag: Tags,
        source_tag: Tags,
    ):
        """Test character with multiple source links."""
        # Create second source
        source2 = Tags(title="Touhou: Lost Word", type=TagType.SOURCE)
        db_session.add(source2)
        await db_session.commit()
        await db_session.refresh(source2)

        # Create links
        link1 = CharacterSourceLinks(
            character_tag_id=character_tag.tag_id,
            source_tag_id=source_tag.tag_id,
        )
        link2 = CharacterSourceLinks(
            character_tag_id=character_tag.tag_id,
            source_tag_id=source2.tag_id,
        )
        db_session.add_all([link1, link2])
        await db_session.commit()

        # Get character tag
        response = await client.get(f"/api/v1/tags/{character_tag.tag_id}")
        assert response.status_code == 200
        data = response.json()
        assert len(data["sources"]) == 2

    async def test_tag_without_links_has_empty_arrays(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ):
        """Test that tags without links have empty sources/characters arrays."""
        # Create character tag with no links
        tag = Tags(title="Lonely Character", type=TagType.CHARACTER)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        response = await client.get(f"/api/v1/tags/{tag.tag_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["sources"] == []

        # Create source tag with no links
        source = Tags(title="Lonely Source", type=TagType.SOURCE)
        db_session.add(source)
        await db_session.commit()
        await db_session.refresh(source)

        response = await client.get(f"/api/v1/tags/{source.tag_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["characters"] == []
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_character_source_links.py::TestTagResponseWithLinks -v`
Expected: Tests fail (sources/characters fields not yet added)

**Step 3: Commit**

```bash
git add tests/api/v1/test_character_source_links.py
git commit -m "test: add failing tests for tag response with linked sources/characters"
```

---

## Task 7: Extend Tag Response with Linked Sources/Characters

**Files:**
- Modify: `app/schemas/tag.py`
- Modify: `app/api/v1/tags.py`

**Step 1: Update TagWithStats schema**

In `app/schemas/tag.py`, modify `TagWithStats`:

```python
class TagWithStats(TagResponse):
    """Schema for tag response with usage statistics"""

    image_count: int
    is_alias: bool = False
    aliased_tag_id: int | None = None
    parent_tag_id: int | None = None
    child_count: int = 0
    created_by: TagCreator | None = None
    date_added: datetime
    links: list[str] = []
    # Character-source links
    sources: list[LinkedTag] = []  # For character tags: linked sources
    characters: list[LinkedTag] = []  # For source tags: linked characters
```

**Step 2: Update get_tag endpoint**

In `app/api/v1/tags.py`, modify the `get_tag` endpoint to fetch linked sources/characters:

After fetching external links, add:

```python
    # Fetch linked sources/characters based on tag type
    sources: list[dict] = []
    characters: list[dict] = []

    if tag.type == TagType.CHARACTER:
        # Get all sources linked to this character
        sources_result = await db.execute(
            select(Tags.tag_id, Tags.title)
            .join(
                CharacterSourceLinks,
                Tags.tag_id == CharacterSourceLinks.source_tag_id,
            )
            .where(CharacterSourceLinks.character_tag_id == tag_id)
            .order_by(Tags.title)
        )
        sources = [{"tag_id": row[0], "title": row[1]} for row in sources_result.all()]

    elif tag.type == TagType.SOURCE:
        # Get all characters linked to this source
        characters_result = await db.execute(
            select(Tags.tag_id, Tags.title)
            .join(
                CharacterSourceLinks,
                Tags.tag_id == CharacterSourceLinks.character_tag_id,
            )
            .where(CharacterSourceLinks.source_tag_id == tag_id)
            .order_by(Tags.title)
        )
        characters = [{"tag_id": row[0], "title": row[1]} for row in characters_result.all()]
```

Then update the return statement to include `sources=sources, characters=characters`.

**Step 3: Run tests**

Run: `uv run pytest tests/api/v1/test_character_source_links.py::TestTagResponseWithLinks -v`
Expected: All tests pass

**Step 4: Run full test suite**

Run: `uv run pytest -x`
Expected: All tests pass

**Step 5: Commit**

```bash
git add app/schemas/tag.py app/api/v1/tags.py
git commit -m "feat: include linked sources/characters in tag responses"
```

---

## Task 8: Write Tests for Discovery Endpoint

**Files:**
- Modify: `tests/api/v1/test_character_source_links.py`

**Step 1: Add test class for discovery endpoint**

```python
@pytest.mark.api
class TestSourceCharactersEndpoint:
    """Tests for GET /api/v1/tags/{source_tag_id}/characters endpoint."""

    async def test_get_characters_for_source(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        character_tag: Tags,
        source_tag: Tags,
    ):
        """Test getting all characters for a source."""
        # Create second character
        char2 = Tags(title="Kirisame Marisa", type=TagType.CHARACTER)
        db_session.add(char2)
        await db_session.commit()
        await db_session.refresh(char2)

        # Create links
        link1 = CharacterSourceLinks(
            character_tag_id=character_tag.tag_id,
            source_tag_id=source_tag.tag_id,
        )
        link2 = CharacterSourceLinks(
            character_tag_id=char2.tag_id,
            source_tag_id=source_tag.tag_id,
        )
        db_session.add_all([link1, link2])
        await db_session.commit()

        response = await client.get(f"/api/v1/tags/{source_tag.tag_id}/characters")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        titles = {tag["title"] for tag in data["tags"]}
        assert "Hakurei Reimu" in titles
        assert "Kirisame Marisa" in titles

    async def test_get_characters_for_nonexistent_source(
        self,
        client: AsyncClient,
    ):
        """Test getting characters for non-existent source returns 404."""
        response = await client.get("/api/v1/tags/999999/characters")
        assert response.status_code == 404

    async def test_get_characters_for_non_source_tag(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        character_tag: Tags,
    ):
        """Test getting characters for non-source tag returns 400."""
        response = await client.get(f"/api/v1/tags/{character_tag.tag_id}/characters")
        assert response.status_code == 400

    async def test_get_characters_for_source_with_no_characters(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        source_tag: Tags,
    ):
        """Test getting characters for source with no links returns empty list."""
        response = await client.get(f"/api/v1/tags/{source_tag.tag_id}/characters")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["tags"] == []
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_character_source_links.py::TestSourceCharactersEndpoint -v`
Expected: Tests fail (endpoint doesn't exist)

**Step 3: Commit**

```bash
git add tests/api/v1/test_character_source_links.py
git commit -m "test: add failing tests for source characters discovery endpoint"
```

---

## Task 9: Implement Discovery Endpoint

**Files:**
- Modify: `app/api/v1/tags.py`

**Step 1: Add discovery endpoint**

Add before the character-source-links endpoints:

```python
@router.get("/{tag_id}/characters", response_model=TagListResponse)
async def get_characters_for_source(
    tag_id: Annotated[int, Path(description="Source tag ID")],
    pagination: Annotated[PaginationParams, Depends()],
    db: AsyncSession = Depends(get_db),
) -> TagListResponse:
    """
    Get all character tags linked to a source tag.

    Returns a paginated list of character tags that are linked to the specified source.
    Returns 400 if the tag is not a Source type.
    Returns 404 if the tag doesn't exist.
    """
    from app.config import TagType

    # Verify tag exists and is a source
    tag_result = await db.execute(select(Tags).where(Tags.tag_id == tag_id))
    tag = tag_result.scalar_one_or_none()

    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    if tag.type != TagType.SOURCE:
        raise HTTPException(
            status_code=400,
            detail=f"Tag must be a Source tag (type={TagType.SOURCE}), got type={tag.type}",
        )

    # Get linked characters
    query = (
        select(Tags)
        .join(
            CharacterSourceLinks,
            Tags.tag_id == CharacterSourceLinks.character_tag_id,
        )
        .where(CharacterSourceLinks.source_tag_id == tag_id)
    )

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Paginate and order
    query = query.order_by(Tags.title).offset(pagination.offset).limit(pagination.per_page)

    result = await db.execute(query)
    tags = result.scalars().all()

    return TagListResponse(
        total=total or 0,
        page=pagination.page,
        per_page=pagination.per_page,
        tags=[TagResponse.model_validate(t) for t in tags],
    )
```

**Step 2: Run tests**

Run: `uv run pytest tests/api/v1/test_character_source_links.py::TestSourceCharactersEndpoint -v`
Expected: All tests pass

**Step 3: Run full test suite**

Run: `uv run pytest -x`
Expected: All tests pass

**Step 4: Commit**

```bash
git add app/api/v1/tags.py
git commit -m "feat: add GET /tags/{tag_id}/characters discovery endpoint"
```

---

## Task 10: Write Cascade Deletion Test

**Files:**
- Modify: `tests/api/v1/test_character_source_links.py`

**Step 1: Add cascade deletion test**

```python
@pytest.mark.api
class TestCharacterSourceLinkCascade:
    """Tests for cascade deletion of character-source links."""

    async def test_link_deleted_when_character_tag_deleted(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        character_tag: Tags,
        source_tag: Tags,
        admin_user_with_tag_create: tuple[Users, str],
    ):
        """Test that links are deleted when character tag is deleted."""
        admin, password = admin_user_with_tag_create

        # Grant TAG_DELETE permission
        perm = Perms(title="tag_delete", desc="Delete tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        user_perm = UserPerms(user_id=admin.user_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(user_perm)
        await db_session.commit()

        # Create link
        link = CharacterSourceLinks(
            character_tag_id=character_tag.tag_id,
            source_tag_id=source_tag.tag_id,
        )
        db_session.add(link)
        await db_session.commit()
        await db_session.refresh(link)
        link_id = link.id

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": admin.username, "password": password},
        )
        access_token = login_response.json()["access_token"]

        # Delete character tag
        response = await client.delete(
            f"/api/v1/tags/{character_tag.tag_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 204

        # Verify link was also deleted
        from sqlalchemy import select
        result = await db_session.execute(
            select(CharacterSourceLinks).where(CharacterSourceLinks.id == link_id)
        )
        assert result.scalar_one_or_none() is None

    async def test_link_deleted_when_source_tag_deleted(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        character_tag: Tags,
        source_tag: Tags,
        admin_user_with_tag_create: tuple[Users, str],
    ):
        """Test that links are deleted when source tag is deleted."""
        admin, password = admin_user_with_tag_create

        # Grant TAG_DELETE permission
        perm = Perms(title="tag_delete", desc="Delete tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        user_perm = UserPerms(user_id=admin.user_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(user_perm)
        await db_session.commit()

        # Create link
        link = CharacterSourceLinks(
            character_tag_id=character_tag.tag_id,
            source_tag_id=source_tag.tag_id,
        )
        db_session.add(link)
        await db_session.commit()
        await db_session.refresh(link)
        link_id = link.id

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": admin.username, "password": password},
        )
        access_token = login_response.json()["access_token"]

        # Delete source tag
        response = await client.delete(
            f"/api/v1/tags/{source_tag.tag_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 204

        # Verify link was also deleted
        from sqlalchemy import select
        result = await db_session.execute(
            select(CharacterSourceLinks).where(CharacterSourceLinks.id == link_id)
        )
        assert result.scalar_one_or_none() is None
```

**Step 2: Run tests**

Run: `uv run pytest tests/api/v1/test_character_source_links.py::TestCharacterSourceLinkCascade -v`
Expected: Tests pass (cascade is defined in migration)

**Step 3: Commit**

```bash
git add tests/api/v1/test_character_source_links.py
git commit -m "test: add cascade deletion tests for character-source links"
```

---

## Task 11: Create Batch Analysis Script

**Files:**
- Create: `scripts/analyze_character_sources.py`

**Step 1: Write the analysis script**

```python
#!/usr/bin/env python3
"""
Analyze co-occurrence patterns between character and source tags.

This script identifies likely character-source relationships by analyzing
which source tags frequently co-occur with character tags on the same images.

Usage:
    uv run python scripts/analyze_character_sources.py [--threshold 0.8] [--min-images 5] [--output results.csv]
"""

import argparse
import asyncio
import csv
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import TagType, settings
from app.models.tag import Tags
from app.models.tag_link import TagLinks


async def analyze_character_sources(
    threshold: float = 0.8,
    min_images: int = 5,
    output_file: str | None = None,
) -> list[dict]:
    """
    Analyze co-occurrence patterns between character and source tags.

    Args:
        threshold: Minimum co-occurrence percentage (0.0-1.0) to flag as likely link
        min_images: Minimum number of images a character must have
        output_file: Optional CSV file to write results

    Returns:
        List of candidate links with statistics
    """
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    results = []

    async with async_session() as db:
        # Get all character tags with usage >= min_images
        char_result = await db.execute(
            select(Tags.tag_id, Tags.title)
            .where(Tags.type == TagType.CHARACTER)
            .where(Tags.usage_count >= min_images)
            .order_by(Tags.title)
        )
        character_tags = char_result.all()

        print(f"Found {len(character_tags)} character tags with >= {min_images} images")

        for char_id, char_title in character_tags:
            # Get all images with this character
            images_result = await db.execute(
                select(TagLinks.image_id)
                .where(TagLinks.tag_id == char_id)
            )
            image_ids = [row[0] for row in images_result.all()]
            total_images = len(image_ids)

            if total_images < min_images:
                continue

            # Count source tags that co-occur
            source_counts_result = await db.execute(
                select(Tags.tag_id, Tags.title, func.count(TagLinks.image_id).label("count"))
                .join(TagLinks, Tags.tag_id == TagLinks.tag_id)
                .where(Tags.type == TagType.SOURCE)
                .where(TagLinks.image_id.in_(image_ids))
                .group_by(Tags.tag_id, Tags.title)
                .order_by(func.count(TagLinks.image_id).desc())
            )

            for source_id, source_title, count in source_counts_result.all():
                percentage = count / total_images
                if percentage >= threshold:
                    results.append({
                        "character_tag_id": char_id,
                        "character_title": char_title,
                        "source_tag_id": source_id,
                        "source_title": source_title,
                        "co_occurrence_count": count,
                        "total_character_images": total_images,
                        "percentage": round(percentage * 100, 1),
                    })

    await engine.dispose()

    # Sort by percentage descending, then by character name
    results.sort(key=lambda x: (-x["percentage"], x["character_title"]))

    # Print results
    print(f"\nFound {len(results)} candidate links (>= {threshold * 100}% co-occurrence):\n")
    for r in results[:50]:  # Show first 50
        print(
            f"  {r['character_title']} → {r['source_title']}: "
            f"{r['co_occurrence_count']}/{r['total_character_images']} ({r['percentage']}%)"
        )
    if len(results) > 50:
        print(f"  ... and {len(results) - 50} more")

    # Write to CSV if requested
    if output_file:
        with open(output_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys() if results else [])
            writer.writeheader()
            writer.writerows(results)
        print(f"\nResults written to {output_file}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Analyze character-source co-occurrence")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.8,
        help="Minimum co-occurrence percentage (0.0-1.0, default: 0.8)",
    )
    parser.add_argument(
        "--min-images",
        type=int,
        default=5,
        help="Minimum images for a character to be considered (default: 5)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output CSV file path",
    )
    args = parser.parse_args()

    asyncio.run(
        analyze_character_sources(
            threshold=args.threshold,
            min_images=args.min_images,
            output_file=args.output,
        )
    )


if __name__ == "__main__":
    main()
```

**Step 2: Make executable**

Run: `chmod +x scripts/analyze_character_sources.py`

**Step 3: Test the script runs**

Run: `uv run python scripts/analyze_character_sources.py --help`
Expected: Help message displayed

**Step 4: Commit**

```bash
git add scripts/analyze_character_sources.py
git commit -m "feat: add batch analysis script for character-source links"
```

---

## Task 12: Final Verification and Cleanup

**Step 1: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests pass

**Step 2: Run type checker**

Run: `uv run mypy app/`
Expected: No errors

**Step 3: Run linter**

Run: `uv run ruff check app/ tests/`
Expected: No errors (or fix any that appear)

**Step 4: Test API manually**

```bash
# List links (should be empty initially)
curl -s http://localhost:8000/api/v1/tags/character-source-links | jq

# Create a test link (requires auth - use httpie or similar with auth header)
# Then verify it appears in tag responses
```

**Step 5: Final commit**

```bash
git add -A
git commit -m "chore: final cleanup for character-source links feature"
```

---

Plan complete and saved to `docs/plans/2026-01-07-character-source-links-impl.md`.

**Two execution options:**

**1. Subagent-Driven (this session)** - I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** - Open new session with executing-plans, batch execution with checkpoints

**Which approach?**
