"""Integration tests: UtcDateTime round-trips through real MariaDB.

Verifies the TypeDecorator wires correctly into the SQLAlchemy machinery and
behaves correctly against the actual aiomysql driver, not just in unit-level
isolation. A throwaway table is created/dropped inside the test so we don't
depend on any production model adopting UtcDateTime yet (that happens in
later chunks).
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, insert, select
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.models.types import UtcDateTime


def _make_temp_table() -> Table:
    """Build an isolated MetaData/Table for the round-trip test.

    Using a fresh MetaData keeps this table out of the global SQLModel registry
    so it doesn't pollute schema-sync tests or other autogenerate workflows.
    """
    metadata = MetaData()
    return Table(
        "utc_datetime_round_trip",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("ts", UtcDateTime, nullable=True),
    )


@pytest.fixture
async def temp_dt_table(engine: AsyncEngine):
    """Create the throwaway table on the test DB; drop it after the test."""
    table = _make_temp_table()

    async with engine.begin() as conn:
        await conn.run_sync(table.create)

    try:
        yield table
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(table.drop)


@pytest.mark.integration
class TestUtcDateTimeRoundTrip:
    """End-to-end round-trip through the aiomysql driver."""

    async def test_utc_aware_round_trips(
        self, engine: AsyncEngine, temp_dt_table: Table
    ):
        """A UTC-aware datetime survives write+read with tz preserved."""
        ts = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)

        async with engine.begin() as conn:
            result = await conn.execute(insert(temp_dt_table).values(ts=ts))
            row_id = result.inserted_primary_key[0]

        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    select(temp_dt_table.c.ts).where(temp_dt_table.c.id == row_id)
                )
            ).one()

        loaded = row.ts
        assert loaded == ts
        assert loaded.tzinfo == UTC

    async def test_non_utc_aware_stored_as_utc_equivalent(
        self, engine: AsyncEngine, temp_dt_table: Table
    ):
        """A non-UTC aware datetime is converted to UTC on bind, returns as UTC-aware."""
        est = timezone(timedelta(hours=-5))
        # 12:00 EST == 17:00 UTC
        ts_est = datetime(2026, 5, 1, 12, 0, 0, tzinfo=est)
        expected_utc = datetime(2026, 5, 1, 17, 0, 0, tzinfo=UTC)

        async with engine.begin() as conn:
            result = await conn.execute(insert(temp_dt_table).values(ts=ts_est))
            row_id = result.inserted_primary_key[0]

        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    select(temp_dt_table.c.ts).where(temp_dt_table.c.id == row_id)
                )
            ).one()

        loaded = row.ts
        assert loaded == expected_utc
        assert loaded == ts_est  # equal as instants
        assert loaded.tzinfo == UTC

    async def test_naive_datetime_bind_raises(
        self, engine: AsyncEngine, temp_dt_table: Table
    ):
        """Binding a naive datetime raises TypeError before hitting the DB.

        SQLAlchemy wraps bind-time exceptions in StatementError; assert the
        underlying TypeError carries our diagnostic message.
        """
        naive = datetime(2026, 5, 1, 12, 0, 0)

        with pytest.raises(StatementError) as exc_info:
            async with engine.begin() as conn:
                await conn.execute(insert(temp_dt_table).values(ts=naive))

        assert isinstance(exc_info.value.orig, TypeError)
        assert "naive datetime" in str(exc_info.value.orig)
