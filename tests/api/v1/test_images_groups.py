"""Tests for groups in image API responses."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.image import Images
from app.models.permissions import Groups, UserGroups


@pytest.mark.asyncio
async def test_list_images_includes_empty_groups(
    client: AsyncClient, db_session: AsyncSession
):
    """Images endpoint returns empty groups array for users without groups."""
    # Create an image (user 1 exists from fixture, has no groups)
    image = Images(
        filename="test-groups-001",
        ext="jpg",
        original_filename="test.jpg",
        md5_hash="groups001hash",
        filesize=1000,
        width=100,
        height=100,
        user_id=1,
        status=1,
        locked=0,
    )
    db_session.add(image)
    await db_session.commit()

    response = await client.get("/api/v1/images")
    assert response.status_code == 200

    data = response.json()
    assert len(data["images"]) >= 1

    # Find our image
    test_image = next(
        (img for img in data["images"] if img["filename"] == "test-groups-001"), None
    )
    assert test_image is not None
    assert test_image["user"]["groups"] == []


@pytest.mark.asyncio
async def test_list_images_includes_user_groups(
    client: AsyncClient, db_session: AsyncSession
):
    """Images endpoint returns user's groups in response."""
    # Create a group and add user 1 to it
    group = Groups(title="mods", desc="Moderators")
    db_session.add(group)
    await db_session.flush()

    user_group = UserGroups(user_id=1, group_id=group.group_id)
    db_session.add(user_group)

    # Create an image
    image = Images(
        filename="test-groups-002",
        ext="jpg",
        original_filename="test.jpg",
        md5_hash="groups002hash",
        filesize=1000,
        width=100,
        height=100,
        user_id=1,
        status=1,
        locked=0,
    )
    db_session.add(image)
    await db_session.commit()

    response = await client.get("/api/v1/images")
    assert response.status_code == 200

    data = response.json()
    test_image = next(
        (img for img in data["images"] if img["filename"] == "test-groups-002"), None
    )
    assert test_image is not None
    assert test_image["user"]["groups"] == ["mods"]
