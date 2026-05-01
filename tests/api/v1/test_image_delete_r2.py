"""Tests for r2_delete_image_job enqueued on hard delete."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, settings
from app.core.r2_constants import R2Location
from app.core.security import create_access_token, get_password_hash
from app.models.image import Images, VariantStatus
from app.models.permissions import GroupPerms, Groups, Perms, UserGroups
from app.models.user import Users


async def _create_admin_with_delete(db_session: AsyncSession) -> Users:
    user = Users(
        username="r2deleter",
        password=get_password_hash("TestPassword123!"),
        password_type="bcrypt",
        salt="saltsalt12345678",
        email="r2deleter@example.com",
        active=1,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    perm = Perms(title="image_delete", desc="Delete images")
    db_session.add(perm)
    await db_session.flush()

    group = Groups(title="r2_delete_test_group", desc="R2 delete test group")
    db_session.add(group)
    await db_session.flush()

    db_session.add(GroupPerms(group_id=group.group_id, perm_id=perm.perm_id, permvalue=1))
    db_session.add(UserGroups(user_id=user.user_id, group_id=group.group_id))
    await db_session.commit()
    return user


@pytest.mark.api
class TestDeleteEnqueuesR2:
    async def test_enqueues_delete_with_prior_location(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        admin = await _create_admin_with_delete(db_session)
        token = create_access_token(admin.user_id)

        image = Images(
            user_id=admin.user_id,
            filename="r2-delete-test-1",
            ext="jpg",
            md5_hash="r2deletetest1hash0000001",
            status=ImageStatus.ACTIVE,
            r2_location=R2Location.PUBLIC,
            medium=VariantStatus.READY,
            large=VariantStatus.NONE,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)
        image_id = image.image_id

        with patch(
            "app.api.v1.images.enqueue_job", new_callable=AsyncMock
        ) as mock_enqueue, patch(
            "app.api.v1.images.remove_from_iqdb", return_value=True
        ):
            response = await client.delete(
                f"/api/v1/images/{image_id}?reason=test",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 204
        delete_calls = [
            c
            for c in mock_enqueue.await_args_list
            if c.args[0] == "r2_delete_image_job"
        ]
        assert len(delete_calls) == 1
        assert delete_calls[0].kwargs["r2_location"] == int(R2Location.PUBLIC)
        assert delete_calls[0].kwargs["filename"] == "r2-delete-test-1"
        assert delete_calls[0].kwargs["ext"] == "jpg"
        assert "fullsize" in delete_calls[0].kwargs["variants"]
        assert "thumbs" in delete_calls[0].kwargs["variants"]
        assert "medium" in delete_calls[0].kwargs["variants"]
        assert "large" not in delete_calls[0].kwargs["variants"]

    async def test_no_enqueue_when_r2_disabled(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", False)
        admin = await _create_admin_with_delete(db_session)
        token = create_access_token(admin.user_id)

        image = Images(
            user_id=admin.user_id,
            filename="r2-delete-test-2",
            ext="jpg",
            md5_hash="r2deletetest2hash0000002",
            status=ImageStatus.ACTIVE,
            r2_location=R2Location.PUBLIC,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        with patch(
            "app.api.v1.images.enqueue_job", new_callable=AsyncMock
        ) as mock_enqueue, patch(
            "app.api.v1.images.remove_from_iqdb", return_value=True
        ):
            await client.delete(
                f"/api/v1/images/{image.image_id}?reason=test",
                headers={"Authorization": f"Bearer {token}"},
            )
        delete_calls = [
            c
            for c in mock_enqueue.await_args_list
            if c.args[0] == "r2_delete_image_job"
        ]
        assert delete_calls == []
