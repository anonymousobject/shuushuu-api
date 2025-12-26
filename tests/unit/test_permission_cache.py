"""Tests for permission caching."""

import json

import pytest
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permission_cache import (
    PERMISSION_CACHE_TTL,
    _make_cache_key,
    get_cached_user_permissions,
    invalidate_group_permissions,
    invalidate_user_permissions,
)
from app.core.security import get_password_hash
from app.models.permissions import GroupPerms, Groups, Perms, UserGroups, UserPerms
from app.models.user import Users


@pytest.mark.unit
class TestPermissionCache:
    """Test permission caching functionality."""

    async def test_cache_miss_queries_database(
        self,
        db_session: AsyncSession,
        redis_client: redis.Redis,  # type: ignore[type-arg]
    ):
        """First call should query database and populate cache."""
        # Create user with a permission
        user = Users(
            username="cachetest1",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="cache1@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create permission
        perm = Perms(title="tag_create", desc="Create tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Add permission to user
        db_session.add(UserPerms(user_id=user.user_id, perm_id=perm.perm_id, permvalue=1))
        await db_session.commit()

        # Clear any existing cache
        await redis_client.delete(_make_cache_key(user.user_id))

        # Get permissions (cache miss)
        perms = await get_cached_user_permissions(db_session, redis_client, user.user_id)

        assert "tag_create" in perms

        # Verify cache was populated
        cached = await redis_client.get(_make_cache_key(user.user_id))
        assert cached is not None
        cached_str = cached.decode("utf-8") if isinstance(cached, bytes) else str(cached)
        assert "tag_create" in json.loads(cached_str)

    async def test_cache_hit_returns_cached_value(
        self,
        db_session: AsyncSession,
        redis_client: redis.Redis,  # type: ignore[type-arg]
    ):
        """Second call should return cached value without database query."""
        # Create user
        user = Users(
            username="cachetest2",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="cache2@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Populate cache manually with known value
        cache_key = _make_cache_key(user.user_id)
        await redis_client.setex(cache_key, PERMISSION_CACHE_TTL, json.dumps(["cached_permission"]))

        # Get permissions (should hit cache)
        perms = await get_cached_user_permissions(db_session, redis_client, user.user_id)

        # Should return cached value
        assert "cached_permission" in perms
        assert len(perms) == 1

    async def test_invalidate_clears_cache(
        self,
        redis_client: redis.Redis,  # type: ignore[type-arg]
    ):
        """Invalidating should remove cache entry."""
        user_id = 999
        cache_key = _make_cache_key(user_id)

        # Set cache
        await redis_client.set(cache_key, json.dumps(["perm1"]))
        assert await redis_client.exists(cache_key)

        # Invalidate
        await invalidate_user_permissions(redis_client, user_id)

        # Cache should be gone
        assert not await redis_client.exists(cache_key)

    async def test_invalidate_group_clears_all_member_caches(
        self,
        db_session: AsyncSession,
        redis_client: redis.Redis,  # type: ignore[type-arg]
    ):
        """Invalidating a group should clear cache for all members."""
        # Create group
        group = Groups(title="TestGroup", desc="Test group")
        db_session.add(group)
        await db_session.commit()
        await db_session.refresh(group)

        # Create two users and add to group
        user1 = Users(
            username="groupuser1",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="groupuser1@example.com",
            active=1,
        )
        user2 = Users(
            username="groupuser2",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="groupuser2@example.com",
            active=1,
        )
        db_session.add_all([user1, user2])
        await db_session.commit()
        await db_session.refresh(user1)
        await db_session.refresh(user2)

        # Add users to group
        db_session.add(UserGroups(user_id=user1.user_id, group_id=group.group_id))
        db_session.add(UserGroups(user_id=user2.user_id, group_id=group.group_id))
        await db_session.commit()

        # Set cache for both users
        cache_key1 = _make_cache_key(user1.user_id)
        cache_key2 = _make_cache_key(user2.user_id)
        await redis_client.set(cache_key1, json.dumps(["perm1"]))
        await redis_client.set(cache_key2, json.dumps(["perm1"]))
        assert await redis_client.exists(cache_key1)
        assert await redis_client.exists(cache_key2)

        # Invalidate group
        await invalidate_group_permissions(redis_client, db_session, group.group_id)

        # Both user caches should be cleared
        assert not await redis_client.exists(cache_key1)
        assert not await redis_client.exists(cache_key2)

    async def test_cache_permissions_are_sorted(
        self,
        db_session: AsyncSession,
        redis_client: redis.Redis,  # type: ignore[type-arg]
    ):
        """Cached permissions should be sorted for consistency."""
        # Create user
        user = Users(
            username="sorttest",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="sort@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create permissions in non-alphabetical order
        perm1 = Perms(title="zebra", desc="Last")
        perm2 = Perms(title="apple", desc="First")
        perm3 = Perms(title="mango", desc="Middle")
        db_session.add_all([perm1, perm2, perm3])
        await db_session.commit()
        await db_session.refresh(perm1)
        await db_session.refresh(perm2)
        await db_session.refresh(perm3)

        # Add permissions to user
        db_session.add(UserPerms(user_id=user.user_id, perm_id=perm1.perm_id, permvalue=1))
        db_session.add(UserPerms(user_id=user.user_id, perm_id=perm2.perm_id, permvalue=1))
        db_session.add(UserPerms(user_id=user.user_id, perm_id=perm3.perm_id, permvalue=1))
        await db_session.commit()

        # Clear cache
        await redis_client.delete(_make_cache_key(user.user_id))

        # Get permissions (populates cache)
        await get_cached_user_permissions(db_session, redis_client, user.user_id)

        # Check cached value is sorted
        cached = await redis_client.get(_make_cache_key(user.user_id))
        assert cached is not None
        cached_str = cached.decode("utf-8") if isinstance(cached, bytes) else str(cached)
        cached_list = json.loads(cached_str)
        assert cached_list == ["apple", "mango", "zebra"]  # Sorted alphabetically
