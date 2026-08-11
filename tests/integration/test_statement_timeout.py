"""Statement-timeout circuit breaker.

These run against the real database because the whole point is what MariaDB does
with `max_statement_time` -- a stub would prove nothing.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import statement_timeout


async def _session_limit(db: AsyncSession) -> float:
    result = await db.execute(text("SELECT @@SESSION.max_statement_time"))
    return float(result.scalar() or 0)


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

    # MariaDB killing the statement aborts the transaction under SQLAlchemy, which
    # emits this on the rollback below. It is the expected consequence of the
    # breaker firing, not a defect -- named explicitly so the suite's output stays
    # clean without hiding anything else.
    @pytest.mark.filterwarnings(
        "ignore:transaction already deassociated from connection:sqlalchemy.exc.SAWarning"
    )
    async def test_a_statement_over_the_limit_is_killed(self, db_session: AsyncSession):
        """The breaker must actually break, not just set a variable.

        A tiny limit is used deliberately: the real 5s ceiling is chosen to never
        fire on legitimate traffic, so provoking it needs an artificial one.
        """
        with pytest.raises(OperationalError):
            async with statement_timeout(db_session, 0.05):
                await db_session.execute(text("SELECT SLEEP(2)"))

        # The session is still usable afterwards, and unbounded again.
        await db_session.rollback()
        assert await _session_limit(db_session) == 0.0
