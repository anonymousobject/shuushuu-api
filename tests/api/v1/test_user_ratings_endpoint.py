"""Tests for GET /api/v1/users/{user_id}/ratings."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ImageRatings, Images, Users

pytestmark = pytest.mark.anyio


async def _make_user(db_session: AsyncSession, username: str) -> Users:
    user = Users(
        username=username,
        password="hashed_password_here",
        password_type="bcrypt",
        salt="saltsalt12345678",
        email=f"{username}@example.com",
        active=1,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _make_image(db_session: AsyncSession, owner: Users, suffix: str, status: int = 1) -> Images:
    # Field set mirrors the `test_image` fixture in tests/conftest.py — `rating`
    # and `locked` are not nullable and have no server default.
    image = Images(
        filename=f"ratings-test-{suffix}",
        ext="jpg",
        original_filename=f"{suffix}.jpg",
        md5_hash=(suffix * 32)[:32],
        filesize=1234,
        width=800,
        height=600,
        caption=f"Ratings test image {suffix}",
        rating=0.0,
        user_id=owner.user_id,
        status=status,
        locked=False,
    )
    db_session.add(image)
    await db_session.commit()
    await db_session.refresh(image)
    return image


async def _rate(db_session: AsyncSession, user: Users, image: Images, rating: int) -> None:
    db_session.add(ImageRatings(user_id=user.user_id, image_id=image.image_id, rating=rating))
    await db_session.commit()


def _authenticate(client: AsyncClient, user: Users) -> AsyncClient:
    from app.core.security import create_access_token

    client.headers.update({"Authorization": f"Bearer {create_access_token(user.id)}"})
    return client


class TestUserRatingsHappyPath:
    async def test_self_sees_own_ratings_with_values(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        rater = await _make_user(db_session, "rater_self")
        image = await _make_image(db_session, rater, "aaa")
        await _rate(db_session, rater, image, 7)

        response = await _authenticate(client, rater).get(
            f"/api/v1/users/{rater.user_id}/ratings"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["page"] == 1
        assert len(body["images"]) == 1
        assert body["images"][0]["image_id"] == image.image_id
        # The inherited `rating` (the image's own average) must still be present
        # and distinct from the subject's score — this is the field the old name
        # was silently shadowing.
        assert "rating" in body["images"][0]
        assert body["images"][0]["subject_rating"] == 7

    async def test_other_users_ratings_are_excluded(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        rater = await _make_user(db_session, "rater_mine")
        stranger = await _make_user(db_session, "rater_theirs")
        mine = await _make_image(db_session, rater, "bbb")
        theirs = await _make_image(db_session, stranger, "ccc")
        await _rate(db_session, rater, mine, 5)
        await _rate(db_session, stranger, theirs, 9)

        response = await _authenticate(client, rater).get(
            f"/api/v1/users/{rater.user_id}/ratings"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert [img["image_id"] for img in body["images"]] == [mine.image_id]


from tests.api.v1.test_users import grant_user_permission


class TestUserRatingsAuthorization:
    async def test_anonymous_is_rejected(self, client: AsyncClient, db_session: AsyncSession):
        rater = await _make_user(db_session, "rater_anon")
        image = await _make_image(db_session, rater, "ddd")
        await _rate(db_session, rater, image, 4)

        response = await client.get(f"/api/v1/users/{rater.user_id}/ratings")

        assert response.status_code == 401

    async def test_other_user_is_forbidden(self, client: AsyncClient, db_session: AsyncSession):
        rater = await _make_user(db_session, "rater_subject")
        nosy = await _make_user(db_session, "rater_nosy")
        image = await _make_image(db_session, rater, "eee")
        await _rate(db_session, rater, image, 6)

        response = await _authenticate(client, nosy).get(
            f"/api/v1/users/{rater.user_id}/ratings"
        )

        assert response.status_code == 403

    async def test_moderator_can_view_another_users_ratings(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        rater = await _make_user(db_session, "rater_audited")
        mod = await _make_user(db_session, "rater_mod")
        await grant_user_permission(db_session, mod.user_id, "user_edit_profile")
        image = await _make_image(db_session, rater, "fff")
        await _rate(db_session, rater, image, 10)

        response = await _authenticate(client, mod).get(
            f"/api/v1/users/{rater.user_id}/ratings"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["images"][0]["subject_rating"] == 10

    async def test_unknown_user_is_404(self, client: AsyncClient, db_session: AsyncSession):
        viewer = await _make_user(db_session, "rater_viewer")

        response = await _authenticate(client, viewer).get("/api/v1/users/99999999/ratings")

        assert response.status_code == 404
