"""
Tests for the GET /api/v1/tags/{tag_id}/related endpoint.

Reads the precomputed `tag_cooccurrence` table and returns related tags ranked
by lift descending, alias-resolved on the input tag, Redis-cached.

The `client` fixture overrides `get_redis` with a mock whose `get` returns None
(cache miss), so these tests exercise the DB read path. The mock's `set` is an
AsyncMock, which lets us assert the response is written to the cache.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.models.tag import Tags
from app.models.tag_cooccurrence import TagCooccurrence

pytestmark = pytest.mark.api


async def _seed(db: AsyncSession) -> None:
    """Seed three tags and two co-occurrence rows for base tag 10.

    Base tag 10 (Reimu, CHARACTER) co-occurs with:
      - 20 (Touhou, SOURCE): lift 19.0  -> ranks first
      - 30 (solo, THEME):    lift  2.5  -> ranks second
    """
    db.add(Tags(tag_id=10, type=TagType.CHARACTER, title="Reimu", usage_count=4000))
    db.add(Tags(tag_id=20, type=TagType.SOURCE, title="Touhou", usage_count=5000))
    db.add(Tags(tag_id=30, type=TagType.THEME, title="solo", usage_count=90000))
    db.add(
        TagCooccurrence(
            tag_id=10,
            related_tag_id=20,
            related_type=int(TagType.SOURCE),
            cooccur_count=3800,
            lift=19.0,
            confidence=0.95,
        )
    )
    db.add(
        TagCooccurrence(
            tag_id=10,
            related_tag_id=30,
            related_type=int(TagType.THEME),
            cooccur_count=3000,
            lift=2.5,
            confidence=0.75,
        )
    )
    await db.commit()


async def test_related_returns_ranked_by_lift(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session)

    r = await client.get("/api/v1/tags/10/related")

    assert r.status_code == 200
    body = r.json()
    assert body["tag_id"] == 10
    items = body["items"]
    assert [i["tag_id"] for i in items] == [20, 30]  # lift desc


async def test_response_fields_are_populated(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed(db_session)

    r = await client.get("/api/v1/tags/10/related")

    assert r.status_code == 200
    first = r.json()["items"][0]
    assert first["tag_id"] == 20
    assert first["title"] == "Touhou"
    assert first["type"] == int(TagType.SOURCE)
    assert first["type_name"] == "Source"  # from TAG_TYPE_NAME
    assert first["usage_count"] == 5000  # free add from the join to tags
    assert first["cooccur_count"] == 3800
    assert first["confidence"] == 0.95
    assert first["lift"] == 19.0


async def test_type_filter(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed(db_session)

    r = await client.get("/api/v1/tags/10/related?type=1")  # THEME only

    assert r.status_code == 200
    assert [i["tag_id"] for i in r.json()["items"]] == [30]


async def test_limit(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed(db_session)

    r = await client.get("/api/v1/tags/10/related?limit=1")

    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["tag_id"] == 20  # highest lift kept


async def test_alias_input_resolves_to_canonical(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session)
    # Tag 11 is an alias of canonical tag 10. The cooccurrence table is keyed by
    # canonical id, so querying the alias must return tag 10's neighbours.
    db_session.add(Tags(tag_id=11, type=TagType.CHARACTER, title="Hakurei Reimu", alias_of=10))
    await db_session.commit()

    r = await client.get("/api/v1/tags/11/related")

    assert r.status_code == 200
    body = r.json()
    assert body["tag_id"] == 10  # response reports the canonical base tag
    assert [i["tag_id"] for i in body["items"]] == [20, 30]


async def test_empty_when_no_neighbours(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed(db_session)
    # Tag 20 exists but has no rows where it is the base tag.
    r = await client.get("/api/v1/tags/20/related")

    assert r.status_code == 200
    body = r.json()
    assert body["tag_id"] == 20
    assert body["items"] == []


async def test_missing_tag_returns_404(client: AsyncClient) -> None:
    """A tag_id that does not exist must 404, not return an empty list."""
    r = await client.get("/api/v1/tags/99999/related")

    assert r.status_code == 404
    assert r.json()["detail"] == "Tag not found"


async def test_response_is_cached(
    client: AsyncClient, db_session: AsyncSession, mock_redis
) -> None:
    """The endpoint writes the response to Redis on a cache miss.

    The mock returns None for `get` (so the DB path runs) but records `set`,
    letting us verify the cache-write half of the read-through. The call args
    confirm the correct key prefix and TTL.
    """
    from app.config import settings

    await _seed(db_session)

    r = await client.get("/api/v1/tags/10/related")

    assert r.status_code == 200
    mock_redis.set.assert_awaited()
    call_args = mock_redis.set.await_args
    assert call_args is not None
    # First positional arg is the cache key — must start with "related:"
    assert call_args.args[0].startswith("related:")
    # Keyword arg ex must equal COOCCUR_CACHE_TTL
    assert call_args.kwargs.get("ex") == settings.COOCCUR_CACHE_TTL


async def test_cache_hit_returns_same_response(
    client_real_redis: AsyncClient, db_session: AsyncSession
) -> None:
    """Calling the endpoint twice hits the cache on the second call and returns
    identical data, exercising the model_validate_json deserialization path."""
    await _seed(db_session)

    r1 = await client_real_redis.get("/api/v1/tags/10/related")
    r2 = await client_real_redis.get("/api/v1/tags/10/related")

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json() == r2.json()


async def test_input_validation_rejects_out_of_range(client: AsyncClient) -> None:
    """?type=0 and ?limit=51 must each return 422 (FastAPI constraint violation)."""
    r_type = await client.get("/api/v1/tags/10/related?type=0")
    assert r_type.status_code == 422

    r_limit = await client.get("/api/v1/tags/10/related?limit=51")
    assert r_limit.status_code == 422
