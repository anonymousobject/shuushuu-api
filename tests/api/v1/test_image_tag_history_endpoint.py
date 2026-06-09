"""
Tests for GET /images/{image_id}/tag-history endpoint.

Tests that image tag history (tags added/removed to a specific image) can be retrieved
with proper pagination, tag info (LinkedTag), and user info.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType, settings
from app.models.image import Images
from app.models.tag import Tags
from app.models.tag_history import TagHistory
from app.models.tag_link import TagLinks
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

    async def test_upload_tags_appear_as_added_events(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Tags linked at upload (tag_links, no tag_history) show as 'added' events.

        This is the core fix: an image tagged only at upload has no tag_history rows,
        but its tag_links carry who/when, so the history must derive 'added' events
        from them instead of showing blank.
        """
        user = Users(
            username="uploadtaguser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="uploadtag@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        image = Images(
            filename="uploadtaghist",
            ext="jpg",
            md5_hash="uploadtaghistmd5000000000000000",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        tag1 = Tags(title="upload tag a", type=TagType.THEME)
        tag2 = Tags(title="upload tag b", type=TagType.ARTIST)
        db_session.add_all([tag1, tag2])
        await db_session.commit()
        await db_session.refresh(tag1)
        await db_session.refresh(tag2)

        # Only tag_links, exactly as link_tags_to_image does on upload — no history.
        db_session.add_all(
            [
                TagLinks(tag_id=tag1.tag_id, image_id=image.image_id, user_id=user.user_id),
                TagLinks(tag_id=tag2.tag_id, image_id=image.image_id, user_id=user.user_id),
            ]
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/images/{image.image_id}/tag-history")
        assert response.status_code == 200
        data = response.json()

        assert data["total"] == 2
        assert {item["action"] for item in data["items"]} == {"added"}
        assert {item["tag_id"] for item in data["items"]} == {tag1.tag_id, tag2.tag_id}
        for item in data["items"]:
            assert item["user"]["user_id"] == user.user_id
            assert item["date"] is not None
            # Synthesized from a tag_link, so there is no tag_history row id.
            assert item["tag_history_id"] is None
            assert item["tag"]["tag_id"] in {tag1.tag_id, tag2.tag_id}

    async def test_present_tag_not_duplicated_when_also_in_history(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A still-present tag with both a tag_link and a tag_history 'a' row appears once."""
        user = Users(
            username="dupetaguser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="dupetag@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        image = Images(
            filename="dupetaghist",
            ext="jpg",
            md5_hash="dupetaghistmd500000000000000000",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        tag = Tags(title="dupe tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Added later via add_tag_to_image: both a tag_link AND a tag_history 'a' exist.
        db_session.add(TagLinks(tag_id=tag.tag_id, image_id=image.image_id, user_id=user.user_id))
        db_session.add(
            TagHistory(
                image_id=image.image_id, tag_id=tag.tag_id, action="a", user_id=user.user_id
            )
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/images/{image.image_id}/tag-history")
        assert response.status_code == 200
        data = response.json()

        assert data["total"] == 1
        assert data["items"][0]["tag_id"] == tag.tag_id
        assert data["items"][0]["action"] == "added"

    async def test_removed_tag_shows_add_and_remove(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A tag added then removed (no longer linked) keeps both events."""
        user = Users(
            username="removedtaguser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="removedtag@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        image = Images(
            filename="removedtaghist",
            ext="jpg",
            md5_hash="removedtaghistmd50000000000000",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        tag = Tags(title="removed tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # No tag_link (it was removed); tag_history carries the add then the remove.
        db_session.add_all(
            [
                TagHistory(
                    image_id=image.image_id, tag_id=tag.tag_id, action="a", user_id=user.user_id
                ),
                TagHistory(
                    image_id=image.image_id, tag_id=tag.tag_id, action="r", user_id=user.user_id
                ),
            ]
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/images/{image.image_id}/tag-history")
        assert response.status_code == 200
        data = response.json()

        assert data["total"] == 2
        assert sorted(item["action"] for item in data["items"]) == ["added", "removed"]

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

    async def test_user_avatar_url_uses_cdn_when_avatar_in_r2(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When R2 is enabled and the user has avatar_in_r2=True, the embedded
        UserSummary's avatar_url must point at the CDN, not local FS.

        Regression: previously the route built UserSummary without passing
        avatar_in_r2 through, so the schema default (False) leaked and forced
        local URLs even after backfill flipped the bit.
        """
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.test")
        monkeypatch.setattr(settings, "IMAGE_BASE_URL", "http://local.test")

        # Create a user with avatar in R2
        user = Users(
            username="r2avataruser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="r2avatar@example.com",
            active=1,
            avatar="abc.png",
            avatar_in_r2=True,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="r2avatar1",
            ext="jpg",
            md5_hash="r2avatarmd511111111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create a tag and history entry attributed to that user
        tag = Tags(title="r2 avatar tag", type=TagType.THEME)
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

        # GET image tag history
        response = await client.get(f"/api/v1/images/{image.image_id}/tag-history")
        assert response.status_code == 200

        data = response.json()
        assert len(data["items"]) == 1

        item_user = data["items"][0]["user"]
        assert item_user is not None
        assert item_user["user_id"] == user.user_id
        assert item_user["avatar"] == "abc.png"
        # avatar_in_r2 is internal routing state and is excluded from
        # serialization; clients only see avatar_url.
        assert "avatar_in_r2" not in item_user
        assert item_user["avatar_url"] == "https://cdn.test/avatars/abc.png"

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
