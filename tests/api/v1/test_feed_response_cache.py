"""Logged-out GET /images/ responses are TTL-cached in Redis (issue #319).

Uses the real-Redis client: the default ``client`` fixture's mock Redis never stores
anything, so it can't observe a cache hit.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.feed_response_cache import FEED_RESPONSE_TTL
from tests.api.v1.test_feed_count import _clear_response_keys, _img, _login, _user

PREFIX = "feed:response:"


async def _response_keys(redis_client) -> list[str]:
    return await redis_client.keys(f"{PREFIX}*")


@pytest.mark.api
class TestFeedResponseCache:
    async def test_anonymous_repeat_is_served_from_cache(
        self, client_real_redis: AsyncClient, db_session: AsyncSession, redis_client
    ):
        owner = await _user(db_session, "frcAnon")
        await _img(db_session, owner, "c0" + "0" * 30, 1)

        first = await client_real_redis.get("/api/v1/images/?per_page=5")
        await _img(db_session, owner, "c1" + "1" * 30, 1)
        second = await client_real_redis.get("/api/v1/images/?per_page=5")

        assert first.status_code == second.status_code == 200
        assert second.content == first.content
        assert second.headers["content-type"] == "application/json"
        assert len(first.json()["images"]) == 1

        await _clear_response_keys(redis_client)
        third = await client_real_redis.get("/api/v1/images/?per_page=5")
        assert len(third.json()["images"]) == 2

    async def test_logged_in_requests_bypass_the_cache(
        self, client_real_redis: AsyncClient, db_session: AsyncSession, redis_client
    ):
        owner = await _user(db_session, "frcUser")
        await _img(db_session, owner, "d0" + "0" * 30, 1)
        headers = {"Authorization": f"Bearer {await _login(client_real_redis, owner.username)}"}
        client_real_redis.cookies.clear()

        first = await client_real_redis.get("/api/v1/images/?per_page=5", headers=headers)
        await _img(db_session, owner, "d1" + "1" * 30, 1)
        second = await client_real_redis.get("/api/v1/images/?per_page=5", headers=headers)

        assert len(first.json()["images"]) == 1
        assert len(second.json()["images"]) == 2
        assert await _response_keys(redis_client) == []

    async def test_logged_in_never_served_anonymous_body_and_vice_versa(
        self, client_real_redis: AsyncClient, db_session: AsyncSession, redis_client
    ):
        owner = await _user(db_session, "frcMix")
        await _img(db_session, owner, "e0" + "0" * 30, 1)
        await _img(db_session, owner, "e1" + "1" * 30, 0)  # hidden from anon, visible to owner
        headers = {"Authorization": f"Bearer {await _login(client_real_redis, owner.username)}"}
        client_real_redis.cookies.clear()  # login set an access_token cookie; stay anonymous
        url = "/api/v1/images/?per_page=5"

        anon = await client_real_redis.get(url)  # populates the cache
        user = await client_real_redis.get(url, headers=headers)
        assert len(anon.json()["images"]) == 1
        assert len(user.json()["images"]) == 2

        await _clear_response_keys(redis_client)
        user_first = await client_real_redis.get(url, headers=headers)
        anon_after = await client_real_redis.get(url)
        assert len(user_first.json()["images"]) == 2
        assert anon_after.content == anon.content

    async def test_query_param_order_is_irrelevant_and_values_are_distinct(
        self, client_real_redis: AsyncClient, db_session: AsyncSession, redis_client
    ):
        owner = await _user(db_session, "frcOrder")
        await _img(db_session, owner, "f0" + "0" * 30, 1)

        await client_real_redis.get("/api/v1/images/?page=1&per_page=3")
        await client_real_redis.get("/api/v1/images/?per_page=3&page=1")
        assert len(await _response_keys(redis_client)) == 1

        await client_real_redis.get("/api/v1/images/?page=1&per_page=4")
        assert len(await _response_keys(redis_client)) == 2

    async def test_entry_has_a_bounded_ttl(
        self, client_real_redis: AsyncClient, db_session: AsyncSession, redis_client
    ):
        await client_real_redis.get("/api/v1/images/?per_page=2")
        (key,) = await _response_keys(redis_client)
        ttl = await redis_client.ttl(key)
        assert 0 < ttl <= FEED_RESPONSE_TTL == 30

    async def test_invalid_requests_return_their_error_and_are_not_cached(
        self, client_real_redis: AsyncClient, redis_client
    ):
        bad_mode = await client_real_redis.get("/api/v1/images/?tags_mode=bogus")
        assert bad_mode.status_code == 422
        too_short = await client_real_redis.get("/api/v1/images/?commentsearch=ab")
        assert too_short.status_code == 400
        # Repeating must still error, not be answered from a cache.
        assert (await client_real_redis.get("/api/v1/images/?commentsearch=ab")).status_code == 400
        assert await _response_keys(redis_client) == []
