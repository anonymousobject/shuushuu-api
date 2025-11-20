"""
Example tests showing how to use fixtures for test data.

This demonstrates the recommended pattern for writing tests:
1. Start with a blank database (auto-migrated)
2. Use fixtures to create test data per test
3. Each test is isolated and repeatable
"""

import pytest


@pytest.mark.asyncio
async def test_user_fixture(test_user, db_session):
    """Example: Using the test_user fixture."""
    # test_user is already created and committed
    assert test_user.user_id is not None
    assert test_user.username == "testuser_fixture"
    assert test_user.email == "fixture@example.com"


@pytest.mark.asyncio
async def test_image_fixture(test_image, test_user, db_session):
    """Example: Using test_image fixture (which depends on test_user)."""
    # test_image automatically gets a test_user created too
    assert test_image.image_id is not None
    assert test_image.user_id == test_user.user_id
    assert test_image.filename == "test-image-001"


@pytest.mark.asyncio
async def test_create_custom_data(test_user, db_session):
    """Example: Creating custom test data within a test."""
    from app.models import Images

    # Create a custom image for this specific test
    custom_image = Images(
        filename="custom-test-image",
        ext="png",
        original_filename="custom.png",
        md5_hash="abc123def456",
        filesize=50000,
        width=800,
        height=600,
        user_id=test_user.user_id,
        status=1,
    )
    db_session.add(custom_image)
    await db_session.commit()
    await db_session.refresh(custom_image)

    # Test the custom image
    assert custom_image.image_id is not None
    assert custom_image.filename == "custom-test-image"


@pytest.mark.asyncio
async def test_isolation_between_tests(db_session):
    """
    Example: Each test starts with a blank database.

    Even though previous tests created users and images,
    this test starts fresh (tables are truncated between tests).
    """
    from sqlalchemy import select

    from app.models.user import Users

    # Query for users - should find none (except the base test users from db_session)
    result = await db_session.execute(select(Users))
    users = result.scalars().all()

    # Only the 3 base test users from db_session fixture should exist
    # (user_id 1, 2, 3 - created for FK constraints)
    assert len(users) == 3


@pytest.mark.asyncio
async def test_using_sample_data_dict(sample_image_data):
    """
    Example: Using sample data dictionaries for API payloads.

    This is useful for testing API endpoints without database setup.
    """
    # sample_image_data is just a dictionary, not a DB object
    assert sample_image_data["filename"] == "test-image-001"
    assert sample_image_data["ext"] == "jpg"

    # You could use this in an API request:
    # response = await client.post("/api/v1/images", json=sample_image_data)
