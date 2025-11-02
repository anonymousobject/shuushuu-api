"""
Pytest configuration and shared fixtures.

This file provides common fixtures for all tests.
"""

from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
from app.main import app as main_app


# Test Database URL - use a separate test database
TEST_DATABASE_URL = (
    "mysql+aiomysql://shuushuu:shuushuu_dev_password@localhost:3306/shuushuu_test?charset=utf8mb4"
)


@pytest.fixture(scope="session")
def anyio_backend():
    """Use asyncio backend for async tests."""
    return "asyncio"


@pytest.fixture(scope="function")
async def engine():
    """
    Create test database engine for each test function.

    Scope is "function" (not "session") to ensure the async engine runs in the same
    event loop as the function-scoped db_session fixture. Using different scopes for
    async fixtures causes "Future attached to a different loop" errors in pytest-asyncio.

    Trade-off: Creates/drops tables for each test (slower) but provides perfect isolation
    and avoids event loop issues. Can optimize to session scope with transaction-based
    isolation later if test performance becomes an issue.
    """
    test_engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
    )

    # Create all tables
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield test_engine

    # Drop all tables after tests
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await test_engine.dispose()


@pytest.fixture(scope="function")
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    """
    Create a new database session for each test.

    Depends on the function-scoped engine fixture, ensuring both run in the same
    event loop. Creates test users (1, 2, 3) that are committed to the database
    for use in tests with foreign key constraints.

    Note: The final rollback() only affects uncommitted changes made during the test.
    Test users are committed, so they persist until tables are dropped by engine teardown.
    """
    from app.models.generated import Users

    async_session_maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session_maker() as session:
        # Create test users for foreign key constraints
        test_user = Users(
            user_id=1,
            username="testuser",
            password="testpassword",
            salt="testsalt12345678",  # CHAR(16)
            email="test@example.com",
        )
        session.add(test_user)

        # Create additional test users
        for i in [2, 3]:
            user = Users(
                user_id=i,
                username=f"testuser{i}",
                password="testpassword",
                salt=f"testsalt{i:07d}",  # CHAR(16) - pad to exactly 16 chars
                email=f"test{i}@example.com",
            )
            session.add(user)

        await session.commit()

        yield session

        # Cleanup - rollback any changes made during the test
        await session.rollback()


@pytest.fixture(scope="function")
def app(db_session: AsyncSession) -> FastAPI:
    """
    Create FastAPI app with test database session.

    This overrides the database dependency to use the test session.
    """
    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    main_app.dependency_overrides[get_db] = override_get_db

    yield main_app

    main_app.dependency_overrides.clear()


@pytest.fixture(scope="function")
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """
    Create async HTTP client for testing API endpoints.

    Usage:
        async def test_endpoint(client):
            response = await client.get("/api/v1/images")
            assert response.status_code == 200
    """
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


# Sample data fixtures for common test scenarios

@pytest.fixture
def sample_image_data() -> dict:
    """Sample image data for testing."""
    return {
        "filename": "test-image-001",
        "ext": "jpg",
        "original_filename": "test.jpg",
        "md5_hash": "d41d8cd98f00b204e9800998ecf8427e",
        "filesize": 123456,
        "width": 1920,
        "height": 1080,
        "caption": "Test image",
        "rating": 0.0,
        "user_id": 1,
        "status": 1,
        "locked": 0,
    }


@pytest.fixture
def sample_tag_data() -> dict:
    """Sample tag data for testing."""
    return {
        "tag": "test_tag",
        "tag_type": 0,
    }


@pytest.fixture
def sample_user_data() -> dict:
    """Sample user data for testing."""
    return {
        "user": "testuser",
        "email": "test@example.com",
        "joindate": "2024-01-01 00:00:00",
        "user_level": 1,
    }
