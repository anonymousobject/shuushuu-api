"""
Tests for GET /users/{user_id}/history endpoint.

Tests that user history (all changes made by a user) can be retrieved with:
- Tag metadata changes (rename, type_change, etc.)
- Tag usage (add/remove on images)
- Status changes (only visible statuses: REPOST, SPOILER, ACTIVE)

Hidden statuses (REVIEW, LOW_QUALITY, INAPPROPRIATE, OTHER) should be excluded.
"""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, TagAuditActionType, TagType
from app.models.image import Images
from app.models.image_status_history import ImageStatusHistory
from app.models.tag import Tags
from app.models.tag_audit_log import TagAuditLog
from app.models.tag_history import TagHistory
from app.models.tag_link import TagLinks
from app.models.user import Users


@pytest.mark.api
class TestGetUserHistory:
    """Tests for GET /users/{user_id}/history endpoint."""

    async def test_returns_tag_metadata_items_correctly(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Should return tag_metadata items with correct structure."""
        # Create a user
        user = Users(
            username="histmetadatauser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="histmetadata@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create a tag
        tag = Tags(title="Cirno", type=TagType.CHARACTER)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Create tag audit log entry (rename)
        audit_log = TagAuditLog(
            tag_id=tag.tag_id,
            user_id=user.user_id,
            action_type=TagAuditActionType.RENAME,
            old_title="Cirno (9)",
            new_title="Cirno",
        )
        db_session.add(audit_log)
        await db_session.commit()

        # GET user history
        response = await client.get(f"/api/v1/users/{user.user_id}/history")
        assert response.status_code == 200

        data = response.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "per_page" in data
        assert data["total"] >= 1

        # Find the tag_metadata item
        tag_metadata_items = [item for item in data["items"] if item["type"] == "tag_metadata"]
        assert len(tag_metadata_items) >= 1

        item = tag_metadata_items[0]
        assert item["type"] == "tag_metadata"
        assert item["action_type"] == "rename"
        assert item["tag"] is not None
        assert item["tag"]["tag_id"] == tag.tag_id
        assert item["tag"]["title"] == "Cirno"
        assert item["old_title"] == "Cirno (9)"
        assert item["new_title"] == "Cirno"
        assert item["created_at"] is not None

    async def test_returns_description_change_items_correctly(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Should return description_change items with old_desc/new_desc (parity)."""
        user = Users(
            username="histdescuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="histdesc@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        tag = Tags(title="Cirno", type=TagType.CHARACTER)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        audit_log = TagAuditLog(
            tag_id=tag.tag_id,
            user_id=user.user_id,
            action_type=TagAuditActionType.DESCRIPTION_CHANGE,
            old_desc="the strongest",
            new_desc="the strongest ice fairy",
        )
        db_session.add(audit_log)
        await db_session.commit()

        response = await client.get(f"/api/v1/users/{user.user_id}/history")
        assert response.status_code == 200

        items = [
            item
            for item in response.json()["items"]
            if item["type"] == "tag_metadata" and item["action_type"] == "description_change"
        ]
        assert len(items) == 1
        item = items[0]
        assert item["old_desc"] == "the strongest"
        assert item["new_desc"] == "the strongest ice fairy"
        assert item["tag"]["tag_id"] == tag.tag_id

    async def test_returns_tag_usage_items_correctly(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Should return tag_usage items with correct structure."""
        # Create a user
        user = Users(
            username="histusageuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="histusage@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="histusage1",
            ext="jpg",
            md5_hash="histusagemd5111111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create a tag
        tag = Tags(title="Cirno", type=TagType.CHARACTER)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Create tag history entry (add)
        history = TagHistory(
            image_id=image.image_id,
            tag_id=tag.tag_id,
            action="a",
            user_id=user.user_id,
        )
        db_session.add(history)
        await db_session.commit()

        # GET user history
        response = await client.get(f"/api/v1/users/{user.user_id}/history")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] >= 1

        # Find the tag_usage item
        tag_usage_items = [item for item in data["items"] if item["type"] == "tag_usage"]
        assert len(tag_usage_items) >= 1

        item = tag_usage_items[0]
        assert item["type"] == "tag_usage"
        assert item["action"] == "added"
        assert item["tag"] is not None
        assert item["tag"]["tag_id"] == tag.tag_id
        assert item["tag"]["title"] == "Cirno"
        assert item["image_id"] == image.image_id
        assert item["date"] is not None

    async def test_returns_status_change_items_correctly_visible_statuses(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Should return status_change items for visible statuses."""
        # Create a user
        user = Users(
            username="histstatususer",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="histstatus@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="histstatus1",
            ext="jpg",
            md5_hash="histstatusmd511111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create status history entry (ACTIVE -> REPOST)
        status_history = ImageStatusHistory(
            image_id=image.image_id,
            user_id=user.user_id,
            old_status=ImageStatus.ACTIVE,
            new_status=ImageStatus.REPOST,
        )
        db_session.add(status_history)
        await db_session.commit()

        # GET user history
        response = await client.get(f"/api/v1/users/{user.user_id}/history")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] >= 1

        # Find the status_change item
        status_items = [item for item in data["items"] if item["type"] == "status_change"]
        assert len(status_items) >= 1

        item = status_items[0]
        assert item["type"] == "status_change"
        assert item["image_id"] == image.image_id
        assert item["old_status"] == ImageStatus.ACTIVE
        assert item["new_status"] == ImageStatus.REPOST
        assert item["new_status_label"] == "repost"
        assert item["created_at"] is not None

    async def test_excludes_status_changes_with_hidden_statuses(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Should exclude status changes where both old and new statuses are hidden."""
        # Create a user
        user = Users(
            username="histhiddenuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="histhidden@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="histhidden1",
            ext="jpg",
            md5_hash="histhiddenmd5111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create status history entries with hidden statuses only
        # These should be excluded from user history
        hidden_status_history1 = ImageStatusHistory(
            image_id=image.image_id,
            user_id=user.user_id,
            old_status=ImageStatus.REVIEW,
            new_status=ImageStatus.LOW_QUALITY,
        )
        hidden_status_history2 = ImageStatusHistory(
            image_id=image.image_id,
            user_id=user.user_id,
            old_status=ImageStatus.LOW_QUALITY,
            new_status=ImageStatus.INAPPROPRIATE,
        )
        hidden_status_history3 = ImageStatusHistory(
            image_id=image.image_id,
            user_id=user.user_id,
            old_status=ImageStatus.INAPPROPRIATE,
            new_status=ImageStatus.OTHER,
        )
        db_session.add_all([hidden_status_history1, hidden_status_history2, hidden_status_history3])
        await db_session.commit()

        # GET user history
        response = await client.get(f"/api/v1/users/{user.user_id}/history")
        assert response.status_code == 200

        data = response.json()
        # Should have no status_change items (all were hidden)
        status_items = [item for item in data["items"] if item["type"] == "status_change"]
        assert len(status_items) == 0

    async def test_includes_status_change_if_one_status_is_visible(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Should include status changes where at least one status is visible."""
        # Create a user
        user = Users(
            username="histpartialuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="histpartial@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="histpartial1",
            ext="jpg",
            md5_hash="histpartialmd51111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create status history where old is hidden but new is visible
        status_history = ImageStatusHistory(
            image_id=image.image_id,
            user_id=user.user_id,
            old_status=ImageStatus.REVIEW,  # Hidden
            new_status=ImageStatus.ACTIVE,  # Visible
        )
        db_session.add(status_history)
        await db_session.commit()

        # GET user history
        response = await client.get(f"/api/v1/users/{user.user_id}/history")
        assert response.status_code == 200

        data = response.json()
        # Should have one status_change item
        status_items = [item for item in data["items"] if item["type"] == "status_change"]
        assert len(status_items) == 1

    async def test_returns_404_for_nonexistent_user(self, client: AsyncClient) -> None:
        """Should return 404 for nonexistent user."""
        response = await client.get("/api/v1/users/99999999/history")
        assert response.status_code == 404

    async def test_returns_empty_list_if_user_has_no_history(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Should return empty list if user has no history."""
        # Create a user with no activity
        user = Users(
            username="histemptyuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="histempty@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # GET user history
        response = await client.get(f"/api/v1/users/{user.user_id}/history")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []
        assert data["page"] == 1

    async def test_pagination_works(self, client: AsyncClient, db_session: AsyncSession) -> None:
        """Should support pagination."""
        # Create a user
        user = Users(
            username="histpageuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="histpage@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create tags and tag audit log entries
        for i in range(5):
            tag = Tags(title=f"hist pagination tag {i}", type=TagType.THEME)
            db_session.add(tag)
            await db_session.commit()
            await db_session.refresh(tag)

            audit = TagAuditLog(
                tag_id=tag.tag_id,
                user_id=user.user_id,
                action_type=TagAuditActionType.RENAME,
                old_title=f"old title {i}",
                new_title=f"hist pagination tag {i}",
            )
            db_session.add(audit)
        await db_session.commit()

        # Get first page with per_page=2
        response = await client.get(f"/api/v1/users/{user.user_id}/history?page=1&per_page=2")
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 1
        assert data["per_page"] == 2
        assert len(data["items"]) == 2
        assert data["total"] == 5

        # Get second page
        response = await client.get(f"/api/v1/users/{user.user_id}/history?page=2&per_page=2")
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 2
        assert len(data["items"]) == 2

        # Get third page
        response = await client.get(f"/api/v1/users/{user.user_id}/history?page=3&per_page=2")
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 3
        assert len(data["items"]) == 1

    async def test_items_sorted_by_date_descending(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Items should be sorted by date descending (most recent first)."""
        # Create a user
        user = Users(
            username="histsortuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="histsort@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create a tag
        tag = Tags(title="history sort tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Create audit log entries with different timestamps
        now = datetime.now(UTC)
        audit1 = TagAuditLog(
            tag_id=tag.tag_id,
            user_id=user.user_id,
            action_type=TagAuditActionType.RENAME,
            old_title="oldest",
            new_title="middle",
            created_at=now - timedelta(hours=2),
        )
        audit2 = TagAuditLog(
            tag_id=tag.tag_id,
            user_id=user.user_id,
            action_type=TagAuditActionType.RENAME,
            old_title="middle",
            new_title="newest",
            created_at=now - timedelta(hours=1),
        )
        audit3 = TagAuditLog(
            tag_id=tag.tag_id,
            user_id=user.user_id,
            action_type=TagAuditActionType.RENAME,
            old_title="newest",
            new_title="latest",
            created_at=now,
        )
        db_session.add_all([audit1, audit2, audit3])
        await db_session.commit()

        # GET user history
        response = await client.get(f"/api/v1/users/{user.user_id}/history")
        assert response.status_code == 200

        data = response.json()
        assert len(data["items"]) == 3

        # Most recent should be first
        assert data["items"][0]["new_title"] == "latest"
        assert data["items"][1]["new_title"] == "newest"
        assert data["items"][2]["new_title"] == "middle"

    async def test_all_types_sorted_together_chronologically(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """All three types (tag_metadata, tag_usage, status_change) should be sorted together."""
        # Create a user
        user = Users(
            username="histmixeduser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="histmixed@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="histmixed1",
            ext="jpg",
            md5_hash="histmixedmd511111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create a tag
        tag = Tags(title="history mixed tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        now = datetime.now(UTC)

        # Create entries with interleaved timestamps:
        # 1. tag_metadata (oldest)
        # 2. status_change (middle)
        # 3. tag_usage (newest)
        audit = TagAuditLog(
            tag_id=tag.tag_id,
            user_id=user.user_id,
            action_type=TagAuditActionType.RENAME,
            old_title="old",
            new_title="history mixed tag",
            created_at=now - timedelta(hours=3),
        )
        db_session.add(audit)

        status_history = ImageStatusHistory(
            image_id=image.image_id,
            user_id=user.user_id,
            old_status=ImageStatus.ACTIVE,
            new_status=ImageStatus.REPOST,
            created_at=now - timedelta(hours=2),
        )
        db_session.add(status_history)

        tag_history = TagHistory(
            image_id=image.image_id,
            tag_id=tag.tag_id,
            action="a",
            user_id=user.user_id,
            date=now - timedelta(hours=1),
        )
        db_session.add(tag_history)
        await db_session.commit()

        # GET user history
        response = await client.get(f"/api/v1/users/{user.user_id}/history")
        assert response.status_code == 200

        data = response.json()
        assert len(data["items"]) == 3

        # Newest first (tag_usage), then status_change, then tag_metadata
        assert data["items"][0]["type"] == "tag_usage"
        assert data["items"][1]["type"] == "status_change"
        assert data["items"][2]["type"] == "tag_metadata"

    async def test_tag_usage_remove_action(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Should correctly show 'removed' action for tag removals."""
        # Create a user
        user = Users(
            username="histremoveuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="histremove@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="histremove1",
            ext="jpg",
            md5_hash="histremovemd5111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create a tag
        tag = Tags(title="removal tag", type=TagType.CHARACTER)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Create tag history entry (remove)
        history = TagHistory(
            image_id=image.image_id,
            tag_id=tag.tag_id,
            action="r",  # 'r' for remove
            user_id=user.user_id,
        )
        db_session.add(history)
        await db_session.commit()

        # GET user history
        response = await client.get(f"/api/v1/users/{user.user_id}/history")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] >= 1

        # Find the tag_usage item
        tag_usage_items = [item for item in data["items"] if item["type"] == "tag_usage"]
        assert len(tag_usage_items) >= 1

        item = tag_usage_items[0]
        assert item["action"] == "removed"


@pytest.mark.api
class TestUserHistoryTagLinks:
    """
    Tags applied at upload time exist only as tag_links rows and never get a
    tag_history row — before this fix, the hottest user's history was empty
    despite 1.13M links. These tests cover the tag_links branch of the union,
    the user-scoped dedup rule (deliberately different from the non-scoped
    rule on GET /tags/{id}/usage-history — see
    _user_history_tag_history_dedup_filter in app/api/v1/history.py), and
    ordering/pagination across all four sources.
    """

    async def _make_user(self, db_session: AsyncSession, username: str) -> Users:
        user = Users(
            username=username,
            password="hashed",
            password_type="bcrypt",
            salt="",
            email=f"{username}@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    async def _make_image(self, db_session: AsyncSession, owner: Users, filename: str) -> Images:
        image = Images(
            filename=filename,
            ext="jpg",
            md5_hash=hashlib.md5(filename.encode()).hexdigest(),
            user_id=owner.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)
        return image

    async def test_upload_time_link_appears_as_added_event(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """An upload-time tag_links row (no tag_history) shows as tag_usage/added
        and counts toward total."""
        user = await self._make_user(db_session, "userhistlinkuser")
        tag = Tags(title="upload link user history tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)
        image = await self._make_image(db_session, user, "userhistlink")

        db_session.add(TagLinks(tag_id=tag.tag_id, image_id=image.image_id, user_id=user.user_id))
        await db_session.commit()

        response = await client.get(f"/api/v1/users/{user.user_id}/history")
        assert response.status_code == 200
        data = response.json()

        assert data["total"] == 1
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["type"] == "tag_usage"
        assert item["action"] == "added"
        assert item["tag"]["tag_id"] == tag.tag_id
        assert item["image_id"] == image.image_id
        assert item["date"] is not None

    async def test_link_and_own_history_add_counted_once(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A tag+image with both this user's link and their own 'a' history row
        appears exactly once (the link wins the dedup)."""
        user = await self._make_user(db_session, "userhistdupeuser")
        tag = Tags(title="dupe user history tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)
        image = await self._make_image(db_session, user, "userhistdupe")

        link_date = datetime(2026, 1, 5, tzinfo=UTC)
        history_date = datetime(2026, 1, 1, tzinfo=UTC)
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
                tag_id=tag.tag_id,
                image_id=image.image_id,
                action="a",
                user_id=user.user_id,
                date=history_date,
            )
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/users/{user.user_id}/history")
        assert response.status_code == 200
        data = response.json()

        assert data["total"] == 1
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["action"] == "added"
        assert item["image_id"] == image.image_id
        # The link wins the dedup: its own date shows through, not the history row's.
        assert item["date"] == link_date.strftime("%Y-%m-%dT%H:%M:%SZ")

    async def test_removed_tag_shows_add_and_remove(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A tag added then removed (link gone) keeps both history events."""
        user = await self._make_user(db_session, "userhistremoveduser")
        tag = Tags(title="removed user history tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)
        image = await self._make_image(db_session, user, "userhistremoved")

        db_session.add_all(
            [
                TagHistory(
                    tag_id=tag.tag_id, image_id=image.image_id, action="a", user_id=user.user_id
                ),
                TagHistory(
                    tag_id=tag.tag_id, image_id=image.image_id, action="r", user_id=user.user_id
                ),
            ]
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/users/{user.user_id}/history")
        assert response.status_code == 200
        data = response.json()

        assert data["total"] == 2
        usage_items = [item for item in data["items"] if item["type"] == "tag_usage"]
        assert sorted(item["action"] for item in usage_items) == ["added", "removed"]

    async def test_user_scoped_dedup_preserves_original_adder_after_reassignment(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """X adds T to I (link + history 'a'), T is removed from I (X's link
        deleted, 'r' row recorded), Y re-adds T (Y's link).

        X's history must still show X's original add AND the remove: the
        dedup EXISTS probe is scoped to the path user's own tag_links, so it
        only drops X's 'a' row if X *currently* holds the matching link. Once
        the live link belongs to Y, the non-scoped rule used by the tag
        endpoint would wrongly match on Y's link and drop X's add — this
        endpoint's user-scoped rule must not. Y's history shows only Y's
        link-derived add.
        """
        user_x = await self._make_user(db_session, "userhistdedupx")
        user_y = await self._make_user(db_session, "userhistdedupy")
        tag = Tags(title="reassigned user history tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)
        image = await self._make_image(db_session, user_x, "userhistdedup")

        add_date = datetime(2026, 1, 1, tzinfo=UTC)
        remove_date = datetime(2026, 1, 2, tzinfo=UTC)
        re_add_date = datetime(2026, 1, 3, tzinfo=UTC)

        # X adds T to I (history side of "link + history"; DB end-state below
        # already reflects X's link having been deleted on removal).
        db_session.add(
            TagHistory(
                tag_id=tag.tag_id,
                image_id=image.image_id,
                action="a",
                user_id=user_x.user_id,
                date=add_date,
            )
        )
        await db_session.commit()

        # T removed from I: X's link is gone, remove event recorded.
        db_session.add(
            TagHistory(
                tag_id=tag.tag_id,
                image_id=image.image_id,
                action="r",
                user_id=user_x.user_id,
                date=remove_date,
            )
        )
        await db_session.commit()

        # Y re-adds T: only Y's link now exists for (tag, image).
        db_session.add(
            TagLinks(
                tag_id=tag.tag_id,
                image_id=image.image_id,
                user_id=user_y.user_id,
                date_linked=re_add_date,
            )
        )
        await db_session.commit()

        # X's feed: original add + remove, both preserved.
        x_response = await client.get(f"/api/v1/users/{user_x.user_id}/history")
        assert x_response.status_code == 200
        x_data = x_response.json()
        x_usage_items = [item for item in x_data["items"] if item["type"] == "tag_usage"]
        assert sorted(item["action"] for item in x_usage_items) == ["added", "removed"]
        assert all(item["image_id"] == image.image_id for item in x_usage_items)

        # Y's feed: just the link-derived add.
        y_response = await client.get(f"/api/v1/users/{user_y.user_id}/history")
        assert y_response.status_code == 200
        y_data = y_response.json()
        assert y_data["total"] == 1
        assert len(y_data["items"]) == 1
        y_item = y_data["items"][0]
        assert y_item["type"] == "tag_usage"
        assert y_item["action"] == "added"
        assert y_item["image_id"] == image.image_id

    async def test_ordering_interleaves_all_four_kinds_by_date(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """All four sources (audit, history, link, status) sort together by
        date, newest first, regardless of kind."""
        user = await self._make_user(db_session, "userhistorderuser")
        tag = Tags(title="order interleave user history tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)
        image = await self._make_image(db_session, user, "userhistorder")

        base_date = datetime(2026, 1, 1, tzinfo=UTC)

        # Oldest to newest: tag_metadata, status_change, tag_usage (history
        # remove), tag_usage (link add).
        db_session.add(
            TagAuditLog(
                tag_id=tag.tag_id,
                user_id=user.user_id,
                action_type=TagAuditActionType.RENAME,
                old_title="old",
                new_title="order interleave user history tag",
                created_at=base_date,
            )
        )
        db_session.add(
            ImageStatusHistory(
                image_id=image.image_id,
                user_id=user.user_id,
                old_status=ImageStatus.ACTIVE,
                new_status=ImageStatus.REPOST,
                created_at=base_date + timedelta(days=1),
            )
        )
        db_session.add(
            TagHistory(
                tag_id=tag.tag_id,
                image_id=image.image_id,
                action="r",
                user_id=user.user_id,
                date=base_date + timedelta(days=2),
            )
        )
        db_session.add(
            TagLinks(
                tag_id=tag.tag_id,
                image_id=image.image_id,
                user_id=user.user_id,
                date_linked=base_date + timedelta(days=3),
            )
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/users/{user.user_id}/history")
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 4

        assert data["items"][0]["type"] == "tag_usage"
        assert data["items"][0]["action"] == "added"
        assert data["items"][1]["type"] == "tag_usage"
        assert data["items"][1]["action"] == "removed"
        assert data["items"][2]["type"] == "status_change"
        assert data["items"][3]["type"] == "tag_metadata"

    async def test_pagination_correct_across_union_boundary_with_tie(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Pagination is correct across the four-branch pushdown, including a
        same-timestamp tie between kind 3 (link) and kind 2 (history) sitting
        exactly at the page boundary.

        10 link rows + 10 history rows ('r', to sidestep the add-dedup),
        interleaved by date, so a per-branch LIMIT of only per_page (instead
        of offset + per_page) would starve this deep page. Row 5 of each
        branch shares the same date to pin down the kind DESC tie-break:
        link (kind 3) sorts before history (kind 2) on an exact tie.
        """
        user = await self._make_user(db_session, "userhistpageboundaryuser")
        tag = Tags(title="page boundary user history tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        link_images = [
            await self._make_image(db_session, user, f"userhistpblink{i}") for i in range(10)
        ]
        history_images = [
            await self._make_image(db_session, user, f"userhistpbhist{i}") for i in range(10)
        ]

        base_date = datetime(2026, 1, 1, tzinfo=UTC)
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
                    tag_id=tag.tag_id,
                    image_id=history_images[i].image_id,
                    action="r",
                    user_id=user.user_id,
                    date=history_date,
                )
            )
        await db_session.commit()

        # Global rank 11 (offset=10) is link[5] (tie winner via kind DESC);
        # rank 12 (offset=11) is history[5].
        response = await client.get(f"/api/v1/users/{user.user_id}/history?page=11&per_page=1")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 20
        assert len(data["items"]) == 1
        assert data["items"][0]["image_id"] == link_images[5].image_id
        assert data["items"][0]["action"] == "added"

        response = await client.get(f"/api/v1/users/{user.user_id}/history?page=12&per_page=1")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 20
        assert len(data["items"]) == 1
        assert data["items"][0]["image_id"] == history_images[5].image_id
        assert data["items"][0]["action"] == "removed"

    async def test_link_with_null_user_absent_from_every_feed(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A tag_link with NULL user_id must not appear in any user's history:
        `tag_links.user_id = :user_id` never matches NULL under SQL semantics.
        """
        owner = await self._make_user(db_session, "userhistnulllinkowner")
        tag = Tags(title="null user link user history tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)
        image = await self._make_image(db_session, owner, "userhistnulllink")

        db_session.add(TagLinks(tag_id=tag.tag_id, image_id=image.image_id, user_id=None))
        await db_session.commit()

        # Not in the image owner's feed either — the link has no creator at all.
        response = await client.get(f"/api/v1/users/{owner.user_id}/history")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []

    async def test_multi_tag_upload_same_timestamp_paginates_without_duplicates_or_omissions(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """N tag_links rows from one upload (same image, same shared
        timestamp) must each appear exactly once across all pages, never
        duplicated or dropped.

        tag_links' primary key is (tag_id, image_id); kind 3's tiebreak is
        image_id alone, so N links on one image sharing a date_linked value
        (exactly what app.services.upload.link_tags_to_image produces: one
        TagLinks row per tag, all left at the same server-default
        timestamp) used to collide on the full (ts, prio, kind, tiebreak)
        sort tuple with nothing left to break the tie. The per-branch LIMIT
        (offset + per_page) then picked an arbitrary subset of that tied
        group — a different subset on every page request, since the LIMIT
        value itself depends on the requested page — so paginating with a
        small per_page could show a link twice or skip it entirely. Uses 12
        tags with per_page=1 so 12 single-row page fetches make any
        arbitrary slicing overwhelmingly likely to duplicate or omit.
        """
        user = await self._make_user(db_session, "userhistmultitaguser")
        image = await self._make_image(db_session, user, "userhistmultitag")

        shared_date = datetime(2026, 1, 1, tzinfo=UTC)
        tags = []
        for i in range(12):
            tag = Tags(title=f"multi tag upload tag {i}", type=TagType.THEME)
            db_session.add(tag)
            await db_session.commit()
            await db_session.refresh(tag)
            tags.append(tag)
            db_session.add(
                TagLinks(
                    tag_id=tag.tag_id,
                    image_id=image.image_id,
                    user_id=user.user_id,
                    date_linked=shared_date,
                )
            )
        await db_session.commit()

        seen_tag_ids: list[int] = []
        for page in range(1, 13):
            response = await client.get(
                f"/api/v1/users/{user.user_id}/history?page={page}&per_page=1"
            )
            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 12
            assert len(data["items"]) == 1
            item = data["items"][0]
            assert item["type"] == "tag_usage"
            assert item["image_id"] == image.image_id
            seen_tag_ids.append(item["tag"]["tag_id"])

        # Every tag appears, and exactly once: sorted equality catches both
        # a duplicate (some tag_id repeated, so another is necessarily
        # missing from a 12-item list) and an outright omission.
        assert sorted(seen_tag_ids) == sorted(tag.tag_id for tag in tags)

    async def test_event_id_distinguishes_audit_rows_sharing_a_timestamp(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Audit rows identical in every other rendered field still get
        distinct event_ids.

        tag_audit_log.created_at is a bare current_timestamp(), so the rows one
        save writes are identical on it; editing a tag's external links emits a
        link_added row per link that way. Nothing else in the payload separates
        them either — same action_type, same tag — so a client with only the
        rendered fields to work from cannot tell them apart. That is exactly
        what broke the frontend feed, which keyed its rows on
        (action_type, tag_id, created_at) and crashed on the collision.
        """
        user = await self._make_user(db_session, "userhisteventiduser")
        tag = Tags(title="event id link tag", type=TagType.ARTIST)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        shared_date = datetime(2026, 1, 1, tzinfo=UTC)
        urls = [
            "https://profcard.info/u/6di8O8hVq9dt",
            "https://x.com/NzzZ_",
            "https://www.pixiv.net/member.php?id=14693767",
        ]
        for url in urls:
            db_session.add(
                TagAuditLog(
                    tag_id=tag.tag_id,
                    user_id=user.user_id,
                    action_type=TagAuditActionType.LINK_ADDED,
                    link_url=url,
                    created_at=shared_date,
                )
            )
        await db_session.commit()

        response = await client.get(f"/api/v1/users/{user.user_id}/history")
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 3

        # The rows really are indistinguishable on the fields a client renders.
        rendered = {(i["action_type"], i["tag"]["tag_id"], i["created_at"]) for i in data["items"]}
        assert len(rendered) == 1

        event_ids = [item["event_id"] for item in data["items"]]
        assert all(event_ids)
        assert len(set(event_ids)) == 3

    async def test_event_id_unique_across_all_four_kinds(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """event_id is unique across a page mixing every union branch.

        The id is built from (kind, id_a, id_b), and the four branches draw
        id_a from unrelated id spaces (tag_audit_log.id, tag_history_id,
        tag_links.tag_id, image_status_history.id) that freely overlap — the
        leading kind is what keeps them apart.
        """
        user = await self._make_user(db_session, "userhisteventidkinduser")
        tag = Tags(title="event id kind tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)
        image = await self._make_image(db_session, user, "userhisteventidkind")

        base_date = datetime(2026, 1, 1, tzinfo=UTC)
        db_session.add(
            TagAuditLog(
                tag_id=tag.tag_id,
                user_id=user.user_id,
                action_type=TagAuditActionType.RENAME,
                old_title="old",
                new_title="event id kind tag",
                created_at=base_date,
            )
        )
        db_session.add(
            ImageStatusHistory(
                image_id=image.image_id,
                user_id=user.user_id,
                old_status=ImageStatus.ACTIVE,
                new_status=ImageStatus.REPOST,
                created_at=base_date + timedelta(days=1),
            )
        )
        db_session.add(
            TagHistory(
                tag_id=tag.tag_id,
                image_id=image.image_id,
                action="r",
                user_id=user.user_id,
                date=base_date + timedelta(days=2),
            )
        )
        db_session.add(
            TagLinks(
                tag_id=tag.tag_id,
                image_id=image.image_id,
                user_id=user.user_id,
                date_linked=base_date + timedelta(days=3),
            )
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/users/{user.user_id}/history")
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 4

        event_ids = [item["event_id"] for item in data["items"]]
        assert all(event_ids)
        assert len(set(event_ids)) == 4
        # Kind prefixes: 3=tag_links, 2=tag_history, 4=status, 1=audit, in the
        # newest-first order the previous test pins.
        assert [eid.split("-")[0] for eid in event_ids] == ["3", "2", "4", "1"]


@pytest.mark.api
class TestUserHistoryLinkedTags:
    """
    The user-history endpoint must surface the *second* tag involved in each
    tag_metadata action (alias_set/removed, parent_set/removed,
    source_linked/unlinked). Without it the frontend renders things like
    "Removed tag alias [yua]" with no way to say what it was removed from.

    Field naming mirrors TagAuditLogResponse 1:1 so the frontend can reuse
    its existing getLinkedTag() helper. See
    https://github.com/anonymousobject/shuushuu-frontend/blob/main/docs/plans/2026-05-23-history-linked-tags-api-requirements.md
    """

    async def _make_user(self, db_session: AsyncSession, username: str) -> Users:
        user = Users(
            username=username,
            password="hashed",
            password_type="bcrypt",
            salt="",
            email=f"{username}@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    async def _find_metadata_item(
        self, client: AsyncClient, user_id: int, action_type: str
    ) -> dict:
        response = await client.get(f"/api/v1/users/{user_id}/history")
        assert response.status_code == 200
        items = [
            item
            for item in response.json()["items"]
            if item["type"] == "tag_metadata" and item["action_type"] == action_type
        ]
        assert len(items) == 1, f"expected one {action_type} row, got {len(items)}"
        return items[0]

    async def test_alias_set_includes_alias_tag(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await self._make_user(db_session, "linkedalias_set")
        source_tag = Tags(title="Pixiv 481037", type=TagType.THEME)
        target_tag = Tags(title="yua", type=TagType.CHARACTER)
        db_session.add_all([source_tag, target_tag])
        await db_session.commit()
        await db_session.refresh(source_tag)
        await db_session.refresh(target_tag)

        db_session.add(
            TagAuditLog(
                tag_id=source_tag.tag_id,
                user_id=user.user_id,
                action_type=TagAuditActionType.ALIAS_SET,
                new_alias_of=target_tag.tag_id,
            )
        )
        await db_session.commit()

        item = await self._find_metadata_item(client, user.user_id, "alias_set")
        assert item["alias_tag"] is not None
        assert item["alias_tag"]["tag_id"] == target_tag.tag_id
        assert item["alias_tag"]["title"] == "yua"
        assert item["alias_tag"]["type"] == TagType.CHARACTER
        assert item["parent_tag"] is None
        assert item["source_tag"] is None
        assert item["character_tag"] is None

    async def test_alias_removed_includes_alias_tag(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await self._make_user(db_session, "linkedalias_rm")
        source_tag = Tags(title="Pixiv 740604", type=TagType.THEME)
        prev_target = Tags(title="yua", type=TagType.CHARACTER)
        db_session.add_all([source_tag, prev_target])
        await db_session.commit()
        await db_session.refresh(source_tag)
        await db_session.refresh(prev_target)

        db_session.add(
            TagAuditLog(
                tag_id=source_tag.tag_id,
                user_id=user.user_id,
                action_type=TagAuditActionType.ALIAS_REMOVED,
                old_alias_of=prev_target.tag_id,
            )
        )
        await db_session.commit()

        item = await self._find_metadata_item(client, user.user_id, "alias_removed")
        assert item["alias_tag"] is not None
        assert item["alias_tag"]["tag_id"] == prev_target.tag_id
        assert item["alias_tag"]["title"] == "yua"
        assert item["alias_tag"]["type"] == TagType.CHARACTER
        assert item["parent_tag"] is None
        assert item["source_tag"] is None
        assert item["character_tag"] is None

    async def test_parent_set_includes_parent_tag(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await self._make_user(db_session, "linkedparent_set")
        child = Tags(title="school uniform", type=TagType.THEME)
        parent = Tags(title="clothing", type=TagType.THEME)
        db_session.add_all([child, parent])
        await db_session.commit()
        await db_session.refresh(child)
        await db_session.refresh(parent)

        db_session.add(
            TagAuditLog(
                tag_id=child.tag_id,
                user_id=user.user_id,
                action_type=TagAuditActionType.PARENT_SET,
                new_parent_id=parent.tag_id,
            )
        )
        await db_session.commit()

        item = await self._find_metadata_item(client, user.user_id, "parent_set")
        assert item["parent_tag"] is not None
        assert item["parent_tag"]["tag_id"] == parent.tag_id
        assert item["parent_tag"]["title"] == "clothing"
        assert item["parent_tag"]["type"] == TagType.THEME
        assert item["alias_tag"] is None
        assert item["source_tag"] is None
        assert item["character_tag"] is None

    async def test_parent_removed_includes_parent_tag(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await self._make_user(db_session, "linkedparent_rm")
        child = Tags(title="school uniform", type=TagType.THEME)
        prev_parent = Tags(title="clothing", type=TagType.THEME)
        db_session.add_all([child, prev_parent])
        await db_session.commit()
        await db_session.refresh(child)
        await db_session.refresh(prev_parent)

        db_session.add(
            TagAuditLog(
                tag_id=child.tag_id,
                user_id=user.user_id,
                action_type=TagAuditActionType.PARENT_REMOVED,
                old_parent_id=prev_parent.tag_id,
            )
        )
        await db_session.commit()

        item = await self._find_metadata_item(client, user.user_id, "parent_removed")
        assert item["parent_tag"] is not None
        assert item["parent_tag"]["tag_id"] == prev_parent.tag_id
        assert item["parent_tag"]["title"] == "clothing"
        assert item["parent_tag"]["type"] == TagType.THEME
        assert item["alias_tag"] is None
        assert item["source_tag"] is None
        assert item["character_tag"] is None

    async def test_source_linked_includes_character_and_source_tags(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await self._make_user(db_session, "linkedsrc_set")
        character = Tags(title="Sakura Kinomoto", type=TagType.CHARACTER)
        source = Tags(title="Cardcaptor Sakura", type=TagType.SOURCE)
        db_session.add_all([character, source])
        await db_session.commit()
        await db_session.refresh(character)
        await db_session.refresh(source)

        db_session.add(
            TagAuditLog(
                tag_id=character.tag_id,
                user_id=user.user_id,
                action_type=TagAuditActionType.SOURCE_LINKED,
                character_tag_id=character.tag_id,
                source_tag_id=source.tag_id,
            )
        )
        await db_session.commit()

        item = await self._find_metadata_item(client, user.user_id, "source_linked")
        assert item["character_tag"] is not None
        assert item["character_tag"]["tag_id"] == character.tag_id
        assert item["character_tag"]["title"] == "Sakura Kinomoto"
        assert item["character_tag"]["type"] == TagType.CHARACTER
        assert item["source_tag"] is not None
        assert item["source_tag"]["tag_id"] == source.tag_id
        assert item["source_tag"]["title"] == "Cardcaptor Sakura"
        assert item["source_tag"]["type"] == TagType.SOURCE
        assert item["alias_tag"] is None
        assert item["parent_tag"] is None

    async def test_source_unlinked_includes_character_and_source_tags(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await self._make_user(db_session, "linkedsrc_rm")
        character = Tags(title="Tomoyo Daidouji", type=TagType.CHARACTER)
        source = Tags(title="Cardcaptor Sakura", type=TagType.SOURCE)
        db_session.add_all([character, source])
        await db_session.commit()
        await db_session.refresh(character)
        await db_session.refresh(source)

        db_session.add(
            TagAuditLog(
                tag_id=character.tag_id,
                user_id=user.user_id,
                action_type=TagAuditActionType.SOURCE_UNLINKED,
                character_tag_id=character.tag_id,
                source_tag_id=source.tag_id,
            )
        )
        await db_session.commit()

        item = await self._find_metadata_item(client, user.user_id, "source_unlinked")
        assert item["character_tag"] is not None
        assert item["character_tag"]["tag_id"] == character.tag_id
        assert item["character_tag"]["title"] == "Tomoyo Daidouji"
        assert item["character_tag"]["type"] == TagType.CHARACTER
        assert item["source_tag"] is not None
        assert item["source_tag"]["tag_id"] == source.tag_id
        assert item["source_tag"]["title"] == "Cardcaptor Sakura"
        assert item["source_tag"]["type"] == TagType.SOURCE
        assert item["alias_tag"] is None
        assert item["parent_tag"] is None

    async def test_type_change_includes_old_and_new_type(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await self._make_user(db_session, "linkedtype")
        tag = Tags(title="bunny ears", type=TagType.CHARACTER)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        db_session.add(
            TagAuditLog(
                tag_id=tag.tag_id,
                user_id=user.user_id,
                action_type=TagAuditActionType.TYPE_CHANGE,
                old_type=TagType.THEME,
                new_type=TagType.CHARACTER,
            )
        )
        await db_session.commit()

        item = await self._find_metadata_item(client, user.user_id, "type_change")
        assert item["old_type"] == TagType.THEME
        assert item["new_type"] == TagType.CHARACTER
        assert item["old_title"] is None
        assert item["new_title"] is None
        assert item["alias_tag"] is None
        assert item["parent_tag"] is None
        assert item["source_tag"] is None
        assert item["character_tag"] is None

    async def test_self_contained_actions_have_null_linked_tag_fields(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Actions with no linked tag (rename, type_change) should leave all four fields null."""
        user = await self._make_user(db_session, "linkedrename")
        tag = Tags(title="Cirno", type=TagType.CHARACTER)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        db_session.add_all(
            [
                TagAuditLog(
                    tag_id=tag.tag_id,
                    user_id=user.user_id,
                    action_type=TagAuditActionType.RENAME,
                    old_title="Cirno (9)",
                    new_title="Cirno",
                ),
                TagAuditLog(
                    tag_id=tag.tag_id,
                    user_id=user.user_id,
                    action_type=TagAuditActionType.TYPE_CHANGE,
                    old_type=TagType.THEME,
                    new_type=TagType.CHARACTER,
                ),
            ]
        )
        await db_session.commit()

        for action_type in ("rename", "type_change"):
            item = await self._find_metadata_item(client, user.user_id, action_type)
            assert item["alias_tag"] is None, action_type
            assert item["parent_tag"] is None, action_type
            assert item["source_tag"] is None, action_type
            assert item["character_tag"] is None, action_type

        # Symmetric pin: rename rows carry no type info; type_change rows
        # carry no title info. Catches accidental cross-population if the
        # serializer is ever refactored.
        rename_item = await self._find_metadata_item(client, user.user_id, "rename")
        assert rename_item["old_type"] is None
        assert rename_item["new_type"] is None
        type_change_item = await self._find_metadata_item(client, user.user_id, "type_change")
        assert type_change_item["old_title"] is None
        assert type_change_item["new_title"] is None

    async def test_non_metadata_rows_have_null_linked_tag_fields(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """tag_usage and status_change rows should never populate the new fields."""
        user = await self._make_user(db_session, "linkedusage")
        tag = Tags(title="aviator glasses", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        image = Images(
            filename="linkedusage1",
            ext="jpg",
            md5_hash="linkedusagemd5111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        db_session.add(
            TagHistory(
                image_id=image.image_id,
                tag_id=tag.tag_id,
                user_id=user.user_id,
                action="a",
                date=datetime.now(UTC),
            )
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/users/{user.user_id}/history")
        assert response.status_code == 200
        usage_items = [i for i in response.json()["items"] if i["type"] == "tag_usage"]
        assert len(usage_items) == 1
        item = usage_items[0]
        assert item["alias_tag"] is None
        assert item["parent_tag"] is None
        assert item["source_tag"] is None
        assert item["character_tag"] is None
        assert item["old_type"] is None
        assert item["new_type"] is None
