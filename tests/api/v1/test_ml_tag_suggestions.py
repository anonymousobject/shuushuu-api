"""
Tests for ML tag suggestions API endpoints.

These tests cover:
- GET /api/v1/images/{image_id}/ml-tag-suggestions
- POST /api/v1/images/{image_id}/ml-tag-suggestions/review
- POST /api/v1/images/{image_id}/ml-tag-suggestions/generate
- Authentication and permission checks
"""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import create_access_token
from app.models.image import Images
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.user import Users

# Patch targets for the synchronous generate path. The router calls
# _get_ml_service() for the ML inference boundary; the pipeline resolves
# external tags and relationships against the real DB. We patch the resolvers
# (same boundary the pipeline's own tests use) so a sync generate produces
# deterministic suggestions without loading an ONNX model.
ROUTER = "app.api.v1.ml_tag_suggestions"
PIPELINE = "app.services.ml_suggestion_pipeline"


class FakeMLService:
    """Minimal stand-in for MLTagSuggestionService used by the sync path.

    The pipeline only calls ``generate_suggestions``; it never loads models
    here because _get_ml_service is patched to return this instance.
    """

    def __init__(self, predictions: list[dict[str, Any]]) -> None:
        self._predictions = predictions

    async def generate_suggestions(
        self, image_path: str, min_confidence: float = 0.35
    ) -> list[dict[str, Any]]:
        return list(self._predictions)


def _resolver_to_tag_ids(rows: list[dict[str, Any]]):
    """Build a fake resolve_external_tags returning the given tag_id rows."""

    async def _resolver(db, suggestions):
        return [dict(r) for r in rows]

    return _resolver


async def _passthrough_resolver(db, suggestions):
    return suggestions


@pytest.mark.api
class TestGetMlTagSuggestions:
    """Tests for GET /api/v1/images/{image_id}/ml-tag-suggestions endpoint."""

    async def test_get_suggestions_for_image(self, client: AsyncClient, db_session: AsyncSession):
        """Test GET /api/v1/images/{image_id}/ml-tag-suggestions"""
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

        sugg1 = MlTagSuggestions(
            image_id=image.image_id,
            tag_id=tag1.tag_id,
            confidence=0.92,
            model_version="v1",
            status="pending",
        )
        sugg2 = MlTagSuggestions(
            image_id=image.image_id,
            tag_id=tag2.tag_id,
            confidence=0.85,
            model_version="v1",
            status="pending",
        )
        db_session.add_all([sugg1, sugg2])
        await db_session.commit()

        # Generate auth token
        access_token = create_access_token(user_id=user.user_id)

        # Make request
        response = await client.get(
            f"/api/v1/images/{image.image_id}/ml-tag-suggestions",
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

        # Verify model_version is exposed (replaces the old model_source)
        assert data["suggestions"][0]["model_version"] == "v1"

    async def test_get_suggestions_requires_auth(self, client: AsyncClient):
        """Test that endpoint requires authentication"""
        response = await client.get("/api/v1/images/123/ml-tag-suggestions")
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
            "/api/v1/images/99999/ml-tag-suggestions",
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
            f"/api/v1/images/{image.image_id}/ml-tag-suggestions",
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
        sugg1 = MlTagSuggestions(
            image_id=image.image_id,
            tag_id=tag1.tag_id,
            confidence=0.92,
            model_version="v1",
            status="pending",
        )
        sugg2 = MlTagSuggestions(
            image_id=image.image_id,
            tag_id=tag2.tag_id,
            confidence=0.85,
            model_version="v1",
            status="approved",
        )
        sugg3 = MlTagSuggestions(
            image_id=image.image_id,
            tag_id=tag3.tag_id,
            confidence=0.75,
            model_version="v1",
            status="rejected",
        )
        db_session.add_all([sugg1, sugg2, sugg3])
        await db_session.commit()

        access_token = create_access_token(user_id=user.user_id)

        # Filter for pending only
        response = await client.get(
            f"/api/v1/images/{image.image_id}/ml-tag-suggestions?status=pending",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["suggestions"]) == 1
        assert data["suggestions"][0]["status"] == "pending"
        # Status counts cover ALL suggestions regardless of the filter
        assert data["pending"] == 1
        assert data["approved"] == 1
        assert data["rejected"] == 1

        # Filter for approved only
        response = await client.get(
            f"/api/v1/images/{image.image_id}/ml-tag-suggestions?status=approved",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["suggestions"]) == 1
        assert data["suggestions"][0]["status"] == "approved"


@pytest.mark.api
class TestReviewMlTagSuggestions:
    """Tests for POST /api/v1/images/{image_id}/ml-tag-suggestions/review endpoint."""

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

        suggestion = MlTagSuggestions(
            image_id=image.image_id,
            tag_id=tag.tag_id,
            confidence=0.92,
            model_version="v1",
            status="pending",
        )
        db_session.add(suggestion)
        await db_session.commit()

        # Approve suggestion
        access_token = create_access_token(user_id=user.user_id)
        response = await client.post(
            f"/api/v1/images/{image.image_id}/ml-tag-suggestions/review",
            json={
                "suggestions": [{"suggestion_id": suggestion.suggestion_id, "action": "approve"}]
            },
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

        suggestion = MlTagSuggestions(
            image_id=image.image_id,
            tag_id=tag.tag_id,
            confidence=0.85,
            model_version="v1",
            status="pending",
        )
        db_session.add(suggestion)
        await db_session.commit()

        # Reject suggestion
        access_token = create_access_token(user_id=user.user_id)
        response = await client.post(
            f"/api/v1/images/{image.image_id}/ml-tag-suggestions/review",
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

        sugg1 = MlTagSuggestions(
            image_id=image.image_id,
            tag_id=tag1.tag_id,
            confidence=0.92,
            model_version="v1",
            status="pending",
        )
        sugg2 = MlTagSuggestions(
            image_id=image.image_id,
            tag_id=tag2.tag_id,
            confidence=0.85,
            model_version="v1",
            status="pending",
        )
        sugg3 = MlTagSuggestions(
            image_id=image.image_id,
            tag_id=tag3.tag_id,
            confidence=0.75,
            model_version="v1",
            status="pending",
        )
        db_session.add_all([sugg1, sugg2, sugg3])
        await db_session.commit()

        # Batch review: approve 2, reject 1
        access_token = create_access_token(user_id=user.user_id)
        response = await client.post(
            f"/api/v1/images/{image.image_id}/ml-tag-suggestions/review",
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
            "/api/v1/images/123/ml-tag-suggestions/review",
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

        suggestion = MlTagSuggestions(
            image_id=image.image_id,
            tag_id=tag.tag_id,
            confidence=0.85,
            model_version="v1",
            status="pending",
        )
        db_session.add(suggestion)
        await db_session.commit()

        # Try to review as other_user
        access_token = create_access_token(user_id=other_user.user_id)
        response = await client.post(
            f"/api/v1/images/{image.image_id}/ml-tag-suggestions/review",
            json={
                "suggestions": [{"suggestion_id": suggestion.suggestion_id, "action": "approve"}]
            },
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
            "/api/v1/images/99999/ml-tag-suggestions/review",
            json={"suggestions": [{"suggestion_id": 1, "action": "approve"}]},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 404

    async def test_review_invalid_suggestion_id(
        self, client: AsyncClient, db_session: AsyncSession
    ):
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
            f"/api/v1/images/{image.image_id}/ml-tag-suggestions/review",
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
        suggestion = MlTagSuggestions(
            image_id=image2.image_id,
            tag_id=tag.tag_id,
            confidence=0.85,
            model_version="v1",
            status="pending",
        )
        db_session.add(suggestion)
        await db_session.commit()

        # Try to review it as if it belongs to image1
        access_token = create_access_token(user_id=user.user_id)
        response = await client.post(
            f"/api/v1/images/{image1.image_id}/ml-tag-suggestions/review",
            json={
                "suggestions": [{"suggestion_id": suggestion.suggestion_id, "action": "approve"}]
            },
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
        existing_link = TagLinks(image_id=image.image_id, tag_id=tag.tag_id, user_id=user.user_id)
        db_session.add(existing_link)
        await db_session.flush()

        # Create suggestion for same tag
        suggestion = MlTagSuggestions(
            image_id=image.image_id,
            tag_id=tag.tag_id,
            confidence=0.92,
            model_version="v1",
            status="pending",
        )
        db_session.add(suggestion)
        await db_session.commit()

        # Approve suggestion
        access_token = create_access_token(user_id=user.user_id)
        response = await client.post(
            f"/api/v1/images/{image.image_id}/ml-tag-suggestions/review",
            json={
                "suggestions": [{"suggestion_id": suggestion.suggestion_id, "action": "approve"}]
            },
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


@pytest.mark.api
class TestGenerateMlTagSuggestions:
    """Tests for POST /api/v1/images/{image_id}/ml-tag-suggestions/generate endpoint."""

    async def test_generate_suggestions_disabled_returns_503(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        """Test that the generate endpoint is unavailable when the flag is off."""
        monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", False)

        user = Users(
            username="test_generate_disabled",
            email="test_generate_disabled@example.com",
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
            md5_hash="disabled123",
            filesize=1024,
            width=800,
            height=600,
        )
        db_session.add(image)
        await db_session.commit()

        access_token = create_access_token(user_id=user.user_id)
        response = await client.post(
            f"/api/v1/images/{image.image_id}/ml-tag-suggestions/generate",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 503
        assert response.json()["detail"] == "ML tag suggestions are disabled"

    async def test_generate_suggestions_as_owner(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        """Test that image owner can trigger async suggestion generation."""
        monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)

        # Create test data
        user = Users(
            username="test_generate_owner",
            email="test_generate_owner@example.com",
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
            md5_hash="generate123",
            filesize=1024,
            width=800,
            height=600,
        )
        db_session.add(image)
        await db_session.commit()

        # Mock enqueue_job (AsyncMock — enqueue_job is awaited in the handler)
        with patch(f"{ROUTER}.enqueue_job", new_callable=AsyncMock) as mock_enqueue:
            mock_enqueue.return_value = "test-job-id"

            access_token = create_access_token(user_id=user.user_id)
            response = await client.post(
                f"/api/v1/images/{image.image_id}/ml-tag-suggestions/generate",
                headers={"Authorization": f"Bearer {access_token}"},
            )

            assert response.status_code == 202
            data = response.json()
            assert data["message"] == "Tag suggestion generation queued"
            assert data["image_id"] == image.image_id
            assert data["job_id"] == "test-job-id"

            # Verify enqueue_job was called with the correct job name and args
            mock_enqueue.assert_called_once_with(
                "generate_ml_tag_suggestions", image_id=image.image_id
            )

    async def test_generate_suggestions_sync(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch, tmp_path
    ):
        """Test that sync mode runs the pipeline inline and returns the count."""
        monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))

        user = Users(
            username="test_generate_sync",
            email="test_generate_sync@example.com",
            password="hashed",
            password_type="bcrypt",
            salt="testsalt12345678",
            active=1,
        )
        db_session.add(user)
        await db_session.flush()

        # Tags the resolver will map predictions onto.
        tag1 = Tags(tag_id=9046, title="long hair", type=1, user_id=user.user_id)
        tag2 = Tags(tag_id=9161, title="blue eyes", type=1, user_id=user.user_id)
        db_session.add_all([tag1, tag2])
        await db_session.flush()

        image = Images(
            filename="2024-01-01-sync",
            ext="jpg",
            user_id=user.user_id,
            md5_hash="sync123",
            filesize=1024,
            width=800,
            height=600,
        )
        db_session.add(image)
        await db_session.commit()

        # Create the local fullsize file the pipeline checks for.
        fake_image = tmp_path / "fullsize" / f"{image.filename}.{image.ext}"
        fake_image.parent.mkdir(parents=True, exist_ok=True)
        fake_image.write_bytes(b"fake image data")

        fake_service = FakeMLService(
            [{"external_tag": "long_hair", "confidence": 0.9, "model_version": "v1"}]
        )
        mapped = [
            {"tag_id": tag1.tag_id, "confidence": 0.92, "model_version": "v1"},
            {"tag_id": tag2.tag_id, "confidence": 0.88, "model_version": "v1"},
        ]

        access_token = create_access_token(user_id=user.user_id)
        with (
            patch(f"{ROUTER}._get_ml_service", AsyncMock(return_value=fake_service)),
            patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(mapped)),
            patch(f"{PIPELINE}.resolve_tag_relationships", _passthrough_resolver),
        ):
            response = await client.post(
                f"/api/v1/images/{image.image_id}/ml-tag-suggestions/generate?sync=true",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Tag suggestions generated"
        assert data["image_id"] == image.image_id
        assert data["suggestions_created"] == 2

        # Verify suggestions were actually written to the DB.
        result = await db_session.execute(
            select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
        )
        suggestions = result.scalars().all()
        assert len(suggestions) == 2
        assert {s.tag_id for s in suggestions} == {tag1.tag_id, tag2.tag_id}
        assert all(s.status == "pending" for s in suggestions)

    async def test_generate_suggestions_sync_missing_file_returns_404(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch, tmp_path
    ):
        """Test that sync mode returns 404 when the image file is missing."""
        monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))

        user = Users(
            username="test_generate_nofile",
            email="test_generate_nofile@example.com",
            password="hashed",
            password_type="bcrypt",
            salt="testsalt12345678",
            active=1,
        )
        db_session.add(user)
        await db_session.flush()

        image = Images(
            filename="2024-01-01-nofile",
            ext="jpg",
            user_id=user.user_id,
            md5_hash="nofile123",
            filesize=1024,
            width=800,
            height=600,
        )
        db_session.add(image)
        await db_session.commit()

        # Deliberately do NOT create the fullsize file.
        fake_service = FakeMLService([])

        access_token = create_access_token(user_id=user.user_id)
        with patch(f"{ROUTER}._get_ml_service", AsyncMock(return_value=fake_service)):
            response = await client.post(
                f"/api/v1/images/{image.image_id}/ml-tag-suggestions/generate?sync=true",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    async def test_generate_suggestions_requires_auth(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that authentication is required."""
        response = await client.post("/api/v1/images/1/ml-tag-suggestions/generate")
        assert response.status_code == 401

    async def test_generate_suggestions_image_not_found(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        """Test that 404 is returned for non-existent image."""
        monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)

        user = Users(
            username="test_generate_404",
            email="test_generate_404@example.com",
            password="hashed",
            password_type="bcrypt",
            salt="testsalt12345678",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()

        access_token = create_access_token(user_id=user.user_id)
        response = await client.post(
            "/api/v1/images/99999/ml-tag-suggestions/generate",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Image not found"

    async def test_generate_suggestions_permission_denied(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        """Test that non-owner without permission cannot trigger generation."""
        monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)

        # Create owner and another user
        owner = Users(
            username="test_generate_owner2",
            email="test_generate_owner2@example.com",
            password="hashed",
            password_type="bcrypt",
            salt="testsalt12345678",
            active=1,
        )
        db_session.add(owner)
        await db_session.flush()

        other_user = Users(
            username="test_generate_other",
            email="test_generate_other@example.com",
            password="hashed",
            password_type="bcrypt",
            salt="testsalt87654321",
            active=1,
        )
        db_session.add(other_user)
        await db_session.flush()

        image = Images(
            filename="test",
            ext="jpg",
            user_id=owner.user_id,
            md5_hash="generate456",
            filesize=1024,
            width=800,
            height=600,
        )
        db_session.add(image)
        await db_session.commit()

        # Try to generate as non-owner
        access_token = create_access_token(user_id=other_user.user_id)
        response = await client.post(
            f"/api/v1/images/{image.image_id}/ml-tag-suggestions/generate",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 403
        assert "your own images" in response.json()["detail"]
