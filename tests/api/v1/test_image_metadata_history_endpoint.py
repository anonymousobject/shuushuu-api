"""Tests for GET /api/v1/images/{image_id}/metadata-history."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.image import Images
from app.models.image_metadata_history import ImageMetadataHistory
from app.models.user import Users


async def _make_user(db_session: AsyncSession, username: str) -> Users:
    user = Users(
        username=username,
        password="hashed",
        password_type="bcrypt",
        salt="",
        email=f"{username}@example.com",
        active=1,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _make_image(db_session: AsyncSession, owner: Users, filename: str) -> Images:
    image = Images(
        filename=filename,
        ext="jpg",
        md5_hash=hashlib.md5(filename.encode()).hexdigest(),
        user_id=owner.user_id,
        width=100,
        height=100,
        filesize=1000,
    )
    db_session.add(image)
    await db_session.commit()
    await db_session.refresh(image)
    return image


@pytest.mark.api
class TestImageMetadataHistoryEndpoint:
    async def test_returns_entries_newest_first_with_editor(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await _make_user(db_session, "mdhistuser")
        image = await _make_image(db_session, user, "mdhist1")
        base = datetime(2026, 1, 1, tzinfo=UTC)
        db_session.add_all(
            [
                ImageMetadataHistory(
                    image_id=image.image_id,
                    user_id=user.user_id,
                    field="source_url",
                    old_value=None,
                    new_value="https://example.com/a",
                    created_at=base,
                ),
                ImageMetadataHistory(
                    image_id=image.image_id,
                    user_id=user.user_id,
                    field="miscmeta",
                    old_value="old",
                    new_value=None,
                    created_at=base + timedelta(days=1),
                ),
            ]
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/images/{image.image_id}/metadata-history")

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["total"] == 2
        assert [(i["field"], i["old_value"], i["new_value"]) for i in data["items"]] == [
            ("miscmeta", "old", None),
            ("source_url", None, "https://example.com/a"),
        ]
        assert data["items"][0]["user"]["username"] == "mdhistuser"
        assert data["items"][0]["image_id"] == image.image_id
        assert data["items"][0]["created_at"] is not None
        assert "avatar_url" in data["items"][0]["user"]

    async def test_same_timestamp_orders_by_id_desc(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await _make_user(db_session, "mdhisttie")
        image = await _make_image(db_session, user, "mdhisttie")
        same = datetime(2026, 1, 1, tzinfo=UTC)
        first = ImageMetadataHistory(
            image_id=image.image_id,
            user_id=user.user_id,
            field="miscmeta",
            old_value=None,
            new_value="one",
            created_at=same,
        )
        db_session.add(first)
        await db_session.commit()
        second = ImageMetadataHistory(
            image_id=image.image_id,
            user_id=user.user_id,
            field="miscmeta",
            old_value="one",
            new_value="two",
            created_at=same,
        )
        db_session.add(second)
        await db_session.commit()

        response = await client.get(f"/api/v1/images/{image.image_id}/metadata-history")

        assert [i["id"] for i in response.json()["items"]] == [second.id, first.id]

    async def test_paginates(self, client: AsyncClient, db_session: AsyncSession) -> None:
        user = await _make_user(db_session, "mdhistpage")
        image = await _make_image(db_session, user, "mdhistpage")
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for day in range(3):
            db_session.add(
                ImageMetadataHistory(
                    image_id=image.image_id,
                    user_id=user.user_id,
                    field="miscmeta",
                    old_value=None,
                    new_value=f"v{day}",
                    created_at=base + timedelta(days=day),
                )
            )
        await db_session.commit()

        response = await client.get(
            f"/api/v1/images/{image.image_id}/metadata-history?page=2&per_page=2"
        )

        data = response.json()
        assert data["total"] == 3
        assert data["page"] == 2
        assert data["per_page"] == 2
        assert [i["new_value"] for i in data["items"]] == ["v0"]

    async def test_only_this_images_entries(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await _make_user(db_session, "mdhistscope")
        image = await _make_image(db_session, user, "mdhistscope1")
        other = await _make_image(db_session, user, "mdhistscope2")
        db_session.add(
            ImageMetadataHistory(
                image_id=other.image_id,
                user_id=user.user_id,
                field="miscmeta",
                old_value=None,
                new_value="elsewhere",
            )
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/images/{image.image_id}/metadata-history")

        assert response.json()["total"] == 0
        assert response.json()["items"] == []

    async def test_deleted_editor_shows_null_user(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        owner = await _make_user(db_session, "mdhistnull")
        image = await _make_image(db_session, owner, "mdhistnull")
        db_session.add(
            ImageMetadataHistory(
                image_id=image.image_id,
                user_id=None,
                field="miscmeta",
                old_value=None,
                new_value="orphaned",
            )
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/images/{image.image_id}/metadata-history")

        assert response.json()["items"][0]["user"] is None

    async def test_unknown_image_returns_404(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/images/999999999/metadata-history")
        assert response.status_code == 404

    async def test_patch_then_read_back(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """End to end: an owner's PATCH shows up here, anonymously readable."""
        owner = await _make_user(db_session, "mdhistpatch")
        image = await _make_image(db_session, owner, "mdhistpatch")

        patch = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"source_url": "https://example.com/src"},
            headers={"Authorization": f"Bearer {create_access_token(owner.user_id)}"},
        )
        assert patch.status_code == 200, patch.text

        response = await client.get(f"/api/v1/images/{image.image_id}/metadata-history")

        items = response.json()["items"]
        assert [(i["field"], i["new_value"], i["user"]["user_id"]) for i in items] == [
            ("source_url", "https://example.com/src", owner.user_id)
        ]
