# Shuushuu API Tests

This directory contains the test suite for the Shuushuu API.

## Structure

```
tests/
├── conftest.py           # Shared fixtures and test configuration
├── unit/                 # Unit tests (fast, no external dependencies)
│   └── test_schemas.py  # Pydantic schema validation tests
├── api/                  # API endpoint tests
│   └── v1/
│       └── test_images.py  # Image API endpoint tests
└── integration/          # Integration tests (database, services)
```

## Running Tests

### Run all tests
```bash
uv run pytest
```

### Run specific test categories
```bash
# Unit tests only (fast)
uv run pytest -m unit

# API tests only
uv run pytest -m api

# Integration tests only
uv run pytest -m integration

# Exclude slow tests
uv run pytest -m "not slow"
```

### Run specific test files
```bash
uv run pytest tests/api/v1/test_images.py
uv run pytest tests/unit/test_schemas.py
```

### Run with coverage
```bash
# Enable coverage in pyproject.toml first, then:
uv run pytest --cov=app --cov-report=html --cov-report=term-missing
```

### Verbose output
```bash
uv run pytest -v
uv run pytest -vv  # Extra verbose
```

## Test Database Configuration

Tests use a **dedicated test database** to ensure complete isolation from development and production environments.

### Required Environment Variables

Add these to your `.env` file:

```bash
# Test Database Configuration
TEST_DATABASE_URL=mysql+aiomysql://user:password@localhost:3306/shuushuu_test?charset=utf8mb4
TEST_DATABASE_URL_SYNC=mysql+pymysql://user:password@localhost:3306/shuushuu_test?charset=utf8mb4
```

### Why Separate Test Credentials?

1. **Safety** - Prevents accidentally testing against dev/prod databases
2. **Isolation** - Test database is completely independent
3. **Flexibility** - Easy to use different credentials in CI/CD vs local
4. **Standard Practice** - Explicit configuration is clearer than auto-derivation

### Local Development

For local development, you can use the same MySQL server and credentials as your dev environment:
- Same host, user, and password as `DATABASE_URL`
- Different database name (`shuushuu_test` vs `shuushuu`)

### CI/CD

In CI/CD environments, set `TEST_DATABASE_URL` environment variable to point to your CI test database.

### Test Database Lifecycle

1. **Session Setup** (`setup_test_database` fixture):
   - Drops and recreates `shuushuu_test` database
   - Creates all tables from SQLModel metadata
   - Runs once per test session

2. **Test Execution** (each test function):
   - Creates test users (IDs 1, 2, 3) for foreign key constraints
   - Test runs with isolated data
   - Tables truncated after test completes

3. **Session Teardown**:
   - Test database is left intact for inspection
   - Uncomment cleanup code in `conftest.py` to auto-drop

## Writing Tests

### Test Markers

Use markers to categorize tests:

```python
import pytest

@pytest.mark.unit
def test_something_fast():
    """Unit test - no database or external dependencies."""
    pass

@pytest.mark.api
async def test_api_endpoint(client):
    """API test - tests HTTP endpoints."""
    response = await client.get("/api/v1/images")
    assert response.status_code == 200

@pytest.mark.integration
async def test_database_operation(db_session):
    """Integration test - tests database operations."""
    pass

@pytest.mark.slow
async def test_slow_operation():
    """Slow test - takes significant time."""
    pass
```

### Using Fixtures

Common fixtures from `conftest.py`:

#### Database Fixtures
- `db_session` - Database session (includes 3 base test users: IDs 1, 2, 3)
- `engine` - Database engine
- `client` - HTTP client for testing API endpoints
- `app` - FastAPI app instance

#### Data Fixtures (create DB objects)
- `test_user` - Creates a user in the database
- `test_image` - Creates an image (depends on `test_user`)
- `test_tag` - Creates a tag in the database

#### Dictionary Fixtures (for API payloads)
- `sample_image_data` - Dictionary of image data
- `sample_tag_data` - Dictionary of tag data
- `sample_user_data` - Dictionary of user registration data

#### Example Usage

```python
async def test_with_client(client: AsyncClient):
    """Use the HTTP client fixture."""
    response = await client.get("/api/v1/images")
    assert response.status_code == 200

async def test_with_fixtures(test_user, test_image, db_session):
    """Use data fixtures - they're already created in the DB."""
    # test_user and test_image are already committed
    assert test_image.user_id == test_user.user_id

async def test_create_custom_data(test_user, db_session):
    """Create custom test data for this specific test."""
    from app.models.image import Image

    image = Image(
        filename="custom-test",
        ext="jpg",
        user_id=test_user.user_id,
        # ... other fields
    )
    db_session.add(image)
    await db_session.commit()

    # Test with the custom image
    assert image.image_id is not None
```

See [test_fixtures_example.py](test_fixtures_example.py) for more examples.

### Test Naming Convention

- Test files: `test_*.py`
- Test classes: `Test*`
- Test functions: `test_*`

### Best Practices

1. **Arrange-Act-Assert**: Structure tests clearly
   ```python
   async def test_create_image(client, sample_image_data):
       # Arrange - set up test data
       data = sample_image_data

       # Act - perform the action
       response = await client.post("/api/v1/images", json=data)

       # Assert - verify the result
       assert response.status_code == 201
   ```

2. **One assertion per test** (when practical)

3. **Clear test names** that describe what's being tested

4. **Use fixtures** for common setup

5. **Clean up after tests** (fixtures handle this automatically)

## Continuous Integration

Tests should be run in CI/CD pipeline before merging PRs:

```bash
# Quick check (unit tests only)
uv run pytest -m unit

# Full test suite
uv run pytest
```
