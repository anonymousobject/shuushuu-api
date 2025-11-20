"""
Tests for comments API endpoints.

These tests cover the /api/v1/comments endpoints including:
- List and search comments
- Get comment details
- Get comments by image
- Get comments by user
- Get comment statistics
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.comment import Comments
from app.models.image import Images


@pytest.mark.api
class TestListComments:
    """Tests for GET /api/v1/comments/ endpoint."""

    async def test_list_comments(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test listing comments."""
        # Create image
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create comments
        for i in range(5):
            comment = Comments(
                image_id=image.image_id,
                user_id=1,
                post_text=f"Test comment {i}",
            )
            db_session.add(comment)
        await db_session.commit()

        response = await client.get("/api/v1/comments/")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 5
        assert "comments" in data

    async def test_filter_comments_by_image(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test filtering comments by image_id."""
        # Create two images
        image1 = Images(**sample_image_data)
        db_session.add(image1)

        image_data2 = sample_image_data.copy()
        image_data2["filename"] = "image2"
        image_data2["md5_hash"] = "differenthash1234567890"
        image2 = Images(**image_data2)
        db_session.add(image2)
        await db_session.commit()
        await db_session.refresh(image1)
        await db_session.refresh(image2)

        # Add comments to image1
        for i in range(3):
            comment = Comments(
                image_id=image1.image_id,
                user_id=1,
                post_text=f"Comment on image1 #{i}",
            )
            db_session.add(comment)

        # Add comments to image2
        for i in range(2):
            comment = Comments(
                image_id=image2.image_id,
                user_id=1,
                post_text=f"Comment on image2 #{i}",
            )
            db_session.add(comment)
        await db_session.commit()

        # Filter by image1
        response = await client.get(f"/api/v1/comments/?image_id={image1.image_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3

    async def test_filter_comments_by_user(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test filtering comments by user_id."""
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Add comments from different users
        for user_id in [1, 2, 3]:
            comment = Comments(
                image_id=image.image_id,
                user_id=user_id,
                post_text=f"Comment by user {user_id}",
            )
            db_session.add(comment)
        await db_session.commit()

        # Filter by user 2
        response = await client.get("/api/v1/comments/?user_id=2")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["comments"][0]["user_id"] == 2

    async def test_search_comments(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test searching comments by text."""
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create comments with different text
        comments_text = [
            "This is an awesome image!",
            "Great artwork",
            "Nice colors",
        ]
        for text in comments_text:
            comment = Comments(
                image_id=image.image_id,
                user_id=1,
                post_text=text,
            )
            db_session.add(comment)
        await db_session.commit()

        # Search for "awesome"
        response = await client.get("/api/v1/comments/?search_text=awesome&search_mode=like")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert "awesome" in data["comments"][0]["post_text"].lower()


@pytest.mark.api
class TestGetComment:
    """Tests for GET /api/v1/comments/{comment_id} endpoint."""

    async def test_get_comment_by_id(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test getting a comment by ID."""
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        comment = Comments(
            image_id=image.image_id,
            user_id=1,
            post_text="Test comment",
        )
        db_session.add(comment)
        await db_session.commit()
        await db_session.refresh(comment)

        response = await client.get(f"/api/v1/comments/{comment.post_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["post_id"] == comment.post_id
        assert data["post_text"] == "Test comment"

    async def test_get_nonexistent_comment(self, client: AsyncClient):
        """Test getting a comment that doesn't exist."""
        response = await client.get("/api/v1/comments/999999")
        assert response.status_code == 404


@pytest.mark.api
class TestGetImageComments:
    """Tests for GET /api/v1/comments/image/{image_id} endpoint."""

    async def test_get_image_comments(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test getting all comments for a specific image."""
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Add comments
        for i in range(3):
            comment = Comments(
                image_id=image.image_id,
                user_id=1,
                post_text=f"Comment {i}",
            )
            db_session.add(comment)
        await db_session.commit()

        response = await client.get(f"/api/v1/comments/image/{image.image_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3

    async def test_get_comments_for_nonexistent_image(self, client: AsyncClient):
        """Test getting comments for non-existent image."""
        response = await client.get("/api/v1/comments/image/999999")
        assert response.status_code == 404


@pytest.mark.api
class TestGetUserComments:
    """Tests for GET /api/v1/comments/user/{user_id} endpoint."""

    async def test_get_user_comments(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test getting all comments by a specific user."""
        # Create images
        image1 = Images(**sample_image_data)
        db_session.add(image1)

        image_data2 = sample_image_data.copy()
        image_data2["filename"] = "image2"
        image_data2["md5_hash"] = "hash2222222222222222222"
        image2 = Images(**image_data2)
        db_session.add(image2)
        await db_session.commit()
        await db_session.refresh(image1)
        await db_session.refresh(image2)

        # Add comments by user 1 on different images
        comment1 = Comments(
            image_id=image1.image_id,
            user_id=1,
            post_text="User 1 comment on image 1",
        )
        comment2 = Comments(
            image_id=image2.image_id,
            user_id=1,
            post_text="User 1 comment on image 2",
        )
        db_session.add_all([comment1, comment2])
        await db_session.commit()

        response = await client.get("/api/v1/comments/user/1")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 2
        for comment in data["comments"]:
            assert comment["user_id"] == 1

    async def test_get_comments_for_nonexistent_user(self, client: AsyncClient):
        """Test getting comments for non-existent user."""
        response = await client.get("/api/v1/comments/user/999999")
        assert response.status_code == 404


@pytest.mark.api
class TestGetCommentStats:
    """Tests for GET /api/v1/comments/stats/summary endpoint."""

    async def test_get_comment_stats(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test getting comment statistics."""
        # Create images
        image1 = Images(**sample_image_data)
        db_session.add(image1)

        image_data2 = sample_image_data.copy()
        image_data2["filename"] = "image2"
        image_data2["md5_hash"] = "statshashhash123456789"
        image2 = Images(**image_data2)
        db_session.add(image2)
        await db_session.commit()
        await db_session.refresh(image1)
        await db_session.refresh(image2)

        # Add comments
        for i in range(3):
            comment = Comments(
                image_id=image1.image_id,
                user_id=1,
                post_text=f"Comment {i}",
            )
            db_session.add(comment)

        comment = Comments(
            image_id=image2.image_id,
            user_id=1,
            post_text="Another comment",
        )
        db_session.add(comment)
        await db_session.commit()

        response = await client.get("/api/v1/comments/stats/summary")
        assert response.status_code == 200
        data = response.json()
        assert "total_comments" in data
        assert "total_images_with_comments" in data
        assert "average_comments_per_image" in data
        assert data["total_comments"] >= 4
        assert data["total_images_with_comments"] >= 2
