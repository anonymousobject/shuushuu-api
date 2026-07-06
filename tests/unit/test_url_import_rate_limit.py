"""Tests for URL-import rate limits (per-user + global resolve, per-user fetch)."""

import pytest
from fastapi import HTTPException

from app.config import settings
from app.services.rate_limit import (
    check_external_fetch_rate_limit,
    check_url_resolve_rate_limit,
)


class FakePipeline:
    def __init__(self, store, ttls):
        self.store = store
        self.ttls = ttls
        self.ops = []

    def incr(self, key):
        self.ops.append(("incr", key))

    def expire(self, key, ttl):
        self.ops.append(("expire", key, ttl))

    async def execute(self):
        for op in self.ops:
            if op[0] == "incr":
                self.store[op[1]] = int(self.store.get(op[1], 0)) + 1
            elif op[0] == "expire":
                self.ttls[op[1]] = op[2]
        self.ops = []


class FakeRedis:
    def __init__(self, initial=None):
        self.store = dict(initial or {})
        self.ttls: dict[str, int] = {}

    async def get(self, key):
        value = self.store.get(key)
        return None if value is None else str(value)

    def pipeline(self):
        return FakePipeline(self.store, self.ttls)


class TestResolveRateLimit:
    async def test_allows_under_limit_and_increments(self):
        redis = FakeRedis()
        await check_url_resolve_rate_limit(1, redis)
        assert redis.store["url_resolve_rate:1"] == 1
        assert redis.store["url_resolve_rate:global"] == 1
        assert redis.ttls["url_resolve_rate:1"] == 60
        assert redis.ttls["url_resolve_rate:global"] == 60

    async def test_user_limit_breach_raises_429(self, monkeypatch):
        monkeypatch.setattr(settings, "URL_RESOLVE_RATE_PER_MINUTE", 5)
        redis = FakeRedis({"url_resolve_rate:1": 5})
        with pytest.raises(HTTPException) as exc_info:
            await check_url_resolve_rate_limit(1, redis)
        assert exc_info.value.status_code == 429

    async def test_global_limit_breach_raises_429(self, monkeypatch):
        monkeypatch.setattr(settings, "URL_RESOLVE_GLOBAL_RATE_PER_MINUTE", 60)
        redis = FakeRedis({"url_resolve_rate:global": 60})
        with pytest.raises(HTTPException) as exc_info:
            await check_url_resolve_rate_limit(1, redis)
        assert exc_info.value.status_code == 429

    async def test_other_users_do_not_consume_my_user_budget(self, monkeypatch):
        monkeypatch.setattr(settings, "URL_RESOLVE_RATE_PER_MINUTE", 5)
        redis = FakeRedis({"url_resolve_rate:2": 5})
        await check_url_resolve_rate_limit(1, redis)  # must not raise

    async def test_redis_failure_degrades_gracefully(self):
        class BrokenRedis:
            async def get(self, key):
                raise ConnectionError("redis down")

        await check_url_resolve_rate_limit(1, BrokenRedis())  # must not raise


class TestFetchRateLimit:
    async def test_allows_under_limit(self, monkeypatch):
        monkeypatch.setattr(settings, "EXTERNAL_FETCH_RATE_PER_MINUTE", 40)
        redis = FakeRedis({"external_fetch_rate:1": 39})
        await check_external_fetch_rate_limit(1, redis)
        assert redis.store["external_fetch_rate:1"] == 40
        assert redis.ttls["external_fetch_rate:1"] == 60

    async def test_breach_raises_429(self, monkeypatch):
        monkeypatch.setattr(settings, "EXTERNAL_FETCH_RATE_PER_MINUTE", 40)
        redis = FakeRedis({"external_fetch_rate:1": 40})
        with pytest.raises(HTTPException) as exc_info:
            await check_external_fetch_rate_limit(1, redis)
        assert exc_info.value.status_code == 429
