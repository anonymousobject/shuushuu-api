"""A disabled counter trigger must be detectable, not silent.

pgloader loads with ``disable triggers`` (scripts/pg_migration/shuushuu.load.template),
which issues ``ALTER TABLE ... DISABLE TRIGGER ALL`` and so takes the counter
triggers down along with the FK internals. ``migrate.py post`` drops and re-adds
every FK constraint, so those come back enabled no matter how the load went —
but nothing re-enables the *user* triggers this module installs. A load that
dies partway can therefore leave the counters silently inert on a database that
passes every row-count check in ``migrate.py validate``.

The cutover runbook's fix is to assert on trigger state before the window
closes; this covers the query that assertion runs.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pg_triggers import disabled_triggers

pytestmark = [pytest.mark.integration, pytest.mark.postgres_only]


async def test_reports_nothing_on_a_healthy_schema(db_session: AsyncSession) -> None:
    """The chain-built schema installs every trigger enabled."""
    assert await disabled_triggers(await db_session.connection()) == []


async def test_detects_a_disabled_counter_trigger(db_session: AsyncSession) -> None:
    """The exact state a half-finished pgloader run leaves behind."""
    await db_session.execute(
        text("ALTER TABLE tag_links DISABLE TRIGGER tag_links_counters_insert")
    )

    assert await disabled_triggers(await db_session.connection()) == [
        "tag_links.tag_links_counters_insert"
    ]


async def test_reports_every_disabled_trigger_not_just_the_first(
    db_session: AsyncSession,
) -> None:
    """``DISABLE TRIGGER ALL`` hits whole tables at a time, so the check must
    enumerate rather than short-circuit on the first hit."""
    await db_session.execute(text("ALTER TABLE tag_links DISABLE TRIGGER ALL"))

    found = await disabled_triggers(await db_session.connection())

    assert found == sorted(found), "results must be ordered for stable reporting"
    assert set(found) == {
        "tag_links.tag_links_counters_insert",
        "tag_links.tag_links_counters_delete",
    }
