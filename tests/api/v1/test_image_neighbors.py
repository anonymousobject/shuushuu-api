"""Tests for GET /images/{image_id}/neighbors.

The endpoint answers "which results sit either side of this image in a search?"
using the listing's own query parameters, so most tests compare it against
GET /images (the source of truth for order and visibility).
"""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, TagType, settings
from app.core.security import create_access_token, get_password_hash
from app.models import Images, TagLinks, Tags, Users

NEIGHBORS = "/api/v1/images/{image_id}/neighbors"
TIMEOUT_DETAIL = "Search navigation timed out"


async def _user(db: AsyncSession, name: str, show_all: int = 0, hide_reposts: int = 0) -> Users:
    user = Users(
        username=name,
        password=get_password_hash("TestPassword123!"),
        password_type="bcrypt",
        salt="",
        email=f"{name}@test.com",
        active=1,
        show_all_images=show_all,
        hide_reposts=hide_reposts,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _hdr(user: Users) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.user_id)}"}


async def _tag(db: AsyncSession, title: str = "NeighborTag") -> Tags:
    tag = Tags(title=title, desc=title, type=TagType.THEME)
    db.add(tag)
    await db.commit()
    await db.refresh(tag)
    return tag


async def _images(
    db: AsyncSession,
    owner: Users,
    statuses: list[ImageStatus],
    tag: Tags | None = None,
    **per_image: list,
) -> list[Images]:
    """Create one image per status, in ascending image_id order.

    `per_image` maps a column name to one value per image (e.g. favorites=[...]).
    """
    images = []
    for index, status in enumerate(statuses):
        image = Images(
            filename=f"nb-{owner.username}-{index}",
            ext="jpg",
            original_filename=f"nb{index}.jpg",
            md5_hash=f"{owner.user_id:08x}{index:024x}",
            filesize=1000,
            width=100,
            height=100,
            user_id=owner.user_id,
            status=status,
            locked=False,
        )
        for column, values in per_image.items():
            setattr(image, column, values[index])
        images.append(image)
    db.add_all(images)
    await db.commit()
    for image in images:
        await db.refresh(image)
        if tag is not None:
            db.add(TagLinks(image_id=image.image_id, tag_id=tag.tag_id, user_id=owner.user_id))
    await db.commit()
    return images


async def _neighbors(
    client: AsyncClient, image: Images, query: str = "", headers: dict | None = None
) -> dict:
    response = await client.get(
        NEIGHBORS.format(image_id=image.image_id) + (f"?{query}" if query else ""),
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestImageNeighbors:
    @pytest.mark.parametrize("sort_order", ["ASC", "DESC"])
    @pytest.mark.parametrize("sort_by", ["image_id", "favorites", "last_post"])
    async def test_walk_matches_listing_endpoint(
        self, client: AsyncClient, db_session: AsyncSession, sort_by: str, sort_order: str
    ):
        owner = await _user(db_session, "walk_owner")
        tag = await _tag(db_session)
        base = datetime(2024, 1, 1, tzinfo=UTC)
        # Ties and NULLs on the sort columns, so the tie-break and NULL placement are exercised.
        favorites = [5, 3, 5, 1, 3, 9, 7, 2]
        last_posts = [base + timedelta(days=d) if d else None for d in (5, 0, 3, 1, 0, 3, 9, 5)]
        await _images(
            db_session,
            owner,
            [ImageStatus.ACTIVE] * 8,
            tag=tag,
            favorites=favorites,
            last_post=last_posts,
        )
        # An untagged image, which the tag filter must keep out of the walk.
        await _images(
            db_session,
            await _user(db_session, "walk_other"),
            [ImageStatus.ACTIVE],
            favorites=[5],
        )

        params = f"tags={tag.tag_id}&sort_by={sort_by}&sort_order={sort_order}"
        listing = await client.get(f"/api/v1/images?{params}&per_page=100")
        assert listing.status_code == 200
        ids = [image["image_id"] for image in listing.json()["images"]]
        assert len(ids) == 8

        for position, image_id in enumerate(ids):
            response = await client.get(NEIGHBORS.format(image_id=image_id) + f"?{params}")
            assert response.status_code == 200, response.text
            assert response.json() == {
                "prev_image_id": ids[position - 1] if position > 0 else None,
                "next_image_id": ids[position + 1] if position < len(ids) - 1 else None,
            }

    async def test_anonymous_never_gets_hidden_neighbours(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await _user(db_session, "anon_owner")
        low, hidden, high = await _images(
            db_session,
            owner,
            [ImageStatus.ACTIVE, ImageStatus.REVIEW, ImageStatus.ACTIVE],
        )
        # Listing order is image_id DESC: high, hidden, low.
        assert await _neighbors(client, high) == {
            "prev_image_id": None,
            "next_image_id": low.image_id,
        }
        assert await _neighbors(client, low) == {
            "prev_image_id": high.image_id,
            "next_image_id": None,
        }

    async def test_show_all_images_user_gets_hidden_neighbours(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await _user(db_session, "showall_owner")
        viewer = await _user(db_session, "showall_viewer", show_all=1)
        low, hidden, high = await _images(
            db_session,
            owner,
            [ImageStatus.ACTIVE, ImageStatus.REVIEW, ImageStatus.ACTIVE],
        )
        assert await _neighbors(client, high, headers=_hdr(viewer)) == {
            "prev_image_id": None,
            "next_image_id": hidden.image_id,
        }
        # The same image for an anonymous caller skips the hidden one.
        assert (await _neighbors(client, high))["next_image_id"] == low.image_id

    async def test_hide_reposts_skips_reposts(self, client: AsyncClient, db_session: AsyncSession):
        owner = await _user(db_session, "repost_owner")
        hider = await _user(db_session, "repost_hider", hide_reposts=1)
        keeper = await _user(db_session, "repost_keeper", hide_reposts=0)
        low, repost, high = await _images(
            db_session,
            owner,
            [ImageStatus.ACTIVE, ImageStatus.REPOST, ImageStatus.ACTIVE],
        )
        assert (await _neighbors(client, high, headers=_hdr(hider)))["next_image_id"] == (
            low.image_id
        )
        assert (await _neighbors(client, high, headers=_hdr(keeper)))["next_image_id"] == (
            repost.image_id
        )

    async def test_explicit_status_overrides_default_visibility(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await _user(db_session, "status_owner")
        hider = await _user(db_session, "status_hider", hide_reposts=1)
        oldest, active, newest = await _images(
            db_session,
            owner,
            [ImageStatus.REPOST, ImageStatus.ACTIVE, ImageStatus.REPOST],
        )
        headers = _hdr(hider)
        # Default visibility drops reposts for this viewer...
        assert (await _neighbors(client, newest, headers=headers))["next_image_id"] == (
            active.image_id
        )
        # ...but an explicit status=-1 asks for them, and excludes the active image.
        explicit = await _neighbors(client, newest, f"status={ImageStatus.REPOST}", headers=headers)
        assert explicit == {"prev_image_id": None, "next_image_id": oldest.image_id}

    async def test_image_outside_filter_gets_nearest_results(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await _user(db_session, "outside_owner")
        tag = await _tag(db_session)
        (low,) = await _images(db_session, owner, [ImageStatus.ACTIVE], tag=tag)
        (outsider,) = await _images(
            db_session, await _user(db_session, "outside_other"), [ImageStatus.ACTIVE]
        )
        (high,) = await _images(
            db_session, await _user(db_session, "outside_third"), [ImageStatus.ACTIVE], tag=tag
        )
        assert low.image_id < outsider.image_id < high.image_id

        assert await _neighbors(client, outsider, f"tags={tag.tag_id}") == {
            "prev_image_id": high.image_id,
            "next_image_id": low.image_id,
        }

    async def test_single_result_has_no_neighbours(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await _user(db_session, "single_owner")
        tag = await _tag(db_session)
        (only,) = await _images(db_session, owner, [ImageStatus.ACTIVE], tag=tag)
        assert await _neighbors(client, only, f"tags={tag.tag_id}") == {
            "prev_image_id": None,
            "next_image_id": None,
        }

    async def test_unknown_image_is_404(self, client: AsyncClient):
        response = await client.get(NEIGHBORS.format(image_id=999999999))
        assert response.status_code == 404
        assert response.json()["detail"] == "Image not found"

    async def test_invalid_sort_is_422(self, client: AsyncClient, db_session: AsyncSession):
        (image,) = await _images(
            db_session, await _user(db_session, "sort_owner"), [ImageStatus.ACTIVE]
        )
        response = await client.get(
            NEIGHBORS.format(image_id=image.image_id) + "?sort_by=not_a_column"
        )
        assert response.status_code == 422

    async def test_too_many_tags_is_400(self, client: AsyncClient, db_session: AsyncSession):
        (image,) = await _images(
            db_session, await _user(db_session, "tags_owner"), [ImageStatus.ACTIVE]
        )
        too_many = ",".join(str(i) for i in range(1, settings.MAX_SEARCH_TAGS + 2))
        response = await client.get(NEIGHBORS.format(image_id=image.image_id) + f"?tags={too_many}")
        assert response.status_code == 400
        assert str(settings.MAX_SEARCH_TAGS) in response.json()["detail"]

    async def test_timeout_is_504(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ):
        owner = await _user(db_session, "timeout_owner")
        low, middle, high = await _images(db_session, owner, [ImageStatus.ACTIVE] * 3)
        # Enough comments on the neighbouring images that a comment search for a
        # word none of them contain cannot finish inside the 1ms limit below.
        # The posts counter triggers would rewrite each image's row once per
        # comment, which is what makes a bulk insert crawl; they are not under test.
        await db_session.execute(text("ALTER TABLE posts DISABLE TRIGGER USER"))
        await db_session.execute(
            text(
                "INSERT INTO posts (image_id, user_id, post_text, deleted, update_count, ip) "
                "SELECT i, :user_id, 'lorem ipsum ' || g, false, 0, '127.0.0.1' "
                "FROM unnest(CAST(:image_ids AS integer[])) AS i, "
                "generate_series(1, 100000) AS g"
            ),
            {"user_id": owner.user_id, "image_ids": [low.image_id, high.image_id]},
        )
        await db_session.execute(text("ALTER TABLE posts ENABLE TRIGGER USER"))
        await db_session.commit()
        monkeypatch.setattr(settings, "IMAGE_NEIGHBORS_TIMEOUT_SECONDS", 0.001)

        with caplog.at_level(logging.INFO):
            response = await client.get(
                NEIGHBORS.format(image_id=middle.image_id) + "?commentsearch=zzzzqqqq"
            )

        assert response.status_code == 504
        assert response.json()["detail"] == TIMEOUT_DETAIL
        # The app's own records: the timeout warning, then the request logger's
        # error line for any 5xx. (The httpx client's INFO line is the test's own.)
        app_records = [r for r in caplog.records if r.name.startswith("app.")]
        assert [(r.name, r.levelno) for r in app_records] == [
            ("app.api.v1.images", logging.WARNING),
            ("app.main", logging.ERROR),
        ]
        assert "image_neighbors_timeout" in app_records[0].message
        assert "status_code" in app_records[1].message and "504" in app_records[1].message
