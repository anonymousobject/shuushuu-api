"""Tests for GET /api/v1/users/{user_id}/ratings.

No `pytest.mark.anyio` here: the suite runs on pytest-asyncio
(`asyncio_mode = "auto"`), and the anyio marker makes both plugins claim
the same test. anyio then runs the test body in a runner of its own while
pytest-asyncio sets up `engine`/`db_session` in the per-test asyncio loop --
or the reverse, depending on which plugin registered first, which is not
stable across environments. The loser's connection belongs to a dead loop
and every test in the file dies on "got Future attached to a different
loop" (see the `engine` fixture docstring in tests/conftest.py).
"""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Favorites, ImageRatings, Images, Users


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

    async def test_is_favorited_reflects_the_viewers_own_favorites(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        rater = await _make_user(db_session, "rater_favs")
        loved = await _make_image(db_session, rater, "fv0")
        plain = await _make_image(db_session, rater, "fv1")
        await _rate(db_session, rater, loved, 6)
        await _rate(db_session, rater, plain, 6)
        db_session.add(Favorites(user_id=rater.user_id, image_id=loved.image_id))
        await db_session.commit()

        response = await _authenticate(client, rater).get(
            f"/api/v1/users/{rater.user_id}/ratings"
        )

        assert response.status_code == 200
        by_id = {img["image_id"]: img["is_favorited"] for img in response.json()["images"]}
        assert by_id[loved.image_id] is True
        assert by_id[plain.image_id] is False


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


from app.config import ImageStatus


class TestUserRatingsVisibility:
    async def test_subject_does_not_see_rating_on_deactivated_image(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        rater = await _make_user(db_session, "rater_vis_self")
        uploader = await _make_user(db_session, "rater_vis_owner")
        visible = await _make_image(db_session, uploader, "ggg", status=ImageStatus.ACTIVE)
        hidden = await _make_image(db_session, uploader, "hhh", status=ImageStatus.DEACTIVATED)
        await _rate(db_session, rater, visible, 3)
        await _rate(db_session, rater, hidden, 8)

        response = await _authenticate(client, rater).get(
            f"/api/v1/users/{rater.user_id}/ratings"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert [img["image_id"] for img in body["images"]] == [visible.image_id]

    async def test_moderator_sees_rating_on_deactivated_image(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        rater = await _make_user(db_session, "rater_vis_subject")
        uploader = await _make_user(db_session, "rater_vis_up2")
        mod = await _make_user(db_session, "rater_vis_mod")
        await grant_user_permission(db_session, mod.user_id, "user_edit_profile")
        visible = await _make_image(db_session, uploader, "iii", status=ImageStatus.ACTIVE)
        hidden = await _make_image(db_session, uploader, "jjj", status=ImageStatus.DEACTIVATED)
        await _rate(db_session, rater, visible, 3)
        await _rate(db_session, rater, hidden, 8)

        response = await _authenticate(client, mod).get(
            f"/api/v1/users/{rater.user_id}/ratings"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert {img["image_id"] for img in body["images"]} == {visible.image_id, hidden.image_id}

    async def test_hide_reposts_applies_to_self_and_is_ignored_for_moderators(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        uploader = await _make_user(db_session, "rater_rp_owner")
        repost = await _make_image(db_session, uploader, "kkk", status=ImageStatus.REPOST)

        rater = await _make_user(db_session, "rater_rp_self")
        rater.hide_reposts = 1
        mod = await _make_user(db_session, "rater_rp_mod")
        mod.hide_reposts = 1
        db_session.add_all([rater, mod])
        await db_session.commit()
        await grant_user_permission(db_session, mod.user_id, "user_edit_profile")
        await _rate(db_session, rater, repost, 2)

        self_response = await _authenticate(client, rater).get(
            f"/api/v1/users/{rater.user_id}/ratings"
        )
        assert self_response.status_code == 200
        assert self_response.json()["total"] == 0

        client.headers.pop("Authorization", None)
        mod_response = await _authenticate(client, mod).get(
            f"/api/v1/users/{rater.user_id}/ratings"
        )
        assert mod_response.status_code == 200
        assert mod_response.json()["total"] == 1


class TestUserRatingsFiltersAndSorting:
    async def test_min_and_max_rating_bound_the_results(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        rater = await _make_user(db_session, "rater_filter")
        for index, score in enumerate([2, 5, 9]):
            image = await _make_image(db_session, rater, f"flt{index}")
            await _rate(db_session, rater, image, score)

        authed = _authenticate(client, rater)
        base = f"/api/v1/users/{rater.user_id}/ratings"

        high = await authed.get(f"{base}?min_rating=5")
        assert high.status_code == 200
        assert sorted(img["subject_rating"] for img in high.json()["images"]) == [5, 9]
        assert high.json()["total"] == 2

        mid = await authed.get(f"{base}?min_rating=5&max_rating=5")
        assert mid.status_code == 200
        assert [img["subject_rating"] for img in mid.json()["images"]] == [5]
        assert mid.json()["total"] == 1

    async def test_out_of_range_rating_filter_is_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        rater = await _make_user(db_session, "rater_range")

        response = await _authenticate(client, rater).get(
            f"/api/v1/users/{rater.user_id}/ratings?min_rating=11"
        )

        assert response.status_code == 422

    async def test_default_sort_is_image_id_descending(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        rater = await _make_user(db_session, "rater_sort")
        images = []
        # Ratings must be distinct AND assigned out of image_id creation order.
        # If they were all equal (or monotonic with creation order), a
        # regression of the default `sort_by` to "rating" would tie (or
        # coincide) with `image_id` DESC and this test would pass either way.
        for index, score in enumerate([7, 3, 9]):
            image = await _make_image(db_session, rater, f"srt{index}")
            await _rate(db_session, rater, image, score)
            images.append(image)

        response = await _authenticate(client, rater).get(
            f"/api/v1/users/{rater.user_id}/ratings"
        )

        assert response.status_code == 200
        returned = [img["image_id"] for img in response.json()["images"]]
        assert returned == sorted([img.image_id for img in images], reverse=True)

    async def test_pagination_returns_disjoint_complete_pages(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """All three rows share one rating value, so this exercises `sort_by=rating`
        paging under a tie on the primary sort key.

        This proves pagination doesn't repeat or drop rows across pages — it
        does NOT prove the `image_id` tiebreaker is doing anything: verified
        empirically (see task-4-report.md) that this test still passes with
        the tiebreaker removed, because `image_ratings`' primary key is
        `(user_id, image_id)`, so InnoDB's clustered-index scan for
        `WHERE user_id = X` already returns rows in `image_id` order. The
        tiebreaker is belt-and-braces.
        """
        rater = await _make_user(db_session, "rater_stable")
        for index in range(3):
            image = await _make_image(db_session, rater, f"stb{index}")
            await _rate(db_session, rater, image, 6)

        authed = _authenticate(client, rater)
        base = f"/api/v1/users/{rater.user_id}/ratings?sort_by=rating&per_page=2"

        first = await authed.get(f"{base}&page=1")
        second = await authed.get(f"{base}&page=2")

        assert first.status_code == 200
        assert second.status_code == 200
        first_ids = [img["image_id"] for img in first.json()["images"]]
        second_ids = [img["image_id"] for img in second.json()["images"]]
        assert len(first_ids) == 2
        assert len(second_ids) == 1
        assert set(first_ids).isdisjoint(second_ids)

    async def test_sort_by_rated_at_is_accepted_despite_null_dates(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        rater = await _make_user(db_session, "rater_dates")
        image = await _make_image(db_session, rater, "dts")
        await _rate(db_session, rater, image, 1)

        response = await _authenticate(client, rater).get(
            f"/api/v1/users/{rater.user_id}/ratings?sort_by=rated_at"
        )

        assert response.status_code == 200
        assert response.json()["total"] == 1
