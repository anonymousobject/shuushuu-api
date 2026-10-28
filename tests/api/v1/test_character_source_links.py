"""
Tests for character-source links API endpoints.

These tests cover the /api/v1/character-source-links endpoints including:
- Create character-source link (admin only)
- List character-source links
- Delete character-source link (admin only)

Uses TDD approach - these tests are written before the endpoints are implemented.
"""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, TagType
from app.core.security import get_password_hash
from app.models.character_source_link import CharacterSourceLinks
from app.models.image import Images
from app.models.permissions import Perms, UserPerms
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.user import Users
from app.services.character_source_counts import _cache_key

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

    async def test_create_link_rejects_alias_character_tag(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        admin_user_with_tag_create: Users,
        character_tag: Tags,
        source_tag: Tags,
    ):
        """Test that alias character tags cannot be used in character-source links."""
        # Create an alias character tag pointing to the canonical one
        alias_char = Tags(
            title="Reimu Hakurei",
            type=TagType.CHARACTER,
            alias_of=character_tag.tag_id,
        )
        db_session.add(alias_char)
        await db_session.commit()
        await db_session.refresh(alias_char)

        access_token = await login_user(client, "cslink_admin", "AdminPassword123!")

        response = await client.post(
            "/api/v1/character-source-links",
            json={
                "character_tag_id": alias_char.tag_id,
                "source_tag_id": source_tag.tag_id,
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "alias" in detail.lower()
        assert "Reimu Hakurei" in detail
        assert "Hakurei Reimu" in detail

    async def test_create_link_rejects_alias_source_tag(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        admin_user_with_tag_create: Users,
        character_tag: Tags,
        source_tag: Tags,
    ):
        """Test that alias source tags cannot be used in character-source links."""
        # Create an alias source tag pointing to the canonical one
        alias_source = Tags(
            title="Touhou",
            type=TagType.SOURCE,
            alias_of=source_tag.tag_id,
        )
        db_session.add(alias_source)
        await db_session.commit()
        await db_session.refresh(alias_source)

        access_token = await login_user(client, "cslink_admin", "AdminPassword123!")

        response = await client.post(
            "/api/v1/character-source-links",
            json={
                "character_tag_id": character_tag.tag_id,
                "source_tag_id": alias_source.tag_id,
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "alias" in detail.lower()
        assert "Touhou" in detail
        assert "Touhou Project" in detail


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
        assert character_tag.title in titles
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


@pytest.mark.api
class TestCharacterSourceLinkCascade:
    """Tests for cascade deletion of character-source links."""

    async def test_link_deleted_when_character_tag_deleted(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        character_tag: Tags,
        source_tag: Tags,
        admin_user_with_tag_create: Users,
        tag_create_permission: Perms,
    ):
        """Test that links are deleted when character tag is deleted."""
        # Grant TAG_DELETE permission to the admin user
        tag_delete_perm = Perms(title="tag_delete", desc="Delete tags")
        db_session.add(tag_delete_perm)
        await db_session.commit()
        await db_session.refresh(tag_delete_perm)

        user_perm = UserPerms(
            user_id=admin_user_with_tag_create.user_id,
            perm_id=tag_delete_perm.perm_id,
            permvalue=1,
        )
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

        # Login and delete character tag
        access_token = await login_user(client, "cslink_admin", "AdminPassword123!")
        response = await client.delete(
            f"/api/v1/tags/{character_tag.tag_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 204

        # Verify link was also deleted (cascade)
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
        admin_user_with_tag_create: Users,
        tag_create_permission: Perms,
    ):
        """Test that links are deleted when source tag is deleted."""
        # Grant TAG_DELETE permission to the admin user
        tag_delete_perm = Perms(title="tag_delete", desc="Delete tags")
        db_session.add(tag_delete_perm)
        await db_session.commit()
        await db_session.refresh(tag_delete_perm)

        user_perm = UserPerms(
            user_id=admin_user_with_tag_create.user_id,
            perm_id=tag_delete_perm.perm_id,
            permvalue=1,
        )
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

        # Login and delete source tag
        access_token = await login_user(client, "cslink_admin", "AdminPassword123!")
        response = await client.delete(
            f"/api/v1/tags/{source_tag.tag_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 204

        # Verify link was also deleted (cascade)
        result = await db_session.execute(
            select(CharacterSourceLinks).where(CharacterSourceLinks.id == link_id)
        )
        assert result.scalar_one_or_none() is None


@pytest.mark.api
class TestLinkedTagUsageCount:
    """Tests for usage_count field in LinkedTag responses."""

    async def test_source_tag_characters_include_usage_count(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        source_tag: Tags,
    ):
        """Test that characters linked to a source tag include usage_count."""
        # Create two character tags with different usage counts
        char1 = Tags(title="Character A", type=TagType.CHARACTER, usage_count=100)
        char2 = Tags(title="Character B", type=TagType.CHARACTER, usage_count=50)
        db_session.add_all([char1, char2])
        await db_session.commit()
        await db_session.refresh(char1)
        await db_session.refresh(char2)

        # Create links
        link1 = CharacterSourceLinks(
            character_tag_id=char1.tag_id,
            source_tag_id=source_tag.tag_id,
        )
        link2 = CharacterSourceLinks(
            character_tag_id=char2.tag_id,
            source_tag_id=source_tag.tag_id,
        )
        db_session.add_all([link1, link2])
        await db_session.commit()

        # Get source tag detail
        response = await client.get(f"/api/v1/tags/{source_tag.tag_id}")
        assert response.status_code == 200
        data = response.json()

        # Verify characters have usage_count field
        assert "characters" in data
        assert len(data["characters"]) == 2
        for char in data["characters"]:
            assert "usage_count" in char

        # Neither character shares an image with the source, so the order here
        # is the alphabetical tiebreaker, not usage_count (see
        # TestLinkedTagSharedImageCount for the ranking rule).
        assert data["characters"][0]["title"] == "Character A"
        assert data["characters"][0]["usage_count"] == 100
        assert data["characters"][1]["title"] == "Character B"
        assert data["characters"][1]["usage_count"] == 50

    async def test_character_tag_sources_include_usage_count(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        character_tag: Tags,
    ):
        """Test that sources linked to a character tag include usage_count."""
        # Create two source tags with different usage counts
        source1 = Tags(title="Source A", type=TagType.SOURCE, usage_count=200)
        source2 = Tags(title="Source B", type=TagType.SOURCE, usage_count=75)
        db_session.add_all([source1, source2])
        await db_session.commit()
        await db_session.refresh(source1)
        await db_session.refresh(source2)

        # Create links
        link1 = CharacterSourceLinks(
            character_tag_id=character_tag.tag_id,
            source_tag_id=source1.tag_id,
        )
        link2 = CharacterSourceLinks(
            character_tag_id=character_tag.tag_id,
            source_tag_id=source2.tag_id,
        )
        db_session.add_all([link1, link2])
        await db_session.commit()

        # Get character tag detail
        response = await client.get(f"/api/v1/tags/{character_tag.tag_id}")
        assert response.status_code == 200
        data = response.json()

        # Verify sources have usage_count field
        assert "sources" in data
        assert len(data["sources"]) == 2
        for source in data["sources"]:
            assert "usage_count" in source

        # As above: no shared images, so this is the alphabetical tiebreaker.
        assert data["sources"][0]["title"] == "Source A"
        assert data["sources"][0]["usage_count"] == 200
        assert data["sources"][1]["title"] == "Source B"
        assert data["sources"][1]["usage_count"] == 75

    async def test_aliases_include_usage_count(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ):
        """Test that alias tags include usage_count."""
        # Create a main tag
        main_tag = Tags(title="Main Tag", type=TagType.THEME, usage_count=500)
        db_session.add(main_tag)
        await db_session.commit()
        await db_session.refresh(main_tag)

        # Create alias tags with different usage counts
        alias1 = Tags(
            title="Alias A",
            type=TagType.THEME,
            usage_count=150,
            alias_of=main_tag.tag_id,
        )
        alias2 = Tags(
            title="Alias B",
            type=TagType.THEME,
            usage_count=25,
            alias_of=main_tag.tag_id,
        )
        db_session.add_all([alias1, alias2])
        await db_session.commit()

        # Get main tag detail
        response = await client.get(f"/api/v1/tags/{main_tag.tag_id}")
        assert response.status_code == 200
        data = response.json()

        # Verify aliases have usage_count field with correct values
        # Aliases are sorted alphabetically by title, so Alias A comes first
        assert "aliases" in data
        assert len(data["aliases"]) == 2
        assert data["aliases"][0]["title"] == "Alias A"
        assert data["aliases"][0]["usage_count"] == 150
        assert data["aliases"][1]["title"] == "Alias B"
        assert data["aliases"][1]["usage_count"] == 25

    async def test_characters_with_no_shared_images_sorted_by_title(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        source_tag: Tags,
    ):
        """Test that characters sharing no images with the source sort by title."""
        # Same usage count, and neither is on any of the source's images
        char_b = Tags(title="Character B", type=TagType.CHARACTER, usage_count=100)
        char_a = Tags(title="Character A", type=TagType.CHARACTER, usage_count=100)
        db_session.add_all([char_b, char_a])
        await db_session.commit()
        await db_session.refresh(char_a)
        await db_session.refresh(char_b)

        # Create links
        link1 = CharacterSourceLinks(
            character_tag_id=char_a.tag_id,
            source_tag_id=source_tag.tag_id,
        )
        link2 = CharacterSourceLinks(
            character_tag_id=char_b.tag_id,
            source_tag_id=source_tag.tag_id,
        )
        db_session.add_all([link1, link2])
        await db_session.commit()

        # Get source tag detail
        response = await client.get(f"/api/v1/tags/{source_tag.tag_id}")
        assert response.status_code == 200
        data = response.json()

        # Tied at zero shared images, so sorted alphabetically by title
        assert data["characters"][0]["title"] == "Character A"
        assert data["characters"][1]["title"] == "Character B"


# =============================================================================
# Shared-image-count ordering
# =============================================================================

# Distinct id ranges so the seed helpers below never collide with other fixtures.
_SIC_IMG_BASE = 971000


async def _tag_images(
    db: AsyncSession, tag_ids: list[int], first_image_id: int, count: int
) -> None:
    """Create ``count`` active images and link every tag in ``tag_ids`` to each."""
    for offset in range(count):
        image_id = first_image_id + offset
        db.add(
            Images(
                image_id=image_id,
                user_id=1,
                ext="jpg",
                status=ImageStatus.ACTIVE,
                width=800,
                height=600,
            )
        )
    # Flush so the image rows exist before the tag_links rows referencing them.
    await db.flush()
    for offset in range(count):
        for tag_id in tag_ids:
            db.add(TagLinks(tag_id=tag_id, image_id=first_image_id + offset))
    await db.flush()


@pytest.mark.api
class TestLinkedTagSharedImageCount:
    """Linked characters/sources rank by images shared with the tag being viewed.

    The old ordering used the linked tag's global usage_count, which floated
    big-franchise characters to the top of a source they barely appear in.
    """

    async def test_characters_rank_by_images_shared_with_the_source(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        source_tag: Tags,
    ):
        """A character with more total images but fewer on this source ranks lower."""
        # Sorts first by title and has the higher usage_count, so ranking it
        # second can only come from the shared count.
        broad = Tags(title="Aaa Broad", type=TagType.CHARACTER)
        focused = Tags(title="Zzz Focused", type=TagType.CHARACTER)
        db_session.add_all([broad, focused])
        await db_session.flush()

        # Broad: 3 images, 1 of them also tagged with the source.
        await _tag_images(db_session, [broad.tag_id, source_tag.tag_id], _SIC_IMG_BASE, 1)
        await _tag_images(db_session, [broad.tag_id], _SIC_IMG_BASE + 10, 2)
        # Focused: 2 images, both also tagged with the source.
        await _tag_images(db_session, [focused.tag_id, source_tag.tag_id], _SIC_IMG_BASE + 20, 2)

        db_session.add_all(
            [
                CharacterSourceLinks(
                    character_tag_id=broad.tag_id, source_tag_id=source_tag.tag_id
                ),
                CharacterSourceLinks(
                    character_tag_id=focused.tag_id, source_tag_id=source_tag.tag_id
                ),
            ]
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/tags/{source_tag.tag_id}")
        assert response.status_code == 200
        characters = response.json()["characters"]

        assert [c["title"] for c in characters] == ["Zzz Focused", "Aaa Broad"]
        assert [c["shared_image_count"] for c in characters] == [2, 1]
        # The global count is still reported, and still favours the loser.
        assert [c["usage_count"] for c in characters] == [2, 3]

    async def test_sources_rank_by_images_shared_with_the_character(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        character_tag: Tags,
    ):
        """Same rule in reverse: sources on a character page."""
        broad = Tags(title="Aaa Broad Source", type=TagType.SOURCE)
        focused = Tags(title="Zzz Focused Source", type=TagType.SOURCE)
        db_session.add_all([broad, focused])
        await db_session.flush()

        await _tag_images(db_session, [broad.tag_id, character_tag.tag_id], _SIC_IMG_BASE + 100, 1)
        await _tag_images(db_session, [broad.tag_id], _SIC_IMG_BASE + 110, 2)
        await _tag_images(
            db_session, [focused.tag_id, character_tag.tag_id], _SIC_IMG_BASE + 120, 2
        )

        db_session.add_all(
            [
                CharacterSourceLinks(
                    character_tag_id=character_tag.tag_id, source_tag_id=broad.tag_id
                ),
                CharacterSourceLinks(
                    character_tag_id=character_tag.tag_id, source_tag_id=focused.tag_id
                ),
            ]
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/tags/{character_tag.tag_id}")
        assert response.status_code == 200
        sources = response.json()["sources"]

        assert [s["title"] for s in sources] == ["Zzz Focused Source", "Aaa Broad Source"]
        assert [s["shared_image_count"] for s in sources] == [2, 1]

    async def test_equal_shared_counts_fall_back_to_title(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        source_tag: Tags,
    ):
        """Characters tied on shared count are ordered alphabetically."""
        char_b = Tags(title="Character B", type=TagType.CHARACTER)
        char_a = Tags(title="Character A", type=TagType.CHARACTER)
        db_session.add_all([char_b, char_a])
        await db_session.flush()

        await _tag_images(db_session, [char_a.tag_id, source_tag.tag_id], _SIC_IMG_BASE + 200, 2)
        await _tag_images(db_session, [char_b.tag_id, source_tag.tag_id], _SIC_IMG_BASE + 210, 2)

        db_session.add_all(
            [
                CharacterSourceLinks(
                    character_tag_id=char_a.tag_id, source_tag_id=source_tag.tag_id
                ),
                CharacterSourceLinks(
                    character_tag_id=char_b.tag_id, source_tag_id=source_tag.tag_id
                ),
            ]
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/tags/{source_tag.tag_id}")
        assert response.status_code == 200
        characters = response.json()["characters"]

        assert [c["title"] for c in characters] == ["Character A", "Character B"]
        assert [c["shared_image_count"] for c in characters] == [2, 2]

    async def test_character_sharing_no_images_reports_zero(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        source_tag: Tags,
    ):
        """A linked character that appears on none of the source's images counts zero."""
        char = Tags(title="Unseen Character", type=TagType.CHARACTER)
        db_session.add(char)
        await db_session.flush()
        await _tag_images(db_session, [char.tag_id], _SIC_IMG_BASE + 300, 1)
        db_session.add(
            CharacterSourceLinks(character_tag_id=char.tag_id, source_tag_id=source_tag.tag_id)
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/tags/{source_tag.tag_id}")
        assert response.status_code == 200
        assert response.json()["characters"][0]["shared_image_count"] == 0

    async def test_shared_counts_are_written_to_the_cache(
        self,
        client_real_redis: AsyncClient,
        redis_client,
        db_session: AsyncSession,
        source_tag: Tags,
    ):
        """A cold request stores the computed count map under the source's key."""
        char = Tags(title="Cached Character", type=TagType.CHARACTER)
        db_session.add(char)
        await db_session.flush()
        await _tag_images(db_session, [char.tag_id, source_tag.tag_id], _SIC_IMG_BASE + 400, 2)
        db_session.add(
            CharacterSourceLinks(character_tag_id=char.tag_id, source_tag_id=source_tag.tag_id)
        )
        await db_session.commit()

        response = await client_real_redis.get(f"/api/v1/tags/{source_tag.tag_id}")
        assert response.status_code == 200
        assert response.json()["characters"][0]["shared_image_count"] == 2

        cached = await redis_client.get(_cache_key(source_tag.tag_id, "character"))
        assert cached is not None
        assert json.loads(cached) == {str(char.tag_id): 2}

    async def test_cached_shared_counts_are_used_instead_of_querying(
        self,
        client_real_redis: AsyncClient,
        redis_client,
        db_session: AsyncSession,
        source_tag: Tags,
    ):
        """A populated key is trusted: the response reports the cached count."""
        char = Tags(title="Cached Character", type=TagType.CHARACTER)
        db_session.add(char)
        await db_session.flush()
        # One image actually shared - the cache will claim otherwise.
        await _tag_images(db_session, [char.tag_id, source_tag.tag_id], _SIC_IMG_BASE + 500, 1)
        db_session.add(
            CharacterSourceLinks(character_tag_id=char.tag_id, source_tag_id=source_tag.tag_id)
        )
        await db_session.commit()
        await redis_client.set(
            _cache_key(source_tag.tag_id, "character"), json.dumps({str(char.tag_id): 42})
        )

        response = await client_real_redis.get(f"/api/v1/tags/{source_tag.tag_id}")
        assert response.status_code == 200
        assert response.json()["characters"][0]["shared_image_count"] == 42
