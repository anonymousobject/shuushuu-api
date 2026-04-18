"""Tests for sync_image_status_job enqueued on status change."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, settings
from app.core.security import create_access_token, get_password_hash
from app.models.image import Images
from app.models.permissions import GroupPerms, Groups, Perms, UserGroups
from app.models.user import Users


async def _create_user(
    db_session: AsyncSession,
    username: str = "statususer",
    email: str = "status@example.com",
) -> Users:
    user = Users(
        username=username,
        password=get_password_hash("TestPassword123!"),
        password_type="bcrypt",
        salt="saltsalt12345678",
        email=email,
        active=1,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _grant_permission(db_session: AsyncSession, user_id: int, perm_title: str):
    result = await db_session.execute(select(Perms).where(Perms.title == perm_title))
    perm = result.scalar_one_or_none()
    if not perm:
        perm = Perms(title=perm_title, desc=f"Test {perm_title}")
        db_session.add(perm)
        await db_session.flush()

    result = await db_session.execute(
        select(Groups).where(Groups.title == "r2_status_test_group")
    )
    group = result.scalar_one_or_none()
    if not group:
        group = Groups(title="r2_status_test_group", desc="R2 status test group")
        db_session.add(group)
        await db_session.flush()

    result = await db_session.execute(
        select(GroupPerms).where(
            GroupPerms.group_id == group.group_id, GroupPerms.perm_id == perm.perm_id
        )
    )
    if not result.scalar_one_or_none():
        db_session.add(GroupPerms(group_id=group.group_id, perm_id=perm.perm_id, permvalue=1))
        await db_session.flush()

    result = await db_session.execute(
        select(UserGroups).where(
            UserGroups.user_id == user_id, UserGroups.group_id == group.group_id
        )
    )
    if not result.scalar_one_or_none():
        db_session.add(UserGroups(user_id=user_id, group_id=group.group_id))

    await db_session.commit()


@pytest.mark.api
class TestStatusChangeEnqueuesSync:
    """Test sync_image_status_job enqueue on admin status change."""

    async def test_enqueues_sync_when_status_changes_and_r2_enabled(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        admin = await _create_user(db_session, "r2admin1", "r2admin1@test.com")
        await _grant_permission(db_session, admin.user_id, "image_edit")
        token = create_access_token(admin.user_id)

        image = Images(
            user_id=admin.user_id,
            filename="r2-status-test-1",
            ext="jpg",
            md5_hash="r2statustest1hash00000000001",
            status=ImageStatus.ACTIVE,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        with patch(
            "app.api.v1.admin.enqueue_job", new_callable=AsyncMock
        ) as mock_enqueue:
            response = await client.patch(
                f"/api/v1/admin/images/{image.image_id}",
                json={"status": ImageStatus.REVIEW},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        sync_calls = [
            c
            for c in mock_enqueue.await_args_list
            if c.args[0] == "sync_image_status_job"
        ]
        assert len(sync_calls) == 1
        assert sync_calls[0].kwargs["image_id"] == image.image_id
        assert sync_calls[0].kwargs["old_status"] == ImageStatus.ACTIVE
        assert sync_calls[0].kwargs["new_status"] == ImageStatus.REVIEW

    async def test_no_enqueue_when_status_unchanged(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        admin = await _create_user(db_session, "r2admin2", "r2admin2@test.com")
        await _grant_permission(db_session, admin.user_id, "image_edit")
        token = create_access_token(admin.user_id)

        image = Images(
            user_id=admin.user_id,
            filename="r2-status-test-2",
            ext="jpg",
            md5_hash="r2statustest2hash00000000002",
            status=ImageStatus.ACTIVE,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        with patch(
            "app.api.v1.admin.enqueue_job", new_callable=AsyncMock
        ) as mock_enqueue:
            response = await client.patch(
                f"/api/v1/admin/images/{image.image_id}",
                json={"locked": True},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        sync_calls = [
            c
            for c in mock_enqueue.await_args_list
            if c.args[0] == "sync_image_status_job"
        ]
        assert sync_calls == []

    async def test_no_enqueue_when_r2_disabled(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", False)
        admin = await _create_user(db_session, "r2admin3", "r2admin3@test.com")
        await _grant_permission(db_session, admin.user_id, "image_edit")
        token = create_access_token(admin.user_id)

        image = Images(
            user_id=admin.user_id,
            filename="r2-status-test-3",
            ext="jpg",
            md5_hash="r2statustest3hash00000000003",
            status=ImageStatus.ACTIVE,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        with patch(
            "app.api.v1.admin.enqueue_job", new_callable=AsyncMock
        ) as mock_enqueue:
            response = await client.patch(
                f"/api/v1/admin/images/{image.image_id}",
                json={"status": ImageStatus.REVIEW},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        sync_calls = [
            c
            for c in mock_enqueue.await_args_list
            if c.args[0] == "sync_image_status_job"
        ]
        assert sync_calls == []

    async def test_owner_spoiler_enqueues_sync(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        """Owner marking ACTIVE→SPOILER via PATCH /api/v1/images/ also enqueues."""
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        owner = await _create_user(db_session, "r2owner", "r2owner@test.com")
        token = create_access_token(owner.user_id)

        image = Images(
            user_id=owner.user_id,
            filename="r2-status-test-4",
            ext="jpg",
            md5_hash="r2statustest4hash00000000004",
            status=ImageStatus.ACTIVE,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        with patch(
            "app.api.v1.images.enqueue_job", new_callable=AsyncMock
        ) as mock_enqueue:
            response = await client.patch(
                f"/api/v1/images/{image.image_id}",
                json={"status": ImageStatus.SPOILER},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        sync_calls = [
            c
            for c in mock_enqueue.await_args_list
            if c.args[0] == "sync_image_status_job"
        ]
        assert len(sync_calls) == 1
        assert sync_calls[0].kwargs["old_status"] == ImageStatus.ACTIVE
        assert sync_calls[0].kwargs["new_status"] == ImageStatus.SPOILER
