"""Helpers for testing the Postgres transient-conflict retry (app/core/db_retry.py).

Two errors get the same rollback-and-replay treatment:

- 40P01 deadlock_detected: two transactions took row locks in opposite orders
  and Postgres aborted one to break the cycle.
- 40001 serialization_failure: a write would break the transaction's snapshot
  (REPEATABLE READ / SERIALIZABLE; rare under the READ COMMITTED default).

Write paths wrap their transactional unit in ``retry_on_transient_conflict`` so
either one is retried on a fresh transaction instead of surfacing a 500.

These helpers inject the error into a route's explicit flush or its commit,
which is what lets a test exercise the real helper without racing two live
requests.
"""

from unittest.mock import patch

from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession


def _db_error(sqlstate: str, message: str) -> DBAPIError:
    """Build the error SQLAlchemy raises for a Postgres error with `sqlstate`.

    The asyncpg adapter translates asyncpg's PostgresError into its own dbapi
    Error, copying the SQLSTATE onto .sqlstate/.pgcode, and SQLAlchemy wraps
    that as a plain DBAPIError (not OperationalError) — which is what the
    helper must catch.
    """
    orig = AsyncAdapt_asyncpg_dbapi.Error(message)
    orig.sqlstate = orig.pgcode = sqlstate
    return DBAPIError("UPDATE ...", None, orig)


def _deadlock_error() -> DBAPIError:
    """The error Postgres raises for the transaction it aborts to break a lock cycle."""
    return _db_error("40P01", "deadlock detected")


def _serialization_error() -> DBAPIError:
    """The error Postgres raises when a write would break the transaction's snapshot."""
    return _db_error("40001", "could not serialize access due to concurrent update")


def _flaky_commit(fail_times: int, error: DBAPIError):
    """Patch AsyncSession.commit to raise `error` for the first `fail_times`
    calls, then delegate to the real commit.

    The flush-based helpers below can't reach a route whose transactional unit
    has no explicit ``db.flush()`` of its own — the repost migration is all
    Core-level execute() calls ending at the route's commit. Failing the commit
    aborts the attempt with nothing persisted, which is exactly what a real
    deadlock does. Returns (patch_ctx, calls)."""
    real_commit = AsyncSession.commit
    calls: list[int] = []

    async def commit(self, *args, **kwargs):
        calls.append(1)
        if len(calls) <= fail_times:
            raise error
        await real_commit(self, *args, **kwargs)

    return patch.object(AsyncSession, "commit", commit), calls


def _flaky_flush(fail_times: int, error: DBAPIError):
    """Patch AsyncSession.flush to raise `error` for the first `fail_times`
    calls, then delegate to the real flush. Only a route's explicit
    ``await db.flush()`` goes through AsyncSession.flush (autoflush runs inside
    the sync Session), so the first intercepted call is the route's own write.
    Returns (patch_ctx, calls) where calls records each intercepted flush."""
    real_flush = AsyncSession.flush
    calls: list[int] = []

    async def flush(self, *args, **kwargs):
        calls.append(1)
        if len(calls) <= fail_times:
            raise error
        await real_flush(self, *args, **kwargs)

    return patch.object(AsyncSession, "flush", flush), calls


def _flaky_flush_nth(n: int, error: DBAPIError):
    """Like `_flaky_flush`, but fails only the `n`th explicit flush (1-indexed).

    Use this to aim the conflict at a specific write within a route that
    flushes more than once — e.g. the upload's tag-link write rather than the
    flush that mints its image_id. Returns (patch_ctx, calls).
    """
    real_flush = AsyncSession.flush
    calls: list[int] = []

    async def flush(self, *args, **kwargs):
        calls.append(1)
        if len(calls) == n:
            raise error
        await real_flush(self, *args, **kwargs)

    return patch.object(AsyncSession, "flush", flush), calls
