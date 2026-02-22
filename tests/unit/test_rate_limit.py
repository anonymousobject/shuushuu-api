"""Tests for similarity check rate limiting."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from redis.exceptions import ConnectionError as RedisConnectionError

from app.services.rate_limit import check_similarity_rate_limit


@pytest.fixture
def mock_redis():
    """Create a mock Redis client with pipeline support.

    pipeline() is synchronous, as are pipeline methods (incr, expire).
    Only pipeline.execute() is async.
    """
    client = AsyncMock()
    mock_pipe = MagicMock()
    mock_pipe.execute = AsyncMock(return_value=[])
    # pipeline() is synchronous in redis.asyncio, so use MagicMock
    client.pipeline = MagicMock(return_value=mock_pipe)
    return client


@pytest.mark.unit
class TestCheckSimilarityRateLimit:
    """Tests for check_similarity_rate_limit."""

    async def test_allows_request_under_limit(self, mock_redis):
        """First request (count=None) should be allowed."""
        mock_redis.get.return_value = None

        # Should not raise
        await check_similarity_rate_limit(user_id=1, redis_client=mock_redis)

    async def test_allows_request_at_limit_minus_one(self, mock_redis):
        """Request at count=4 (limit-1) should be allowed."""
        mock_redis.get.return_value = b"4"

        # Should not raise
        await check_similarity_rate_limit(user_id=1, redis_client=mock_redis)

    async def test_rejects_request_at_limit(self, mock_redis):
        """Request at count=5 (limit) should raise 429 with Retry-After header."""
        mock_redis.get.return_value = b"5"

        with pytest.raises(HTTPException) as exc_info:
            await check_similarity_rate_limit(user_id=1, redis_client=mock_redis)

        assert exc_info.value.status_code == 429
        assert exc_info.value.headers["Retry-After"] == "60"

    async def test_rejects_request_over_limit(self, mock_redis):
        """Request at count=10 (over limit) should raise 429."""
        mock_redis.get.return_value = b"10"

        with pytest.raises(HTTPException) as exc_info:
            await check_similarity_rate_limit(user_id=1, redis_client=mock_redis)

        assert exc_info.value.status_code == 429

    async def test_sets_expiry_on_first_request(self, mock_redis):
        """First request (count=None) should set 60s expiry on the key."""
        mock_redis.get.return_value = None
        mock_pipe = mock_redis.pipeline.return_value

        await check_similarity_rate_limit(user_id=1, redis_client=mock_redis)

        mock_pipe.expire.assert_called_once()
        args = mock_pipe.expire.call_args[0]
        # First arg is the key, second is the TTL (60 seconds)
        assert args[1] == 60

    async def test_does_not_set_expiry_on_subsequent_requests(self, mock_redis):
        """Subsequent requests (count > 0) should not set expiry."""
        mock_redis.get.return_value = b"2"
        mock_pipe = mock_redis.pipeline.return_value

        await check_similarity_rate_limit(user_id=1, redis_client=mock_redis)

        mock_pipe.expire.assert_not_called()

    async def test_uses_correct_redis_key(self, mock_redis):
        """Should use key 'similarity_check_rate:{user_id}'."""
        mock_redis.get.return_value = None

        await check_similarity_rate_limit(user_id=42, redis_client=mock_redis)

        mock_redis.get.assert_called_once_with("similarity_check_rate:42")

    async def test_allows_request_when_redis_unavailable(self, mock_redis):
        """Should allow request (not raise) when Redis is down."""
        mock_redis.get.side_effect = RedisConnectionError("Connection refused")

        # Should not raise - graceful degradation
        await check_similarity_rate_limit(user_id=1, redis_client=mock_redis)
