from collections.abc import AsyncGenerator

from meilisearch_python_sdk import AsyncClient

from app.config import settings


async def get_meilisearch() -> AsyncGenerator[AsyncClient]:
    """Dependency for getting async Meilisearch client."""
    client = AsyncClient(
        url=settings.MEILISEARCH_URL,
        api_key=settings.MEILISEARCH_API_KEY,
    )
    try:
        yield client
    finally:
        await client.aclose()
