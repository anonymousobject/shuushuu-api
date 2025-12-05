"""
Tests for tag suggestions API endpoints.

These tests cover:
- GET /api/v1/images/{image_id}/tag-suggestions
- POST /api/v1/images/{image_id}/tag-suggestions/review
- Authentication and permission checks
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.image import Images
from app.models.tag import Tags
from app.models.tag_link import TagLinks
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


@pytest.mark.api
class TestReviewTagSuggestions:
    """Tests for POST /api/v1/images/{image_id}/tag-suggestions/review endpoint."""

    async def test_approve_suggestion_creates_tag_link(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test approving a suggestion creates a TagLink"""
        # Create test data
        user = Users(
            username="test_approve",
            email="test_approve@example.com",
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

        tag = Tags(title="long hair", type=1, user_id=user.user_id)
        db_session.add(tag)
        await db_session.flush()

        suggestion = TagSuggestion(
            image_id=image.image_id,
            tag_id=tag.tag_id,
            confidence=0.92,
            model_source="custom_theme",
            model_version="v1",
            status="pending",
        )
        db_session.add(suggestion)
        await db_session.commit()

        # Approve suggestion
        access_token = create_access_token(user_id=user.user_id)
        response = await client.post(
            f"/api/v1/images/{image.image_id}/tag-suggestions/review",
            json={"suggestions": [{"suggestion_id": suggestion.suggestion_id, "action": "approve"}]},
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["approved"] == 1
        assert data["rejected"] == 0
        assert data["errors"] == []

        # Verify TagLink was created
        result = await db_session.execute(
            select(TagLinks).where(
                TagLinks.image_id == image.image_id, TagLinks.tag_id == tag.tag_id
            )
        )
        tag_link = result.scalar_one_or_none()
        assert tag_link is not None
        assert tag_link.user_id == user.user_id

        # Verify suggestion status updated
        await db_session.refresh(suggestion)
        assert suggestion.status == "approved"
        assert suggestion.reviewed_by_user_id == user.user_id
        assert suggestion.reviewed_at is not None

    async def test_reject_suggestion(self, client: AsyncClient, db_session: AsyncSession):
        """Test rejecting a suggestion does not create TagLink"""
        # Create test data
        user = Users(
            username="test_reject",
            email="test_reject@example.com",
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
            md5_hash="abc456",
            filesize=1024,
            width=800,
            height=600,
        )
        db_session.add(image)
        await db_session.flush()

        tag = Tags(title="smile", type=1, user_id=user.user_id)
        db_session.add(tag)
        await db_session.flush()

        suggestion = TagSuggestion(
            image_id=image.image_id,
            tag_id=tag.tag_id,
            confidence=0.85,
            model_source="custom_theme",
            model_version="v1",
            status="pending",
        )
        db_session.add(suggestion)
        await db_session.commit()

        # Reject suggestion
        access_token = create_access_token(user_id=user.user_id)
        response = await client.post(
            f"/api/v1/images/{image.image_id}/tag-suggestions/review",
            json={"suggestions": [{"suggestion_id": suggestion.suggestion_id, "action": "reject"}]},
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["approved"] == 0
        assert data["rejected"] == 1
        assert data["errors"] == []

        # Verify NO TagLink was created
        result = await db_session.execute(
            select(TagLinks).where(
                TagLinks.image_id == image.image_id, TagLinks.tag_id == tag.tag_id
            )
        )
        tag_link = result.scalar_one_or_none()
        assert tag_link is None

        # Verify suggestion status updated
        await db_session.refresh(suggestion)
        assert suggestion.status == "rejected"
        assert suggestion.reviewed_by_user_id == user.user_id
        assert suggestion.reviewed_at is not None

    async def test_batch_review_mixed_actions(self, client: AsyncClient, db_session: AsyncSession):
        """Test batch reviewing with both approve and reject actions"""
        # Create test data
        user = Users(
            username="test_batch",
            email="test_batch@example.com",
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
            md5_hash="batch123",
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
        sugg3 = TagSuggestion(
            image_id=image.image_id,
            tag_id=tag3.tag_id,
            confidence=0.75,
            model_source="custom_theme",
            model_version="v1",
            status="pending",
        )
        db_session.add_all([sugg1, sugg2, sugg3])
        await db_session.commit()

        # Batch review: approve 2, reject 1
        access_token = create_access_token(user_id=user.user_id)
        response = await client.post(
            f"/api/v1/images/{image.image_id}/tag-suggestions/review",
            json={
                "suggestions": [
                    {"suggestion_id": sugg1.suggestion_id, "action": "approve"},
                    {"suggestion_id": sugg2.suggestion_id, "action": "approve"},
                    {"suggestion_id": sugg3.suggestion_id, "action": "reject"},
                ]
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["approved"] == 2
        assert data["rejected"] == 1
        assert data["errors"] == []

        # Verify 2 TagLinks created
        result = await db_session.execute(
            select(TagLinks).where(TagLinks.image_id == image.image_id)
        )
        tag_links = result.scalars().all()
        assert len(tag_links) == 2
        link_tag_ids = {link.tag_id for link in tag_links}
        assert tag1.tag_id in link_tag_ids
        assert tag2.tag_id in link_tag_ids
        assert tag3.tag_id not in link_tag_ids

    async def test_review_requires_auth(self, client: AsyncClient):
        """Test that review endpoint requires authentication"""
        response = await client.post(
            "/api/v1/images/123/tag-suggestions/review",
            json={"suggestions": [{"suggestion_id": 1, "action": "approve"}]},
        )
        assert response.status_code == 401

    async def test_review_permission_denied(self, client: AsyncClient, db_session: AsyncSession):
        """Test that non-owner cannot review suggestions"""
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
        await db_session.flush()

        tag = Tags(title="test", type=1, user_id=owner.user_id)
        db_session.add(tag)
        await db_session.flush()

        suggestion = TagSuggestion(
            image_id=image.image_id,
            tag_id=tag.tag_id,
            confidence=0.85,
            model_source="custom_theme",
            model_version="v1",
            status="pending",
        )
        db_session.add(suggestion)
        await db_session.commit()

        # Try to review as other_user
        access_token = create_access_token(user_id=other_user.user_id)
        response = await client.post(
            f"/api/v1/images/{image.image_id}/tag-suggestions/review",
            json={"suggestions": [{"suggestion_id": suggestion.suggestion_id, "action": "approve"}]},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 403

    async def test_review_image_not_found(self, client: AsyncClient, db_session: AsyncSession):
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
        response = await client.post(
            "/api/v1/images/99999/tag-suggestions/review",
            json={"suggestions": [{"suggestion_id": 1, "action": "approve"}]},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 404

    async def test_review_invalid_suggestion_id(self, client: AsyncClient, db_session: AsyncSession):
        """Test error when suggestion_id doesn't exist"""
        user = Users(
            username="test_invalid",
            email="test_invalid@example.com",
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
            md5_hash="invalid123",
            filesize=1024,
            width=800,
            height=600,
        )
        db_session.add(image)
        await db_session.commit()

        access_token = create_access_token(user_id=user.user_id)
        response = await client.post(
            f"/api/v1/images/{image.image_id}/tag-suggestions/review",
            json={"suggestions": [{"suggestion_id": 99999, "action": "approve"}]},
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["approved"] == 0
        assert data["rejected"] == 0
        assert len(data["errors"]) > 0
        assert "not found" in data["errors"][0].lower()

    async def test_review_suggestion_from_different_image(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test error when suggestion belongs to a different image"""
        user = Users(
            username="test_cross",
            email="test_cross@example.com",
            password="hashed",
            password_type="bcrypt",
            salt="testsalt12345678",
            active=1,
        )
        db_session.add(user)
        await db_session.flush()

        image1 = Images(
            filename="test1",
            ext="jpg",
            user_id=user.user_id,
            md5_hash="cross1",
            filesize=1024,
            width=800,
            height=600,
        )
        image2 = Images(
            filename="test2",
            ext="jpg",
            user_id=user.user_id,
            md5_hash="cross2",
            filesize=1024,
            width=800,
            height=600,
        )
        db_session.add_all([image1, image2])
        await db_session.flush()

        tag = Tags(title="test", type=1, user_id=user.user_id)
        db_session.add(tag)
        await db_session.flush()

        # Create suggestion for image2
        suggestion = TagSuggestion(
            image_id=image2.image_id,
            tag_id=tag.tag_id,
            confidence=0.85,
            model_source="custom_theme",
            model_version="v1",
            status="pending",
        )
        db_session.add(suggestion)
        await db_session.commit()

        # Try to review it as if it belongs to image1
        access_token = create_access_token(user_id=user.user_id)
        response = await client.post(
            f"/api/v1/images/{image1.image_id}/tag-suggestions/review",
            json={"suggestions": [{"suggestion_id": suggestion.suggestion_id, "action": "approve"}]},
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["approved"] == 0
        assert len(data["errors"]) > 0
        assert "not found" in data["errors"][0].lower()

    async def test_approve_does_not_duplicate_existing_tag_link(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that approving suggestion doesn't create duplicate TagLink"""
        user = Users(
            username="test_duplicate",
            email="test_duplicate@example.com",
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
            md5_hash="dup123",
            filesize=1024,
            width=800,
            height=600,
        )
        db_session.add(image)
        await db_session.flush()

        tag = Tags(title="existing", type=1, user_id=user.user_id)
        db_session.add(tag)
        await db_session.flush()

        # Create existing TagLink
        existing_link = TagLinks(
            image_id=image.image_id, tag_id=tag.tag_id, user_id=user.user_id
        )
        db_session.add(existing_link)
        await db_session.flush()

        # Create suggestion for same tag
        suggestion = TagSuggestion(
            image_id=image.image_id,
            tag_id=tag.tag_id,
            confidence=0.92,
            model_source="custom_theme",
            model_version="v1",
            status="pending",
        )
        db_session.add(suggestion)
        await db_session.commit()

        # Approve suggestion
        access_token = create_access_token(user_id=user.user_id)
        response = await client.post(
            f"/api/v1/images/{image.image_id}/tag-suggestions/review",
            json={"suggestions": [{"suggestion_id": suggestion.suggestion_id, "action": "approve"}]},
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["approved"] == 1

        # Verify still only one TagLink exists
        result = await db_session.execute(
            select(TagLinks).where(
                TagLinks.image_id == image.image_id, TagLinks.tag_id == tag.tag_id
            )
        )
        tag_links = result.scalars().all()
        assert len(tag_links) == 1
