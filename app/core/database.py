"""
Database configuration and session management
"""

from collections.abc import AsyncGenerator

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
)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
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
