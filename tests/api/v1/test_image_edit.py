"""Tests for PATCH /api/v1/images/{image_id} endpoint."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_password_hash
from app.models.image import Images
from app.models.permissions import GroupPerms, Groups, Perms, UserGroups
from app.models.user import Users


async def create_user(
    db_session: AsyncSession,
    username: str = "edituser",
    email: str = "edit@example.com",
    admin: int = 0,
) -> Users:
    """Create a user for testing."""
    user = Users(
        username=username,
        password=get_password_hash("TestPassword123!"),
        password_type="bcrypt",
        salt="saltsalt12345678",
        email=email,
        active=1,
        admin=admin,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def create_image(
    db_session: AsyncSession,
    user_id: int,
    caption: str = "original caption",
    miscmeta: str | None = None,
) -> Images:
    """Create a test image."""
    image = Images(
        filename="test-edit-001",
        ext="jpg",
        original_filename="test.jpg",
        md5_hash="abcdef1234567890abcdef1234567890",
        filesize=100000,
        width=800,
        height=600,
        caption=caption,
        miscmeta=miscmeta,
        user_id=user_id,
        status=1,
    )
    db_session.add(image)
    await db_session.commit()
    await db_session.refresh(image)
    return image


async def grant_permission(db_session: AsyncSession, user_id: int, perm_title: str):
    """Grant a permission to a user via a group."""
    result = await db_session.execute(select(Perms).where(Perms.title == perm_title))
    perm = result.scalar_one_or_none()
    if not perm:
        perm = Perms(title=perm_title, desc=f"Test permission {perm_title}")
        db_session.add(perm)
        await db_session.flush()

    result = await db_session.execute(
        select(Groups).where(Groups.title == "edit_test_group")
    )
    group = result.scalar_one_or_none()
    if not group:
        group = Groups(title="edit_test_group", desc="Image edit test group")
        db_session.add(group)
        await db_session.flush()

    result = await db_session.execute(
        select(GroupPerms).where(
            GroupPerms.group_id == group.group_id, GroupPerms.perm_id == perm.perm_id
        )
    )
    if not result.scalar_one_or_none():
        group_perm = GroupPerms(group_id=group.group_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(group_perm)
        await db_session.flush()

    result = await db_session.execute(
        select(UserGroups).where(
            UserGroups.user_id == user_id, UserGroups.group_id == group.group_id
        )
    )
    if not result.scalar_one_or_none():
        user_group = UserGroups(user_id=user_id, group_id=group.group_id)
        db_session.add(user_group)

    await db_session.commit()


def auth_header(user: Users) -> dict[str, str]:
    """Create an Authorization header for a user."""
    token = create_access_token(user.user_id)
    return {"Authorization": f"Bearer {token}"}


class TestImageEdit:
    """Tests for PATCH /api/v1/images/{image_id}."""

    @pytest.mark.asyncio
    async def test_owner_can_update_caption(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Image owner can update their image's caption."""
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id)

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"caption": "new caption"},
            headers=auth_header(owner),
        )

        assert response.status_code == 200
        assert response.json()["caption"] == "new caption"

    @pytest.mark.asyncio
    async def test_owner_can_update_miscmeta(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Image owner can update their image's miscmeta."""
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id)

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"miscmeta": "pixiv: 99999"},
            headers=auth_header(owner),
        )

        assert response.status_code == 200
        assert response.json()["miscmeta"] == "pixiv: 99999"

    @pytest.mark.asyncio
    async def test_non_owner_without_permission_gets_403(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Non-owner without IMAGE_EDIT_META permission cannot edit."""
        owner = await create_user(db_session, username="owner", email="owner@test.com")
        other = await create_user(db_session, username="other", email="other@test.com")
        image = await create_image(db_session, owner.user_id)

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"caption": "hacked"},
            headers=auth_header(other),
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_user_with_image_edit_meta_permission_can_edit(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """User with IMAGE_EDIT_META permission can edit any image."""
        owner = await create_user(db_session, username="owner2", email="owner2@test.com")
        mod = await create_user(db_session, username="mod", email="mod@test.com")
        image = await create_image(db_session, owner.user_id, caption="before")

        await grant_permission(db_session, mod.user_id, "image_edit_meta")

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"caption": "mod edited"},
            headers=auth_header(mod),
        )

        assert response.status_code == 200
        assert response.json()["caption"] == "mod edited"

    @pytest.mark.asyncio
    async def test_admin_can_edit_any_image(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Admin users can edit any image."""
        owner = await create_user(db_session, username="owner3", email="owner3@test.com")
        admin = await create_user(
            db_session, username="admin", email="admin@test.com", admin=1
        )
        image = await create_image(db_session, owner.user_id, caption="before")

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"caption": "admin edited"},
            headers=auth_header(admin),
        )

        assert response.status_code == 200
        assert response.json()["caption"] == "admin edited"

    @pytest.mark.asyncio
    async def test_empty_update_returns_400(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Empty update body (no fields set) returns 400."""
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id)

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={},
            headers=auth_header(owner),
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_image_not_found_returns_404(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Editing a nonexistent image returns 404."""
        user = await create_user(db_session)

        response = await client.patch(
            "/api/v1/images/999999",
            json={"caption": "ghost"},
            headers=auth_header(user),
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_partial_update_only_changes_provided_fields(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Setting caption does not clear miscmeta, and vice versa."""
        owner = await create_user(db_session)
        image = await create_image(
            db_session, owner.user_id, caption="keep me", miscmeta="keep me too"
        )

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"caption": "changed"},
            headers=auth_header(owner),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["caption"] == "changed"
        assert data["miscmeta"] == "keep me too"
