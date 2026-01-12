"""
Pytest configuration and shared fixtures.

This file provides common fixtures for all tests.
"""

import os
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from dotenv import load_dotenv
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import get_db
from app.core.redis import get_redis
from app.main import app as main_app

# =============================================================================
# Test Database Configuration
# =============================================================================
# Default credentials match CI workflow (.github/workflows/ci.yml)
# Override via environment variables if your local setup differs

DEFAULT_TEST_DB_USER = "shuushuu"
DEFAULT_TEST_DB_PASSWORD = "shuushuu_password"  # Matches local .env; CI overrides via TEST_DATABASE_URL
DEFAULT_TEST_DB_HOST = "localhost"
DEFAULT_TEST_DB_PORT = "3306"
DEFAULT_TEST_DB_NAME = "shuushuu_pytest"  # Separate from staging environment (shuushuu_test in .env.test)
DEFAULT_ROOT_PASSWORD = "root_password"


# Load .env file if present (optional - defaults work without it)
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)


# =============================================================================
# Pytest Hooks for Custom Markers
# =============================================================================


def pytest_addoption(parser):
    """Add custom command line options."""
    parser.addoption(
        "--schema-sync",
        action="store_true",
        default=False,
        help="Run schema sync tests (compares models vs migrations)",
    )


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "schema_sync: marks tests that compare model schema vs migration schema (run with --schema-sync)",
    )


def pytest_collection_modifyitems(config, items):
    """Skip schema_sync tests unless --schema-sync is passed."""
    if config.getoption("--schema-sync"):
        # --schema-sync given: don't skip schema_sync tests
        return

    skip_schema_sync = pytest.mark.skip(reason="need --schema-sync option to run")
    for item in items:
        if "schema_sync" in item.keywords:
            item.add_marker(skip_schema_sync)


def _get_test_database_url() -> tuple[str, str]:
    """
    Get test database URLs with sensible defaults.

    Uses hardcoded defaults that match CI workflow. Override via environment
    variables if your local setup differs:
    - TEST_DATABASE_URL: Full async URL (overrides all other settings)
    - TEST_DATABASE_URL_SYNC: Full sync URL
    - TEST_DB_USER, TEST_DB_PASSWORD, TEST_DB_HOST, TEST_DB_PORT, TEST_DB_NAME:
      Individual components (used if TEST_DATABASE_URL not set)

    Returns:
        Tuple of (async_url, sync_url)
    """
    # Check for full URL override first
    test_url = os.getenv("TEST_DATABASE_URL")

    if not test_url:
        # Build URL from individual components with defaults
        user = os.getenv("TEST_DB_USER", DEFAULT_TEST_DB_USER)
        password = os.getenv("TEST_DB_PASSWORD", DEFAULT_TEST_DB_PASSWORD)
        host = os.getenv("TEST_DB_HOST", DEFAULT_TEST_DB_HOST)
        port = os.getenv("TEST_DB_PORT", DEFAULT_TEST_DB_PORT)
        db = os.getenv("TEST_DB_NAME", DEFAULT_TEST_DB_NAME)
        test_url = f"mysql+aiomysql://{user}:{password}@{host}:{port}/{db}?charset=utf8mb4"

    # Allow sync URL to be derived from async URL if not explicitly set
    test_url_sync = os.getenv("TEST_DATABASE_URL_SYNC", test_url.replace("+aiomysql", "+pymysql"))

    return test_url, test_url_sync


# Get test database URLs (uses defaults if env vars not set)
TEST_DATABASE_URL, TEST_DATABASE_URL_SYNC = _get_test_database_url()


def _create_tag_search_and_popularity_features(sync_engine):
    """
    Create FULLTEXT index and triggers for tag search and popularity tracking.

    This mirrors the schema created by alembic/versions/tag_search_and_popularity.py
    so that test database matches production exactly. These features must be created
    after SQLModel.metadata.create_all() since they depend on tables existing.

    Args:
        sync_engine: SQLAlchemy sync engine connected to test database
    """
    from sqlalchemy import text

    with sync_engine.connect() as conn:
        # Create FULLTEXT index for tag search
        try:
            conn.execute(text("ALTER TABLE tags ADD FULLTEXT INDEX ft_tags_title (title)"))
        except Exception:
            # Index may already exist; silently continue
            pass

        # Create trigger for INSERT on tag_links (increment usage_count)
        try:
            conn.execute(
                text("""
                    CREATE TRIGGER trig_tag_links_insert AFTER INSERT ON tag_links
                    FOR EACH ROW
                    BEGIN
                        UPDATE tags SET usage_count = usage_count + 1
                        WHERE tag_id = NEW.tag_id;
                    END
                """)
            )
        except Exception:
            # Trigger may already exist; silently continue
            pass

        # Create trigger for DELETE on tag_links (decrement usage_count)
        try:
            conn.execute(
                text("""
                    CREATE TRIGGER trig_tag_links_delete AFTER DELETE ON tag_links
                    FOR EACH ROW
                    BEGIN
                        UPDATE tags SET usage_count = GREATEST(0, usage_count - 1)
                        WHERE tag_id = OLD.tag_id;
                    END
                """)
            )
        except Exception:
            # Trigger may already exist; silently continue
            pass

        conn.commit()


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
    4. Test user is created with proper permissions

    Note: Uses root credentials to create database and user, then runs migrations
    with the test user credentials.
    """
    import os

    # Get MySQL root password from environment or use default
    # First try MARIADB_ROOT_PASSWORD (from .env files), then fall back to MYSQL_ROOT_PASSWORD
    root_password = os.getenv("MARIADB_ROOT_PASSWORD") or os.getenv("MYSQL_ROOT_PASSWORD", DEFAULT_ROOT_PASSWORD)

    # Get test user credentials (these can be overridden via environment)
    test_user = os.getenv("TEST_DB_USER", DEFAULT_TEST_DB_USER)
    test_password = os.getenv("TEST_DB_PASSWORD", DEFAULT_TEST_DB_PASSWORD)

    # Use root user to drop/create database (needs elevated privileges)
    admin_engine = create_engine(
        f"mysql+pymysql://root:{root_password}@localhost:3306/mysql",
        isolation_level="AUTOCOMMIT",
    )

    # Extract database name from test URL for operations
    test_db_name = os.getenv("TEST_DB_NAME", DEFAULT_TEST_DB_NAME)

    with admin_engine.connect() as conn:
        # Drop and recreate test database
        conn.execute(text(f"DROP DATABASE IF EXISTS {test_db_name}"))
        conn.execute(
            text(f"CREATE DATABASE {test_db_name} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        )

        # Create test user if it doesn't exist
        conn.execute(
            text("CREATE USER IF NOT EXISTS :username@'%' IDENTIFIED BY :password"),
            {"username": test_user, "password": test_password},
        )

        # Grant permissions to test user on test database
        conn.execute(
            text(f"GRANT ALL PRIVILEGES ON {test_db_name}.* TO :username@'%'"),
            {"username": test_user},
        )
        conn.execute(text("FLUSH PRIVILEGES"))

    admin_engine.dispose()

    # Create tables using SQLAlchemy metadata (sync engine)
    # Note: SQLModel doesn't support database triggers natively, so we create tables first,
    # then manually create triggers to match the production Alembic migration.
    # This ensures test schema matches production exactly.
    from sqlmodel import SQLModel

    from app.models.admin_action import AdminActions  # noqa: F401
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
    from app.models.review_vote import ReviewVotes  # noqa: F401
    from app.models.tag import Tags  # noqa: F401
    from app.models.tag_history import TagHistory  # noqa: F401
    from app.models.tag_link import TagLinks  # noqa: F401
    from app.models.user import Users  # noqa: F401
    from app.models.user_suspension import UserSuspensions  # noqa: F401

    sync_engine = create_engine(TEST_DATABASE_URL_SYNC, echo=False)

    # Create base tables from SQLModel metadata
    SQLModel.metadata.create_all(sync_engine)

    # Create database enhancements (FULLTEXT index and triggers) that match production
    # These are defined in alembic/versions/tag_search_and_popularity.py
    # We create them here so tests use identical schema to production
    _create_tag_search_and_popularity_features(sync_engine)

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
    #     conn.execute(text(f"DROP DATABASE IF EXISTS {test_db_name}"))
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

        # Get database name from the connection URL
        db_url = make_url(TEST_DATABASE_URL)
        db_name = db_url.database

        # Get all tables
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                f"WHERE table_schema = '{db_name}' AND table_type = 'BASE TABLE'"
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


@pytest.fixture
async def mock_redis():
    """Mock Redis client for permission caching and other features."""
    mock = MagicMock()
    mock.get = AsyncMock(return_value=None)  # Cache miss by default
    mock.set = AsyncMock()
    mock.setex = AsyncMock()  # For permission cache with TTL
    mock.delete = AsyncMock()  # For cache invalidation
    mock.incr = AsyncMock()
    mock.expire = AsyncMock()
    mock.close = AsyncMock()

    # Setup pipeline mock
    pipeline_mock = MagicMock()
    pipeline_mock.execute = AsyncMock()
    mock.pipeline.return_value = pipeline_mock

    return mock


@pytest.fixture(scope="function")
def app(db_session: AsyncSession, mock_redis) -> FastAPI:
    """
    Create FastAPI app with test database session.

    This overrides the database dependency to use the test session.
    """

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    async def override_get_redis():
        yield mock_redis

    main_app.dependency_overrides[get_db] = override_get_db
    main_app.dependency_overrides[get_redis] = override_get_redis

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
async def another_test_image(db_session: AsyncSession, test_user):
    """Create a second test image for tests that need multiple images."""
    from app.models.image import Images

    image = Images(
        filename="test-image-002",
        ext="jpg",
        original_filename="test2.jpg",
        md5_hash="e41d8cd98f00b204e9800998ecf8427f",
        filesize=234567,
        width=1280,
        height=720,
        caption="Second test image from fixture",
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
async def test_images_batch(db_session: AsyncSession, test_user):
    """Create a batch of test images for tests that need multiple images."""
    from app.models.image import Images

    images = []
    for i in range(5):
        image = Images(
            filename=f"test-batch-image-{i:03d}",
            ext="jpg",
            original_filename=f"batch{i}.jpg",
            md5_hash=f"{i:032x}",  # 32-char hex string
            filesize=100000 + i * 1000,
            width=800,
            height=600,
            caption=f"Batch test image {i}",
            rating=0.0,
            user_id=test_user.user_id,
            status=1,
            locked=False,
        )
        db_session.add(image)
        images.append(image)

    await db_session.commit()
    for img in images:
        await db_session.refresh(img)
    return images


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


@pytest.fixture
async def sample_user(db_session: AsyncSession):
    """
    Create a sample user in the database for testing.

    This is an alias for test_user but with a different name for semantic clarity
    in tests that need a user to perform actions (like favoriting images).
    """
    from app.models.user import Users

    user = Users(
        username="sampleuser",
        password="hashed_password_here",
        password_type="bcrypt",
        salt="saltsalt12345678",
        email="sample@example.com",
        active=1,  # User must be active to authenticate
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def authenticated_client(client: AsyncClient, sample_user) -> AsyncClient:
    """
    Create an HTTP client with authentication headers for the sample user.

    Uses the existing client fixture and just adds authentication headers.

    Usage:
        async def test_protected_endpoint(authenticated_client, sample_user):
            response = await authenticated_client.get("/api/v1/protected")
            assert response.status_code == 200
    """
    from app.core.security import create_access_token

    # Create access token for the sample user
    access_token = create_access_token(sample_user.id)

    # Add auth header to existing client
    client.headers.update({"Authorization": f"Bearer {access_token}"})

    return client
