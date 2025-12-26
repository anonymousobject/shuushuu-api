"""
Permission caching layer for high-performance permission checks.

Caches user permissions in Redis with configurable TTL.
Handles cache invalidation on permission changes.
"""

import json
from typing import cast

import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import get_user_permissions
from app.models.permissions import UserGroups

# Cache TTL: 5 minutes (permissions rarely change)
PERMISSION_CACHE_TTL = 300


def _make_cache_key(user_id: int) -> str:
    """Generate Redis cache key for user permissions."""
    return f"user_permissions:{user_id}"


async def get_cached_user_permissions(
    db: AsyncSession,
    redis_client: redis.Redis,  # type: ignore[type-arg]
    user_id: int,
) -> set[str]:
    """
    Get user permissions with caching.

    First checks Redis cache, falls back to database query if cache miss.
    Stores result in cache with TTL for subsequent requests.

    Args:
        db: Database session
        redis_client: Redis client
        user_id: User ID

    Returns:
        Set of permission strings
    """
    cache_key = _make_cache_key(user_id)

    # Try cache first
    cached = await redis_client.get(cache_key)
    if cached:
        try:
            cached_str = cast(str, cached.decode("utf-8") if isinstance(cached, bytes) else cached)
            return set(json.loads(cached_str))
        except (json.JSONDecodeError, TypeError, AttributeError):
            # Cache corrupted, fall through to database
            pass

    # Cache miss - query database
    permissions = await get_user_permissions(db, user_id)

    # Store in cache (convert set to list for JSON serialization)
    await redis_client.setex(
        cache_key,
        PERMISSION_CACHE_TTL,
        json.dumps(sorted(permissions)),  # Sort for deterministic cache keys
    )

    return permissions


async def invalidate_user_permissions(
    redis_client: redis.Redis,  # type: ignore[type-arg]
    user_id: int,
) -> None:
    """
    Invalidate cached permissions for a user.

    Call this when user's groups or permissions are modified.

    Args:
        redis_client: Redis client
        user_id: User ID whose cache should be invalidated
    """
    cache_key = _make_cache_key(user_id)
    await redis_client.delete(cache_key)


async def invalidate_group_permissions(
    redis_client: redis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    group_id: int,
) -> None:
    """
    Invalidate cached permissions for all users in a group.

    Call this when group permissions are modified.
    More expensive operation as it affects multiple users.

    Args:
        redis_client: Redis client
        db: Database session
        group_id: Group ID whose members' caches should be invalidated
    """
    # Get all users in the group
    result = await db.execute(select(UserGroups.user_id).where(UserGroups.group_id == group_id))  # type: ignore[arg-type]
    user_ids = [row[0] for row in result.fetchall()]

    # Invalidate each user's cache
    for user_id in user_ids:
        await invalidate_user_permissions(redis_client, user_id)
