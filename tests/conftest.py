"""
Pytest configuration and shared fixtures.

This file provides common fixtures for all tests.
"""

import os
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from dotenv import load_dotenv
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import get_db
from app.main import app as main_app

# Load .env file at module import time to make TEST_DATABASE_URL available
# This must happen before _get_test_database_url() is called
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)


def _get_test_database_url() -> tuple[str, str]:
    """
    Get test database URLs from environment variables.

    Tests MUST use explicit TEST_DATABASE_URL to avoid accidentally
    testing against dev/prod databases. This ensures safe test isolation.

    Required environment variables:
    - TEST_DATABASE_URL: Async database URL (e.g., mysql+aiomysql://...)
    - TEST_DATABASE_URL_SYNC: (Optional) Sync database URL, defaults to async URL
      with driver changed from aiomysql to pymysql

    Example .env:
        TEST_DATABASE_URL=mysql+aiomysql://user:pass@localhost:3306/shuushuu_test?charset=utf8mb4
        TEST_DATABASE_URL_SYNC=mysql+pymysql://user:pass@localhost:3306/shuushuu_test?charset=utf8mb4

    Raises:
        ValueError: If TEST_DATABASE_URL is not set
    """
    test_url = os.getenv("TEST_DATABASE_URL")
    if not test_url:
        raise ValueError(
            "TEST_DATABASE_URL environment variable is required for running tests.\n"
            "Tests should use dedicated test database credentials to ensure isolation.\n"
            "Add to your .env file:\n"
            "  TEST_DATABASE_URL=mysql+aiomysql://user:pass@localhost:3306/shuushuu_test?charset=utf8mb4\n"
            "  TEST_DATABASE_URL_SYNC=mysql+pymysql://user:pass@localhost:3306/shuushuu_test?charset=utf8mb4"
        )

    # Allow sync URL to be derived from async URL if not explicitly set
    test_url_sync = os.getenv("TEST_DATABASE_URL_SYNC", test_url.replace("+aiomysql", "+pymysql"))

    return test_url, test_url_sync


# Get test database URLs (no hardcoded credentials!)
TEST_DATABASE_URL, TEST_DATABASE_URL_SYNC = _get_test_database_url()


@pytest.fixture(scope="session")
def anyio_backend():
    """Use asyncio backend for async tests."""
    return "asyncio"


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """
    Recreate test database and run migrations before ALL tests.

    This runs once per test session (autouse=True) and ensures:
    1. Test database is dropped and recreated (clean slate)
    2. Latest Alembic migrations are applied
    3. Schema matches production exactly

    Note: Uses root credentials to create database, then runs migrations
    with the shuushuu user credentials.
    """
    import os

    # Get MySQL root password from environment or use default
    root_password = os.getenv("MYSQL_ROOT_PASSWORD", "root_password")

    # Use root user to drop/create database (needs elevated privileges)
    admin_engine = create_engine(
        f"mysql+pymysql://root:{root_password}@localhost:3306/mysql",
        isolation_level="AUTOCOMMIT",
    )

    with admin_engine.connect() as conn:
        # Drop and recreate test database
        conn.execute(text("DROP DATABASE IF EXISTS shuushuu_test"))
        conn.execute(
            text("CREATE DATABASE shuushuu_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        )

        # Grant permissions to shuushuu user on test database
        conn.execute(text("GRANT ALL PRIVILEGES ON shuushuu_test.* TO 'shuushuu'@'%'"))
        conn.execute(text("FLUSH PRIVILEGES"))

    admin_engine.dispose()

    # Create tables using SQLAlchemy metadata (sync engine)
    # Note: Alembic migrations are incomplete (initial migration is empty),
    # so we use the ORM models as source of truth for the schema
    from sqlmodel import SQLModel

    # Import ALL SQLModel-based models to register them with SQLModel.metadata
    # This ensures all tables are created in the test database
    from app.models.ban import Bans  # noqa: F401
    from app.models.comment import Comments  # noqa: F401
    from app.models.favorite import Favorites  # noqa: F401
    from app.models.image import Images  # noqa: F401
    from app.models.image_rating import ImageRatings  # noqa: F401
    from app.models.image_report import ImageReports  # noqa: F401
    from app.models.image_review import ImageReviews  # noqa: F401
    from app.models.misc import (  # noqa: F401
        Banners,
        Donations,
        ImageRatingsAvg,
        Quicklinks,
        Tips,
    )
    from app.models.news import News  # noqa: F401
    from app.models.permissions import (  # noqa: F401
        GroupPerms,
        Groups,
        Perms,
        UserGroups,
        UserPerms,
    )
    from app.models.privmsg import Privmsgs  # noqa: F401
    from app.models.refresh_token import RefreshTokens  # noqa: F401
    from app.models.tag import Tags  # noqa: F401
    from app.models.tag_history import TagHistory  # noqa: F401
    from app.models.tag_link import TagLinks  # noqa: F401
    from app.models.user import Users  # noqa: F401
    from app.models.user_session import UserSessions  # noqa: F401

    sync_engine = create_engine(TEST_DATABASE_URL_SYNC, echo=False)

    # Create tables from SQLModel metadata ONLY
    # (We skip Base.metadata to avoid conflicts with deprecated generated.py models)
    SQLModel.metadata.create_all(sync_engine)

    sync_engine.dispose()

    # Optional: If you want to track migrations in the test DB, uncomment this
    # alembic_cfg = Config()
    # alembic_cfg.set_main_option("script_location", "alembic")
    # alembic_cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL_SYNC)
    # command.stamp(alembic_cfg, "head")  # Mark as migrated without running migrations

    yield

    # Optional: Drop test database after all tests complete
    # Commented out to allow inspection of test data after failures
    # admin_engine = create_engine(
    #     f"mysql+pymysql://root:{root_password}@localhost:3306/mysql",
    #     isolation_level="AUTOCOMMIT",
    # )
    # with admin_engine.connect() as conn:
    #     conn.execute(text("DROP DATABASE IF EXISTS shuushuu_test"))
    # admin_engine.dispose()


@pytest.fixture(scope="function")
async def engine():
    """
    Create test database engine for each test function.

    Scope is "function" (not "session") to ensure the async engine runs in the same
    event loop as the function-scoped db_session fixture. Using different scopes for
    async fixtures causes "Future attached to a different loop" errors in pytest-asyncio.

    Note: Tables are created by Alembic migrations in setup_test_database fixture,
    so we don't call create_all() here. We DO clean up tables between tests for isolation.
    """
    test_engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
    )

    yield test_engine

    # Clean up: Truncate all tables between tests (faster than drop/recreate)
    # This keeps the schema but removes all data for the next test
    async with test_engine.begin() as conn:
        # Disable foreign key checks temporarily
        await conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))

        # Get all tables
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'shuushuu_test' AND table_type = 'BASE TABLE'"
            )
        )
        tables = [row[0] for row in result]

        # Truncate each table
        for table in tables:
            if table != "alembic_version":  # Don't truncate migration tracking table
                await conn.execute(text(f"TRUNCATE TABLE `{table}`"))

        # Re-enable foreign key checks
        await conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))

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
    from app.models.user import Users

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
            password_type="bcrypt",  # Required field
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
                password_type="bcrypt",  # Required field
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


# =============================================================================
# Test Data Fixtures
# =============================================================================
# These fixtures create database objects for specific tests.
# They run AFTER setup_test_database creates the schema via migrations.
# Each test gets a fresh, isolated set of data.


@pytest.fixture
async def test_user(db_session: AsyncSession):
    """
    Create a test user in the database.

    Usage:
        async def test_favorite(test_user, db_session):
            # test_user is already created and committed
            assert test_user.user_id is not None
    """
    from app.models.user import Users

    user = Users(
        username="testuser_fixture",
        password="hashed_password_here",
        password_type="bcrypt",  # Required field
        salt="saltsalt12345678",  # CHAR(16)
        email="fixture@example.com",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def test_image(db_session: AsyncSession, test_user):
    """
    Create a test image in the database.

    Depends on test_user fixture to satisfy foreign key constraint.

    Usage:
        async def test_favorite_image(test_image, db_session):
            # test_image is already created
            assert test_image.image_id is not None
    """
    from app.models.image import Images

    image = Images(
        filename="test-image-001",
        ext="jpg",
        original_filename="test.jpg",
        md5_hash="d41d8cd98f00b204e9800998ecf8427e",
        filesize=123456,
        width=1920,
        height=1080,
        caption="Test image from fixture",
        rating=0.0,
        user_id=test_user.user_id,
        status=1,
        locked=False,
    )
    db_session.add(image)
    await db_session.commit()
    await db_session.refresh(image)
    return image


@pytest.fixture
async def test_tag(db_session: AsyncSession):
    """
    Create a test tag in the database.

    Usage:
        async def test_tag_search(test_tag, db_session):
            assert test_tag.tag_id is not None
    """
    from app.config import TagType
    from app.models.tag import Tags

    tag = Tags(
        title="Test Tag Fixture",
        desc="A test tag for testing",
        type=TagType.THEME,  # 1 = THEME
    )
    db_session.add(tag)
    await db_session.commit()
    await db_session.refresh(tag)
    return tag


# =============================================================================
# Sample Data Dictionaries (for API request payloads)
# =============================================================================
# These return plain dictionaries, useful for API testing without DB access


@pytest.fixture
def sample_image_data() -> dict:
    """Sample image data dictionary for API requests."""
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
    """Sample tag data dictionary for API requests."""
    return {
        "tag": "test_tag",
        "tag_type": 0,
    }


@pytest.fixture
def sample_user_data() -> dict:
    """Sample user registration data for API requests."""
    return {
        "username": "newuser",
        "email": "newuser@example.com",
        "password": "SecurePassword123!",
    }
