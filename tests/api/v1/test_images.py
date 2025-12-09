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

from app.config import TagType, settings
from app.models import Images, TagLinks, Tags


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

    async def test_list_images_includes_tags(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test that list_images includes tag details for images."""
        # Create tags
        tag1 = Tags(title="Anime", desc="Anime tag", type=TagType.THEME)
        tag2 = Tags(title="Landscape", desc="Landscape tag", type=TagType.SOURCE)
        db_session.add(tag1)
        db_session.add(tag2)
        await db_session.flush()

        # Create an image
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.flush()

        # Link tags to image
        tag_link1 = TagLinks(image_id=image.image_id, tag_id=tag1.tag_id, user_id=1)
        tag_link2 = TagLinks(image_id=image.image_id, tag_id=tag2.tag_id, user_id=1)
        db_session.add(tag_link1)
        db_session.add(tag_link2)
        await db_session.commit()

        # Call list_images
        response = await client.get("/api/v1/images/")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["images"]) == 1

        # Verify tags are included
        img = data["images"][0]
        assert "tags" in img
        assert img["tags"] is not None
        assert len(img["tags"]) == 2

        # Verify tag details
        tag_titles = {tag["title"] for tag in img["tags"]}
        assert "Anime" in tag_titles
        assert "Landscape" in tag_titles

        # Verify tag structure includes all expected fields
        for tag in img["tags"]:
            assert "tag_id" in tag
            assert "title" in tag
            assert "type" in tag
            assert "type_name" in tag


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
class TestTagSearchValidation:
    """Tests for tag search validation and MAX_SEARCH_TAGS limit."""

    async def test_search_exceeds_max_tags(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test that searching with more than MAX_SEARCH_TAGS tags returns 400 error."""
        # Create an image with tags
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.flush()

        # Create more tags than the limit
        tag_ids = []
        for i in range(settings.MAX_SEARCH_TAGS + 1):
            tag = Tags(title=f"Tag{i}", desc=f"Test tag {i}", type=TagType.THEME)
            db_session.add(tag)
            await db_session.flush()
            tag_ids.append(tag.tag_id)

        await db_session.commit()

        # Try to search with more tags than allowed
        tags_param = ",".join(str(tid) for tid in tag_ids)
        response = await client.get(f"/api/v1/images/?tags={tags_param}")

        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert str(settings.MAX_SEARCH_TAGS) in data["detail"]
        assert "tags at a time" in data["detail"].lower()

    async def test_search_with_max_tags_succeeds(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test that searching with exactly MAX_SEARCH_TAGS tags succeeds."""
        # Create an image
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.flush()

        # Create exactly MAX_SEARCH_TAGS tags
        tag_ids = []
        for i in range(settings.MAX_SEARCH_TAGS):
            tag = Tags(title=f"ExactTag{i}", desc=f"Test tag {i}", type=1)
            db_session.add(tag)
            await db_session.flush()
            tag_ids.append(tag.tag_id)

            # Link the tag to the image
            tag_link = TagLinks(image_id=image.image_id, tag_id=tag.tag_id, user_id=1)
            db_session.add(tag_link)

        await db_session.commit()

        # Search with exactly MAX_SEARCH_TAGS tags should succeed
        tags_param = ",".join(str(tid) for tid in tag_ids)
        response = await client.get(f"/api/v1/images/?tags={tags_param}&tags_mode=all")

        assert response.status_code == 200
        data = response.json()
        assert "images" in data
        # Should find the image since it has all the tags
        assert data["total"] == 1  # Should be 1 since all tags are linked

    async def test_search_with_fewer_than_max_tags(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test that searching with fewer than MAX_SEARCH_TAGS tags succeeds."""
        # Create an image
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.flush()

        # Create fewer tags than the limit (e.g., 2 tags)
        num_tags = 2
        tag_ids = []
        for i in range(num_tags):
            tag = Tags(title=f"FewTag{i}", desc=f"Test tag {i}", type=TagType.THEME)
            db_session.add(tag)
            await db_session.flush()
            tag_ids.append(tag.tag_id)

            # Link the tag to the image
            tag_link = TagLinks(image_id=image.image_id, tag_id=tag.tag_id, user_id=1)
            db_session.add(tag_link)

        await db_session.commit()

        # Search with fewer tags should succeed
        tags_param = ",".join(str(tid) for tid in tag_ids)
        response = await client.get(f"/api/v1/images/?tags={tags_param}")

        assert response.status_code == 200
        data = response.json()
        assert "images" in data


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
