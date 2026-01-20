"""
Tests for GET /tags/{tag_id}/usage-history endpoint.

Tests that tag usage history (tag adds/removes on images) can be retrieved
for a specific tag with proper pagination and user info.
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
class TestGetTagUsageHistory:
    """Tests for GET /tags/{tag_id}/usage-history endpoint."""

    async def test_returns_usage_history_entries_for_tag(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Should return tag history entries (adds/removes) for the specified tag."""
        # Create a user
        user = Users(
            username="usagehistoryuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="usagehistory@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create a tag
        tag = Tags(title="usage history tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Create images
        image1 = Images(
            filename="usage1",
            ext="jpg",
            md5_hash="usagemd5111111111111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        image2 = Images(
            filename="usage2",
            ext="jpg",
            md5_hash="usagemd5222222222222222222222222",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add_all([image1, image2])
        await db_session.commit()
        await db_session.refresh(image1)
        await db_session.refresh(image2)

        # Create tag history entries
        history1 = TagHistory(
            image_id=image1.image_id,
            tag_id=tag.tag_id,
            action="a",
            user_id=user.user_id,
        )
        history2 = TagHistory(
            image_id=image2.image_id,
            tag_id=tag.tag_id,
            action="a",
            user_id=user.user_id,
        )
        history3 = TagHistory(
            image_id=image1.image_id,
            tag_id=tag.tag_id,
            action="r",
            user_id=user.user_id,
        )
        db_session.add_all([history1, history2, history3])
        await db_session.commit()

        # GET tag usage history
        response = await client.get(f"/api/v1/tags/{tag.tag_id}/usage-history")
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
            assert item["tag_id"] == tag.tag_id
            assert item["action"] in ["added", "removed"]

    async def test_includes_user_info(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """History entries should include user info."""
        # Create a user
        user = Users(
            username="usageuserinfo",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="usageuserinfo@example.com",
            active=1,
            avatar="test-avatar.jpg",
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create a tag
        tag = Tags(title="user info usage tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Create an image
        image = Images(
            filename="userinfo",
            ext="jpg",
            md5_hash="userinfomd5111111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create tag history entry
        history = TagHistory(
            image_id=image.image_id,
            tag_id=tag.tag_id,
            action="a",
            user_id=user.user_id,
        )
        db_session.add(history)
        await db_session.commit()

        # GET tag usage history
        response = await client.get(f"/api/v1/tags/{tag.tag_id}/usage-history")
        assert response.status_code == 200

        data = response.json()
        assert len(data["items"]) == 1

        # Verify user info is present
        item = data["items"][0]
        assert "user" in item
        assert item["user"] is not None
        assert item["user"]["user_id"] == user.user_id
        assert item["user"]["username"] == "usageuserinfo"
        assert item["user"]["avatar"] == "test-avatar.jpg"

    async def test_returns_404_for_nonexistent_tag(self, client: AsyncClient) -> None:
        """Should return 404 for nonexistent tag."""
        response = await client.get("/api/v1/tags/99999999/usage-history")
        assert response.status_code == 404

    async def test_pagination_works(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Should support pagination."""
        # Create a user
        user = Users(
            username="usagepageuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="usagepage@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create a tag
        tag = Tags(title="pagination usage tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Create images and history entries
        for i in range(5):
            image = Images(
                filename=f"page{i}",
                ext="jpg",
                md5_hash=f"pagemd5{i:032d}"[:32],
                user_id=user.user_id,
                width=100,
                height=100,
                filesize=1000,
            )
            db_session.add(image)
            await db_session.commit()
            await db_session.refresh(image)

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
            f"/api/v1/tags/{tag.tag_id}/usage-history?page=1&per_page=2"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 1
        assert data["per_page"] == 2
        assert len(data["items"]) == 2
        assert data["total"] == 5

        # Get second page
        response = await client.get(
            f"/api/v1/tags/{tag.tag_id}/usage-history?page=2&per_page=2"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 2
        assert len(data["items"]) == 2

        # Get third page
        response = await client.get(
            f"/api/v1/tags/{tag.tag_id}/usage-history?page=3&per_page=2"
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
            username="usageorderuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="usageorder@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create a tag
        tag = Tags(title="order usage tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Create images
        image1 = Images(
            filename="order1",
            ext="jpg",
            md5_hash="ordermd51111111111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        image2 = Images(
            filename="order2",
            ext="jpg",
            md5_hash="ordermd52222222222222222222222",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        image3 = Images(
            filename="order3",
            ext="jpg",
            md5_hash="ordermd53333333333333333333333",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add_all([image1, image2, image3])
        await db_session.commit()
        await db_session.refresh(image1)
        await db_session.refresh(image2)
        await db_session.refresh(image3)

        # Create history entries in order (first, second, third)
        # tag_history_id is auto-increment, so higher ID = more recent
        history1 = TagHistory(
            image_id=image1.image_id,
            tag_id=tag.tag_id,
            action="a",
            user_id=user.user_id,
        )
        db_session.add(history1)
        await db_session.commit()
        await db_session.refresh(history1)

        history2 = TagHistory(
            image_id=image2.image_id,
            tag_id=tag.tag_id,
            action="a",
            user_id=user.user_id,
        )
        db_session.add(history2)
        await db_session.commit()
        await db_session.refresh(history2)

        history3 = TagHistory(
            image_id=image3.image_id,
            tag_id=tag.tag_id,
            action="a",
            user_id=user.user_id,
        )
        db_session.add(history3)
        await db_session.commit()
        await db_session.refresh(history3)

        # GET tag usage history
        response = await client.get(f"/api/v1/tags/{tag.tag_id}/usage-history")
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
        # Create a tag
        tag = Tags(title="null user usage tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Create an image (need a valid user for this)
        user = Users(
            username="nulluserimgowner",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="nulluserimgowner@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        image = Images(
            filename="nulluser",
            ext="jpg",
            md5_hash="nullusermd5111111111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create tag history entry with null user_id
        history = TagHistory(
            image_id=image.image_id,
            tag_id=tag.tag_id,
            action="a",
            user_id=None,  # Null user
        )
        db_session.add(history)
        await db_session.commit()

        # GET tag usage history
        response = await client.get(f"/api/v1/tags/{tag.tag_id}/usage-history")
        assert response.status_code == 200

        data = response.json()
        assert len(data["items"]) == 1

        # User should be null
        assert data["items"][0]["user"] is None
