"""Tests for the transient-conflict retry helper (app/core/db_retry.py)."""

import pytest
from sqlalchemy.exc import DBAPIError, OperationalError

from app.core.db_retry import is_transient_conflict, retry_on_transient_conflict
from tests.transient_conflict import _db_error, _deadlock_error, _serialization_error


class _StubSession:
    """Records rollback calls; the helper must roll back between attempts."""

    def __init__(self) -> None:
        self.rollbacks = 0

    async def rollback(self) -> None:
        self.rollbacks += 1


class TestIsTransientConflict:
    def test_matches_deadlock_40p01(self):
        assert is_transient_conflict(_deadlock_error()) is True

    def test_matches_serialization_failure_40001(self):
        assert is_transient_conflict(_serialization_error()) is True

    def test_rejects_unique_violation(self):
        # Duplicate key is a real data conflict, not a transient lock conflict:
        # replaying it produces the same error, so it must surface to the caller.
        assert is_transient_conflict(_db_error("23505", "duplicate key value")) is False

    def test_rejects_lock_timeout(self):
        # 55P03 fires only after lock_timeout elapsed, so replaying it multiplies
        # an already pathological latency — see the module docstring.
        assert (
            is_transient_conflict(_db_error("55P03", "canceling statement due to lock timeout"))
            is False
        )

    def test_rejects_error_without_sqlstate(self):
        assert is_transient_conflict(DBAPIError("STATEMENT", None, Exception())) is False

    def test_rejects_operational_error_without_sqlstate(self):
        # A driver-level OperationalError (connection dropped) carries no SQLSTATE.
        assert (
            is_transient_conflict(OperationalError("STATEMENT", None, Exception("gone"))) is False
        )


class TestRetryOnTransientConflict:
    @pytest.mark.asyncio
    async def test_returns_value_on_first_success_without_rollback(self):
        db = _StubSession()

        async def fn() -> str:
            return "ok"

        assert await retry_on_transient_conflict(db, fn, what="test") == "ok"
        assert db.rollbacks == 0

    @pytest.mark.asyncio
    async def test_retries_deadlock_with_rollback_between_attempts(self):
        db = _StubSession()
        calls = 0

        async def fn() -> str:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise _deadlock_error()
            return "ok"

        assert await retry_on_transient_conflict(db, fn, what="test") == "ok"
        assert calls == 3
        assert db.rollbacks == 2  # one per failed attempt

    @pytest.mark.asyncio
    async def test_retries_serialization_failure_with_rollback_between_attempts(self):
        db = _StubSession()
        calls = 0

        async def fn() -> str:
            nonlocal calls
            calls += 1
            if calls < 2:
                raise _serialization_error()
            return "ok"

        assert await retry_on_transient_conflict(db, fn, what="test") == "ok"
        assert calls == 2
        assert db.rollbacks == 1

    @pytest.mark.asyncio
    async def test_reraises_after_attempts_exhausted(self):
        db = _StubSession()
        calls = 0

        async def fn() -> None:
            nonlocal calls
            calls += 1
            raise _deadlock_error()

        with pytest.raises(DBAPIError):
            await retry_on_transient_conflict(db, fn, what="test")
        assert calls == 3  # default bound
        assert db.rollbacks == 2  # no rollback after the final, re-raised failure

    @pytest.mark.asyncio
    async def test_does_not_retry_other_db_errors(self):
        db = _StubSession()
        calls = 0

        async def fn() -> None:
            nonlocal calls
            calls += 1
            raise _db_error("23505", "duplicate key value")

        with pytest.raises(DBAPIError):
            await retry_on_transient_conflict(db, fn, what="test")
        assert calls == 1
        assert db.rollbacks == 0

    @pytest.mark.asyncio
    async def test_does_not_swallow_non_db_errors(self):
        db = _StubSession()

        async def fn() -> None:
            raise ValueError("boom")

        with pytest.raises(ValueError):
            await retry_on_transient_conflict(db, fn, what="test")
        assert db.rollbacks == 0

    @pytest.mark.asyncio
    async def test_attempts_override(self):
        db = _StubSession()
        calls = 0

        async def fn() -> None:
            nonlocal calls
            calls += 1
            raise _deadlock_error()

        with pytest.raises(DBAPIError):
            await retry_on_transient_conflict(db, fn, what="test", attempts=5)
        assert calls == 5
        assert db.rollbacks == 4
