"""Endpoint-level tests for the forum thread/post creation rate limit.

These use the real Redis client (client_real_redis) so the per-user counter
actually accumulates across requests; the default mock_redis reports every
lookup as a cache miss and so can never trip the limit.
"""

from httpx import AsyncClient

from app.config import settings


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestForumCreateRateLimit:
    """Anti-spam per-user rate limit shared by thread and post creation."""

    async def test_thread_creation_rate_limited(
        self, client_real_redis: AsyncClient, public_category, user_token, monkeypatch
    ):
        monkeypatch.setattr(settings, "FORUM_CREATE_RATE_LIMIT", 2)
        url = f"/api/v1/forum/categories/{public_category.category_id}/threads"
        body = {"title": "T", "post_text": "body"}
        assert (
            await client_real_redis.post(url, json=body, headers=_auth(user_token))
        ).status_code == 201
        assert (
            await client_real_redis.post(url, json=body, headers=_auth(user_token))
        ).status_code == 201
        third = await client_real_redis.post(url, json=body, headers=_auth(user_token))
        assert third.status_code == 429

    async def test_post_creation_rate_limited(
        self, client_real_redis: AsyncClient, public_thread, user_token, monkeypatch
    ):
        monkeypatch.setattr(settings, "FORUM_CREATE_RATE_LIMIT", 2)
        url = f"/api/v1/forum/threads/{public_thread.thread_id}/posts"
        body = {"post_text": "reply"}
        assert (
            await client_real_redis.post(url, json=body, headers=_auth(user_token))
        ).status_code == 201
        assert (
            await client_real_redis.post(url, json=body, headers=_auth(user_token))
        ).status_code == 201
        third = await client_real_redis.post(url, json=body, headers=_auth(user_token))
        assert third.status_code == 429

    async def test_thread_and_post_share_one_budget(
        self, client_real_redis: AsyncClient, public_thread, public_category, user_token, monkeypatch
    ):
        # A spammer must not bypass the cap by alternating thread and post
        # creation: both draw from the same per-user budget.
        monkeypatch.setattr(settings, "FORUM_CREATE_RATE_LIMIT", 2)
        thread_url = f"/api/v1/forum/categories/{public_category.category_id}/threads"
        post_url = f"/api/v1/forum/threads/{public_thread.thread_id}/posts"
        assert (
            await client_real_redis.post(
                thread_url, json={"title": "T", "post_text": "b"}, headers=_auth(user_token)
            )
        ).status_code == 201
        assert (
            await client_real_redis.post(
                post_url, json={"post_text": "reply"}, headers=_auth(user_token)
            )
        ).status_code == 201
        # Budget (2) is now spent across the two endpoints.
        blocked = await client_real_redis.post(
            post_url, json={"post_text": "reply"}, headers=_auth(user_token)
        )
        assert blocked.status_code == 429

    async def test_normal_paced_request_succeeds(
        self, client_real_redis: AsyncClient, public_category, user_token, monkeypatch
    ):
        monkeypatch.setattr(settings, "FORUM_CREATE_RATE_LIMIT", 10)
        url = f"/api/v1/forum/categories/{public_category.category_id}/threads"
        r = await client_real_redis.post(
            url, json={"title": "T", "post_text": "body"}, headers=_auth(user_token)
        )
        assert r.status_code == 201

    async def test_moderator_exempt_from_rate_limit(
        self, client_real_redis: AsyncClient, public_category, staff_token, monkeypatch
    ):
        monkeypatch.setattr(settings, "FORUM_CREATE_RATE_LIMIT", 1)
        url = f"/api/v1/forum/categories/{public_category.category_id}/threads"
        body = {"title": "T", "post_text": "body"}
        assert (
            await client_real_redis.post(url, json=body, headers=_auth(staff_token))
        ).status_code == 201
        # A second creation would exceed the cap for a normal user; moderators
        # are exempt (they need to be able to post freely while moderating).
        assert (
            await client_real_redis.post(url, json=body, headers=_auth(staff_token))
        ).status_code == 201
