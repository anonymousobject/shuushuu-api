from collections.abc import AsyncGenerator

import redis.asyncio as redis
from fastapi import Request

from app.config import settings


def create_redis_pool() -> redis.ConnectionPool:  # type: ignore[type-arg]
    """
    Build the process-wide Redis connection pool.

    Called once per process in the app lifespan (app/main.py) and stored on
    app.state.redis_pool, so every get_redis() dependency resolution shares it
    instead of paying a TCP connect per request (see #381).
    """
    return redis.ConnectionPool.from_url(
        str(settings.REDIS_URL),
        encoding="utf-8",
        decode_responses=True,
        max_connections=settings.REDIS_MAX_CONNECTIONS,
    )


async def get_redis(request: Request) -> AsyncGenerator[redis.Redis]:  # type: ignore[type-arg]
    """
    Dependency for getting an async redis connection.

    Binds to the shared per-process pool at app.state.redis_pool (created in
    the app lifespan) instead of opening a new connection per request.
    Closing the yielded client does not close the shared pool -- redis-py
    only auto-closes a pool it created internally; a pool passed in
    explicitly (as here) is left for its owner (the lifespan) to dispose of.
    """
    client = redis.Redis(connection_pool=request.app.state.redis_pool)
    try:
        yield client
    finally:
        await client.aclose()  # type: ignore[attr-defined]  # stub lags runtime; aclose is correct
