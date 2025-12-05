"""
Tests for tag suggestions API endpoints.

These tests cover:
- GET /api/v1/images/{image_id}/tag-suggestions
- Authentication and permission checks
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.image import Images
from app.models.tag import Tags
from app.models.tag_suggestion import TagSuggestion
from app.models.user import Users


@pytest.mark.api
class TestGetTagSuggestions:
    """Tests for GET /api/v1/images/{image_id}/tag-suggestions endpoint."""

    async def test_get_suggestions_for_image(self, client: AsyncClient, db_session: AsyncSession):
        """Test GET /api/v1/images/{image_id}/tag-suggestions"""
        # Create test data
        user = Users(
            username="test_suggestions",
            email="test_suggestions@example.com",
            password="hashed",
            password_type="bcrypt",
            salt="testsalt12345678",
            active=1,
        )
        db_session.add(user)
        await db_session.flush()

        image = Images(
            filename="test",
            ext="jpg",
            user_id=user.user_id,
            md5_hash="abc123",
            filesize=1024,
            width=800,
            height=600,
        )
        db_session.add(image)
        await db_session.flush()

        tag1 = Tags(title="long hair", type=1, user_id=user.user_id)
        tag2 = Tags(title="smile", type=1, user_id=user.user_id)
        db_session.add_all([tag1, tag2])
        await db_session.flush()

        sugg1 = TagSuggestion(
            image_id=image.image_id,
            tag_id=tag1.tag_id,
            confidence=0.92,
            model_source="custom_theme",
            model_version="v1",
            status="pending",
        )
        sugg2 = TagSuggestion(
            image_id=image.image_id,
            tag_id=tag2.tag_id,
            confidence=0.85,
            model_source="custom_theme",
            model_version="v1",
            status="pending",
        )
        db_session.add_all([sugg1, sugg2])
        await db_session.commit()

        # Generate auth token
        access_token = create_access_token(user_id=user.user_id)

        # Make request
        response = await client.get(
            f"/api/v1/images/{image.image_id}/tag-suggestions",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["image_id"] == image.image_id
        assert len(data["suggestions"]) == 2
        assert data["total"] == 2
        assert data["pending"] == 2
        assert data["approved"] == 0
        assert data["rejected"] == 0

        # Verify suggestions are sorted by confidence (descending)
        assert data["suggestions"][0]["confidence"] == 0.92
        assert data["suggestions"][1]["confidence"] == 0.85

    async def test_get_suggestions_requires_auth(self, client: AsyncClient):
        """Test that endpoint requires authentication"""
        response = await client.get("/api/v1/images/123/tag-suggestions")
        assert response.status_code == 401

    async def test_get_suggestions_image_not_found(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test 404 when image doesn't exist"""
        user = Users(
            username="test_404",
            email="test_404@example.com",
            password="hashed",
            password_type="bcrypt",
            salt="testsalt12345678",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()

        access_token = create_access_token(user_id=user.user_id)

        response = await client.get(
            "/api/v1/images/99999/tag-suggestions",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 404

    async def test_get_suggestions_permission_denied(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that user cannot view other user's suggestions"""
        # Create owner
        owner = Users(
            username="owner",
            email="owner@example.com",
            password="hashed",
            password_type="bcrypt",
            salt="testsalt12345678",
            active=1,
        )
        db_session.add(owner)
        await db_session.flush()

        # Create other user
        other_user = Users(
            username="other_user",
            email="other@example.com",
            password="hashed",
            password_type="bcrypt",
            salt="testsalt87654321",
            active=1,
        )
        db_session.add(other_user)
        await db_session.flush()

        # Create image owned by owner
        image = Images(
            filename="test",
            ext="jpg",
            user_id=owner.user_id,
            md5_hash="abc123",
            filesize=1024,
            width=800,
            height=600,
        )
        db_session.add(image)
        await db_session.commit()

        # Try to access as other_user
        access_token = create_access_token(user_id=other_user.user_id)

        response = await client.get(
            f"/api/v1/images/{image.image_id}/tag-suggestions",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 403

    async def test_get_suggestions_filter_by_status(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test filtering suggestions by status"""
        user = Users(
            username="test_filter",
            email="test_filter@example.com",
            password="hashed",
            password_type="bcrypt",
            salt="testsalt12345678",
            active=1,
        )
        db_session.add(user)
        await db_session.flush()

        image = Images(
            filename="test",
            ext="jpg",
            user_id=user.user_id,
            md5_hash="abc123",
            filesize=1024,
            width=800,
            height=600,
        )
        db_session.add(image)
        await db_session.flush()

        tag1 = Tags(title="tag1", type=1, user_id=user.user_id)
        tag2 = Tags(title="tag2", type=1, user_id=user.user_id)
        tag3 = Tags(title="tag3", type=1, user_id=user.user_id)
        db_session.add_all([tag1, tag2, tag3])
        await db_session.flush()

        # Create suggestions with different statuses
        sugg1 = TagSuggestion(
            image_id=image.image_id,
            tag_id=tag1.tag_id,
            confidence=0.92,
            model_source="custom_theme",
            model_version="v1",
            status="pending",
        )
        sugg2 = TagSuggestion(
            image_id=image.image_id,
            tag_id=tag2.tag_id,
            confidence=0.85,
            model_source="custom_theme",
            model_version="v1",
            status="approved",
        )
        sugg3 = TagSuggestion(
            image_id=image.image_id,
            tag_id=tag3.tag_id,
            confidence=0.75,
            model_source="custom_theme",
            model_version="v1",
            status="rejected",
        )
        db_session.add_all([sugg1, sugg2, sugg3])
        await db_session.commit()

        access_token = create_access_token(user_id=user.user_id)

        # Filter for pending only
        response = await client.get(
            f"/api/v1/images/{image.image_id}/tag-suggestions?status=pending",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["suggestions"]) == 1
        assert data["suggestions"][0]["status"] == "pending"

        # Filter for approved only
        response = await client.get(
            f"/api/v1/images/{image.image_id}/tag-suggestions?status=approved",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["suggestions"]) == 1
        assert data["suggestions"][0]["status"] == "approved"
