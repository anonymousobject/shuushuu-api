from collections.abc import AsyncGenerator

import redis.asyncio as redis

from app.config import settings


async def get_redis() -> AsyncGenerator[redis.Redis, None]:  # type: ignore[type-arg]
    """
    Dependency for getting async redis connection.
    """
    client = redis.from_url(
        str(settings.REDIS_URL),
        encoding="utf-8",
        decode_responses=True,
    )
    try:
        yield client
    finally:
        await client.close()
