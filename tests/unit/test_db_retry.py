"""Tests for the transient-conflict retry helper (app/core/db_retry.py)."""

import pymysql
import pytest
from sqlalchemy.exc import OperationalError

from app.core.db_retry import is_transient_conflict, retry_on_transient_conflict


def _db_error(errno: int, message: str) -> OperationalError:
    return OperationalError("STATEMENT", None, pymysql.err.OperationalError(errno, message))


def _conflict() -> OperationalError:
    return _db_error(1020, "Record has changed since last read in table 'images'")


def _deadlock() -> OperationalError:
    return _db_error(1213, "Deadlock found when trying to get lock; try restarting transaction")


class _StubSession:
    """Records rollback calls; the helper must roll back between attempts."""

    def __init__(self) -> None:
        self.rollbacks = 0

    async def rollback(self) -> None:
        self.rollbacks += 1


class TestIsTransientConflict:
    def test_matches_snapshot_conflict_errno_1020(self):
        assert is_transient_conflict(_conflict()) is True

    def test_matches_deadlock_errno_1213(self):
        assert is_transient_conflict(_deadlock()) is True

    def test_rejects_other_errnos(self):
        # Duplicate key is a real data conflict, not a transient lock conflict:
        # replaying it produces the same error, so it must surface to the caller.
        assert is_transient_conflict(_db_error(1062, "Duplicate entry")) is False

    def test_rejects_lock_wait_timeout(self):
        # 1205 is deliberately excluded — see the module docstring.
        assert is_transient_conflict(_db_error(1205, "Lock wait timeout exceeded")) is False

    def test_rejects_error_without_args(self):
        assert is_transient_conflict(OperationalError("STATEMENT", None, Exception())) is False


class TestRetryOnTransientConflict:
    @pytest.mark.asyncio
    async def test_returns_value_on_first_success_without_rollback(self):
        db = _StubSession()

        async def fn() -> str:
            return "ok"

        assert await retry_on_transient_conflict(db, fn, what="test") == "ok"
        assert db.rollbacks == 0

    @pytest.mark.asyncio
    async def test_retries_conflict_with_rollback_between_attempts(self):
        db = _StubSession()
        calls = 0

        async def fn() -> str:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise _conflict()
            return "ok"

        assert await retry_on_transient_conflict(db, fn, what="test") == "ok"
        assert calls == 3
        assert db.rollbacks == 2  # one per failed attempt

    @pytest.mark.asyncio
    async def test_retries_deadlock_with_rollback_between_attempts(self):
        db = _StubSession()
        calls = 0

        async def fn() -> str:
            nonlocal calls
            calls += 1
            if calls < 2:
                raise _deadlock()
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
            raise _conflict()

        with pytest.raises(OperationalError):
            await retry_on_transient_conflict(db, fn, what="test")
        assert calls == 3  # default bound
        assert db.rollbacks == 2  # no rollback after the final, re-raised failure

    @pytest.mark.asyncio
    async def test_does_not_retry_other_operational_errors(self):
        db = _StubSession()
        calls = 0

        async def fn() -> None:
            nonlocal calls
            calls += 1
            raise _db_error(1062, "Duplicate entry")

        with pytest.raises(OperationalError):
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
            raise _conflict()

        with pytest.raises(OperationalError):
            await retry_on_transient_conflict(db, fn, what="test", attempts=5)
        assert calls == 5
        assert db.rollbacks == 4
