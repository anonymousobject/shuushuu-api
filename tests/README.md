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

## Test Database

Tests use a separate test database (`shuushuu_test`) to avoid affecting development data.

### Create test database
```bash
docker compose exec mysql mysql -u root -proot_password -e "CREATE DATABASE IF NOT EXISTS shuushuu_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
```

### Drop test database
```bash
docker compose exec mysql mysql -u root -proot_password -e "DROP DATABASE IF EXISTS shuushuu_test;"
```

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

```python
async def test_with_client(client: AsyncClient):
    """Use the HTTP client fixture."""
    response = await client.get("/api/v1/images")
    assert response.status_code == 200

async def test_with_database(db_session: AsyncSession, sample_image_data: dict):
    """Use database session and sample data fixtures."""
    from app.models.generated import Images

    image = Images(**sample_image_data)
    db_session.add(image)
    await db_session.commit()
```

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
