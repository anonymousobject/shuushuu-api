"""
Tests for GET /images/{image_id}/reposts endpoint.

Returns the images whose live `replacement_id` points at this image and whose
status is still REPOST. Derived from the images row, not image_status_history —
the history table never recorded replacement_id and has no rows for the legacy
reposts.
"""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus
from app.models.image import Images
from app.models.user import Users
from app.services.image_status import change_image_status


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


async def _make_image(
    db_session: AsyncSession,
    owner: Users,
    slug: str,
    *,
    status: int = ImageStatus.ACTIVE,
    replacement_id: int | None = None,
    status_user_id: int | None = None,
    status_updated: datetime | None = None,
) -> Images:
    image = Images(
        filename=slug,
        ext="jpg",
        md5_hash=f"{slug}{'0' * 32}"[:32],
        user_id=owner.user_id,
        width=100,
        height=100,
        filesize=1000,
        status=status,
        replacement_id=replacement_id,
        status_user_id=status_user_id,
        status_updated=status_updated,
    )
    db_session.add(image)
    await db_session.commit()
    await db_session.refresh(image)
    return image


@pytest.mark.api
class TestGetImageReposts:
    """Tests for GET /images/{image_id}/reposts endpoint."""

    async def test_returns_404_for_nonexistent_image(self, client: AsyncClient) -> None:
        """Should return 404 for a nonexistent image."""
        response = await client.get("/api/v1/images/99999999/reposts")
        assert response.status_code == 404
        assert response.json()["detail"] == "Image not found"

    async def test_returns_empty_list_when_no_reposts(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """An image nothing points at should return an empty list."""
        owner = await _make_user(db_session, "repostsempty")
        original = await _make_image(db_session, owner, "repostsempty")

        response = await client.get(f"/api/v1/images/{original.image_id}/reposts")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []

    async def test_returns_repost_with_user_and_marked_at(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A repost pointing at the original is listed with its marking user and time."""
        owner = await _make_user(db_session, "repostsowner")
        marker = await _make_user(db_session, "repostsmarker")
        original = await _make_image(db_session, owner, "repostsorig")
        repost = await _make_image(
            db_session,
            owner,
            "repostsdupe",
            status=ImageStatus.REPOST,
            replacement_id=original.image_id,
            status_user_id=marker.user_id,
            status_updated=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
        )

        response = await client.get(f"/api/v1/images/{original.image_id}/reposts")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1

        item = data["items"][0]
        assert item["image_id"] == repost.image_id
        assert item["user"] is not None
        assert item["user"]["user_id"] == marker.user_id
        assert item["user"]["username"] == "repostsmarker"
        assert item["marked_at"] is not None

        # The repost itself has nothing pointing at it.
        response = await client.get(f"/api/v1/images/{repost.image_id}/reposts")
        assert response.status_code == 200
        assert response.json()["total"] == 0

    async def test_excludes_stale_replacement_pointer(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """An image with a leftover replacement_id but a non-REPOST status is excluded.

        Prod has hundreds of these: restoring a repost to ACTIVE historically left
        replacement_id set. They are not reposts and must not be listed.
        """
        owner = await _make_user(db_session, "repostsstale")
        original = await _make_image(db_session, owner, "repostsstaleorig")
        await _make_image(
            db_session,
            owner,
            "repostsstalerestored",
            status=ImageStatus.ACTIVE,
            replacement_id=original.image_id,
            status_user_id=owner.user_id,
            status_updated=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
        )

        response = await client.get(f"/api/v1/images/{original.image_id}/reposts")
        assert response.status_code == 200
        assert response.json()["total"] == 0

    async def test_excludes_repost_of_a_different_original(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A repost pointing at another image is not listed here."""
        owner = await _make_user(db_session, "repostsother")
        original = await _make_image(db_session, owner, "repostsotherA")
        elsewhere = await _make_image(db_session, owner, "repostsotherB")
        await _make_image(
            db_session,
            owner,
            "repostsotherC",
            status=ImageStatus.REPOST,
            replacement_id=elsewhere.image_id,
            status_user_id=owner.user_id,
            status_updated=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
        )

        response = await client.get(f"/api/v1/images/{original.image_id}/reposts")
        assert response.status_code == 200
        assert response.json()["total"] == 0

        response = await client.get(f"/api/v1/images/{elsewhere.image_id}/reposts")
        assert response.status_code == 200
        assert response.json()["total"] == 1

    async def test_ordered_newest_marked_first(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Reposts are ordered by marking time, most recent first."""
        owner = await _make_user(db_session, "repostsorder")
        original = await _make_image(db_session, owner, "repostsorderorig")
        older = await _make_image(
            db_session,
            owner,
            "repostsorderold",
            status=ImageStatus.REPOST,
            replacement_id=original.image_id,
            status_user_id=owner.user_id,
            status_updated=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        )
        newer = await _make_image(
            db_session,
            owner,
            "repostsordernew",
            status=ImageStatus.REPOST,
            replacement_id=original.image_id,
            status_user_id=owner.user_id,
            status_updated=datetime(2026, 6, 7, 8, 9, 10, tzinfo=UTC),
        )

        response = await client.get(f"/api/v1/images/{original.image_id}/reposts")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 2
        assert [item["image_id"] for item in data["items"]] == [newer.image_id, older.image_id]

    async def test_handles_null_status_user(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A repost with no recorded marking user returns a null user."""
        owner = await _make_user(db_session, "repostsnulluser")
        original = await _make_image(db_session, owner, "repostsnullorig")
        repost = await _make_image(
            db_session,
            owner,
            "repostsnulldupe",
            status=ImageStatus.REPOST,
            replacement_id=original.image_id,
            status_user_id=None,
            status_updated=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
        )

        response = await client.get(f"/api/v1/images/{original.image_id}/reposts")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["image_id"] == repost.image_id
        assert data["items"][0]["user"] is None

    async def test_reflects_real_marking_and_restore_path(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Marking and restoring through change_image_status adds then removes the row."""
        owner = await _make_user(db_session, "repostsservice")
        actor = await _make_user(db_session, "repostsserviceactor")
        original = await _make_image(db_session, owner, "repostssvcorig")
        repost = await _make_image(db_session, owner, "repostssvcdupe")

        await change_image_status(
            db_session,
            repost,
            actor,
            new_status=ImageStatus.REPOST,
            replacement_id=original.image_id,
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/images/{original.image_id}/reposts")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["image_id"] == repost.image_id
        assert data["items"][0]["user"]["username"] == "repostsserviceactor"
        assert data["items"][0]["marked_at"] is not None

        await change_image_status(
            db_session,
            repost,
            actor,
            new_status=ImageStatus.ACTIVE,
            reason="not actually a repost",
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/images/{original.image_id}/reposts")
        assert response.status_code == 200
        assert response.json()["total"] == 0
