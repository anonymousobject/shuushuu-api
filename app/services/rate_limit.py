"""Rate limiting service using Redis."""

from datetime import timedelta

import redis.asyncio as redis
from fastapi import HTTPException, status

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def check_registration_rate_limit(ip_address: str, redis_client: redis.Redis) -> None:  # type: ignore[type-arg]
    """
    Enforce registration rate limit per IP address.

    Limit: 5 registrations per IP per hour (configurable)

    Uses Redis for fast lookups and automatic expiration.

    Args:
        ip_address: Client IP address
        redis_client: Redis client instance

    Raises:
        HTTPException: 429 if rate limit exceeded
    """
    key = f"registration_rate:{ip_address}"

    # Get current count
    count_bytes = await redis_client.get(key)
    count = int(count_bytes) if count_bytes else 0

    if count >= settings.REGISTRATION_RATE_LIMIT:
        logger.warning(
            "registration_rate_limit_exceeded",
            ip_address=ip_address,
            count=count,
            limit=settings.REGISTRATION_RATE_LIMIT,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many registration attempts. Please try again in {settings.REGISTRATION_RATE_WINDOW_HOURS} hour(s).",
        )

    # Increment counter with expiration
    pipe = redis_client.pipeline()
    pipe.incr(key)
    if count == 0:
        # First registration from this IP - set expiration
        pipe.expire(key, timedelta(hours=settings.REGISTRATION_RATE_WINDOW_HOURS))
    await pipe.execute()

    logger.debug(
        "registration_rate_check",
        ip_address=ip_address,
        count=count + 1,
        limit=settings.REGISTRATION_RATE_LIMIT,
    )


async def check_similarity_rate_limit(user_id: int, redis_client: redis.Redis) -> None:  # type: ignore[type-arg]
    """
    Enforce similarity check rate limit per user.

    Limit: 5 checks per user per 60 seconds (configurable via SIMILARITY_CHECK_RATE_LIMIT).

    Uses Redis for fast lookups and automatic expiration.
    Gracefully degrades if Redis is unavailable (allows the request).

    Args:
        user_id: ID of the user making the request
        redis_client: Redis client instance

    Raises:
        HTTPException: 429 if rate limit exceeded
    """
    try:
        key = f"similarity_check_rate:{user_id}"

        # Get current count
        count_bytes = await redis_client.get(key)
        count = int(count_bytes) if count_bytes else 0

        if count >= settings.SIMILARITY_CHECK_RATE_LIMIT:
            logger.warning(
                "similarity_check_rate_limit_exceeded",
                user_id=user_id,
                count=count,
                limit=settings.SIMILARITY_CHECK_RATE_LIMIT,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many similarity checks. Please try again in 60 seconds.",
                headers={"Retry-After": "60"},
            )

        # Increment counter with expiration
        pipe = redis_client.pipeline()
        pipe.incr(key)
        if count == 0:
            # First check from this user in this window - set expiration
            pipe.expire(key, 60)
        await pipe.execute()

        logger.debug(
            "similarity_check_rate_check",
            user_id=user_id,
            count=count + 1,
            limit=settings.SIMILARITY_CHECK_RATE_LIMIT,
        )
    except HTTPException:
        raise
    except Exception:
        logger.warning(
            "similarity_check_rate_limit_redis_error",
            user_id=user_id,
            exc_info=True,
        )


async def check_url_resolve_rate_limit(user_id: int, redis_client: redis.Redis) -> None:  # type: ignore[type-arg]
    """
    Per-user + global rate limit for external URL resolution.

    Per-user is checked first so one abuser hits their own cap before
    consuming the global budget.

    Gracefully degrades if Redis is unavailable (allows the request).

    Args:
        user_id: ID of the user making the request
        redis_client: Redis client instance

    Raises:
        HTTPException: 429 if rate limit exceeded
    """
    try:
        user_key = f"url_resolve_rate:{user_id}"
        global_key = "url_resolve_rate:global"

        user_count = await redis_client.get(user_key)
        if user_count is not None and int(user_count) >= settings.URL_RESOLVE_RATE_PER_MINUTE:
            logger.warning(
                "url_resolve_rate_limit_exceeded_per_user",
                user_id=user_id,
                count=int(user_count),
                limit=settings.URL_RESOLVE_RATE_PER_MINUTE,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many URL lookups. Please wait a minute and try again.",
                headers={"Retry-After": "60"},
            )
        global_count = await redis_client.get(global_key)
        if (
            global_count is not None
            and int(global_count) >= settings.URL_RESOLVE_GLOBAL_RATE_PER_MINUTE
        ):
            logger.warning(
                "url_resolve_rate_limit_exceeded_global",
                global_count=int(global_count),
                limit=settings.URL_RESOLVE_GLOBAL_RATE_PER_MINUTE,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="URL import is busy right now. Please try again in a minute.",
                headers={"Retry-After": "60"},
            )
        pipe = redis_client.pipeline()
        pipe.incr(user_key)
        pipe.expire(user_key, 60)
        pipe.incr(global_key)
        pipe.expire(global_key, 60)
        await pipe.execute()

        logger.debug(
            "url_resolve_rate_check",
            user_id=user_id,
            user_count=int(user_count) + 1 if user_count else 1,
            user_limit=settings.URL_RESOLVE_RATE_PER_MINUTE,
            global_count=int(global_count) + 1 if global_count else 1,
            global_limit=settings.URL_RESOLVE_GLOBAL_RATE_PER_MINUTE,
        )
    except HTTPException:
        raise
    except Exception:
        logger.warning(
            "url_resolve_rate_limit_redis_error",
            user_id=user_id,
            exc_info=True,
        )


async def check_external_fetch_rate_limit(user_id: int, redis_client: redis.Redis) -> None:  # type: ignore[type-arg]
    """
    Per-user rate limit for the external image fetch proxy.

    Gracefully degrades if Redis is unavailable (allows the request).

    Args:
        user_id: ID of the user making the request
        redis_client: Redis client instance

    Raises:
        HTTPException: 429 if rate limit exceeded
    """
    try:
        key = f"external_fetch_rate:{user_id}"
        count = await redis_client.get(key)
        if count is not None and int(count) >= settings.EXTERNAL_FETCH_RATE_PER_MINUTE:
            logger.warning(
                "external_fetch_rate_limit_exceeded",
                user_id=user_id,
                count=int(count),
                limit=settings.EXTERNAL_FETCH_RATE_PER_MINUTE,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many image fetches. Please wait a minute and try again.",
                headers={"Retry-After": "60"},
            )
        pipe = redis_client.pipeline()
        pipe.incr(key)
        pipe.expire(key, 60)
        await pipe.execute()

        logger.debug(
            "external_fetch_rate_check",
            user_id=user_id,
            count=int(count) + 1 if count else 1,
            limit=settings.EXTERNAL_FETCH_RATE_PER_MINUTE,
        )
    except HTTPException:
        raise
    except Exception:
        logger.warning(
            "external_fetch_rate_limit_redis_error",
            user_id=user_id,
            exc_info=True,
        )
