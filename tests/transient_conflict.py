"""Helpers for testing the MariaDB transient-conflict retry (app/core/db_retry.py).

Two errors get the same rollback-and-replay treatment:

- ER_CHECKREAD (1020), raised under ``innodb_snapshot_isolation=ON`` when a
  locking statement meets a row version committed after this transaction's
  snapshot rather than reading the stale version.
- ER_LOCK_DEADLOCK (1213), raised when InnoDB breaks a lock cycle by rolling
  one of the transactions back.

Write paths wrap their transactional unit in ``retry_on_transient_conflict`` so
either one is retried on a fresh transaction instead of surfacing a 500.

These helpers inject the error into a route's explicit flush or its commit,
which is what lets a test exercise the real helper without racing two live
requests.
"""

from unittest.mock import patch

import pymysql
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession


def _db_error(errno: int, message: str) -> OperationalError:
    """Build the sqlalchemy error the aiomysql/pymysql driver raises for `errno`."""
    return OperationalError("UPDATE ...", None, pymysql.err.OperationalError(errno, message))


def _snapshot_conflict_error(table: str = "images") -> OperationalError:
    """The error MariaDB raises under innodb_snapshot_isolation (ER_CHECKREAD).

    `table` is the table the conflict is reported against. For the tag write
    paths that is `tag_history`: its INSERT takes a locking read on each FK
    parent (tags/images/users), and ER_CHECKREAD names the child table's
    handler, not the parent row that actually moved.
    """
    return _db_error(1020, f"Record has changed since last read in table '{table}'")


def _deadlock_error() -> OperationalError:
    """The error MariaDB raises when it picks this transaction as the deadlock
    victim (ER_LOCK_DEADLOCK). Carries no table name — InnoDB reports the cycle,
    not a single row."""
    return _db_error(1213, "Deadlock found when trying to get lock; try restarting transaction")


def _flaky_commit(fail_times: int, error: OperationalError):
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


def _flaky_flush(fail_times: int, error: OperationalError):
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


def _flaky_flush_nth(n: int, error: OperationalError):
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
