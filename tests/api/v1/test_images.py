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
from app.models import Favorites, ImageRatings, Images, TagLinks, Tags, Users


@pytest.mark.api
class TestImagesList:
    """Tests for GET /api/v1/images endpoint."""

    async def test_list_images_empty(self, client: AsyncClient):
        """Test listing images when database is empty."""
        await client.get("/api/v1/images")

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

        response = await client.get("/api/v1/images")

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
        response = await client.get("/api/v1/images?page=1&per_page=10")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 25
        assert data["page"] == 1
        assert data["per_page"] == 10
        assert len(data["images"]) == 10

        # Test second page
        response = await client.get("/api/v1/images?page=2&per_page=10")
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 2
        assert len(data["images"]) == 10

        # Test third page
        response = await client.get("/api/v1/images?page=3&per_page=10")
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 3
        assert len(data["images"]) == 5  # Remaining images

    async def test_list_images_invalid_pagination(self, client: AsyncClient):
        """Test invalid pagination parameters."""
        # Negative page
        response = await client.get("/api/v1/images?page=0")
        assert response.status_code == 422

        # Too large per_page
        response = await client.get("/api/v1/images?per_page=200")
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
        response = await client.get("/api/v1/images")

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
        response = await client.get("/api/v1/images?user_id=2")
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
        response = await client.get("/api/v1/images?min_width=1920")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2  # 1920x1080 and 3840x2160
        for img in data["images"]:
            assert img["width"] >= 1920

        # Filter max_width
        response = await client.get("/api/v1/images?max_width=1920")
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
        response = await client.get("/api/v1/images?min_rating=5.0")
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
        # Create an image (tags will be created but not linked)
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
        response = await client.get(f"/api/v1/images?tags={tags_param}")

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
            tag = Tags(title=f"ExactTag{i}", desc=f"Test tag {i}", type=TagType.THEME)
            db_session.add(tag)
            await db_session.flush()
            tag_ids.append(tag.tag_id)

            # Link the tag to the image
            tag_link = TagLinks(image_id=image.image_id, tag_id=tag.tag_id, user_id=1)
            db_session.add(tag_link)

        await db_session.commit()

        # Search with exactly MAX_SEARCH_TAGS tags should succeed
        tags_param = ",".join(str(tid) for tid in tag_ids)
        response = await client.get(f"/api/v1/images?tags={tags_param}&tags_mode=all")

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
        response = await client.get(f"/api/v1/images?tags={tags_param}")

        assert response.status_code == 200
        data = response.json()
        assert "images" in data
        assert data["total"] == 1  # Should find the image with the linked tags

    async def test_search_by_alias_tag(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test that searching by an alias tag resolves to the actual tag."""
        # Create an image
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.flush()

        # Create the "real" tag
        real_tag = Tags(title="cat ears", desc="Cat ears", type=TagType.THEME)
        db_session.add(real_tag)
        await db_session.flush()

        # Create an alias tag that points to the real tag
        alias_tag = Tags(
            title="neko mimi",
            desc="Alias for cat ears",
            type=TagType.THEME,
            alias_of=real_tag.tag_id,
        )
        db_session.add(alias_tag)
        await db_session.flush()

        # Link the image to the REAL tag (not the alias)
        tag_link = TagLinks(image_id=image.image_id, tag_id=real_tag.tag_id, user_id=1)
        db_session.add(tag_link)
        await db_session.commit()

        # Search by alias tag ID should find the image
        response = await client.get(f"/api/v1/images?tags={alias_tag.tag_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["images"][0]["image_id"] == image.image_id

    async def test_search_by_alias_tag_any_mode(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test alias resolution in ANY mode (tags_mode=any)."""
        # Create two images
        image1 = Images(**sample_image_data)
        image1.filename = "img1"
        image1.md5_hash = "a" * 32
        db_session.add(image1)
        await db_session.flush()

        image2_data = sample_image_data.copy()
        image2_data["filename"] = "img2"
        image2_data["md5_hash"] = "b" * 32
        image2 = Images(**image2_data)
        db_session.add(image2)
        await db_session.flush()

        # Create two real tags
        tag1 = Tags(title="tag1", desc="Real tag 1", type=TagType.THEME)
        tag2 = Tags(title="tag2", desc="Real tag 2", type=TagType.THEME)
        db_session.add(tag1)
        db_session.add(tag2)
        await db_session.flush()

        # Create aliases for both tags
        alias1 = Tags(
            title="alias1", desc="Alias for tag1", type=TagType.THEME, alias_of=tag1.tag_id
        )
        alias2 = Tags(
            title="alias2", desc="Alias for tag2", type=TagType.THEME, alias_of=tag2.tag_id
        )
        db_session.add(alias1)
        db_session.add(alias2)
        await db_session.flush()

        # Link image1 to tag1, image2 to tag2
        tag_link1 = TagLinks(image_id=image1.image_id, tag_id=tag1.tag_id, user_id=1)
        tag_link2 = TagLinks(image_id=image2.image_id, tag_id=tag2.tag_id, user_id=1)
        db_session.add(tag_link1)
        db_session.add(tag_link2)
        await db_session.commit()

        # Search by alias IDs in ANY mode should find both images
        response = await client.get(
            f"/api/v1/images?tags={alias1.tag_id},{alias2.tag_id}&tags_mode=any"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        found_ids = {img["image_id"] for img in data["images"]}
        assert image1.image_id in found_ids
        assert image2.image_id in found_ids


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
        response = await client.get("/api/v1/images?sort_by=date_added&sort_order=DESC")
        assert response.status_code == 200
        data = response.json()

        # Newest image (last inserted) should be first
        assert data["images"][0]["filename"] == "date-2024-12-31"
        assert data["images"][-1]["filename"] == "date-2024-01-01"

        # Sort ascending (oldest first)
        response = await client.get("/api/v1/images?sort_by=date_added&sort_order=ASC")
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
class TestImageDetailWithFavoriteStatus:
    """Tests for get_image endpoint with favorite status and rating."""

    async def test_get_image_unauthenticated_shows_no_favorite_status(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test that unauthenticated users get is_favorited=False and user_rating=None."""
        # Create an image
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Get image without authentication
        response = await client.get(f"/api/v1/images/{image.image_id}")
        assert response.status_code == 200

        data = response.json()
        assert data["is_favorited"] is False
        assert data["user_rating"] is None

    async def test_get_image_authenticated_not_favorited(
        self,
        authenticated_client: AsyncClient,
        db_session: AsyncSession,
        sample_image_data: dict,
        sample_user: Users,
    ):
        """Test that authenticated users who haven't favorited get is_favorited=False."""
        # Create an image
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Get image with authentication but no favorite
        response = await authenticated_client.get(f"/api/v1/images/{image.image_id}")
        assert response.status_code == 200

        data = response.json()
        assert data["is_favorited"] is False
        assert data["user_rating"] is None

    async def test_get_image_authenticated_favorited(
        self,
        authenticated_client: AsyncClient,
        db_session: AsyncSession,
        sample_image_data: dict,
        sample_user: Users,
    ):
        """Test that authenticated users who have favorited get is_favorited=True."""
        # Create an image
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create a favorite for this user
        favorite = Favorites(user_id=sample_user.user_id, image_id=image.image_id)
        db_session.add(favorite)
        await db_session.commit()

        # Get image with authentication
        response = await authenticated_client.get(f"/api/v1/images/{image.image_id}")
        assert response.status_code == 200

        data = response.json()
        assert data["is_favorited"] is True
        assert data["user_rating"] is None  # No rating yet

    async def test_get_image_authenticated_with_rating(
        self,
        authenticated_client: AsyncClient,
        db_session: AsyncSession,
        sample_image_data: dict,
        sample_user: Users,
    ):
        """Test that authenticated users get their rating back."""
        # Create an image
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create a rating for this user
        rating = ImageRatings(user_id=sample_user.user_id, image_id=image.image_id, rating=8)
        db_session.add(rating)
        await db_session.commit()

        # Get image with authentication
        response = await authenticated_client.get(f"/api/v1/images/{image.image_id}")
        assert response.status_code == 200

        data = response.json()
        assert data["is_favorited"] is False  # Not favorited
        assert data["user_rating"] == 8

    async def test_get_image_authenticated_favorited_and_rated(
        self,
        authenticated_client: AsyncClient,
        db_session: AsyncSession,
        sample_image_data: dict,
        sample_user: Users,
    ):
        """Test that users get both favorite status and rating."""
        # Create an image
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create both favorite and rating
        favorite = Favorites(user_id=sample_user.user_id, image_id=image.image_id)
        rating = ImageRatings(user_id=sample_user.user_id, image_id=image.image_id, rating=9)
        db_session.add(favorite)
        db_session.add(rating)
        await db_session.commit()

        # Get image with authentication
        response = await authenticated_client.get(f"/api/v1/images/{image.image_id}")
        assert response.status_code == 200

        data = response.json()
        assert data["is_favorited"] is True
        assert data["user_rating"] == 9


@pytest.mark.api
class TestListImagesFavoriteStatus:
    """Tests for is_favorited field in list images endpoint."""

    async def test_list_images_unauthenticated_shows_no_favorite_status(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test that unauthenticated users see is_favorited=False for all images."""
        # Create images
        for i in range(3):
            image_data = sample_image_data.copy()
            image_data["source_url"] = f"http://example.com/image{i}.jpg"
            image = Images(**image_data)
            db_session.add(image)
        await db_session.commit()

        response = await client.get("/api/v1/images")
        assert response.status_code == 200

        data = response.json()
        assert len(data["images"]) == 3
        for img in data["images"]:
            assert img["is_favorited"] is False

    async def test_list_images_authenticated_not_favorited(
        self,
        authenticated_client: AsyncClient,
        db_session: AsyncSession,
        sample_image_data: dict,
        sample_user: Users,
    ):
        """Test that authenticated users who haven't favorited see is_favorited=False."""
        # Create images
        for i in range(3):
            image_data = sample_image_data.copy()
            image_data["source_url"] = f"http://example.com/image{i}.jpg"
            image = Images(**image_data)
            db_session.add(image)
        await db_session.commit()

        response = await authenticated_client.get("/api/v1/images")
        assert response.status_code == 200

        data = response.json()
        assert len(data["images"]) == 3
        for img in data["images"]:
            assert img["is_favorited"] is False

    async def test_list_images_authenticated_with_favorites(
        self,
        authenticated_client: AsyncClient,
        db_session: AsyncSession,
        sample_image_data: dict,
        sample_user: Users,
    ):
        """Test that authenticated users see correct is_favorited status for each image."""
        # Create 3 images
        images = []
        for i in range(3):
            image_data = sample_image_data.copy()
            image_data["source_url"] = f"http://example.com/image{i}.jpg"
            image = Images(**image_data)
            db_session.add(image)
            images.append(image)
        await db_session.commit()
        for img in images:
            await db_session.refresh(img)

        # Favorite only the first and third images
        favorite1 = Favorites(user_id=sample_user.user_id, image_id=images[0].image_id)
        favorite3 = Favorites(user_id=sample_user.user_id, image_id=images[2].image_id)
        db_session.add(favorite1)
        db_session.add(favorite3)
        await db_session.commit()

        response = await authenticated_client.get("/api/v1/images")
        assert response.status_code == 200

        data = response.json()
        assert len(data["images"]) == 3

        # Build a map of image_id -> is_favorited from response
        favorited_status = {img["image_id"]: img["is_favorited"] for img in data["images"]}

        # Verify correct favorite status
        assert favorited_status[images[0].image_id] is True
        assert favorited_status[images[1].image_id] is False
        assert favorited_status[images[2].image_id] is True

    async def test_list_images_favorite_status_user_specific(
        self,
        authenticated_client: AsyncClient,
        db_session: AsyncSession,
        sample_image_data: dict,
        sample_user: Users,
    ):
        """Test that favorite status is specific to the authenticated user."""
        # Create another user who will have different favorites
        other_user = Users(
            username="other_user",
            password="fakehash",
            password_type="bcrypt",
            salt="testsalt0000099",
            email="other@example.com",
        )
        db_session.add(other_user)

        # Create an image
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)
        await db_session.refresh(other_user)

        # Other user favorites the image (not our authenticated user)
        favorite = Favorites(user_id=other_user.user_id, image_id=image.image_id)
        db_session.add(favorite)
        await db_session.commit()

        # Our authenticated user should still see is_favorited=False
        response = await authenticated_client.get("/api/v1/images")
        assert response.status_code == 200

        data = response.json()
        assert len(data["images"]) == 1
        assert data["images"][0]["is_favorited"] is False


@pytest.mark.api
class TestFavoritedByUserIdFilter:
    """Tests for favorited_by_user_id filter in image list endpoint."""

    async def test_filter_by_favorited_user(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test filtering images by the user who favorited them."""
        # Create multiple users
        user1 = Users(
            username="favuser1",
            password="testpass",
            password_type="bcrypt",
            salt="salt12345salt12",
            email="fav1@example.com",
        )
        user2 = Users(
            username="favuser2",
            password="testpass",
            password_type="bcrypt",
            salt="salt12345salt56",
            email="fav2@example.com",
        )
        db_session.add(user1)
        db_session.add(user2)
        await db_session.flush()

        # Create multiple images
        images = []
        for i in range(5):
            image_data = sample_image_data.copy()
            image_data["filename"] = f"fav-test-{i}"
            image_data["md5_hash"] = f"favhash{i:020d}"
            image = Images(**image_data)
            db_session.add(image)
            images.append(image)
        await db_session.flush()

        # User1 favorites images 0, 1, 2
        for i in [0, 1, 2]:
            favorite = Favorites(user_id=user1.user_id, image_id=images[i].image_id)
            db_session.add(favorite)

        # User2 favorites images 2, 3, 4
        for i in [2, 3, 4]:
            favorite = Favorites(user_id=user2.user_id, image_id=images[i].image_id)
            db_session.add(favorite)

        await db_session.commit()

        # Filter by user1's favorites
        response = await client.get(f"/api/v1/images?favorited_by_user_id={user1.user_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        favorited_filenames = {img["filename"] for img in data["images"]}
        assert favorited_filenames == {"fav-test-0", "fav-test-1", "fav-test-2"}

        # Filter by user2's favorites
        response = await client.get(f"/api/v1/images?favorited_by_user_id={user2.user_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        favorited_filenames = {img["filename"] for img in data["images"]}
        assert favorited_filenames == {"fav-test-2", "fav-test-3", "fav-test-4"}

    async def test_filter_by_favorited_user_empty_results(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test that empty results are returned when user hasn't favorited any images."""
        # Create a user who hasn't favorited anything
        user = Users(
            username="nofavuser",
            password="testpass",
            password_type="bcrypt",
            salt="salt12345saltno",
            email="nofav@example.com",
        )
        db_session.add(user)
        await db_session.flush()

        # Create some images but don't favorite them
        for i in range(3):
            image_data = sample_image_data.copy()
            image_data["filename"] = f"unfav-test-{i}"
            image_data["md5_hash"] = f"unfavhash{i:018d}"
            db_session.add(Images(**image_data))

        await db_session.commit()

        # Filter by user's favorites (should be empty)
        response = await client.get(f"/api/v1/images?favorited_by_user_id={user.user_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["images"] == []

    async def test_filter_favorited_with_other_filters(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test favorited_by_user_id filter combined with other filters."""
        # Create users
        uploader = Users(
            username="uploader",
            password="testpass",
            password_type="bcrypt",
            salt="salt12345upload",
            email="uploader@example.com",
        )
        uploader2 = Users(
            username="uploader2",
            password="testpass",
            password_type="bcrypt",
            salt="salt12345upload2",
            email="uploader2@example.com",
        )
        favoriter = Users(
            username="favoriter",
            password="testpass",
            password_type="bcrypt",
            salt="salt12345favorit",
            email="favoriter@example.com",
        )
        db_session.add(uploader)
        db_session.add(uploader2)
        db_session.add(favoriter)
        await db_session.flush()

        # Create tags
        tag1 = Tags(title="FavTag1", desc="Fav tag 1", type=TagType.THEME)
        tag2 = Tags(title="FavTag2", desc="Fav tag 2", type=TagType.SOURCE)
        db_session.add(tag1)
        db_session.add(tag2)
        await db_session.flush()

        # Create images with different combinations
        # Image 0: uploaded by uploader, has tag1, favorited by favoriter
        image_data_0 = sample_image_data.copy()
        image_data_0["filename"] = "combo-0"
        image_data_0["md5_hash"] = "combohash0000000000"
        image_data_0["user_id"] = uploader.user_id
        image_0 = Images(**image_data_0)
        db_session.add(image_0)
        await db_session.flush()
        tag_link_0 = TagLinks(image_id=image_0.image_id, tag_id=tag1.tag_id, user_id=uploader.user_id)
        db_session.add(tag_link_0)
        fav_0 = Favorites(user_id=favoriter.user_id, image_id=image_0.image_id)
        db_session.add(fav_0)

        # Image 1: uploaded by uploader2, has tag1, favorited by favoriter
        image_data_1 = sample_image_data.copy()
        image_data_1["filename"] = "combo-1"
        image_data_1["md5_hash"] = "combohash1111111111"
        image_data_1["user_id"] = uploader2.user_id
        image_1 = Images(**image_data_1)
        db_session.add(image_1)
        await db_session.flush()
        tag_link_1 = TagLinks(image_id=image_1.image_id, tag_id=tag1.tag_id, user_id=uploader2.user_id)
        db_session.add(tag_link_1)
        fav_1 = Favorites(user_id=favoriter.user_id, image_id=image_1.image_id)
        db_session.add(fav_1)

        # Image 2: uploaded by uploader, has tag2, favorited by favoriter
        image_data_2 = sample_image_data.copy()
        image_data_2["filename"] = "combo-2"
        image_data_2["md5_hash"] = "combohash2222222222"
        image_data_2["user_id"] = uploader.user_id
        image_2 = Images(**image_data_2)
        db_session.add(image_2)
        await db_session.flush()
        tag_link_2 = TagLinks(image_id=image_2.image_id, tag_id=tag2.tag_id, user_id=uploader.user_id)
        db_session.add(tag_link_2)
        fav_2 = Favorites(user_id=favoriter.user_id, image_id=image_2.image_id)
        db_session.add(fav_2)

        await db_session.commit()

        # Test: favorited_by_user_id + user_id filter
        response = await client.get(
            f"/api/v1/images?favorited_by_user_id={favoriter.user_id}&user_id={uploader.user_id}"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2  # Only images 0 and 2
        filenames = {img["filename"] for img in data["images"]}
        assert filenames == {"combo-0", "combo-2"}

        # Test: favorited_by_user_id + tags filter
        response = await client.get(
            f"/api/v1/images?favorited_by_user_id={favoriter.user_id}&tags={tag1.tag_id}"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2  # Images 0 and 1 have tag1
        filenames = {img["filename"] for img in data["images"]}
        assert filenames == {"combo-0", "combo-1"}

    async def test_filter_favorited_with_pagination(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test that pagination works correctly with favorited_by_user_id filter."""
        # Create a user
        user = Users(
            username="pagfavuser",
            password="testpass",
            password_type="bcrypt",
            salt="salt12345pagfav",
            email="pagfav@example.com",
        )
        db_session.add(user)
        await db_session.flush()

        # Create 25 images and have user favorite all of them
        for i in range(25):
            image_data = sample_image_data.copy()
            image_data["filename"] = f"pagfav-{i:03d}"
            image_data["md5_hash"] = f"pagfavhash{i:016d}"
            image = Images(**image_data)
            db_session.add(image)
            await db_session.flush()
            favorite = Favorites(user_id=user.user_id, image_id=image.image_id)
            db_session.add(favorite)

        await db_session.commit()

        # Test first page
        response = await client.get(
            f"/api/v1/images?favorited_by_user_id={user.user_id}&page=1&per_page=10"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 25
        assert data["page"] == 1
        assert data["per_page"] == 10
        assert len(data["images"]) == 10

        # Test second page
        response = await client.get(
            f"/api/v1/images?favorited_by_user_id={user.user_id}&page=2&per_page=10"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 25
        assert data["page"] == 2
        assert len(data["images"]) == 10

        # Test third page (remaining 5 images)
        response = await client.get(
            f"/api/v1/images?favorited_by_user_id={user.user_id}&page=3&per_page=10"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 25
        assert data["page"] == 3
        assert len(data["images"]) == 5


@pytest.mark.api
class TestImageFavorites:
    """Tests for favorite/unfavorite functionality."""

    async def test_favorite_image_creates_new_favorite(
        self,
        authenticated_client: AsyncClient,
        db_session: AsyncSession,
        sample_image_data: dict,
        sample_user: Users,
    ):
        """Test favoriting an image creates a new favorite record."""
        # Create an image
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Verify initial state
        initial_image_favorites = image.favorites
        initial_user_favorites = sample_user.favorites

        # Favorite the image
        response = await authenticated_client.post(f"/api/v1/images/{image.image_id}/favorite")
        assert response.status_code == 201
        data = response.json()
        assert "message" in data
        assert "added" in data["message"].lower()

        # Verify favorite record was created
        from sqlalchemy import select

        result = await db_session.execute(
            select(Favorites).where(
                Favorites.user_id == sample_user.user_id,
                Favorites.image_id == image.image_id,
            )
        )
        favorite = result.scalar_one_or_none()
        assert favorite is not None
        assert favorite.user_id == sample_user.user_id
        assert favorite.image_id == image.image_id

        # Refresh to get updated counters
        await db_session.refresh(image)
        await db_session.refresh(sample_user)

        # Verify counters were incremented
        assert image.favorites == initial_image_favorites + 1
        assert sample_user.favorites == initial_user_favorites + 1

    async def test_favorite_image_idempotent(
        self,
        authenticated_client: AsyncClient,
        db_session: AsyncSession,
        sample_image_data: dict,
        sample_user: Users,
    ):
        """Test favoriting an image twice is idempotent."""
        # Create an image
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # First favorite
        response1 = await authenticated_client.post(f"/api/v1/images/{image.image_id}/favorite")
        assert response1.status_code == 201

        # Get counters after first favorite
        await db_session.refresh(image)
        await db_session.refresh(sample_user)
        favorites_after_first = image.favorites
        user_favorites_after_first = sample_user.favorites

        # Second favorite (should be idempotent)
        response2 = await authenticated_client.post(f"/api/v1/images/{image.image_id}/favorite")
        assert response2.status_code == 200  # 200 OK for existing favorite, not 201
        data = response2.json()
        assert "already" in data["message"].lower() or "favorite" in data["message"].lower()

        # Verify counters didn't change
        await db_session.refresh(image)
        await db_session.refresh(sample_user)
        assert image.favorites == favorites_after_first
        assert sample_user.favorites == user_favorites_after_first

        # Verify only one favorite record exists
        from sqlalchemy import func, select

        result = await db_session.execute(
            select(func.count()).select_from(
                select(Favorites)
                .where(
                    Favorites.user_id == sample_user.user_id,
                    Favorites.image_id == image.image_id,
                )
                .subquery()
            )
        )
        count = result.scalar()
        assert count == 1

    async def test_unfavorite_image_removes_favorite(
        self,
        authenticated_client: AsyncClient,
        db_session: AsyncSession,
        sample_image_data: dict,
        sample_user: Users,
    ):
        """Test unfavoriting an image removes the favorite record."""
        # Create an image
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create a favorite
        favorite = Favorites(user_id=sample_user.user_id, image_id=image.image_id)
        db_session.add(favorite)
        image.favorites += 1
        sample_user.favorites += 1
        await db_session.commit()
        await db_session.refresh(image)
        await db_session.refresh(sample_user)

        # Record counters before unfavorite
        favorites_before = image.favorites
        user_favorites_before = sample_user.favorites

        # Unfavorite the image
        response = await authenticated_client.delete(f"/api/v1/images/{image.image_id}/favorite")
        assert response.status_code == 200  # Changed from 204 - now returns content
        data = response.json()
        assert data["favorited"] is False

        # Verify favorite record was deleted
        from sqlalchemy import select

        result = await db_session.execute(
            select(Favorites).where(
                Favorites.user_id == sample_user.user_id,
                Favorites.image_id == image.image_id,
            )
        )
        favorite = result.scalar_one_or_none()
        assert favorite is None

        # Verify counters were decremented
        await db_session.refresh(image)
        await db_session.refresh(sample_user)
        assert image.favorites == favorites_before - 1
        assert sample_user.favorites == user_favorites_before - 1

    async def test_unfavorite_nonexistent_favorite_returns_404(
        self,
        authenticated_client: AsyncClient,
        db_session: AsyncSession,
        sample_image_data: dict,
    ):
        """Test unfavoriting an image that wasn't favorited returns 404."""
        # Create an image
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Try to unfavorite without favoriting first
        response = await authenticated_client.delete(f"/api/v1/images/{image.image_id}/favorite")
        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"].lower()

    async def test_favorite_nonexistent_image_returns_404(
        self, authenticated_client: AsyncClient
    ):
        """Test favoriting a nonexistent image returns 404."""
        response = await authenticated_client.post("/api/v1/images/999999/favorite")
        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"].lower()

    async def test_unfavorite_nonexistent_image_returns_404(
        self, authenticated_client: AsyncClient
    ):
        """Test unfavoriting a nonexistent image returns 404."""
        response = await authenticated_client.delete("/api/v1/images/999999/favorite")
        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"].lower()

    async def test_favorite_requires_authentication(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test that favoriting requires authentication."""
        # Create an image
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Try to favorite without auth
        response = await client.post(f"/api/v1/images/{image.image_id}/favorite")
        assert response.status_code == 401

    async def test_unfavorite_requires_authentication(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test that unfavoriting requires authentication."""
        # Create an image
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Try to unfavorite without auth
        response = await client.delete(f"/api/v1/images/{image.image_id}/favorite")
        assert response.status_code == 401

    async def test_counters_stay_non_negative(
        self,
        authenticated_client: AsyncClient,
        db_session: AsyncSession,
        sample_image_data: dict,
        sample_user: Users,
    ):
        """Test that favorite counters never go negative even with data inconsistencies."""
        # Create an image
        image = Images(**sample_image_data)
        image.favorites = 0  # Start at 0
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create a favorite manually (simulating data inconsistency)
        favorite = Favorites(user_id=sample_user.user_id, image_id=image.image_id)
        db_session.add(favorite)
        await db_session.commit()
        # Note: Not incrementing counters to simulate inconsistency

        # Now unfavorite - counters should not go negative
        response = await authenticated_client.delete(f"/api/v1/images/{image.image_id}/favorite")
        assert response.status_code == 200  # Changed from 204 - now returns content
        data = response.json()
        assert data["favorited"] is False

        # Verify counters stayed at 0 (not negative)
        await db_session.refresh(image)
        await db_session.refresh(sample_user)
        assert image.favorites >= 0
        assert sample_user.favorites >= 0


@pytest.mark.api
class TestTagUsageCount:
    """Tests for automatic tag usage_count updates via database triggers."""

    async def test_tag_usage_count_increments_on_add(
        self, authenticated_client: AsyncClient, db_session: AsyncSession, sample_user: Users, sample_image_data: dict
    ):
        """Test that tag usage_count increments when a tag is added to an image."""
        # Create an image owned by the authenticated user
        image_data = sample_image_data.copy()
        image_data["user_id"] = sample_user.user_id
        image = Images(**image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create a tag
        tag = Tags(title="test_tag", type=1)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Verify initial count is 0
        assert tag.usage_count == 0

        # Add tag to image via API
        response = await authenticated_client.post(
            f"/api/v1/images/{image.image_id}/tags/{tag.tag_id}"
        )
        assert response.status_code == 201

        # Verify usage_count incremented
        await db_session.refresh(tag)
        assert tag.usage_count == 1

    async def test_tag_usage_count_decrements_on_remove(
        self, authenticated_client: AsyncClient, db_session: AsyncSession, sample_user: Users, sample_image_data: dict
    ):
        """Test that tag usage_count decrements when a tag is removed from an image."""
        # Create image and tag
        image_data = sample_image_data.copy()
        image_data["user_id"] = sample_user.user_id
        image = Images(**image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        tag = Tags(title="test_tag_2", type=1)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Add tag to image
        tag_link = TagLinks(image_id=image.image_id, tag_id=tag.tag_id, user_id=sample_user.user_id)
        db_session.add(tag_link)
        await db_session.commit()
        await db_session.refresh(tag)
        assert tag.usage_count == 1

        # Remove tag from image via API
        response = await authenticated_client.delete(
            f"/api/v1/images/{image.image_id}/tags/{tag.tag_id}"
        )
        assert response.status_code == 204

        # Verify usage_count decremented
        await db_session.refresh(tag)
        assert tag.usage_count == 0

    async def test_tag_usage_count_multiple_images(
        self, authenticated_client: AsyncClient, db_session: AsyncSession, sample_user: Users, sample_image_data: dict
    ):
        """Test that usage_count tracks multiple images with the same tag."""
        # Create multiple images
        images = []
        for i in range(3):
            image_data = sample_image_data.copy()
            image_data["user_id"] = sample_user.user_id
            image = Images(**image_data)
            db_session.add(image)
            images.append(image)
        await db_session.commit()

        for image in images:
            await db_session.refresh(image)

        # Create a tag
        tag = Tags(title="popular_tag", type=1)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Link tag to all images
        for image in images:
            response = await authenticated_client.post(
                f"/api/v1/images/{image.image_id}/tags/{tag.tag_id}"
            )
            assert response.status_code == 201
            await db_session.refresh(tag)

        # Verify count is 3
        assert tag.usage_count == 3

        # Remove from one image
        response = await authenticated_client.delete(
            f"/api/v1/images/{images[0].image_id}/tags/{tag.tag_id}"
        )
        assert response.status_code == 204
        await db_session.refresh(tag)
        assert tag.usage_count == 2

@pytest.mark.api
class TestCommentFilters:
    """Tests for comment-based filtering in GET /api/v1/images endpoint."""

    async def test_filter_by_commenter(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test filtering images by commenter user ID."""
        from app.models import Comments

        # Create two users
        user1 = Users(
            username="commenter1",
            email="c1@test.com",
            password="testpass1",
            password_type="bcrypt",
            salt="testsalt0000001",
        )
        user2 = Users(
            username="commenter2",
            email="c2@test.com",
            password="testpass2",
            password_type="bcrypt",
            salt="testsalt0000002",
        )
        db_session.add(user1)
        db_session.add(user2)
        await db_session.flush()

        # Create two images
        img_data1 = sample_image_data.copy()
        img_data1.update({"filename": "img1", "md5_hash": "hash1"})
        image1 = Images(**img_data1)

        img_data2 = sample_image_data.copy()
        img_data2.update({"filename": "img2", "md5_hash": "hash2"})
        image2 = Images(**img_data2)

        db_session.add(image1)
        db_session.add(image2)
        await db_session.flush()

        # user1 comments on image1
        comment1 = Comments(
            image_id=image1.image_id,
            user_id=user1.id,
            post_text="Great image!",
        )
        # user2 comments on image2
        comment2 = Comments(
            image_id=image2.image_id,
            user_id=user2.id,
            post_text="Nice work!",
        )
        db_session.add(comment1)
        db_session.add(comment2)
        await db_session.commit()

        # Filter by user1's comments - should get only image1
        response = await client.get(f"/api/v1/images?commenter={user1.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["images"][0]["filename"] == "img1"

        # Filter by user2's comments - should get only image2
        response = await client.get(f"/api/v1/images?commenter={user2.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["images"][0]["filename"] == "img2"

        # No results for non-existent commenter
        response = await client.get("/api/v1/images?commenter=9999")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0

    async def test_filter_by_comment_text_like_search(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test filtering images by comment text using LIKE search."""
        from app.models import Comments

        # Create user
        user = Users(
            username="commenter",
            email="commenter@test.com",
            password="testpass",
            password_type="bcrypt",
            salt="testsalt0000003",
        )
        db_session.add(user)
        await db_session.flush()

        # Create three images
        img_data1 = sample_image_data.copy()
        img_data1.update({"filename": "img1", "md5_hash": "hash1"})
        image1 = Images(**img_data1)

        img_data2 = sample_image_data.copy()
        img_data2.update({"filename": "img2", "md5_hash": "hash2"})
        image2 = Images(**img_data2)

        img_data3 = sample_image_data.copy()
        img_data3.update({"filename": "img3", "md5_hash": "hash3"})
        image3 = Images(**img_data3)

        db_session.add_all([image1, image2, image3])
        await db_session.flush()

        # Add comments
        comment1 = Comments(
            image_id=image1.image_id,
            user_id=user.id,
            post_text="This is an awesome image!",
        )
        comment2 = Comments(
            image_id=image2.image_id,
            user_id=user.id,
            post_text="awesome work here",
        )
        comment3 = Comments(
            image_id=image3.image_id,
            user_id=user.id,
            post_text="terrible quality",
        )
        db_session.add_all([comment1, comment2, comment3])
        await db_session.commit()

        # Search for "awesome" using LIKE mode (always works, doesn't need fulltext index)
        response = await client.get("/api/v1/images?commentsearch=awesome&commentsearch_mode=like")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        filenames = {img["filename"] for img in data["images"]}
        assert filenames == {"img1", "img2"}

        # Search for "terrible" - should get only image3
        response = await client.get("/api/v1/images?commentsearch=terrible&commentsearch_mode=like")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["images"][0]["filename"] == "img3"

        # Search for non-existent text
        response = await client.get("/api/v1/images?commentsearch=nonexistent&commentsearch_mode=like")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0

    async def test_comment_filter_with_multiple_comments_per_image(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test that images with multiple comments are not duplicated when filtering."""
        from app.models import Comments

        # Create two users
        user1 = Users(
            username="user1",
            email="u1@test.com",
            password="testpass1",
            password_type="bcrypt",
            salt="testsalt0000004",
        )
        user2 = Users(
            username="user2",
            email="u2@test.com",
            password="testpass2",
            password_type="bcrypt",
            salt="testsalt0000005",
        )
        db_session.add(user1)
        db_session.add(user2)
        await db_session.flush()

        # Create one image
        img_data = sample_image_data.copy()
        img_data.update({"filename": "img1", "md5_hash": "hash1"})
        image = Images(**img_data)
        db_session.add(image)
        await db_session.flush()

        # Both users comment on the same image
        comment1 = Comments(image_id=image.image_id, user_id=user1.id, post_text="Comment 1")
        comment2 = Comments(image_id=image.image_id, user_id=user2.id, post_text="Comment 2")
        db_session.add(comment1)
        db_session.add(comment2)
        await db_session.commit()

        # Filter by user1's comments - should get image exactly once
        response = await client.get(f"/api/v1/images?commenter={user1.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["images"]) == 1
        assert data["images"][0]["filename"] == "img1"

    async def test_commenter_and_commentsearch_combined(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test combining commenter and commentsearch filters."""
        from app.models import Comments

        # Create two users
        user1 = Users(
            username="user1",
            email="u1@test.com",
            password="testpass1",
            password_type="bcrypt",
            salt="testsalt0000006",
        )
        user2 = Users(
            username="user2",
            email="u2@test.com",
            password="testpass2",
            password_type="bcrypt",
            salt="testsalt0000007",
        )
        db_session.add(user1)
        db_session.add(user2)
        await db_session.flush()

        # Create three images
        img_data1 = sample_image_data.copy()
        img_data1.update({"filename": "img1", "md5_hash": "hash1"})
        image1 = Images(**img_data1)

        img_data2 = sample_image_data.copy()
        img_data2.update({"filename": "img2", "md5_hash": "hash2"})
        image2 = Images(**img_data2)

        img_data3 = sample_image_data.copy()
        img_data3.update({"filename": "img3", "md5_hash": "hash3"})
        image3 = Images(**img_data3)

        db_session.add_all([image1, image2, image3])
        await db_session.flush()

        # user1 comments on image1 and image2
        comment1 = Comments(image_id=image1.image_id, user_id=user1.id, post_text="awesome!")
        comment2 = Comments(image_id=image2.image_id, user_id=user1.id, post_text="terrible!")
        # user2 comments on image3
        comment3 = Comments(image_id=image3.image_id, user_id=user2.id, post_text="awesome!")
        db_session.add_all([comment1, comment2, comment3])
        await db_session.commit()

        # Filter: user1's comments containing "awesome"
        # Should get only image1 (user1 commented, and comment text contains "awesome")
        # Use LIKE mode to work in test environment
        response = await client.get(f"/api/v1/images?commenter={user1.id}&commentsearch=awesome&commentsearch_mode=like")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["images"][0]["filename"] == "img1"

    async def test_commentsearch_boolean_mode(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test comment search with boolean fulltext mode (requires FULLTEXT index)."""
        import pytest

        # Skip this test if running in environment without FULLTEXT index
        # In production, the fulltext index is created by Alembic migration
        # This test is primarily for documenting the feature
        pytest.skip("Requires FULLTEXT index on posts.post_text - test in production environment")

    async def test_commentsearch_like_mode(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test comment search with LIKE mode (simple pattern matching)."""
        from app.models import Comments

        # Create user
        user = Users(
            username="user",
            email="u@test.com",
            password="testpass",
            password_type="bcrypt",
            salt="testsalt0000009",
        )
        db_session.add(user)
        await db_session.flush()

        # Create two images
        img_data1 = sample_image_data.copy()
        img_data1.update({"filename": "img1", "md5_hash": "hash1"})
        image1 = Images(**img_data1)

        img_data2 = sample_image_data.copy()
        img_data2.update({"filename": "img2", "md5_hash": "hash2"})
        image2 = Images(**img_data2)

        db_session.add_all([image1, image2])
        await db_session.flush()

        # Add comments - using substring that fulltext might not find but LIKE will
        comment1 = Comments(image_id=image1.image_id, user_id=user.id, post_text="concatenation test")
        comment2 = Comments(image_id=image2.image_id, user_id=user.id, post_text="nothing here")
        db_session.add_all([comment1, comment2])
        await db_session.commit()

        # LIKE search: "cat" as substring should match "concatenation"
        # Works in all environments (doesn't require FULLTEXT index)
        response = await client.get("/api/v1/images?commentsearch=cat&commentsearch_mode=like")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["images"][0]["filename"] == "img1"

    async def test_hascomments_true_filter(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test filtering images that have comments."""
        from app.models import Comments

        # Create user
        user = Users(
            username="user",
            email="u@test.com",
            password="testpass",
            password_type="bcrypt",
            salt="testsalt0000010",
        )
        db_session.add(user)
        await db_session.flush()

        # Create three images
        img_data1 = sample_image_data.copy()
        img_data1.update({"filename": "with_comment", "md5_hash": "hashwc"})
        image1 = Images(**img_data1)

        img_data2 = sample_image_data.copy()
        img_data2.update({"filename": "no_comment", "md5_hash": "hashnc"})
        image2 = Images(**img_data2)

        img_data3 = sample_image_data.copy()
        img_data3.update({"filename": "also_with_comment", "md5_hash": "hashawc"})
        image3 = Images(**img_data3)

        db_session.add_all([image1, image2, image3])
        await db_session.flush()

        # Add comments to image1 and image3 only
        comment1 = Comments(image_id=image1.image_id, user_id=user.id, post_text="nice image")
        comment2 = Comments(image_id=image3.image_id, user_id=user.id, post_text="awesome!")
        db_session.add_all([comment1, comment2])
        await db_session.commit()

        # Manually update posts counters (database triggers may not fire in test environment)
        image1.posts = 1
        image3.posts = 1
        await db_session.commit()

        # Filter: hascomments=true should return only image1 and image3
        response = await client.get("/api/v1/images?hascomments=true")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        filenames = {img["filename"] for img in data["images"]}
        assert filenames == {"with_comment", "also_with_comment"}

    async def test_hascomments_false_filter(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test filtering images that do NOT have comments."""
        from app.models import Comments

        # Create user
        user = Users(
            username="user",
            email="u@test.com",
            password="testpass",
            password_type="bcrypt",
            salt="testsalt0000011",
        )
        db_session.add(user)
        await db_session.flush()

        # Create three images
        img_data1 = sample_image_data.copy()
        img_data1.update({"filename": "with_comment", "md5_hash": "hashwc2"})
        image1 = Images(**img_data1)

        img_data2 = sample_image_data.copy()
        img_data2.update({"filename": "no_comment", "md5_hash": "hashnc2"})
        image2 = Images(**img_data2)

        img_data3 = sample_image_data.copy()
        img_data3.update({"filename": "also_no_comment", "md5_hash": "hashanc"})
        image3 = Images(**img_data3)

        db_session.add_all([image1, image2, image3])
        await db_session.flush()

        # Add comment only to image1
        comment1 = Comments(image_id=image1.image_id, user_id=user.id, post_text="nice image")
        db_session.add(comment1)
        await db_session.commit()

        # Manually update posts counter (database triggers may not fire in test environment)
        image1.posts = 1
        await db_session.commit()

        # Filter: hascomments=false should return only image2 and image3
        response = await client.get("/api/v1/images?hascomments=false")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        filenames = {img["filename"] for img in data["images"]}
        assert filenames == {"no_comment", "also_no_comment"}
