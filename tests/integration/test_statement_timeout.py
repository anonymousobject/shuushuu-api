"""Statement-timeout circuit breaker.

These run against the real database because the whole point is what the server
does with its statement-time limit (`statement_timeout` on Postgres) -- a stub
would prove nothing.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import force_custom_plans, statement_timeout

_QUERY_CANCELED = "57014"
_DIVISION_BY_ZERO = "22012"
# pg_settings.setting reports statement_timeout in ms as a bare number, which
# sidesteps parsing the unit-suffixed SHOW output.
_SLEEP_2 = text("SELECT pg_sleep(2)")
# asyncpg surfaces the cancelled statement as QueryCanceledError, which
# SQLAlchemy wraps as a generic DBAPIError.
_TIMEOUT_ERROR: type[Exception] = DBAPIError


async def _session_limit(db: AsyncSession) -> float:
    result = await db.execute(
        text("SELECT setting FROM pg_settings WHERE name = 'statement_timeout'")
    )
    return float(result.scalar() or 0) / 1000.0


class TestStatementTimeout:
    async def test_applies_the_limit_inside_the_block(self, db_session: AsyncSession):
        async with statement_timeout(db_session, 5.0):
            assert await _session_limit(db_session) == 5.0

    async def test_restores_the_limit_afterwards(self, db_session: AsyncSession):
        before = await _session_limit(db_session)
        async with statement_timeout(db_session, 5.0):
            pass
        assert await _session_limit(db_session) == before

    async def test_restores_the_limit_even_when_the_body_raises(self, db_session: AsyncSession):
        """Connections are pooled, so a leaked limit would hit the next request."""
        before = await _session_limit(db_session)
        with pytest.raises(RuntimeError):
            async with statement_timeout(db_session, 5.0):
                raise RuntimeError("boom")
        assert await _session_limit(db_session) == before

    async def test_none_is_a_no_op(self, db_session: AsyncSession):
        before = await _session_limit(db_session)
        async with statement_timeout(db_session, None):
            assert await _session_limit(db_session) == before
        assert await _session_limit(db_session) == before

    async def test_a_statement_over_the_limit_is_killed(self, db_session: AsyncSession):
        """The breaker must actually break, not just set a variable.

        A tiny limit is used deliberately: the real 5s ceiling is chosen to never
        fire on legitimate traffic, so provoking it needs an artificial one.
        """
        with pytest.raises(_TIMEOUT_ERROR) as raised:
            async with statement_timeout(db_session, 0.05):
                await db_session.execute(_SLEEP_2)

        # The cancellation itself, not the restore failing in the aborted transaction.
        assert raised.value.orig.sqlstate == _QUERY_CANCELED  # type: ignore[union-attr]
        # The session is still usable afterwards, and unbounded again.
        assert await _session_limit(db_session) == 0.0

    async def test_a_failure_in_a_nested_block_surfaces_as_itself(self, db_session: AsyncSession):
        """Whichever statement fails inside, entering an inner block included, the
        outer block's restore must not replace the error with its own."""
        with pytest.raises(DBAPIError) as raised:
            async with statement_timeout(db_session, 5.0), force_custom_plans(db_session):
                await db_session.execute(text("SELECT 1 / 0"))

        assert raised.value.orig.sqlstate == _DIVISION_BY_ZERO  # type: ignore[union-attr]
        assert await _session_limit(db_session) == 0.0
        plan_cache_mode = await db_session.execute(text("SHOW plan_cache_mode"))
        assert plan_cache_mode.scalar_one() == "auto"
