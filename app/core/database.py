"""
Database configuration and session management
"""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import text as sql_text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.config import settings

# Create declarative base for models
Base = declarative_base()

# Ensure all connections use UTC timezone for consistent datetime handling.
_connect_args: dict[str, Any] = {"server_settings": {"timezone": "UTC"}}

# Create async engine
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,  # Verify connections before using
    pool_recycle=3600,  # Recycle connections every hour
    connect_args=_connect_args,
)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession]:
    """
    Dependency for getting async database sessions.

    Usage in FastAPI:
        @app.get("/items")
        async def read_items(db: DbSession):
            result = await db.execute(select(Item))
            return result.scalars().all()
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Type alias for dependency injection
DbSession = Annotated[AsyncSession, Depends(get_db)]


def get_async_session() -> AsyncSession:
    """
    Get a standalone async database session for background tasks.

    This is a context manager that should be used with 'async with':
        async with get_async_session() as db:
            await db.execute(...)
            await db.commit()

    Note: Caller is responsible for committing/rolling back.
    """
    return AsyncSessionLocal()


async def set_statement_timeout(db: AsyncSession, seconds: float) -> None:
    """Limit each following statement on `db`'s connection to `seconds`.

    Call it bare only inside a `statement_timeout` block, to tighten the block's
    limit: the block's exit is what restores the connection for its next user.
    """
    # int() coerces the value: SET does not take bind parameters, so this is
    # interpolated, and the coercion is what keeps that safe. Postgres takes whole
    # milliseconds, and 0 would switch the limit off, hence at least 1.
    await db.execute(sql_text(f"SET statement_timeout = {max(1, int(seconds * 1000))}"))


@asynccontextmanager
async def _restoring_default(db: AsyncSession, setting: str) -> AsyncIterator[None]:
    """Restore `setting` (a trusted literal) to the server default after the block.

    Restoring is not optional. Connections are pooled and returned to the pool
    without resetting session variables, so a value left set would silently apply
    to whatever request picks that connection up next. `DEFAULT` restores the
    server's global value rather than assuming one.

    A database error inside the block rolls the session back first, expiring its
    loaded objects: the error left the transaction aborted, where the restoring
    SET would fail and replace the error with its own.
    """
    try:
        yield
    except DBAPIError:
        await db.rollback()
        raise
    finally:
        await db.execute(sql_text(f"SET {setting} = DEFAULT"))


@asynccontextmanager
async def statement_timeout(db: AsyncSession, seconds: float | None) -> AsyncIterator[None]:
    """Bound how long each statement inside the block may run.

    A circuit breaker for query plans that go wrong, not a performance policy:
    the limit should sit well clear of the slowest legitimate query so it never
    fires on real traffic. `statement_timeout` is per *statement*, so a request
    issuing several still has a total ceiling of the limit times the statement
    count, unless it tightens the limit as it goes (`set_statement_timeout`).

    The limit is restored on exit, and a database error inside the block rolls the
    session back (see `_restoring_default`). Do not nest it: the inner block's exit
    restores DEFAULT, not the outer block's limit.

    Passing `seconds=None` is a no-op, so callers can wrap a statement
    unconditionally and decide per request whether the bound applies.
    """
    if seconds is None:
        yield
        return

    await set_statement_timeout(db, seconds)
    async with _restoring_default(db, "statement_timeout"):
        yield


@asynccontextmanager
async def force_custom_plans(db: AsyncSession) -> AsyncIterator[None]:
    """Plan each statement inside the block for its actual parameter values.

    Under the default `plan_cache_mode = auto`, Postgres may switch a prepared
    statement to a generic plan, made without looking at the parameters, from its
    sixth execution, and asyncpg prepares every statement on each pooled connection.
    Use this where a statement's best plan depends on its bind values.

    Restored on exit, with a rollback on a database error, as `statement_timeout`
    is. Do not nest it: the inner block's exit restores DEFAULT, not the outer setting.
    """
    await db.execute(sql_text("SET plan_cache_mode = force_custom_plan"))
    async with _restoring_default(db, "plan_cache_mode"):
        yield
