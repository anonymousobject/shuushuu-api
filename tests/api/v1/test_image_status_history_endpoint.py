"""
Tests for GET /images/{image_id}/status-history endpoint.

Tests that image status history (status changes) can be retrieved
with proper pagination, status labels, and user visibility rules.

User visibility rules:
- Show user for: REPOST (-1), SPOILER (2), ACTIVE (1)
- Hide user for: REVIEW (-4), LOW_QUALITY (-3), INAPPROPRIATE (-2), OTHER (0)
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DeactivationReason, ImageStatus
from app.core.security import get_password_hash
from app.models.image import Images
from app.models.image_report import ImageReports
from app.models.image_status_history import ImageStatusHistory
from app.models.permissions import GroupPerms, Groups, Perms, UserGroups
from app.models.user import Users


@pytest.mark.api
class TestGetImageStatusHistory:
    """Tests for GET /images/{image_id}/status-history endpoint."""

    async def test_returns_status_history_entries_for_image(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Should return status history entries for the specified image."""
        # Create a user
        user = Users(
            username="statushistuser1",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="statushistuser1@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="statushist1",
            ext="jpg",
            md5_hash="statushistmd511111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
            status=ImageStatus.ACTIVE,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create status history entries
        history1 = ImageStatusHistory(
            image_id=image.image_id,
            old_status=ImageStatus.REVIEW,
            new_status=ImageStatus.ACTIVE,
            user_id=user.user_id,
        )
        history2 = ImageStatusHistory(
            image_id=image.image_id,
            old_status=ImageStatus.ACTIVE,
            new_status=ImageStatus.SPOILER,
            user_id=user.user_id,
        )
        db_session.add_all([history1, history2])
        await db_session.commit()

        # GET image status history
        response = await client.get(f"/api/v1/images/{image.image_id}/status-history")
        assert response.status_code == 200

        data = response.json()
        assert "total" in data
        assert "page" in data
        assert "per_page" in data
        assert "items" in data
        assert data["total"] == 2
        assert len(data["items"]) == 2

        # Verify items contain expected fields
        for item in data["items"]:
            assert "id" in item
            assert "image_id" in item
            assert "old_status" in item
            assert "new_status" in item
            assert "created_at" in item
            assert item["image_id"] == image.image_id

    async def test_includes_status_labels(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Status history entries should include human-readable status labels."""
        # Create a user
        user = Users(
            username="statuslabeluser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="statuslabeluser@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="statuslabel1",
            ext="jpg",
            md5_hash="statuslabelmd5111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
            status=ImageStatus.ACTIVE,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create status history entry with specific statuses
        history = ImageStatusHistory(
            image_id=image.image_id,
            old_status=ImageStatus.REVIEW,
            new_status=ImageStatus.ACTIVE,
            user_id=user.user_id,
        )
        db_session.add(history)
        await db_session.commit()

        # GET image status history
        response = await client.get(f"/api/v1/images/{image.image_id}/status-history")
        assert response.status_code == 200

        data = response.json()
        assert len(data["items"]) == 1

        item = data["items"][0]
        assert "old_status_label" in item
        assert "new_status_label" in item
        assert item["old_status_label"] == "review"
        assert item["new_status_label"] == "active"

    async def test_shows_user_for_visible_statuses(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """User should be shown when old or new status is REPOST, SPOILER, or ACTIVE."""
        # Create a user
        user = Users(
            username="visiblestatususer",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="visiblestatususer@example.com",
            active=1,
            avatar="visible-avatar.jpg",
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="visiblestatus1",
            ext="jpg",
            md5_hash="visiblestatusmd51111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
            status=ImageStatus.ACTIVE,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create history entries with visible statuses
        # ACTIVE -> SPOILER (both visible)
        history1 = ImageStatusHistory(
            image_id=image.image_id,
            old_status=ImageStatus.ACTIVE,
            new_status=ImageStatus.SPOILER,
            user_id=user.user_id,
        )
        # REVIEW -> ACTIVE (new_status is visible)
        history2 = ImageStatusHistory(
            image_id=image.image_id,
            old_status=ImageStatus.REVIEW,
            new_status=ImageStatus.ACTIVE,
            user_id=user.user_id,
        )
        # ACTIVE -> REPOST (both visible)
        history3 = ImageStatusHistory(
            image_id=image.image_id,
            old_status=ImageStatus.ACTIVE,
            new_status=ImageStatus.REPOST,
            user_id=user.user_id,
        )
        db_session.add_all([history1, history2, history3])
        await db_session.commit()

        # GET image status history
        response = await client.get(f"/api/v1/images/{image.image_id}/status-history")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 3

        # All entries should show user because at least one status is visible
        for item in data["items"]:
            assert item["user"] is not None
            assert item["user"]["user_id"] == user.user_id
            assert item["user"]["username"] == "visiblestatususer"
            assert item["user"]["avatar"] == "visible-avatar.jpg"

    async def test_hides_user_for_hidden_statuses(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """User should be hidden when both old and new status are hidden statuses."""
        # Create a user
        user = Users(
            username="hiddenstatususer",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="hiddenstatususer@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="hiddenstatus1",
            ext="jpg",
            md5_hash="hiddenstatusmd51111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
            status=ImageStatus.REVIEW,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create history entries where BOTH old and new are hidden statuses
        # REVIEW -> LOW_QUALITY (both hidden)
        history1 = ImageStatusHistory(
            image_id=image.image_id,
            old_status=ImageStatus.REVIEW,
            new_status=ImageStatus.LOW_QUALITY,
            user_id=user.user_id,
        )
        # LOW_QUALITY -> INAPPROPRIATE (both hidden)
        history2 = ImageStatusHistory(
            image_id=image.image_id,
            old_status=ImageStatus.LOW_QUALITY,
            new_status=ImageStatus.INAPPROPRIATE,
            user_id=user.user_id,
        )
        # INAPPROPRIATE -> OTHER (both hidden)
        history3 = ImageStatusHistory(
            image_id=image.image_id,
            old_status=ImageStatus.INAPPROPRIATE,
            new_status=ImageStatus.OTHER,
            user_id=user.user_id,
        )
        db_session.add_all([history1, history2, history3])
        await db_session.commit()

        # GET image status history
        response = await client.get(f"/api/v1/images/{image.image_id}/status-history")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 3

        # All entries should hide user because both statuses are hidden
        for item in data["items"]:
            assert item["user"] is None

    async def test_returns_404_for_nonexistent_image(self, client: AsyncClient) -> None:
        """Should return 404 for nonexistent image."""
        response = await client.get("/api/v1/images/99999999/status-history")
        assert response.status_code == 404

    async def test_pagination_works(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Should support pagination."""
        # Create a user
        user = Users(
            username="statuspageuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="statuspageuser@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="statuspage1",
            ext="jpg",
            md5_hash="statuspagemd5111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
            status=ImageStatus.ACTIVE,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create 5 status history entries
        for i in range(5):
            history = ImageStatusHistory(
                image_id=image.image_id,
                old_status=ImageStatus.ACTIVE,
                new_status=ImageStatus.SPOILER,
                user_id=user.user_id,
            )
            db_session.add(history)
        await db_session.commit()

        # Get first page with per_page=2
        response = await client.get(
            f"/api/v1/images/{image.image_id}/status-history?page=1&per_page=2"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 1
        assert data["per_page"] == 2
        assert len(data["items"]) == 2
        assert data["total"] == 5

        # Get second page
        response = await client.get(
            f"/api/v1/images/{image.image_id}/status-history?page=2&per_page=2"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 2
        assert len(data["items"]) == 2

        # Get third page
        response = await client.get(
            f"/api/v1/images/{image.image_id}/status-history?page=3&per_page=2"
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
            username="statusorderuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="statusorderuser@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="statusorder1",
            ext="jpg",
            md5_hash="statusordermd5111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
            status=ImageStatus.ACTIVE,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create history entries in order (first, second, third)
        # id is auto-increment, so higher ID = more recent
        history1 = ImageStatusHistory(
            image_id=image.image_id,
            old_status=ImageStatus.REVIEW,
            new_status=ImageStatus.ACTIVE,
            user_id=user.user_id,
        )
        db_session.add(history1)
        await db_session.commit()
        await db_session.refresh(history1)

        history2 = ImageStatusHistory(
            image_id=image.image_id,
            old_status=ImageStatus.ACTIVE,
            new_status=ImageStatus.SPOILER,
            user_id=user.user_id,
        )
        db_session.add(history2)
        await db_session.commit()
        await db_session.refresh(history2)

        history3 = ImageStatusHistory(
            image_id=image.image_id,
            old_status=ImageStatus.SPOILER,
            new_status=ImageStatus.ACTIVE,
            user_id=user.user_id,
        )
        db_session.add(history3)
        await db_session.commit()
        await db_session.refresh(history3)

        # GET image status history
        response = await client.get(f"/api/v1/images/{image.image_id}/status-history")
        assert response.status_code == 200

        data = response.json()
        assert len(data["items"]) == 3

        # Most recent (highest ID) should be first
        assert data["items"][0]["id"] == history3.id
        assert data["items"][1]["id"] == history2.id
        assert data["items"][2]["id"] == history1.id

    async def test_handles_null_user(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Should handle history entries with null user_id gracefully."""
        # Create a user for image ownership
        user = Users(
            username="statusnulluserowner",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="statusnulluserowner@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="statusnulluser1",
            ext="jpg",
            md5_hash="statusnullusermd511111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
            status=ImageStatus.ACTIVE,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create history entry with null user (system action) - visible status
        history = ImageStatusHistory(
            image_id=image.image_id,
            old_status=ImageStatus.REVIEW,
            new_status=ImageStatus.ACTIVE,
            user_id=None,  # System action
        )
        db_session.add(history)
        await db_session.commit()

        # GET image status history
        response = await client.get(f"/api/v1/images/{image.image_id}/status-history")
        assert response.status_code == 200

        data = response.json()
        assert len(data["items"]) == 1

        # User should be null (system action)
        assert data["items"][0]["user"] is None

    async def test_all_status_labels_correct(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Verify all status labels map correctly."""
        # Create a user
        user = Users(
            username="alllabelsuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="alllabelsuser@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="alllabels1",
            ext="jpg",
            md5_hash="alllabelsmd51111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
            status=ImageStatus.ACTIVE,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create history entries for all status types
        status_pairs = [
            (ImageStatus.REVIEW, ImageStatus.LOW_QUALITY, "review", "low_quality"),
            (ImageStatus.LOW_QUALITY, ImageStatus.INAPPROPRIATE, "low_quality", "inappropriate"),
            (ImageStatus.INAPPROPRIATE, ImageStatus.REPOST, "inappropriate", "repost"),
            (ImageStatus.REPOST, ImageStatus.OTHER, "repost", "deactivated"),
            (ImageStatus.OTHER, ImageStatus.ACTIVE, "deactivated", "active"),
            (ImageStatus.ACTIVE, ImageStatus.SPOILER, "active", "spoiler"),
        ]

        for old_status, new_status, _, _ in status_pairs:
            history = ImageStatusHistory(
                image_id=image.image_id,
                old_status=old_status,
                new_status=new_status,
                user_id=user.user_id,
            )
            db_session.add(history)
        await db_session.commit()

        # GET image status history
        response = await client.get(f"/api/v1/images/{image.image_id}/status-history")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 6

        # Create a map of (old_status, new_status) -> item for verification
        items_by_statuses = {
            (item["old_status"], item["new_status"]): item for item in data["items"]
        }

        # Verify each status label
        for old_status, new_status, old_label, new_label in status_pairs:
            item = items_by_statuses.get((old_status, new_status))
            assert item is not None, f"Missing entry for {old_status} -> {new_status}"
            assert item["old_status_label"] == old_label
            assert item["new_status_label"] == new_label


async def _make_user(db_session, username, password="TestPassword123!"):
    user = Users(username=username, password=get_password_hash(password),
                 password_type="bcrypt", salt="", email=f"{username}@example.com", active=1)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _grant_image_edit(db_session, user_id):
    perm = (await db_session.execute(select(Perms).where(Perms.title == "image_edit"))).scalar_one_or_none()
    if not perm:
        perm = Perms(title="image_edit", desc="edit images")
        db_session.add(perm)
        await db_session.flush()
    group = Groups(title=f"hist_mod_{user_id}", desc="hist mod group")
    db_session.add(group)
    await db_session.flush()
    db_session.add(GroupPerms(group_id=group.group_id, perm_id=perm.perm_id, permvalue=1))
    db_session.add(UserGroups(user_id=user_id, group_id=group.group_id))
    await db_session.commit()


async def _grant_report_view(db_session, user_id):
    perm = (await db_session.execute(select(Perms).where(Perms.title == "report_view"))).scalar_one_or_none()
    if not perm:
        perm = Perms(title="report_view", desc="view reports")
        db_session.add(perm)
        await db_session.flush()
    group = Groups(title=f"hist_rv_{user_id}", desc="hist report-view group")
    db_session.add(group)
    await db_session.flush()
    db_session.add(GroupPerms(group_id=group.group_id, perm_id=perm.perm_id, permvalue=1))
    db_session.add(UserGroups(user_id=user_id, group_id=group.group_id))
    await db_session.commit()


async def _login(client, username, password="TestPassword123!"):
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return r.json()["access_token"]


async def _seed_deactivation(db_session, owner):
    image = Images(filename="histvis", ext="jpg", md5_hash="a" * 32,
                   user_id=owner.user_id, width=100, height=100, filesize=1000,
                   status=ImageStatus.DEACTIVATED,
                   reason_category=DeactivationReason.LOW_QUALITY,
                   status_reason="blurry and low res")
    db_session.add(image)
    await db_session.commit()
    await db_session.refresh(image)
    db_session.add(ImageStatusHistory(
        image_id=image.image_id, old_status=ImageStatus.ACTIVE,
        new_status=ImageStatus.DEACTIVATED, user_id=owner.user_id,
        reason_category=DeactivationReason.LOW_QUALITY, reason="blurry and low res"))
    await db_session.commit()
    return image


@pytest.mark.api
class TestStatusHistoryReasonVisibility:
    """reason_category is public; free-text reason is owner/mod-only for hidden-status transitions."""

    async def test_reason_hidden_from_anonymous_for_deactivation(self, client, db_session):
        owner = await _make_user(db_session, "histowner1")
        image = await _seed_deactivation(db_session, owner)

        r = await client.get(f"/api/v1/images/{image.image_id}/status-history")
        assert r.status_code == 200
        row = r.json()["items"][0]
        assert row["reason_category"] == DeactivationReason.LOW_QUALITY  # category always shown
        assert row["reason"] is None  # free-text hidden from anonymous on a hidden-status transition

    async def test_reason_visible_to_owner_and_mod(self, client, db_session):
        owner = await _make_user(db_session, "histowner2")
        image = await _seed_deactivation(db_session, owner)

        owner_token = await _login(client, owner.username)
        r = await client.get(f"/api/v1/images/{image.image_id}/status-history",
                             headers={"Authorization": f"Bearer {owner_token}"})
        assert r.json()["items"][0]["reason"] == "blurry and low res"

        mod = await _make_user(db_session, "histmod2")
        await _grant_image_edit(db_session, mod.user_id)
        mod_token = await _login(client, mod.username)
        r = await client.get(f"/api/v1/images/{image.image_id}/status-history",
                             headers={"Authorization": f"Bearer {mod_token}"})
        assert r.json()["items"][0]["reason"] == "blurry and low res"

    async def test_reason_public_for_spoiler_transition(self, client, db_session):
        owner = await _make_user(db_session, "histowner3")
        image = Images(filename="histspoil", ext="jpg", md5_hash="b" * 32,
                       user_id=owner.user_id, width=100, height=100, filesize=1000,
                       status=ImageStatus.SPOILER, status_reason="mild nudity")
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)
        db_session.add(ImageStatusHistory(
            image_id=image.image_id, old_status=ImageStatus.ACTIVE,
            new_status=ImageStatus.SPOILER, user_id=owner.user_id, reason="mild nudity"))
        await db_session.commit()

        r = await client.get(f"/api/v1/images/{image.image_id}/status-history")
        assert r.status_code == 200
        row = r.json()["items"][0]
        assert row["reason"] == "mild nudity"  # public: destination status is publicly visible
        assert row["reason_category"] is None

    async def test_restore_reason_hidden_from_anonymous(self, client, db_session):
        """Un-hiding from a hidden state carries moderation rationale -> owner/mod only."""
        owner = await _make_user(db_session, "histowner4")
        image = Images(filename="histrestore", ext="jpg", md5_hash="d" * 32,
                       user_id=owner.user_id, width=100, height=100, filesize=1000,
                       status=ImageStatus.ACTIVE, status_reason="appeal granted")
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)
        db_session.add(ImageStatusHistory(
            image_id=image.image_id, old_status=ImageStatus.DEACTIVATED,
            new_status=ImageStatus.ACTIVE, user_id=owner.user_id, reason="appeal granted"))
        await db_session.commit()

        r = await client.get(f"/api/v1/images/{image.image_id}/status-history")
        assert r.status_code == 200
        assert r.json()["items"][0]["reason"] is None  # source was hidden -> reason hidden

    async def test_unspoiler_reason_public(self, client, db_session):
        """SPOILER -> ACTIVE is visible -> visible: the reason stays public."""
        owner = await _make_user(db_session, "histowner5")
        image = Images(filename="histunspoil", ext="jpg", md5_hash="e" * 32,
                       user_id=owner.user_id, width=100, height=100, filesize=1000,
                       status=ImageStatus.ACTIVE, status_reason="not actually a spoiler")
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)
        db_session.add(ImageStatusHistory(
            image_id=image.image_id, old_status=ImageStatus.SPOILER,
            new_status=ImageStatus.ACTIVE, user_id=owner.user_id, reason="not actually a spoiler"))
        await db_session.commit()

        r = await client.get(f"/api/v1/images/{image.image_id}/status-history")
        assert r.status_code == 200
        assert r.json()["items"][0]["reason"] == "not actually a spoiler"

    async def test_report_id_visible_to_mod_hidden_from_anon(self, client, db_session):
        """The originating report_id is exposed to REPORT_VIEW mods, NULL to others."""
        owner = await _make_user(db_session, "histreport1")
        image = Images(filename="histrep", ext="jpg", md5_hash="f" * 32,
                       user_id=owner.user_id, width=100, height=100, filesize=1000,
                       status=ImageStatus.DEACTIVATED)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)
        report = ImageReports(image_id=image.image_id, user_id=owner.user_id, category=2, status=1)
        db_session.add(report)
        await db_session.commit()
        await db_session.refresh(report)
        db_session.add(ImageStatusHistory(
            image_id=image.image_id, old_status=ImageStatus.ACTIVE,
            new_status=ImageStatus.DEACTIVATED, user_id=owner.user_id,
            reason_category=DeactivationReason.INAPPROPRIATE, reason="x",
            report_id=report.report_id))
        await db_session.commit()

        # Anonymous: report_id hidden.
        r = await client.get(f"/api/v1/images/{image.image_id}/status-history")
        assert r.status_code == 200
        assert r.json()["items"][0]["report_id"] is None

        # Mod with report_view: report_id exposed.
        mod = await _make_user(db_session, "histreportmod1")
        await _grant_report_view(db_session, mod.user_id)
        token = await _login(client, mod.username)
        r = await client.get(
            f"/api/v1/images/{image.image_id}/status-history",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.json()["items"][0]["report_id"] == report.report_id
