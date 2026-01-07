"""
Tests for character-source links API endpoints.

These tests cover the /api/v1/character-source-links endpoints including:
- Create character-source link (admin only)
- List character-source links
- Delete character-source link (admin only)

Uses TDD approach - these tests are written before the endpoints are implemented.
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


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
async def character_tag(db_session: AsyncSession) -> Tags:
    """Create a tag with type=CHARACTER (type=4)."""
    tag = Tags(
        title="Hakurei Reimu",
        desc="Shrine maiden from Touhou",
        type=TagType.CHARACTER,
    )
    db_session.add(tag)
    await db_session.commit()
    await db_session.refresh(tag)
    return tag


@pytest.fixture
async def source_tag(db_session: AsyncSession) -> Tags:
    """Create a tag with type=SOURCE (type=2)."""
    tag = Tags(
        title="Touhou Project",
        desc="Bullet hell game series",
        type=TagType.SOURCE,
    )
    db_session.add(tag)
    await db_session.commit()
    await db_session.refresh(tag)
    return tag


@pytest.fixture
async def theme_tag(db_session: AsyncSession) -> Tags:
    """Create a tag with type=THEME (type=1) - for testing validation."""
    tag = Tags(
        title="Miko",
        desc="Shrine maiden theme",
        type=TagType.THEME,
    )
    db_session.add(tag)
    await db_session.commit()
    await db_session.refresh(tag)
    return tag


@pytest.fixture
async def tag_create_permission(db_session: AsyncSession) -> Perms:
    """Create the TAG_CREATE permission."""
    perm = Perms(title="tag_create", desc="Create tags and tag links")
    db_session.add(perm)
    await db_session.commit()
    await db_session.refresh(perm)
    return perm


@pytest.fixture
async def admin_user_with_tag_create(
    db_session: AsyncSession, tag_create_permission: Perms
) -> Users:
    """Create an admin user with TAG_CREATE permission."""
    admin = Users(
        username="cslink_admin",
        password=get_password_hash("AdminPassword123!"),
        password_type="bcrypt",
        salt="",
        email="cslink_admin@example.com",
        active=1,
        admin=1,
    )
    db_session.add(admin)
    await db_session.commit()
    await db_session.refresh(admin)

    # Grant TAG_CREATE permission
    user_perm = UserPerms(
        user_id=admin.user_id,
        perm_id=tag_create_permission.perm_id,
        permvalue=1,
    )
    db_session.add(user_perm)
    await db_session.commit()

    return admin


@pytest.fixture
async def regular_user(db_session: AsyncSession) -> Users:
    """Create a regular user without special permissions."""
    user = Users(
        username="cslink_regular",
        password=get_password_hash("Password123!"),
        password_type="bcrypt",
        salt="",
        email="cslink_regular@example.com",
        active=1,
        admin=0,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def login_user(client: AsyncClient, username: str, password: str) -> str:
    """Helper to login and return access token."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, f"Login failed: {response.text}"
    return response.json()["access_token"]


# =============================================================================
# Test Classes
# =============================================================================


@pytest.mark.api
class TestCreateCharacterSourceLink:
    """Tests for POST /api/v1/character-source-links endpoint."""

    async def test_create_link_as_admin(
        self,
        client: AsyncClient,
        admin_user_with_tag_create: Users,
        character_tag: Tags,
        source_tag: Tags,
    ):
        """Test creating a character-source link as admin with TAG_CREATE permission."""
        access_token = await login_user(client, "cslink_admin", "AdminPassword123!")

        response = await client.post(
            "/api/v1/character-source-links",
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
        assert "created_at" in data
        assert data["created_by_user_id"] == admin_user_with_tag_create.user_id

    async def test_create_link_rejects_non_character_tag(
        self,
        client: AsyncClient,
        admin_user_with_tag_create: Users,
        theme_tag: Tags,
        source_tag: Tags,
    ):
        """Test that creating a link with a non-CHARACTER tag as character_tag_id fails."""
        access_token = await login_user(client, "cslink_admin", "AdminPassword123!")

        response = await client.post(
            "/api/v1/character-source-links",
            json={
                "character_tag_id": theme_tag.tag_id,  # THEME type, not CHARACTER
                "source_tag_id": source_tag.tag_id,
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 400
        data = response.json()
        assert "character" in data["detail"].lower()

    async def test_create_link_rejects_non_source_tag(
        self,
        client: AsyncClient,
        admin_user_with_tag_create: Users,
        character_tag: Tags,
        theme_tag: Tags,
    ):
        """Test that creating a link with a non-SOURCE tag as source_tag_id fails."""
        access_token = await login_user(client, "cslink_admin", "AdminPassword123!")

        response = await client.post(
            "/api/v1/character-source-links",
            json={
                "character_tag_id": character_tag.tag_id,
                "source_tag_id": theme_tag.tag_id,  # THEME type, not SOURCE
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 400
        data = response.json()
        assert "source" in data["detail"].lower()

    async def test_create_duplicate_link_fails(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        admin_user_with_tag_create: Users,
        character_tag: Tags,
        source_tag: Tags,
    ):
        """Test that creating a duplicate character-source link returns 409."""
        # Create the link directly in the database first
        existing_link = CharacterSourceLinks(
            character_tag_id=character_tag.tag_id,
            source_tag_id=source_tag.tag_id,
            created_by_user_id=admin_user_with_tag_create.user_id,
        )
        db_session.add(existing_link)
        await db_session.commit()

        access_token = await login_user(client, "cslink_admin", "AdminPassword123!")

        response = await client.post(
            "/api/v1/character-source-links",
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
        regular_user: Users,
        character_tag: Tags,
        source_tag: Tags,
    ):
        """Test that users without TAG_CREATE permission cannot create links."""
        access_token = await login_user(client, "cslink_regular", "Password123!")

        response = await client.post(
            "/api/v1/character-source-links",
            json={
                "character_tag_id": character_tag.tag_id,
                "source_tag_id": source_tag.tag_id,
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 403

    async def test_create_link_unauthenticated(
        self,
        client: AsyncClient,
        character_tag: Tags,
        source_tag: Tags,
    ):
        """Test that unauthenticated requests cannot create links."""
        response = await client.post(
            "/api/v1/character-source-links",
            json={
                "character_tag_id": character_tag.tag_id,
                "source_tag_id": source_tag.tag_id,
            },
        )

        assert response.status_code == 401

    async def test_create_link_nonexistent_character_tag(
        self,
        client: AsyncClient,
        admin_user_with_tag_create: Users,
        source_tag: Tags,
    ):
        """Test that creating a link with nonexistent character tag returns 404."""
        access_token = await login_user(client, "cslink_admin", "AdminPassword123!")

        response = await client.post(
            "/api/v1/character-source-links",
            json={
                "character_tag_id": 999999,
                "source_tag_id": source_tag.tag_id,
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 404

    async def test_create_link_nonexistent_source_tag(
        self,
        client: AsyncClient,
        admin_user_with_tag_create: Users,
        character_tag: Tags,
    ):
        """Test that creating a link with nonexistent source tag returns 404."""
        access_token = await login_user(client, "cslink_admin", "AdminPassword123!")

        response = await client.post(
            "/api/v1/character-source-links",
            json={
                "character_tag_id": character_tag.tag_id,
                "source_tag_id": 999999,
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 404


@pytest.mark.api
class TestListCharacterSourceLinks:
    """Tests for GET /api/v1/character-source-links endpoint."""

    async def test_list_links(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        character_tag: Tags,
        source_tag: Tags,
    ):
        """Test listing character-source links with pagination."""
        # Create some links
        link = CharacterSourceLinks(
            character_tag_id=character_tag.tag_id,
            source_tag_id=source_tag.tag_id,
        )
        db_session.add(link)
        await db_session.commit()

        response = await client.get("/api/v1/character-source-links")

        assert response.status_code == 200
        data = response.json()
        assert "total" in data
        assert "page" in data
        assert "per_page" in data
        assert "links" in data
        assert data["total"] >= 1
        assert len(data["links"]) >= 1

    async def test_filter_by_character_tag_id(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ):
        """Test filtering links by character_tag_id."""
        # Create two character tags
        char1 = Tags(title="Character 1", type=TagType.CHARACTER)
        char2 = Tags(title="Character 2", type=TagType.CHARACTER)
        source = Tags(title="Source", type=TagType.SOURCE)
        db_session.add_all([char1, char2, source])
        await db_session.commit()
        await db_session.refresh(char1)
        await db_session.refresh(char2)
        await db_session.refresh(source)

        # Create links
        link1 = CharacterSourceLinks(
            character_tag_id=char1.tag_id,
            source_tag_id=source.tag_id,
        )
        link2 = CharacterSourceLinks(
            character_tag_id=char2.tag_id,
            source_tag_id=source.tag_id,
        )
        db_session.add_all([link1, link2])
        await db_session.commit()

        # Filter by char1
        response = await client.get(
            f"/api/v1/character-source-links?character_tag_id={char1.tag_id}"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["links"][0]["character_tag_id"] == char1.tag_id

    async def test_filter_by_source_tag_id(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ):
        """Test filtering links by source_tag_id."""
        # Create tags
        char = Tags(title="Character", type=TagType.CHARACTER)
        source1 = Tags(title="Source 1", type=TagType.SOURCE)
        source2 = Tags(title="Source 2", type=TagType.SOURCE)
        db_session.add_all([char, source1, source2])
        await db_session.commit()
        await db_session.refresh(char)
        await db_session.refresh(source1)
        await db_session.refresh(source2)

        # Create links
        link1 = CharacterSourceLinks(
            character_tag_id=char.tag_id,
            source_tag_id=source1.tag_id,
        )
        link2 = CharacterSourceLinks(
            character_tag_id=char.tag_id,
            source_tag_id=source2.tag_id,
        )
        db_session.add_all([link1, link2])
        await db_session.commit()

        # Filter by source1
        response = await client.get(
            f"/api/v1/character-source-links?source_tag_id={source1.tag_id}"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["links"][0]["source_tag_id"] == source1.tag_id

    async def test_list_links_pagination(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ):
        """Test that pagination works correctly."""
        # Create multiple character-source pairs
        source = Tags(title="Test Source", type=TagType.SOURCE)
        db_session.add(source)
        await db_session.commit()
        await db_session.refresh(source)

        for i in range(5):
            char = Tags(title=f"Character {i}", type=TagType.CHARACTER)
            db_session.add(char)
            await db_session.commit()
            await db_session.refresh(char)

            link = CharacterSourceLinks(
                character_tag_id=char.tag_id,
                source_tag_id=source.tag_id,
            )
            db_session.add(link)

        await db_session.commit()

        # Request with pagination
        response = await client.get("/api/v1/character-source-links?page=1&per_page=2")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert data["page"] == 1
        assert data["per_page"] == 2
        assert len(data["links"]) == 2

    async def test_list_links_empty(
        self,
        client: AsyncClient,
    ):
        """Test listing links when none exist."""
        response = await client.get("/api/v1/character-source-links")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["links"] == []


@pytest.mark.api
class TestDeleteCharacterSourceLink:
    """Tests for DELETE /api/v1/character-source-links/{link_id} endpoint."""

    async def test_delete_link_as_admin(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        admin_user_with_tag_create: Users,
        character_tag: Tags,
        source_tag: Tags,
    ):
        """Test deleting a character-source link as admin."""
        # Create a link
        link = CharacterSourceLinks(
            character_tag_id=character_tag.tag_id,
            source_tag_id=source_tag.tag_id,
        )
        db_session.add(link)
        await db_session.commit()
        await db_session.refresh(link)

        access_token = await login_user(client, "cslink_admin", "AdminPassword123!")

        response = await client.delete(
            f"/api/v1/character-source-links/{link.id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 204

        # Verify link is deleted
        get_response = await client.get(
            f"/api/v1/character-source-links?character_tag_id={character_tag.tag_id}"
        )
        assert get_response.json()["total"] == 0

    async def test_delete_nonexistent_link(
        self,
        client: AsyncClient,
        admin_user_with_tag_create: Users,
    ):
        """Test deleting a non-existent link returns 404."""
        access_token = await login_user(client, "cslink_admin", "AdminPassword123!")

        response = await client.delete(
            "/api/v1/character-source-links/999999",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 404

    async def test_delete_link_without_permission(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        regular_user: Users,
        character_tag: Tags,
        source_tag: Tags,
    ):
        """Test that users without TAG_CREATE permission cannot delete links."""
        # Create a link
        link = CharacterSourceLinks(
            character_tag_id=character_tag.tag_id,
            source_tag_id=source_tag.tag_id,
        )
        db_session.add(link)
        await db_session.commit()
        await db_session.refresh(link)

        access_token = await login_user(client, "cslink_regular", "Password123!")

        response = await client.delete(
            f"/api/v1/character-source-links/{link.id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 403

    async def test_delete_link_unauthenticated(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        character_tag: Tags,
        source_tag: Tags,
    ):
        """Test that unauthenticated requests cannot delete links."""
        # Create a link
        link = CharacterSourceLinks(
            character_tag_id=character_tag.tag_id,
            source_tag_id=source_tag.tag_id,
        )
        db_session.add(link)
        await db_session.commit()
        await db_session.refresh(link)

        response = await client.delete(
            f"/api/v1/character-source-links/{link.id}",
        )

        assert response.status_code == 401


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
        assert data["sources"][0]["title"] == source_tag.title

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
        assert data["characters"][0]["title"] == character_tag.title

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
        char_tag = Tags(title="Lonely Character", type=TagType.CHARACTER)
        db_session.add(char_tag)
        await db_session.commit()
        await db_session.refresh(char_tag)

        response = await client.get(f"/api/v1/tags/{char_tag.tag_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["sources"] == []

        # Create source tag with no links
        src_tag = Tags(title="Lonely Source", type=TagType.SOURCE)
        db_session.add(src_tag)
        await db_session.commit()
        await db_session.refresh(src_tag)

        response = await client.get(f"/api/v1/tags/{src_tag.tag_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["characters"] == []
