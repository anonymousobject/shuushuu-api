"""
Tests for the permission system.

Tests cover:
- Permission resolution from groups
- Permission resolution from direct user assignments
- Combined permissions (groups + user overrides)
- Permission checking functions
- FastAPI dependencies for route protection
"""

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permission_deps import (
    require_all_permissions,
    require_any_permission,
    require_permission,
)
from app.core.permissions import (
    Permission,
    get_user_permissions,
    has_all_permissions,
    has_any_permission,
    has_permission,
)
from app.models.permissions import GroupPerms, Groups, Perms, UserGroups, UserPerms
from app.models.user import Users


@pytest.fixture
async def test_permissions(db_session: AsyncSession) -> dict[str, int]:
    """Create test permissions and return their IDs."""
    # Create test permissions
    perm1 = Perms(title="image_edit", desc="Edit images")
    perm2 = Perms(title="tag_create", desc="Create tags")
    perm3 = Perms(title="user_ban", desc="Ban users")
    perm4 = Perms(title="level_admin", desc="Admin level")

    db_session.add_all([perm1, perm2, perm3, perm4])
    await db_session.commit()
    await db_session.refresh(perm1)
    await db_session.refresh(perm2)
    await db_session.refresh(perm3)
    await db_session.refresh(perm4)

    return {
        "image_edit": perm1.perm_id,
        "tag_create": perm2.perm_id,
        "user_ban": perm3.perm_id,
        "level_admin": perm4.perm_id,
    }


@pytest.fixture
async def test_groups(db_session: AsyncSession, test_permissions: dict[str, int]) -> dict[str, int]:
    """Create test groups with permissions."""
    # Create groups
    group_mod = Groups(title="Moderators", desc="Site moderators")
    group_tagger = Groups(title="Taggers", desc="Image taggers")

    db_session.add_all([group_mod, group_tagger])
    await db_session.commit()
    await db_session.refresh(group_mod)
    await db_session.refresh(group_tagger)

    # Assign permissions to moderator group
    db_session.add_all(
        [
            GroupPerms(
                group_id=group_mod.group_id, perm_id=test_permissions["image_edit"], permvalue=1
            ),
            GroupPerms(group_id=group_mod.group_id, perm_id=test_permissions["user_ban"], permvalue=1),
        ]
    )

    # Assign permissions to tagger group
    db_session.add(
        GroupPerms(
            group_id=group_tagger.group_id, perm_id=test_permissions["tag_create"], permvalue=1
        )
    )

    await db_session.commit()

    return {"moderators": group_mod.group_id, "taggers": group_tagger.group_id}


@pytest.fixture
async def test_user_with_group(db_session: AsyncSession, test_groups: dict[str, int]) -> Users:
    """Create a test user assigned to the moderators group."""
    user = Users(
        username="mod_user",
        email="mod@example.com",
        password="hashed",
        salt="salt",
        active=1,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    # Assign user to moderators group
    db_session.add(UserGroups(user_id=user.user_id, group_id=test_groups["moderators"]))
    await db_session.commit()

    return user


@pytest.fixture
async def test_user_with_direct_perm(db_session: AsyncSession, test_permissions: dict[str, int]) -> Users:
    """Create a test user with direct permission assignment."""
    user = Users(
        username="tagged_user",
        email="tagged@example.com",
        password="hashed",
        salt="salt",
        active=1,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    # Assign direct permission
    db_session.add(
        UserPerms(user_id=user.user_id, perm_id=test_permissions["tag_create"], permvalue=1)
    )
    await db_session.commit()

    return user


@pytest.fixture
async def test_user_with_both(
    db_session: AsyncSession, test_groups: dict[str, int], test_permissions: dict[str, int]
) -> Users:
    """Create a user with both group and direct permissions."""
    user = Users(
        username="combo_user",
        email="combo@example.com",
        password="hashed",
        salt="salt",
        active=1,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    # Add to taggers group
    db_session.add(UserGroups(user_id=user.user_id, group_id=test_groups["taggers"]))

    # Add direct admin permission
    db_session.add(
        UserPerms(user_id=user.user_id, perm_id=test_permissions["level_admin"], permvalue=1)
    )

    await db_session.commit()
    return user


class TestPermissionResolution:
    """Test permission resolution from database."""

    async def test_get_permissions_from_group(
        self, db_session: AsyncSession, test_user_with_group: Users
    ):
        """User should get permissions from their group."""
        permissions = await get_user_permissions(db_session, test_user_with_group.user_id)

        assert "image_edit" in permissions
        assert "user_ban" in permissions
        assert "tag_create" not in permissions

    async def test_get_permissions_from_direct_assignment(
        self, db_session: AsyncSession, test_user_with_direct_perm: Users
    ):
        """User should get direct permissions."""
        permissions = await get_user_permissions(db_session, test_user_with_direct_perm.user_id)

        assert "tag_create" in permissions
        assert "image_edit" not in permissions

    async def test_get_permissions_combined(
        self, db_session: AsyncSession, test_user_with_both: Users
    ):
        """User should get permissions from both groups and direct assignments."""
        permissions = await get_user_permissions(db_session, test_user_with_both.user_id)

        # From group
        assert "tag_create" in permissions
        # From direct assignment
        assert "level_admin" in permissions
        # Not assigned anywhere
        assert "user_ban" not in permissions

    async def test_get_permissions_empty(self, db_session: AsyncSession):
        """User with no permissions should get empty set."""
        # Create user with no permissions
        user = Users(
            username="no_perm_user",
            email="noperm@example.com",
            password="hashed",
            salt="salt",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        permissions = await get_user_permissions(db_session, user.user_id)
        assert len(permissions) == 0

    async def test_inactive_permissions_ignored(
        self, db_session: AsyncSession, test_permissions: dict[str, int]
    ):
        """Permissions with permvalue=0 should not be included."""
        user = Users(
            username="inactive_perm_user",
            email="inactive@example.com",
            password="hashed",
            salt="salt",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Add permission with permvalue=0 (inactive)
        db_session.add(
            UserPerms(user_id=user.user_id, perm_id=test_permissions["user_ban"], permvalue=0)
        )
        await db_session.commit()

        permissions = await get_user_permissions(db_session, user.user_id)
        assert "user_ban" not in permissions


class TestPermissionChecking:
    """Test permission checking helper functions."""

    async def test_has_permission_true(self, db_session: AsyncSession, test_user_with_group: Users):
        """User with permission should return True."""
        assert await has_permission(db_session, test_user_with_group.user_id, "image_edit")
        assert await has_permission(db_session, test_user_with_group.user_id, Permission.IMAGE_EDIT)

    async def test_has_permission_false(self, db_session: AsyncSession, test_user_with_group: Users):
        """User without permission should return False."""
        assert not await has_permission(db_session, test_user_with_group.user_id, "tag_create")
        assert not await has_permission(db_session, test_user_with_group.user_id, Permission.TAG_CREATE)

    async def test_has_any_permission_true(self, db_session: AsyncSession, test_user_with_group: Users):
        """User with at least one permission should return True."""
        assert await has_any_permission(
            db_session, test_user_with_group.user_id, ["tag_create", "image_edit", "user_ban"]
        )

    async def test_has_any_permission_false(self, db_session: AsyncSession, test_user_with_group: Users):
        """User with none of the permissions should return False."""
        assert not await has_any_permission(
            db_session, test_user_with_group.user_id, ["tag_create", "level_admin"]
        )

    async def test_has_all_permissions_true(self, db_session: AsyncSession, test_user_with_group: Users):
        """User with all permissions should return True."""
        assert await has_all_permissions(db_session, test_user_with_group.user_id, ["image_edit", "user_ban"])

    async def test_has_all_permissions_false(self, db_session: AsyncSession, test_user_with_group: Users):
        """User missing any permission should return False."""
        assert not await has_all_permissions(
            db_session, test_user_with_group.user_id, ["image_edit", "tag_create"]
        )


class TestPermissionDependencies:
    """Test FastAPI permission dependencies."""

    async def test_require_permission_allowed(
        self, db_session: AsyncSession, test_user_with_group: Users, mock_redis
    ):
        """Dependency should allow user with permission."""
        dep = require_permission(Permission.IMAGE_EDIT)
        # Should not raise
        await dep(user=test_user_with_group, db=db_session, redis_client=mock_redis)

    async def test_require_permission_denied(
        self, db_session: AsyncSession, test_user_with_group: Users, mock_redis
    ):
        """Dependency should deny user without permission."""
        dep = require_permission(Permission.TAG_CREATE)

        with pytest.raises(HTTPException) as exc_info:
            await dep(user=test_user_with_group, db=db_session, redis_client=mock_redis)

        assert exc_info.value.status_code == 403
        assert "tag_create" in exc_info.value.detail

    async def test_require_any_permission_allowed(
        self, db_session: AsyncSession, test_user_with_group: Users, mock_redis
    ):
        """Dependency should allow user with at least one permission."""
        dep = require_any_permission([Permission.TAG_CREATE, Permission.IMAGE_EDIT])
        # Should not raise
        await dep(user=test_user_with_group, db=db_session, redis_client=mock_redis)

    async def test_require_any_permission_denied(
        self, db_session: AsyncSession, test_user_with_group: Users, mock_redis
    ):
        """Dependency should deny user without any permission."""
        dep = require_any_permission([Permission.TAG_CREATE, Permission.IMAGE_TAG_ADD])

        with pytest.raises(HTTPException) as exc_info:
            await dep(user=test_user_with_group, db=db_session, redis_client=mock_redis)

        assert exc_info.value.status_code == 403
        assert "Requires one of" in exc_info.value.detail

    async def test_require_all_permissions_allowed(
        self, db_session: AsyncSession, test_user_with_group: Users, mock_redis
    ):
        """Dependency should allow user with all permissions."""
        dep = require_all_permissions([Permission.IMAGE_EDIT, Permission.USER_BAN])
        # Should not raise
        await dep(user=test_user_with_group, db=db_session, redis_client=mock_redis)

    async def test_require_all_permissions_denied(
        self, db_session: AsyncSession, test_user_with_group: Users, mock_redis
    ):
        """Dependency should deny user missing any permission."""
        dep = require_all_permissions([Permission.IMAGE_EDIT, Permission.TAG_CREATE])

        with pytest.raises(HTTPException) as exc_info:
            await dep(user=test_user_with_group, db=db_session, redis_client=mock_redis)

        assert exc_info.value.status_code == 403
        assert "Missing:" in exc_info.value.detail
        assert "tag_create" in exc_info.value.detail


class TestPermissionEnum:
    """Test Permission enum constants."""

    def test_permission_values_match_database(self):
        """Permission enum values should match database perm titles."""
        assert Permission.IMAGE_EDIT.value == "image_edit"
        assert Permission.TAG_CREATE.value == "tag_create"
        assert Permission.USER_BAN.value == "user_ban"

    def test_permission_enum_is_string(self):
        """Permission enum should be usable as string."""
        perm = Permission.IMAGE_EDIT
        assert isinstance(perm.value, str)
        assert perm == Permission.IMAGE_EDIT
