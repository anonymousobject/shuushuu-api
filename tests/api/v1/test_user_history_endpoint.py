"""
Tests for GET /users/{user_id}/history endpoint.

Tests that user history (all changes made by a user) can be retrieved with:
- Tag metadata changes (rename, type_change, etc.)
- Tag usage (add/remove on images)
- Status changes (only visible statuses: REPOST, SPOILER, ACTIVE)

Hidden statuses (REVIEW, LOW_QUALITY, INAPPROPRIATE, OTHER) should be excluded.
"""

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

    async def test_pagination_works(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
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
        response = await client.get(
            f"/api/v1/users/{user.user_id}/history?page=1&per_page=2"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 1
        assert data["per_page"] == 2
        assert len(data["items"]) == 2
        assert data["total"] == 5

        # Get second page
        response = await client.get(
            f"/api/v1/users/{user.user_id}/history?page=2&per_page=2"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 2
        assert len(data["items"]) == 2

        # Get third page
        response = await client.get(
            f"/api/v1/users/{user.user_id}/history?page=3&per_page=2"
        )
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
        now = datetime.now(UTC).replace(tzinfo=None)
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

        now = datetime.now(UTC).replace(tzinfo=None)

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
