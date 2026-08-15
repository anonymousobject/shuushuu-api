"""
Tests for GET /tags/{tag_id}/usage-history endpoint.

Tests that tag usage history (tag adds/removes on images) can be retrieved
for a specific tag with proper pagination and user info.
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.models.image import Images
from app.models.tag import Tags
from app.models.tag_history import TagHistory
from app.models.tag_link import TagLinks
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

    async def test_includes_user_info(self, client: AsyncClient, db_session: AsyncSession) -> None:
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

    async def test_pagination_works(self, client: AsyncClient, db_session: AsyncSession) -> None:
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
        response = await client.get(f"/api/v1/tags/{tag.tag_id}/usage-history?page=1&per_page=2")
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 1
        assert data["per_page"] == 2
        assert len(data["items"]) == 2
        assert data["total"] == 5

        # Get second page
        response = await client.get(f"/api/v1/tags/{tag.tag_id}/usage-history?page=2&per_page=2")
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 2
        assert len(data["items"]) == 2

        # Get third page
        response = await client.get(f"/api/v1/tags/{tag.tag_id}/usage-history?page=3&per_page=2")
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

    async def test_handles_null_user(self, client: AsyncClient, db_session: AsyncSession) -> None:
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

    async def test_upload_time_link_appears_as_added_event(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A tag_links row with no tag_history (upload-time tag) shows as 'added'.

        This is the core fix: a tag applied at upload never gets a tag_history
        row, but tag_links carries who/when, so the union must derive an
        'added' event from it instead of the tag's history being blank.
        """
        user = Users(
            username="usagelinkuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="usagelink@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        tag = Tags(title="upload link usage tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        image = Images(
            filename="usagelink",
            ext="jpg",
            md5_hash="usagelinkmd511111111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Only a tag_link, exactly as link_tags_to_image does on upload — no history.
        db_session.add(TagLinks(tag_id=tag.tag_id, image_id=image.image_id, user_id=user.user_id))
        await db_session.commit()

        response = await client.get(f"/api/v1/tags/{tag.tag_id}/usage-history")
        assert response.status_code == 200
        data = response.json()

        assert data["total"] == 1
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["action"] == "added"
        assert item["image_id"] == image.image_id
        # Synthesized from a tag_link, so there is no tag_history row id.
        assert item["tag_history_id"] is None
        assert item["date"] is not None
        assert item["user"]["user_id"] == user.user_id

    async def test_link_and_history_add_for_same_image_counted_once(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A tag+image with both a link and an 'a' history row appears once (link wins)."""
        user = Users(
            username="usagedupeuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="usagedupe@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        tag = Tags(title="dupe usage tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        image = Images(
            filename="usagedupe",
            ext="jpg",
            md5_hash="usagedupemd511111111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        link_date = datetime(2026, 1, 5, tzinfo=UTC)
        history_date = datetime(2026, 1, 1, tzinfo=UTC)

        # Added later via add_tag_to_image: both a tag_link AND a tag_history 'a' exist.
        db_session.add(
            TagLinks(
                tag_id=tag.tag_id,
                image_id=image.image_id,
                user_id=user.user_id,
                date_linked=link_date,
            )
        )
        db_session.add(
            TagHistory(
                image_id=image.image_id,
                tag_id=tag.tag_id,
                action="a",
                user_id=user.user_id,
                date=history_date,
            )
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/tags/{tag.tag_id}/usage-history")
        assert response.status_code == 200
        data = response.json()

        assert data["total"] == 1
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["action"] == "added"
        assert item["image_id"] == image.image_id
        # The link's row wins the dedup: no tag_history_id, and the link's own date.
        assert item["tag_history_id"] is None
        assert item["date"] == link_date.strftime("%Y-%m-%dT%H:%M:%SZ")

    async def test_removed_tag_with_no_link_shows_add_and_remove(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A tag added then removed (no longer linked) keeps both history events."""
        user = Users(
            username="usageremoveduser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="usageremoved@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        tag = Tags(title="removed usage tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        image = Images(
            filename="usageremoved",
            ext="jpg",
            md5_hash="usageremovedmd51111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

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

        response = await client.get(f"/api/v1/tags/{tag.tag_id}/usage-history")
        assert response.status_code == 200
        data = response.json()

        assert data["total"] == 2
        assert sorted(item["action"] for item in data["items"]) == ["added", "removed"]

    async def test_ordering_interleaves_link_and_history_by_date(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Link and history rows sort together, most recent first, regardless of source."""
        user = Users(
            username="usageorderlinkuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="usageorderlink@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        tag = Tags(title="interleaved order usage tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        images = [
            Images(
                filename=f"usageorderlink{i}",
                ext="jpg",
                md5_hash=f"usageorderlinkmd5{i:015d}",
                user_id=user.user_id,
                width=100,
                height=100,
                filesize=1000,
            )
            for i in range(3)
        ]
        db_session.add_all(images)
        await db_session.commit()
        for image in images:
            await db_session.refresh(image)

        base_date = datetime(2026, 1, 1, tzinfo=UTC)
        # Oldest event first: history remove (day 1), link add (day 2), history add (day 3).
        db_session.add(
            TagHistory(
                image_id=images[0].image_id,
                tag_id=tag.tag_id,
                action="r",
                user_id=user.user_id,
                date=base_date,
            )
        )
        db_session.add(
            TagLinks(
                tag_id=tag.tag_id,
                image_id=images[1].image_id,
                user_id=user.user_id,
                date_linked=base_date + timedelta(days=1),
            )
        )
        db_session.add(
            TagHistory(
                image_id=images[2].image_id,
                tag_id=tag.tag_id,
                action="a",
                user_id=user.user_id,
                date=base_date + timedelta(days=2),
            )
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/tags/{tag.tag_id}/usage-history")
        assert response.status_code == 200
        data = response.json()

        assert data["total"] == 3
        assert [item["image_id"] for item in data["items"]] == [
            images[2].image_id,
            images[1].image_id,
            images[0].image_id,
        ]

    async def test_link_with_null_user_renders_null_user(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A tag_link with no user_id renders user: null without crashing."""
        owner = Users(
            username="usagelinknulluserowner",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="usagelinknulluserowner@example.com",
            active=1,
        )
        db_session.add(owner)
        await db_session.commit()
        await db_session.refresh(owner)

        tag = Tags(title="null user link usage tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        image = Images(
            filename="usagelinknulluser",
            ext="jpg",
            md5_hash="usagelinknulluser1111111111111",
            user_id=owner.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        db_session.add(TagLinks(tag_id=tag.tag_id, image_id=image.image_id, user_id=None))
        await db_session.commit()

        response = await client.get(f"/api/v1/tags/{tag.tag_id}/usage-history")
        assert response.status_code == 200
        data = response.json()

        assert len(data["items"]) == 1
        assert data["items"][0]["user"] is None
        assert data["items"][0]["action"] == "added"

    async def test_pagination_correct_across_union_boundary_with_tie(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Pagination is correct across the link/history union, including a
        same-timestamp tie sitting exactly at the page boundary.

        Uses 10 link rows and 10 history rows, interleaved by date so that the
        per-branch pushdown must actually fetch offset+per_page rows (not just
        per_page) from each branch: with per_page=1, a per-branch LIMIT of
        per_page instead of offset+per_page would fetch only each branch's
        single most-recent row, and the deep page requested below would come
        back empty instead of the correct row. Rows 6 of each branch share the
        exact same date to also pin down tie-break ordering (history before
        link on a tie, per lane DESC) at that same boundary.
        """
        user = Users(
            username="usagepageboundaryuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="usagepageboundary@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        tag = Tags(title="page boundary usage tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        link_images = [
            Images(
                filename=f"usagepblink{i}",
                ext="jpg",
                md5_hash=f"usagepblinkmd5{i:016d}",
                user_id=user.user_id,
                width=100,
                height=100,
                filesize=1000,
            )
            for i in range(10)
        ]
        history_images = [
            Images(
                filename=f"usagepbhist{i}",
                ext="jpg",
                md5_hash=f"usagepbhistmd5{i:016d}",
                user_id=user.user_id,
                width=100,
                height=100,
                filesize=1000,
            )
            for i in range(10)
        ]
        db_session.add_all(link_images + history_images)
        await db_session.commit()
        for image in link_images + history_images:
            await db_session.refresh(image)

        base_date = datetime(2026, 1, 1, tzinfo=UTC)
        # link i (0-indexed) dated (28 - 2*i) days after base; history i dated one
        # day earlier than link i, except history[5], which ties link[5] exactly —
        # that tie lands precisely on the page-11/page-12 boundary tested below.
        for i in range(10):
            link_date = base_date + timedelta(days=28 - 2 * i)
            history_date = link_date if i == 5 else link_date - timedelta(days=1)
            db_session.add(
                TagLinks(
                    tag_id=tag.tag_id,
                    image_id=link_images[i].image_id,
                    user_id=user.user_id,
                    date_linked=link_date,
                )
            )
            db_session.add(
                TagHistory(
                    image_id=history_images[i].image_id,
                    tag_id=tag.tag_id,
                    action="r",
                    user_id=user.user_id,
                    date=history_date,
                )
            )
        await db_session.commit()

        # Global rank 11 (offset=10) is history[5] (tie winner via lane DESC);
        # rank 12 (offset=11) is link[5].
        response = await client.get(f"/api/v1/tags/{tag.tag_id}/usage-history?page=11&per_page=1")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 20
        assert len(data["items"]) == 1
        assert data["items"][0]["image_id"] == history_images[5].image_id
        assert data["items"][0]["action"] == "removed"

        response = await client.get(f"/api/v1/tags/{tag.tag_id}/usage-history?page=12&per_page=1")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 20
        assert len(data["items"]) == 1
        assert data["items"][0]["image_id"] == link_images[5].image_id
        assert data["items"][0]["action"] == "added"
