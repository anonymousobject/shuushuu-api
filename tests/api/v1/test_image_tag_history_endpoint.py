"""
Tests for GET /images/{image_id}/tag-history endpoint.

Tests that image tag history (tags added/removed to a specific image) can be retrieved
with proper pagination, tag info (LinkedTag), and user info.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.models.image import Images
from app.models.tag import Tags
from app.models.tag_history import TagHistory
from app.models.user import Users


@pytest.mark.api
class TestGetImageTagHistory:
    """Tests for GET /images/{image_id}/tag-history endpoint."""

    async def test_returns_tag_history_entries_for_image(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Should return tag history entries (adds/removes) for the specified image."""
        # Create a user
        user = Users(
            username="imagetaghistuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="imagetaghist@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="taghist1",
            ext="jpg",
            md5_hash="taghistmd5111111111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create tags
        tag1 = Tags(title="tag history tag 1", type=TagType.THEME)
        tag2 = Tags(title="tag history tag 2", type=TagType.CHARACTER)
        db_session.add_all([tag1, tag2])
        await db_session.commit()
        await db_session.refresh(tag1)
        await db_session.refresh(tag2)

        # Create tag history entries for the image
        history1 = TagHistory(
            image_id=image.image_id,
            tag_id=tag1.tag_id,
            action="a",
            user_id=user.user_id,
        )
        history2 = TagHistory(
            image_id=image.image_id,
            tag_id=tag2.tag_id,
            action="a",
            user_id=user.user_id,
        )
        history3 = TagHistory(
            image_id=image.image_id,
            tag_id=tag1.tag_id,
            action="r",
            user_id=user.user_id,
        )
        db_session.add_all([history1, history2, history3])
        await db_session.commit()

        # GET image tag history
        response = await client.get(f"/api/v1/images/{image.image_id}/tag-history")
        assert response.status_code == 200

        data = response.json()
        assert "total" in data
        assert "page" in data
        assert "per_page" in data
        assert "items" in data
        assert data["total"] == 3
        assert len(data["items"]) == 3

        # Verify items contain expected fields
        for item in data["items"]:
            assert "tag_history_id" in item
            assert "image_id" in item
            assert "tag_id" in item
            assert "action" in item
            assert "date" in item
            assert item["image_id"] == image.image_id
            assert item["action"] in ["added", "removed"]

    async def test_includes_tag_info(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """History entries should include tag info (LinkedTag)."""
        # Create a user
        user = Users(
            username="imagetaginfo",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="imagetaginfo@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="taginfo1",
            ext="jpg",
            md5_hash="taginfomd5111111111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create a tag
        tag = Tags(title="included tag info", type=TagType.SOURCE)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Create tag history entry
        history = TagHistory(
            image_id=image.image_id,
            tag_id=tag.tag_id,
            action="a",
            user_id=user.user_id,
        )
        db_session.add(history)
        await db_session.commit()

        # GET image tag history
        response = await client.get(f"/api/v1/images/{image.image_id}/tag-history")
        assert response.status_code == 200

        data = response.json()
        assert len(data["items"]) == 1

        # Verify tag info is present (LinkedTag)
        item = data["items"][0]
        assert "tag" in item
        assert item["tag"] is not None
        assert item["tag"]["tag_id"] == tag.tag_id
        assert item["tag"]["title"] == "included tag info"

    async def test_includes_user_info(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """History entries should include user info."""
        # Create a user
        user = Users(
            username="imageuserinfo",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="imageuserinfo@example.com",
            active=1,
            avatar="user-avatar.jpg",
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="userinfo1",
            ext="jpg",
            md5_hash="userinfomd51111111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create a tag
        tag = Tags(title="user info tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Create tag history entry
        history = TagHistory(
            image_id=image.image_id,
            tag_id=tag.tag_id,
            action="a",
            user_id=user.user_id,
        )
        db_session.add(history)
        await db_session.commit()

        # GET image tag history
        response = await client.get(f"/api/v1/images/{image.image_id}/tag-history")
        assert response.status_code == 200

        data = response.json()
        assert len(data["items"]) == 1

        # Verify user info is present
        item = data["items"][0]
        assert "user" in item
        assert item["user"] is not None
        assert item["user"]["user_id"] == user.user_id
        assert item["user"]["username"] == "imageuserinfo"
        assert item["user"]["avatar"] == "user-avatar.jpg"

    async def test_returns_404_for_nonexistent_image(self, client: AsyncClient) -> None:
        """Should return 404 for nonexistent image."""
        response = await client.get("/api/v1/images/99999999/tag-history")
        assert response.status_code == 404

    async def test_pagination_works(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Should support pagination."""
        # Create a user
        user = Users(
            username="imagepageuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="imagepage@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="page1",
            ext="jpg",
            md5_hash="pagemd51111111111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create tags and history entries
        for i in range(5):
            tag = Tags(title=f"pagination tag {i}", type=TagType.THEME)
            db_session.add(tag)
            await db_session.commit()
            await db_session.refresh(tag)

            history = TagHistory(
                image_id=image.image_id,
                tag_id=tag.tag_id,
                action="a",
                user_id=user.user_id,
            )
            db_session.add(history)
        await db_session.commit()

        # Get first page with per_page=2
        response = await client.get(
            f"/api/v1/images/{image.image_id}/tag-history?page=1&per_page=2"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 1
        assert data["per_page"] == 2
        assert len(data["items"]) == 2
        assert data["total"] == 5

        # Get second page
        response = await client.get(
            f"/api/v1/images/{image.image_id}/tag-history?page=2&per_page=2"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 2
        assert len(data["items"]) == 2

        # Get third page
        response = await client.get(
            f"/api/v1/images/{image.image_id}/tag-history?page=3&per_page=2"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 3
        assert len(data["items"]) == 1

    async def test_ordered_by_most_recent_first(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """History should be ordered by most recent first."""
        # Create a user
        user = Users(
            username="imageorderuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="imageorder@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="order1",
            ext="jpg",
            md5_hash="ordermd511111111111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create tags
        tag1 = Tags(title="order tag 1", type=TagType.THEME)
        tag2 = Tags(title="order tag 2", type=TagType.THEME)
        tag3 = Tags(title="order tag 3", type=TagType.THEME)
        db_session.add_all([tag1, tag2, tag3])
        await db_session.commit()
        await db_session.refresh(tag1)
        await db_session.refresh(tag2)
        await db_session.refresh(tag3)

        # Create history entries in order (first, second, third)
        # tag_history_id is auto-increment, so higher ID = more recent
        history1 = TagHistory(
            image_id=image.image_id,
            tag_id=tag1.tag_id,
            action="a",
            user_id=user.user_id,
        )
        db_session.add(history1)
        await db_session.commit()
        await db_session.refresh(history1)

        history2 = TagHistory(
            image_id=image.image_id,
            tag_id=tag2.tag_id,
            action="a",
            user_id=user.user_id,
        )
        db_session.add(history2)
        await db_session.commit()
        await db_session.refresh(history2)

        history3 = TagHistory(
            image_id=image.image_id,
            tag_id=tag3.tag_id,
            action="a",
            user_id=user.user_id,
        )
        db_session.add(history3)
        await db_session.commit()
        await db_session.refresh(history3)

        # GET image tag history
        response = await client.get(f"/api/v1/images/{image.image_id}/tag-history")
        assert response.status_code == 200

        data = response.json()
        assert len(data["items"]) == 3

        # Most recent (highest ID) should be first
        assert data["items"][0]["tag_history_id"] == history3.tag_history_id
        assert data["items"][1]["tag_history_id"] == history2.tag_history_id
        assert data["items"][2]["tag_history_id"] == history1.tag_history_id

    async def test_handles_null_user(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Should handle history entries with null user_id gracefully."""
        # Create a user for image ownership
        user = Users(
            username="nulluserimgown",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="nulluserimgown@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="nulluser1",
            ext="jpg",
            md5_hash="nullusermd5111111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create a tag
        tag = Tags(title="null user tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Create tag history entry with null user_id
        history = TagHistory(
            image_id=image.image_id,
            tag_id=tag.tag_id,
            action="a",
            user_id=None,  # Null user
        )
        db_session.add(history)
        await db_session.commit()

        # GET image tag history
        response = await client.get(f"/api/v1/images/{image.image_id}/tag-history")
        assert response.status_code == 200

        data = response.json()
        assert len(data["items"]) == 1

        # User should be null
        assert data["items"][0]["user"] is None
