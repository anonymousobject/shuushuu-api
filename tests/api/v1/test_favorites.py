"""
Tests for favorites API endpoints (deprecated routes).

These tests cover the /api/v1/favorites endpoints including:
- Get favorite images by user (deprecated, use /users/{user_id}/favorites)
- Get users who favorited an image (deprecated, use /images/{image_id}/favorites)
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash
from app.models.favorite import Favorites
from app.models.image import Images
from app.models.permissions import Groups, UserGroups
from app.models.user import Users


@pytest.mark.api
class TestGetFavoriteImages:
    """Tests for GET /api/v1/favorites/user/{user_id} endpoint (deprecated)."""

    async def test_get_favorite_images(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test getting images favorited by a user."""
        # Create images
        images = []
        for i in range(3):
            image_data = sample_image_data.copy()
            image_data["filename"] = f"fav-image-{i}"
            image_data["md5_hash"] = f"favhash{i:021d}"
            image = Images(**image_data)
            db_session.add(image)
            images.append(image)
        await db_session.commit()

        # Add favorites for user 1
        for image in images:
            await db_session.refresh(image)
            favorite = Favorites(user_id=1, image_id=image.image_id)
            db_session.add(favorite)
        await db_session.commit()

        response = await client.get("/api/v1/favorites/user/1")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3

    async def test_get_favorite_images_nonexistent_user(self, client: AsyncClient):
        """Test getting favorites for non-existent user."""
        response = await client.get("/api/v1/favorites/user/999999")
        assert response.status_code == 404

    async def test_get_favorite_images_empty(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test getting favorites when user has no favorites."""
        response = await client.get("/api/v1/favorites/user/1")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["images"] == []


@pytest.mark.api
class TestGetImageFavorites:
    """Tests for GET /api/v1/favorites/image/{image_id} endpoint (deprecated)."""

    async def test_get_image_favorites(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test getting users who favorited an image."""
        # Create image
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Add favorites from multiple users
        for user_id in [1, 2, 3]:
            favorite = Favorites(user_id=user_id, image_id=image.image_id)
            db_session.add(favorite)
        await db_session.commit()

        response = await client.get(f"/api/v1/favorites/image/{image.image_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert len(data["users"]) == 3

    async def test_get_image_favorites_nonexistent_image(self, client: AsyncClient):
        """Test getting favorites for non-existent image."""
        response = await client.get("/api/v1/favorites/image/999999")
        assert response.status_code == 404

    async def test_get_image_favorites_empty(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test getting favorites when image has no favorites."""
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        response = await client.get(f"/api/v1/favorites/image/{image.image_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["users"] == []


@pytest.mark.api
class TestImageFavoritesWithGroups:
    """Tests for groups field in image favorites user responses."""

    async def test_image_favorites_includes_user_groups(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test that GET /favorites/image/{id} returns groups for each user who favorited."""
        # Create an image
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create a user with a group
        user = Users(
            username="favgroupuser",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="favgroupuser@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create a group and add user to it
        group = Groups(title="Collectors", desc="Image collectors")
        db_session.add(group)
        await db_session.commit()
        await db_session.refresh(group)

        user_group = UserGroups(user_id=user.user_id, group_id=group.group_id)
        db_session.add(user_group)

        # User favorites the image
        favorite = Favorites(user_id=user.user_id, image_id=image.image_id)
        db_session.add(favorite)
        await db_session.commit()

        # Get users who favorited the image
        response = await client.get(f"/api/v1/favorites/image/{image.image_id}")
        assert response.status_code == 200
        data = response.json()

        assert data["total"] >= 1
        test_user = next(u for u in data["users"] if u["username"] == "favgroupuser")

        # Verify groups are included
        assert "groups" in test_user
        assert isinstance(test_user["groups"], list)
        assert "Collectors" in test_user["groups"]
