"""
Tests for tags API endpoints.

These tests cover the /api/v1/tags endpoints including:
- List and search tags
- Get tag details
- Create tag (admin only)
- Update tag (admin only)
- Delete tag (admin only)
- Get images by tag
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.core.security import get_password_hash
from app.models.image import Images
from app.models.permissions import Perms, UserPerms
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.user import Users


@pytest.mark.api
class TestListTags:
    """Tests for GET /api/v1/tags/ endpoint."""

    async def test_list_tags(self, client: AsyncClient, db_session: AsyncSession):
        """Test listing tags."""
        # Create test tags
        for i in range(5):
            tag = Tags(
                title=f"Test Tag {i}",
                desc=f"Description for tag {i}",
                type=TagType.THEME,
            )
            db_session.add(tag)
        await db_session.commit()

        response = await client.get("/api/v1/tags/")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 5
        assert "tags" in data

    async def test_search_tags(self, client: AsyncClient, db_session: AsyncSession):
        """Test searching tags by name."""
        # Create tags with different names
        tag1 = Tags(title="anime girl", desc="Anime female character", type=TagType.THEME)
        tag2 = Tags(title="school uniform", desc="School clothing", type=TagType.CHARACTER)
        tag3 = Tags(title="cat ears", desc="Feline ears", type=TagType.ARTIST)
        db_session.add_all([tag1, tag2, tag3])
        await db_session.commit()

        # Search for "school"
        response = await client.get("/api/v1/tags/?search=school")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["tags"][0]["title"] == "school uniform"

    async def test_filter_tags_by_type(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test filtering tags by type."""
        # Create tags of different types
        tag1 = Tags(title="tag1", type=TagType.THEME)
        tag2 = Tags(title="tag2", type=TagType.CHARACTER)
        tag3 = Tags(title="tag3", type=TagType.THEME)
        db_session.add_all([tag1, tag2, tag3])
        await db_session.commit()

        # Filter by THEME type
        response = await client.get(f"/api/v1/tags/?type={TagType.THEME}")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        for tag in data["tags"]:
            assert tag["type"] == TagType.THEME

    async def test_filter_tags_by_ids(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test filtering tags by specific IDs."""
        # Create test tags
        tag1 = Tags(title="tag1", type=TagType.THEME)
        tag2 = Tags(title="tag2", type=TagType.CHARACTER)
        tag3 = Tags(title="tag3", type=TagType.ARTIST)
        db_session.add_all([tag1, tag2, tag3])
        await db_session.commit()
        await db_session.refresh(tag1)
        await db_session.refresh(tag2)
        await db_session.refresh(tag3)

        # Filter by specific IDs
        response = await client.get(f"/api/v1/tags/?ids={tag1.tag_id},{tag3.tag_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        returned_ids = {tag["tag_id"] for tag in data["tags"]}
        assert returned_ids == {tag1.tag_id, tag3.tag_id}
        # No invalid IDs
        assert data.get("invalid_ids") is None

    async def test_filter_tags_with_invalid_ids(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test filtering tags with mix of valid and invalid IDs."""
        # Create test tags
        tag1 = Tags(title="tag1", type=TagType.THEME)
        tag2 = Tags(title="tag2", type=TagType.CHARACTER)
        db_session.add_all([tag1, tag2])
        await db_session.commit()
        await db_session.refresh(tag1)
        await db_session.refresh(tag2)

        # Mix valid IDs with invalid ones (non-numeric)
        response = await client.get(f"/api/v1/tags/?ids={tag1.tag_id},abc,{tag2.tag_id},xyz")
        assert response.status_code == 200
        data = response.json()
        
        # Should return valid tags
        assert data["total"] == 2
        returned_ids = {tag["tag_id"] for tag in data["tags"]}
        assert returned_ids == {tag1.tag_id, tag2.tag_id}
        
        # Should report invalid IDs
        assert data["invalid_ids"] is not None
        assert set(data["invalid_ids"]) == {"abc", "xyz"}

    async def test_filter_tags_with_only_invalid_ids(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test filtering tags with only invalid IDs."""
        # Create some tags
        tag = Tags(title="tag1", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()

        # Only invalid IDs
        response = await client.get("/api/v1/tags/?ids=abc,xyz,foo")
        assert response.status_code == 200
        data = response.json()
        
        # Should return no tags
        assert data["total"] == 0
        assert len(data["tags"]) == 0
        
        # Should report all invalid IDs
        assert data["invalid_ids"] is not None
        assert set(data["invalid_ids"]) == {"abc", "xyz", "foo"}

    async def test_filter_tags_with_empty_id_strings(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test filtering tags handles empty strings in ID list gracefully."""
        # Create test tags
        tag1 = Tags(title="tag1", type=TagType.THEME)
        db_session.add(tag1)
        await db_session.commit()
        await db_session.refresh(tag1)

        # IDs with empty strings (trailing commas, double commas)
        response = await client.get(f"/api/v1/tags/?ids={tag1.tag_id},,")
        assert response.status_code == 200
        data = response.json()
        
        # Should return valid tag
        assert data["total"] == 1
        assert data["tags"][0]["tag_id"] == tag1.tag_id
        
        # Empty strings should not be reported as invalid
        assert data.get("invalid_ids") is None



@pytest.mark.api
class TestGetTag:
    """Tests for GET /api/v1/tags/{tag_id} endpoint."""

    async def test_get_tag_by_id(self, client: AsyncClient, db_session: AsyncSession):
        """Test getting a tag by ID."""
        tag = Tags(title="Test Tag", desc="Test description", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        response = await client.get(f"/api/v1/tags/{tag.tag_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["tag_id"] == tag.tag_id
        assert data["title"] == "Test Tag"
        assert data["image_count"] == 0  # No images yet

    async def test_get_nonexistent_tag(self, client: AsyncClient):
        """Test getting a tag that doesn't exist."""
        response = await client.get("/api/v1/tags/999999")
        assert response.status_code == 404


@pytest.mark.api
class TestGetImagesByTag:
    """Tests for GET /api/v1/tags/{tag_id}/images endpoint."""

    async def test_get_images_by_tag(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test getting images with a specific tag."""
        # Create tag
        tag = Tags(title="sunset", desc="Sunset scenes", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Create images
        images = []
        for i in range(3):
            image_data = sample_image_data.copy()
            image_data["filename"] = f"sunset-{i}"
            image_data["md5_hash"] = f"sunset{i:021d}"
            image = Images(**image_data)
            db_session.add(image)
            images.append(image)
        await db_session.commit()

        # Link images to tag
        for image in images:
            await db_session.refresh(image)
            tag_link = TagLinks(tag_id=tag.tag_id, image_id=image.image_id)
            db_session.add(tag_link)
        await db_session.commit()

        response = await client.get(f"/api/v1/tags/{tag.tag_id}/images")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3

    async def test_get_images_by_nonexistent_tag(self, client: AsyncClient):
        """Test getting images for non-existent tag."""
        response = await client.get("/api/v1/tags/999999/images")
        assert response.status_code == 404


@pytest.mark.api
class TestCreateTag:
    """Tests for POST /api/v1/tags/ endpoint (admin only)."""

    async def test_create_tag_as_admin(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test creating a tag as admin."""
        # Create TAG_CREATE permission
        perm = Perms(title="tag_create", desc="Create tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="adminuser",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="admin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_CREATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "adminuser", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Create tag
        tag_data = {
            "title": "new tag",
            "desc": "A new test tag",
            "type": TagType.THEME,
        }
        response = await client.post(
            "/api/v1/tags/",
            json=tag_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "new tag"
        assert data["type"] == TagType.THEME

    async def test_create_tag_as_non_admin(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test non-admin user cannot create tags."""
        # Create regular user
        user = Users(
            username="regularuser",
            password=get_password_hash("Password123!"),
            password_type="bcrypt",
            salt="",
            email="regular@example.com",
            active=1,
            admin=0,
        )
        db_session.add(user)
        await db_session.commit()

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "regularuser", "password": "Password123!"},
        )
        access_token = login_response.json()["access_token"]

        # Try to create tag
        tag_data = {
            "title": "forbidden tag",
            "desc": "Should not be created",
            "type": TagType.THEME,
        }
        response = await client.post(
            "/api/v1/tags/",
            json=tag_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 403

    async def test_create_tag_unauthenticated(self, client: AsyncClient):
        """Test creating tag without authentication."""
        tag_data = {
            "title": "unauthenticated tag",
            "desc": "Should not be created",
            "type": TagType.THEME,
        }
        response = await client.post("/api/v1/tags/", json=tag_data)
        assert response.status_code == 401

    async def test_create_duplicate_tag(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test creating a duplicate tag."""
        # Create TAG_CREATE permission
        perm = Perms(title="tag_create", desc="Create tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="adminuser2",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="admin2@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_CREATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)

        # Create existing tag
        existing_tag = Tags(title="existing", desc="Already exists", type=TagType.THEME)
        db_session.add(existing_tag)
        await db_session.commit()

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "adminuser2", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Try to create duplicate tag
        tag_data = {
            "title": "existing",
            "desc": "Duplicate",
            "type": TagType.THEME,
        }
        response = await client.post(
            "/api/v1/tags/",
            json=tag_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 409


@pytest.mark.api
class TestUpdateTag:
    """Tests for PUT /api/v1/tags/{tag_id} endpoint (admin only)."""

    async def test_update_tag_as_admin(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test admin updating a tag."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="adminupdate",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="adminupdate@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)

        # Create tag to update
        tag = Tags(title="old title", desc="old description", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "adminupdate", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Update tag
        update_data = {
            "title": "new title",
            "desc": "new description",
            "type": TagType.CHARACTER,
        }
        response = await client.put(
            f"/api/v1/tags/{tag.tag_id}",
            json=update_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "new title"
        assert data["type"] == TagType.CHARACTER

    async def test_update_tag_as_non_admin(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test non-admin cannot update tags."""
        # Create regular user
        user = Users(
            username="regularupdate",
            password=get_password_hash("Password123!"),
            password_type="bcrypt",
            salt="",
            email="regularupdate@example.com",
            active=1,
            admin=0,
        )
        db_session.add(user)

        # Create tag
        tag = Tags(title="tag to update", desc="description", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "regularupdate", "password": "Password123!"},
        )
        access_token = login_response.json()["access_token"]

        # Try to update tag
        update_data = {
            "title": "hacked title",
            "desc": "hacked description",
            "type": TagType.THEME,
        }
        response = await client.put(
            f"/api/v1/tags/{tag.tag_id}",
            json=update_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 403


@pytest.mark.api
class TestDeleteTag:
    """Tests for DELETE /api/v1/tags/{tag_id} endpoint (admin only)."""

    async def test_delete_tag_as_admin(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test admin deleting a tag."""
        # Create TAG_DELETE permission
        perm = Perms(title="tag_delete", desc="Delete tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="admindelete",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="admindelete@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_DELETE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)

        # Create tag to delete
        tag = Tags(title="tag to delete", desc="will be deleted", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "admindelete", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Delete tag
        response = await client.delete(
            f"/api/v1/tags/{tag.tag_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 204

        # Verify tag is deleted
        get_response = await client.get(f"/api/v1/tags/{tag.tag_id}")
        assert get_response.status_code == 404

    async def test_delete_tag_as_non_admin(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test non-admin cannot delete tags."""
        # Create regular user
        user = Users(
            username="regulardelete",
            password=get_password_hash("Password123!"),
            password_type="bcrypt",
            salt="",
            email="regulardelete@example.com",
            active=1,
            admin=0,
        )
        db_session.add(user)

        # Create tag
        tag = Tags(title="protected tag", desc="should not be deleted", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "regulardelete", "password": "Password123!"},
        )
        access_token = login_response.json()["access_token"]

        # Try to delete tag
        response = await client.delete(
            f"/api/v1/tags/{tag.tag_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 403
