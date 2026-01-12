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
