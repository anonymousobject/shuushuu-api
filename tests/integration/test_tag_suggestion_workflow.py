"""
Integration tests for tag suggestion workflow.

Tests the complete end-to-end flow:
1. Upload an image (simulated)
2. Generate tag suggestions via background job
3. GET suggestions via API
4. Review (approve/reject) suggestions via API
5. Verify TagLinks are created on approval
"""

from contextlib import asynccontextmanager
from pathlib import Path as FilePath
from unittest.mock import AsyncMock, MagicMock, patch

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
from app.tasks.tag_suggestion_job import generate_tag_suggestions


@asynccontextmanager
async def mock_get_async_session(db_session):
    """Mock get_async_session to return the test database session"""
    yield db_session


@pytest.mark.integration
class TestTagSuggestionWorkflow:
    """End-to-end integration tests for tag suggestion system."""

    async def test_complete_workflow_upload_to_approval(
        self, client: AsyncClient, db_session: AsyncSession, tmp_path
    ):
        """
        Test complete workflow from upload to tag approval.

        Flow:
        1. Create image (simulates upload)
        2. Generate suggestions via background job
        3. GET suggestions via API
        4. Approve suggestions via API
        5. Verify TagLinks created
        """
        # Step 1: Create test data (simulate image upload)
        user = Users(
            username="workflow_user",
            email="workflow@example.com",
            password="hashed",
            password_type="bcrypt",
            salt="testsalt12345678",
            active=1,
        )
        db_session.add(user)
        await db_session.flush()

        image = Images(
            filename="workflow-test",
            ext="jpg",
            user_id=user.user_id,
            md5_hash="workflow123abc",
            filesize=1024,
            width=800,
            height=600,
        )
        db_session.add(image)
        await db_session.flush()

        # Create tags that will be suggested
        tag1 = Tags(tag_id=46, tag="long_hair", type=1, user_id=user.user_id)
        tag2 = Tags(tag_id=161, tag="short_hair", type=1, user_id=user.user_id)
        tag3 = Tags(tag_id=25, tag="blush", type=1, user_id=user.user_id)
        db_session.add_all([tag1, tag2, tag3])
        await db_session.commit()

        # Step 2: Generate suggestions via background job
        # Mock ML service predictions
        mock_predictions = [
            {"tag_id": 46, "confidence": 0.92, "model_source": "custom_theme", "model_version": "v1"},
            {"tag_id": 161, "confidence": 0.88, "model_source": "custom_theme", "model_version": "v1"},
            {"tag_id": 25, "confidence": 0.85, "model_source": "danbooru", "model_version": "v1"},
        ]

        mock_ml_service = MagicMock()
        mock_ml_service.generate_suggestions = AsyncMock(return_value=mock_predictions)

        # Mock tag mapping and resolver (pass through for this test)
        async def mock_tag_mapping(db, suggestions):
            return suggestions

        async def mock_resolver(db, suggestions):
            return suggestions

        # Create fake image file
        fake_image = tmp_path / "fullsize" / "workflow-test.jpg"
        fake_image.parent.mkdir(parents=True, exist_ok=True)
        fake_image.write_bytes(b"fake image data")

        # Execute background job
        with (
            patch("app.tasks.tag_suggestion_job.resolve_external_tags", mock_tag_mapping),
            patch("app.tasks.tag_suggestion_job.resolve_tag_relationships", mock_resolver),
            patch("app.tasks.tag_suggestion_job.settings") as mock_settings,
            patch(
                "app.tasks.tag_suggestion_job.get_async_session",
                lambda: mock_get_async_session(db_session),
            ),
        ):
            mock_settings.STORAGE_PATH = str(tmp_path)
            ctx = {"ml_service": mock_ml_service}
            job_result = await generate_tag_suggestions(ctx, image.image_id)

        # Verify job succeeded
        assert job_result["status"] == "completed"
        assert job_result["suggestions_created"] == 3

        # Step 3: GET suggestions via API
        access_token = create_access_token(user_id=user.user_id)
        response = await client.get(
            f"/api/v1/images/{image.image_id}/tag-suggestions",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["image_id"] == image.image_id
        assert len(data["suggestions"]) == 3
        assert data["total"] == 3
        assert data["pending"] == 3
        assert data["approved"] == 0
        assert data["rejected"] == 0

        # Verify suggestions are sorted by confidence
        assert data["suggestions"][0]["confidence"] == 0.92
        assert data["suggestions"][1]["confidence"] == 0.88
        assert data["suggestions"][2]["confidence"] == 0.85

        # Step 4: Approve suggestions via API
        suggestion_ids = [s["suggestion_id"] for s in data["suggestions"]]
        response = await client.post(
            f"/api/v1/images/{image.image_id}/tag-suggestions/review",
            json={
                "suggestions": [
                    {"suggestion_id": suggestion_ids[0], "action": "approve"},
                    {"suggestion_id": suggestion_ids[1], "action": "approve"},
                    {"suggestion_id": suggestion_ids[2], "action": "approve"},
                ]
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 200
        review_data = response.json()
        assert review_data["approved"] == 3
        assert review_data["rejected"] == 0
        assert review_data["errors"] == []

        # Step 5: Verify TagLinks were created
        result = await db_session.execute(
            select(TagLinks).where(TagLinks.image_id == image.image_id)
        )
        tag_links = result.scalars().all()
        assert len(tag_links) == 3

        # Verify all expected tags are linked
        linked_tag_ids = {link.tag_id for link in tag_links}
        assert linked_tag_ids == {46, 161, 25}

        # Verify all links have correct user_id
        assert all(link.user_id == user.user_id for link in tag_links)

        # Verify suggestions are marked as approved
        result = await db_session.execute(
            select(TagSuggestion).where(TagSuggestion.image_id == image.image_id)
        )
        suggestions = result.scalars().all()
        assert all(s.status == "approved" for s in suggestions)
        assert all(s.reviewed_by_user_id == user.user_id for s in suggestions)
        assert all(s.reviewed_at is not None for s in suggestions)

    async def test_workflow_with_rejection(
        self, client: AsyncClient, db_session: AsyncSession, tmp_path
    ):
        """
        Test workflow with rejected suggestions.

        Verifies that rejected suggestions don't create TagLinks.
        """
        # Create test data
        user = Users(
            username="reject_user",
            email="reject@example.com",
            password="hashed",
            password_type="bcrypt",
            salt="testsalt12345678",
            active=1,
        )
        db_session.add(user)
        await db_session.flush()

        image = Images(
            filename="reject-test",
            ext="jpg",
            user_id=user.user_id,
            md5_hash="reject123abc",
            filesize=1024,
            width=800,
            height=600,
        )
        db_session.add(image)
        await db_session.flush()

        tag1 = Tags(tag_id=46, tag="long_hair", type=1, user_id=user.user_id)
        tag2 = Tags(tag_id=161, tag="short_hair", type=1, user_id=user.user_id)
        db_session.add_all([tag1, tag2])
        await db_session.commit()

        # Generate suggestions
        mock_predictions = [
            {"tag_id": 46, "confidence": 0.92, "model_source": "custom_theme", "model_version": "v1"},
            {"tag_id": 161, "confidence": 0.88, "model_source": "custom_theme", "model_version": "v1"},
        ]

        mock_ml_service = MagicMock()
        mock_ml_service.generate_suggestions = AsyncMock(return_value=mock_predictions)

        async def mock_tag_mapping(db, suggestions):
            return suggestions

        async def mock_resolver(db, suggestions):
            return suggestions

        fake_image = tmp_path / "fullsize" / "reject-test.jpg"
        fake_image.parent.mkdir(parents=True, exist_ok=True)
        fake_image.write_bytes(b"fake image data")

        with (
            patch("app.tasks.tag_suggestion_job.resolve_external_tags", mock_tag_mapping),
            patch("app.tasks.tag_suggestion_job.resolve_tag_relationships", mock_resolver),
            patch("app.tasks.tag_suggestion_job.settings") as mock_settings,
            patch(
                "app.tasks.tag_suggestion_job.get_async_session",
                lambda: mock_get_async_session(db_session),
            ),
        ):
            mock_settings.STORAGE_PATH = str(tmp_path)
            ctx = {"ml_service": mock_ml_service}
            await generate_tag_suggestions(ctx, image.image_id)

        # GET suggestions
        access_token = create_access_token(user_id=user.user_id)
        response = await client.get(
            f"/api/v1/images/{image.image_id}/tag-suggestions",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        data = response.json()
        suggestion_ids = [s["suggestion_id"] for s in data["suggestions"]]

        # Reject all suggestions
        response = await client.post(
            f"/api/v1/images/{image.image_id}/tag-suggestions/review",
            json={
                "suggestions": [
                    {"suggestion_id": suggestion_ids[0], "action": "reject"},
                    {"suggestion_id": suggestion_ids[1], "action": "reject"},
                ]
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 200
        review_data = response.json()
        assert review_data["approved"] == 0
        assert review_data["rejected"] == 2

        # Verify NO TagLinks were created
        result = await db_session.execute(
            select(TagLinks).where(TagLinks.image_id == image.image_id)
        )
        tag_links = result.scalars().all()
        assert len(tag_links) == 0

        # Verify suggestions are marked as rejected
        result = await db_session.execute(
            select(TagSuggestion).where(TagSuggestion.image_id == image.image_id)
        )
        suggestions = result.scalars().all()
        assert all(s.status == "rejected" for s in suggestions)

    async def test_workflow_with_mixed_approval_rejection(
        self, client: AsyncClient, db_session: AsyncSession, tmp_path
    ):
        """
        Test workflow with both approvals and rejections.

        Verifies that only approved suggestions create TagLinks.
        """
        # Create test data
        user = Users(
            username="mixed_user",
            email="mixed@example.com",
            password="hashed",
            password_type="bcrypt",
            salt="testsalt12345678",
            active=1,
        )
        db_session.add(user)
        await db_session.flush()

        image = Images(
            filename="mixed-test",
            ext="jpg",
            user_id=user.user_id,
            md5_hash="mixed123abc",
            filesize=1024,
            width=800,
            height=600,
        )
        db_session.add(image)
        await db_session.flush()

        tag1 = Tags(tag_id=46, tag="long_hair", type=1, user_id=user.user_id)
        tag2 = Tags(tag_id=161, tag="short_hair", type=1, user_id=user.user_id)
        tag3 = Tags(tag_id=25, tag="blush", type=1, user_id=user.user_id)
        db_session.add_all([tag1, tag2, tag3])
        await db_session.commit()

        # Generate suggestions
        mock_predictions = [
            {"tag_id": 46, "confidence": 0.92, "model_source": "custom_theme", "model_version": "v1"},
            {"tag_id": 161, "confidence": 0.88, "model_source": "custom_theme", "model_version": "v1"},
            {"tag_id": 25, "confidence": 0.85, "model_source": "danbooru", "model_version": "v1"},
        ]

        mock_ml_service = MagicMock()
        mock_ml_service.generate_suggestions = AsyncMock(return_value=mock_predictions)

        async def mock_tag_mapping(db, suggestions):
            return suggestions

        async def mock_resolver(db, suggestions):
            return suggestions

        fake_image = tmp_path / "fullsize" / "mixed-test.jpg"
        fake_image.parent.mkdir(parents=True, exist_ok=True)
        fake_image.write_bytes(b"fake image data")

        with (
            patch("app.tasks.tag_suggestion_job.resolve_external_tags", mock_tag_mapping),
            patch("app.tasks.tag_suggestion_job.resolve_tag_relationships", mock_resolver),
            patch("app.tasks.tag_suggestion_job.settings") as mock_settings,
            patch(
                "app.tasks.tag_suggestion_job.get_async_session",
                lambda: mock_get_async_session(db_session),
            ),
        ):
            mock_settings.STORAGE_PATH = str(tmp_path)
            ctx = {"ml_service": mock_ml_service}
            await generate_tag_suggestions(ctx, image.image_id)

        # GET suggestions
        access_token = create_access_token(user_id=user.user_id)
        response = await client.get(
            f"/api/v1/images/{image.image_id}/tag-suggestions",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        data = response.json()
        suggestion_ids = [s["suggestion_id"] for s in data["suggestions"]]

        # Approve 2, reject 1
        response = await client.post(
            f"/api/v1/images/{image.image_id}/tag-suggestions/review",
            json={
                "suggestions": [
                    {"suggestion_id": suggestion_ids[0], "action": "approve"},
                    {"suggestion_id": suggestion_ids[1], "action": "approve"},
                    {"suggestion_id": suggestion_ids[2], "action": "reject"},
                ]
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 200
        review_data = response.json()
        assert review_data["approved"] == 2
        assert review_data["rejected"] == 1

        # Verify only 2 TagLinks were created
        result = await db_session.execute(
            select(TagLinks).where(TagLinks.image_id == image.image_id)
        )
        tag_links = result.scalars().all()
        assert len(tag_links) == 2

        linked_tag_ids = {link.tag_id for link in tag_links}
        assert linked_tag_ids == {46, 161}
        assert 25 not in linked_tag_ids

    async def test_workflow_permission_non_owner_cannot_review(
        self, client: AsyncClient, db_session: AsyncSession, tmp_path
    ):
        """
        Test that non-owner cannot review suggestions.

        Verifies permissions are enforced in the workflow.
        """
        # Create owner and other user
        owner = Users(
            username="owner_perm",
            email="owner_perm@example.com",
            password="hashed",
            password_type="bcrypt",
            salt="testsalt12345678",
            active=1,
        )
        db_session.add(owner)
        await db_session.flush()

        other_user = Users(
            username="other_perm",
            email="other_perm@example.com",
            password="hashed",
            password_type="bcrypt",
            salt="testsalt87654321",
            active=1,
        )
        db_session.add(other_user)
        await db_session.flush()

        image = Images(
            filename="perm-test",
            ext="jpg",
            user_id=owner.user_id,
            md5_hash="perm123abc",
            filesize=1024,
            width=800,
            height=600,
        )
        db_session.add(image)
        await db_session.flush()

        tag = Tags(tag_id=46, tag="long_hair", type=1, user_id=owner.user_id)
        db_session.add(tag)
        await db_session.commit()

        # Generate suggestions
        mock_predictions = [
            {"tag_id": 46, "confidence": 0.92, "model_source": "custom_theme", "model_version": "v1"},
        ]

        mock_ml_service = MagicMock()
        mock_ml_service.generate_suggestions = AsyncMock(return_value=mock_predictions)

        async def mock_tag_mapping(db, suggestions):
            return suggestions

        async def mock_resolver(db, suggestions):
            return suggestions

        fake_image = tmp_path / "fullsize" / "perm-test.jpg"
        fake_image.parent.mkdir(parents=True, exist_ok=True)
        fake_image.write_bytes(b"fake image data")

        with (
            patch("app.tasks.tag_suggestion_job.resolve_external_tags", mock_tag_mapping),
            patch("app.tasks.tag_suggestion_job.resolve_tag_relationships", mock_resolver),
            patch("app.tasks.tag_suggestion_job.settings") as mock_settings,
            patch(
                "app.tasks.tag_suggestion_job.get_async_session",
                lambda: mock_get_async_session(db_session),
            ),
        ):
            mock_settings.STORAGE_PATH = str(tmp_path)
            ctx = {"ml_service": mock_ml_service}
            await generate_tag_suggestions(ctx, image.image_id)

        # Try to GET suggestions as other_user (should fail)
        other_token = create_access_token(user_id=other_user.user_id)
        response = await client.get(
            f"/api/v1/images/{image.image_id}/tag-suggestions",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert response.status_code == 403

        # Try to review as other_user (should fail)
        result = await db_session.execute(
            select(TagSuggestion).where(TagSuggestion.image_id == image.image_id)
        )
        suggestion = result.scalar_one()

        response = await client.post(
            f"/api/v1/images/{image.image_id}/tag-suggestions/review",
            json={"suggestions": [{"suggestion_id": suggestion.suggestion_id, "action": "approve"}]},
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert response.status_code == 403

    async def test_workflow_job_error_missing_image_file(
        self, client: AsyncClient, db_session: AsyncSession, tmp_path
    ):
        """
        Test workflow when image file is missing.

        Verifies that job handles errors gracefully.
        """
        # Create test data
        user = Users(
            username="error_user",
            email="error@example.com",
            password="hashed",
            password_type="bcrypt",
            salt="testsalt12345678",
            active=1,
        )
        db_session.add(user)
        await db_session.flush()

        image = Images(
            filename="missing-test",
            ext="jpg",
            user_id=user.user_id,
            md5_hash="missing123abc",
            filesize=1024,
            width=800,
            height=600,
        )
        db_session.add(image)
        await db_session.commit()

        # Don't create the image file (simulate missing file)

        mock_ml_service = MagicMock()
        mock_ml_service.generate_suggestions = AsyncMock()

        # Execute background job (should fail gracefully)
        with (
            patch("app.tasks.tag_suggestion_job.settings") as mock_settings,
            patch(
                "app.tasks.tag_suggestion_job.get_async_session",
                lambda: mock_get_async_session(db_session),
            ),
        ):
            mock_settings.STORAGE_PATH = str(tmp_path)
            ctx = {"ml_service": mock_ml_service}
            job_result = await generate_tag_suggestions(ctx, image.image_id)

        # Verify job returned error status
        assert job_result["status"] == "error"
        assert "not found" in job_result["error"].lower()

        # Verify no suggestions were created
        result = await db_session.execute(
            select(TagSuggestion).where(TagSuggestion.image_id == image.image_id)
        )
        suggestions = result.scalars().all()
        assert len(suggestions) == 0

        # ML service should not have been called
        mock_ml_service.generate_suggestions.assert_not_called()

    async def test_workflow_job_error_ml_service_failure(
        self, client: AsyncClient, db_session: AsyncSession, tmp_path
    ):
        """
        Test workflow when ML service fails.

        Verifies that job handles ML errors gracefully.
        """
        # Create test data
        user = Users(
            username="ml_error_user",
            email="ml_error@example.com",
            password="hashed",
            password_type="bcrypt",
            salt="testsalt12345678",
            active=1,
        )
        db_session.add(user)
        await db_session.flush()

        image = Images(
            filename="ml-error-test",
            ext="jpg",
            user_id=user.user_id,
            md5_hash="mlerror123abc",
            filesize=1024,
            width=800,
            height=600,
        )
        db_session.add(image)
        await db_session.commit()

        # Mock ML service that raises an error
        mock_ml_service = MagicMock()
        mock_ml_service.generate_suggestions = AsyncMock(
            side_effect=RuntimeError("Model inference failed")
        )

        # Create fake image file
        fake_image = tmp_path / "fullsize" / "ml-error-test.jpg"
        fake_image.parent.mkdir(parents=True, exist_ok=True)
        fake_image.write_bytes(b"fake image data")

        # Execute background job (should handle error gracefully)
        with (
            patch("app.tasks.tag_suggestion_job.settings") as mock_settings,
            patch(
                "app.tasks.tag_suggestion_job.get_async_session",
                lambda: mock_get_async_session(db_session),
            ),
        ):
            mock_settings.STORAGE_PATH = str(tmp_path)
            ctx = {"ml_service": mock_ml_service}
            job_result = await generate_tag_suggestions(ctx, image.image_id)

        # Verify job returned error status
        assert job_result["status"] == "error"
        assert "Model inference failed" in job_result["error"]

        # Verify no suggestions were created
        result = await db_session.execute(
            select(TagSuggestion).where(TagSuggestion.image_id == image.image_id)
        )
        suggestions = result.scalars().all()
        assert len(suggestions) == 0

    async def test_workflow_skips_existing_tags(
        self, client: AsyncClient, db_session: AsyncSession, tmp_path
    ):
        """
        Test workflow skips tags already applied to image.

        Verifies that suggestions are not created for existing TagLinks.
        """
        # Create test data
        user = Users(
            username="existing_user",
            email="existing@example.com",
            password="hashed",
            password_type="bcrypt",
            salt="testsalt12345678",
            active=1,
        )
        db_session.add(user)
        await db_session.flush()

        image = Images(
            filename="existing-test",
            ext="jpg",
            user_id=user.user_id,
            md5_hash="existing123abc",
            filesize=1024,
            width=800,
            height=600,
        )
        db_session.add(image)
        await db_session.flush()

        tag1 = Tags(tag_id=46, tag="long_hair", type=1, user_id=user.user_id)
        tag2 = Tags(tag_id=161, tag="short_hair", type=1, user_id=user.user_id)
        tag3 = Tags(tag_id=25, tag="blush", type=1, user_id=user.user_id)
        db_session.add_all([tag1, tag2, tag3])
        await db_session.flush()

        # Create existing TagLink for tag1
        existing_link = TagLinks(image_id=image.image_id, tag_id=46, user_id=user.user_id)
        db_session.add(existing_link)
        await db_session.commit()

        # Generate suggestions (includes tag1 which is already linked)
        mock_predictions = [
            {"tag_id": 46, "confidence": 0.92, "model_source": "custom_theme", "model_version": "v1"},
            {"tag_id": 161, "confidence": 0.88, "model_source": "custom_theme", "model_version": "v1"},
            {"tag_id": 25, "confidence": 0.85, "model_source": "danbooru", "model_version": "v1"},
        ]

        mock_ml_service = MagicMock()
        mock_ml_service.generate_suggestions = AsyncMock(return_value=mock_predictions)

        async def mock_tag_mapping(db, suggestions):
            return suggestions

        async def mock_resolver(db, suggestions):
            return suggestions

        fake_image = tmp_path / "fullsize" / "existing-test.jpg"
        fake_image.parent.mkdir(parents=True, exist_ok=True)
        fake_image.write_bytes(b"fake image data")

        with (
            patch("app.tasks.tag_suggestion_job.resolve_external_tags", mock_tag_mapping),
            patch("app.tasks.tag_suggestion_job.resolve_tag_relationships", mock_resolver),
            patch("app.tasks.tag_suggestion_job.settings") as mock_settings,
            patch(
                "app.tasks.tag_suggestion_job.get_async_session",
                lambda: mock_get_async_session(db_session),
            ),
        ):
            mock_settings.STORAGE_PATH = str(tmp_path)
            ctx = {"ml_service": mock_ml_service}
            job_result = await generate_tag_suggestions(ctx, image.image_id)

        # Verify only 2 suggestions created (tag1 was skipped)
        assert job_result["status"] == "completed"
        assert job_result["suggestions_created"] == 2

        # Verify suggestions don't include tag1
        result = await db_session.execute(
            select(TagSuggestion).where(TagSuggestion.image_id == image.image_id)
        )
        suggestions = result.scalars().all()
        assert len(suggestions) == 2
        suggested_tag_ids = {s.tag_id for s in suggestions}
        assert suggested_tag_ids == {161, 25}
        assert 46 not in suggested_tag_ids

        # Verify GET API returns only the 2 suggestions
        access_token = create_access_token(user_id=user.user_id)
        response = await client.get(
            f"/api/v1/images/{image.image_id}/tag-suggestions",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        data = response.json()
        assert len(data["suggestions"]) == 2
        assert data["total"] == 2
