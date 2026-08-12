"""
Database configuration and session management
"""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.config import settings

# Create declarative base for models
Base = declarative_base()

# Create async engine
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,  # Verify connections before using
    pool_recycle=3600,  # Recycle connections every hour (MariaDB wait_timeout is 8 hours)
    # Ensure all connections use UTC timezone for consistent datetime handling
    connect_args={"init_command": "SET time_zone = '+00:00'"},
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
        async def read_items(db: AsyncSession = Depends(get_db)):
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


@asynccontextmanager
async def statement_timeout(db: AsyncSession, seconds: float | None) -> AsyncIterator[None]:
    """Bound how long each statement inside the block may run.

    A circuit breaker for query plans that go wrong, not a performance policy:
    the limit should sit well clear of the slowest legitimate query so it never
    fires on real traffic. MariaDB's `max_statement_time` is per *statement*, so
    a request issuing several still has a total ceiling of the limit times the
    statement count.

    Restoring on exit is not optional. Connections are pooled and returned to the
    pool without resetting session variables, so a limit left set would silently
    apply to whatever request picks that connection up next. `DEFAULT` restores
    the server's global value rather than assuming it is 0.

    Passing `seconds=None` is a no-op, so callers can wrap a statement
    unconditionally and decide per request whether the bound applies.
    """
    if seconds is None:
        yield
        return

    # float() coerces the value: SET does not take bind parameters, so this is
    # interpolated, and the coercion is what keeps that safe.
    await db.execute(sql_text(f"SET SESSION max_statement_time = {float(seconds)}"))
    try:
        yield
    finally:
        await db.execute(sql_text("SET SESSION max_statement_time = DEFAULT"))
