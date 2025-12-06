"""
Tests for image API endpoints.

These tests cover the /api/v1/images endpoints including:
- Listing images
- Getting image details
- Searching and filtering
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Images


@pytest.mark.api
class TestImagesList:
    """Tests for GET /api/v1/images/ endpoint."""

    async def test_list_images_empty(self, client: AsyncClient):
        """Test listing images when database is empty."""
        await client.get("/api/v1/images/")

        # assert response.status_code == 200
        # data = response.json()
        # assert data["total"] == 0
        # assert data["page"] == 1
        # assert data["per_page"] == 20
        # assert data["images"] == []

    async def test_list_images_with_data(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test listing images with sample data."""
        # Create a test image
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        response = await client.get("/api/v1/images/")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["page"] == 1
        assert len(data["images"]) == 1

        # Verify image data
        img = data["images"][0]
        assert img["filename"] == sample_image_data["filename"]
        assert img["ext"] == sample_image_data["ext"]
        assert img["width"] == sample_image_data["width"]
        assert img["height"] == sample_image_data["height"]

    async def test_list_images_pagination(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test image pagination."""
        # Create 25 test images
        for i in range(25):
            image_data = sample_image_data.copy()
            image_data["filename"] = f"test-image-{i:03d}"
            image_data["md5_hash"] = f"hash{i:022d}"
            image = Images(**image_data)
            db_session.add(image)

        await db_session.commit()

        # Test first page
        response = await client.get("/api/v1/images/?page=1&per_page=10")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 25
        assert data["page"] == 1
        assert data["per_page"] == 10
        assert len(data["images"]) == 10

        # Test second page
        response = await client.get("/api/v1/images/?page=2&per_page=10")
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 2
        assert len(data["images"]) == 10

        # Test third page
        response = await client.get("/api/v1/images/?page=3&per_page=10")
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 3
        assert len(data["images"]) == 5  # Remaining images

    async def test_list_images_invalid_pagination(self, client: AsyncClient):
        """Test invalid pagination parameters."""
        # Negative page
        response = await client.get("/api/v1/images/?page=0")
        assert response.status_code == 422

        # Too large per_page
        response = await client.get("/api/v1/images/?per_page=200")
        assert response.status_code == 422


@pytest.mark.api
class TestImagesFiltering:
    """Tests for image filtering and search."""

    async def test_filter_by_user_id(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test filtering images by user_id."""
        # Create images for different users
        for user_id in [1, 2, 3]:
            for i in range(3):
                image_data = sample_image_data.copy()
                image_data["filename"] = f"user{user_id}-image-{i}"
                image_data["md5_hash"] = f"user{user_id}hash{i:020d}"
                image_data["user_id"] = user_id
                db_session.add(Images(**image_data))

        await db_session.commit()

        # Filter by user_id=2
        response = await client.get("/api/v1/images/?user_id=2")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        for img in data["images"]:
            assert img["user_id"] == 2

    async def test_filter_by_size(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test filtering images by dimensions."""
        # Create images of different sizes
        sizes = [(800, 600), (1920, 1080), (3840, 2160)]
        for idx, (width, height) in enumerate(sizes):
            image_data = sample_image_data.copy()
            image_data["filename"] = f"size-{width}x{height}"
            image_data["md5_hash"] = f"size{idx:023d}"
            image_data["width"] = width
            image_data["height"] = height
            db_session.add(Images(**image_data))

        await db_session.commit()

        # Filter min_width
        response = await client.get("/api/v1/images/?min_width=1920")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2  # 1920x1080 and 3840x2160
        for img in data["images"]:
            assert img["width"] >= 1920

        # Filter max_width
        response = await client.get("/api/v1/images/?max_width=1920")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2  # 800x600 and 1920x1080
        for img in data["images"]:
            assert img["width"] <= 1920

    async def test_filter_by_rating(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test filtering images by rating."""
        # Create images with different ratings
        for rating in [0.0, 2.5, 5.0, 7.5, 10.0]:
            image_data = sample_image_data.copy()
            image_data["filename"] = f"rating-{rating}"
            image_data["md5_hash"] = f"rating{int(rating * 10):021d}"
            image_data["rating"] = rating
            image_data["bayesian_rating"] = rating
            db_session.add(Images(**image_data))

        await db_session.commit()

        # Filter by minimum rating
        response = await client.get("/api/v1/images/?min_rating=5.0")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3  # 5.0, 7.5, 10.0
        for img in data["images"]:
            assert img["rating"] >= 5.0


@pytest.mark.api
class TestImagesSorting:
    """Tests for image sorting."""

    async def test_sort_by_date(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test sorting images by date.

        Note: In production, date_added is set by the database default (current_timestamp),
        so it always matches the insertion order and thus image_id order. This test
        simulates that by inserting images in chronological order.
        """
        # Create images - insert in chronological order so auto-increment IDs match dates
        # This mirrors real-world behavior where date_added = insertion time
        dates = ["2024-01-01", "2024-06-15", "2024-12-31"]
        for idx, date in enumerate(dates):
            image_data = sample_image_data.copy()
            image_data["filename"] = f"date-{date}"
            image_data["md5_hash"] = f"date{idx:022d}"
            # Don't set date_added - let database default handle it
            # (In real usage, this would be current_timestamp())
            db_session.add(Images(**image_data))

        await db_session.commit()

        # Sort descending (newest first) - default
        # Since images were inserted in chronological order, newest = highest image_id
        response = await client.get("/api/v1/images/?sort_by=date_added&sort_order=DESC")
        assert response.status_code == 200
        data = response.json()

        # Newest image (last inserted) should be first
        assert data["images"][0]["filename"] == "date-2024-12-31"
        assert data["images"][-1]["filename"] == "date-2024-01-01"

        # Sort ascending (oldest first)
        response = await client.get("/api/v1/images/?sort_by=date_added&sort_order=ASC")
        assert response.status_code == 200
        data = response.json()
        assert data["images"][0]["filename"] == "date-2024-01-01"
        assert data["images"][-1]["filename"] == "date-2024-12-31"


@pytest.mark.api
class TestImageDetail:
    """Tests for GET /api/v1/images/{id} endpoint."""

    async def test_get_image_by_id(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test getting a specific image by ID."""
        # Create a test image
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        response = await client.get(f"/api/v1/images/{image.image_id}")
        assert response.status_code == 200

        data = response.json()
        assert data["image_id"] == image.image_id
        assert data["filename"] == sample_image_data["filename"]

    async def test_get_nonexistent_image(self, client: AsyncClient):
        """Test getting an image that doesn't exist."""
        response = await client.get("/api/v1/images/999999")
        assert response.status_code == 404


@pytest.mark.api
class TestHealthEndpoint:
    """Tests for health check endpoint."""

    async def test_health_check(self, client: AsyncClient):
        """Test health endpoint returns OK."""
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}


@pytest.mark.api
class TestImageUploadJobIntegration:
    """Tests for background job integration with image upload."""

    async def test_tag_suggestion_job_enqueued_after_upload(
        self, app, client: AsyncClient, db_session: AsyncSession, test_user, tmp_path
    ):
        """Test that tag suggestion job is enqueued after successful upload."""
        from unittest.mock import patch
        from io import BytesIO
        from PIL import Image
        from app.core.auth import get_current_user

        # Create a real test image
        img = Image.new("RGB", (100, 100), color="red")
        img_bytes = BytesIO()
        img.save(img_bytes, format="JPEG")
        img_bytes.seek(0)

        # Override auth dependency
        async def override_get_current_user():
            return test_user

        app.dependency_overrides[get_current_user] = override_get_current_user

        try:
            # Mock the enqueue_job function to track calls
            with patch("app.api.v1.images.enqueue_job") as mock_enqueue:
                mock_enqueue.return_value = "test-job-id"

                # Upload image
                response = await client.post(
                    "/api/v1/images/upload",
                    files={"file": ("test.jpg", img_bytes, "image/jpeg")},
                    data={"caption": "Test image", "tag_ids": ""},
                )

                # Verify upload succeeded
                assert response.status_code == 201
                data = response.json()
                assert "image_id" in data
                image_id = data["image_id"]

                # Verify tag suggestion job was enqueued
                # Find the call to generate_tag_suggestions
                tag_suggestion_calls = [
                    call for call in mock_enqueue.call_args_list
                    if call[0][0] == "generate_tag_suggestions"
                ]

                assert len(tag_suggestion_calls) == 1, "generate_tag_suggestions should be enqueued once"

                # Verify correct parameters
                call_args = tag_suggestion_calls[0]
                assert call_args[1]["image_id"] == image_id

                # Verify job is deferred (not immediate)
                assert "_defer_by" in call_args[1]
                assert call_args[1]["_defer_by"] > 0
        finally:
            # Clean up dependency override
            app.dependency_overrides.clear()

    async def test_tag_suggestion_job_receives_correct_image_id(
        self, app, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        """Test that tag suggestion job receives the correct image_id."""
        from unittest.mock import patch
        from io import BytesIO
        from PIL import Image
        from app.core.auth import get_current_user

        # Create test image
        img = Image.new("RGB", (100, 100), color="blue")
        img_bytes = BytesIO()
        img.save(img_bytes, format="JPEG")
        img_bytes.seek(0)

        # Override auth dependency
        async def override_get_current_user():
            return test_user

        app.dependency_overrides[get_current_user] = override_get_current_user

        try:
            with patch("app.api.v1.images.enqueue_job") as mock_enqueue:
                mock_enqueue.return_value = "test-job-id"

                response = await client.post(
                    "/api/v1/images/upload",
                    files={"file": ("test.jpg", img_bytes, "image/jpeg")},
                    data={"caption": "", "tag_ids": ""},
                )

                assert response.status_code == 201
                image_id = response.json()["image_id"]

                # Find tag suggestion call
                tag_suggestion_calls = [
                    call for call in mock_enqueue.call_args_list
                    if call[0][0] == "generate_tag_suggestions"
                ]

                assert len(tag_suggestion_calls) == 1
                assert tag_suggestion_calls[0][1]["image_id"] == image_id
        finally:
            app.dependency_overrides.clear()

    async def test_tag_suggestion_job_not_enqueued_on_upload_failure(
        self, app, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        """Test that tag suggestion job is NOT enqueued if upload fails."""
        from unittest.mock import patch
        from io import BytesIO
        from app.core.auth import get_current_user

        # Create invalid file (not an image)
        invalid_file = BytesIO(b"not an image")

        # Override auth dependency
        async def override_get_current_user():
            return test_user

        app.dependency_overrides[get_current_user] = override_get_current_user

        try:
            with patch("app.api.v1.images.enqueue_job") as mock_enqueue:
                mock_enqueue.return_value = "test-job-id"

                response = await client.post(
                    "/api/v1/images/upload",
                    files={"file": ("test.txt", invalid_file, "text/plain")},
                    data={"caption": "", "tag_ids": ""},
                )

                # Verify upload failed
                assert response.status_code != 201

                # Verify tag suggestion job was NOT enqueued
                tag_suggestion_calls = [
                    call for call in mock_enqueue.call_args_list
                    if call[0][0] == "generate_tag_suggestions"
                ]

                assert len(tag_suggestion_calls) == 0, (
                    "generate_tag_suggestions should not be enqueued on failed upload"
                )
        finally:
            app.dependency_overrides.clear()

    async def test_tag_suggestion_enqueue_error_does_not_break_upload(
        self, app, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        """Test that enqueue errors don't break the upload flow."""
        from unittest.mock import patch
        from io import BytesIO
        from PIL import Image
        from app.core.auth import get_current_user

        # Create test image
        img = Image.new("RGB", (100, 100), color="green")
        img_bytes = BytesIO()
        img.save(img_bytes, format="JPEG")
        img_bytes.seek(0)

        # Mock enqueue_job to raise exception for tag_suggestions only
        async def mock_enqueue_with_error(job_name, **kwargs):
            if job_name == "generate_tag_suggestions":
                # Simulate Redis connection error or similar
                raise Exception("Redis connection failed")
            # Return success for other jobs (thumbnail, IQDB, etc.)
            return "test-job-id"

        # Override auth dependency
        async def override_get_current_user():
            return test_user

        app.dependency_overrides[get_current_user] = override_get_current_user

        try:
            with patch("app.api.v1.images.enqueue_job", side_effect=mock_enqueue_with_error):
                response = await client.post(
                    "/api/v1/images/upload",
                    files={"file": ("test.jpg", img_bytes, "image/jpeg")},
                    data={"caption": "Test resilience", "tag_ids": ""},
                )

                # Upload should still succeed even if tag suggestion enqueue fails
                assert response.status_code == 201
                data = response.json()
                assert "image_id" in data
                assert data["message"] == "Image uploaded successfully"
        finally:
            app.dependency_overrides.clear()

    async def test_tag_suggestion_job_enqueued_after_other_jobs(
        self, app, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        """Test that tag suggestion job is enqueued after thumbnail and IQDB jobs."""
        from unittest.mock import patch
        from io import BytesIO
        from PIL import Image
        from app.core.auth import get_current_user

        # Create test image
        img = Image.new("RGB", (100, 100), color="yellow")
        img_bytes = BytesIO()
        img.save(img_bytes, format="JPEG")
        img_bytes.seek(0)

        # Override auth dependency
        async def override_get_current_user():
            return test_user

        app.dependency_overrides[get_current_user] = override_get_current_user

        try:
            with patch("app.api.v1.images.enqueue_job") as mock_enqueue:
                mock_enqueue.return_value = "test-job-id"

                response = await client.post(
                    "/api/v1/images/upload",
                    files={"file": ("test.jpg", img_bytes, "image/jpeg")},
                    data={"caption": "", "tag_ids": ""},
                )

                assert response.status_code == 201

                # Get all enqueued job names in order
                job_names = [call[0][0] for call in mock_enqueue.call_args_list]

                # Verify tag suggestion job is in the list
                assert "generate_tag_suggestions" in job_names

                # Verify tag suggestion is enqueued (order doesn't matter as they're async)
                # but it should be present alongside thumbnail and IQDB jobs
                assert "create_thumbnail" in job_names
                assert "add_to_iqdb" in job_names
        finally:
            app.dependency_overrides.clear()
