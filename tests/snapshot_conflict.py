"""Helpers for testing the MariaDB snapshot-conflict retry (app/core/db_retry.py).

Under ``innodb_snapshot_isolation=ON`` a locking statement that meets a row
version committed after this transaction's snapshot aborts with ER_CHECKREAD
(errno 1020) rather than reading the stale version. Write paths wrap their
transactional unit in ``retry_on_snapshot_conflict`` so the conflict is retried
on a fresh snapshot instead of surfacing a 500.

These helpers inject that error into a route's explicit flush, which is what
lets a test exercise the real helper without racing two live requests.
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
