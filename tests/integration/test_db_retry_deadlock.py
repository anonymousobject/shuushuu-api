"""The retry helper against a real Postgres deadlock (app/core/db_retry.py).

The unit tests fabricate the adapter's error; this provokes the genuine one.
Two sessions update the same two users rows in opposite order. Postgres
detects the cycle after deadlock_timeout (1s by default) and aborts one side
with SQLSTATE 40P01; the aborted side must replay on a fresh transaction and
succeed.
"""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db_retry import retry_on_transient_conflict

# needs_commit: users 1-3 are really committed, so both sessions see them.
pytestmark = [pytest.mark.integration, pytest.mark.needs_commit]


async def test_deadlock_victim_replays_and_succeeds(db_session, engine):
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    # Both sides must hold their first row before either asks for the second,
    # or there is no cycle. Only the first attempt waits: a replay runs after
    # the winner committed and just proceeds.
    first_locks_taken = asyncio.Barrier(2)
    calls = {"a": 0, "b": 0}

    async def cross_update(name: str, first: int, second: int) -> str:
        async with sessions() as db:

            async def unit() -> str:
                calls[name] += 1
                await db.execute(
                    text("UPDATE users SET location = :v WHERE user_id = :id"),
                    {"v": name, "id": first},
                )
                if calls[name] == 1:
                    await first_locks_taken.wait()
                await db.execute(
                    text("UPDATE users SET location = :v WHERE user_id = :id"),
                    {"v": name, "id": second},
                )
                await db.commit()
                return name

            return await retry_on_transient_conflict(db, unit, what=f"deadlock_{name}")

    results = await asyncio.gather(cross_update("a", 1, 2), cross_update("b", 2, 1))

    assert sorted(results) == ["a", "b"]
    # Exactly one side was the victim and replayed once.
    assert sorted(calls.values()) == [1, 2]
