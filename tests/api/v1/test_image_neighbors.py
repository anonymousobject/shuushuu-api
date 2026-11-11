"""Tests for GET /images/{image_id}/neighbors.

The endpoint answers "which results sit either side of this image in a search?"
using the listing's own query parameters, so most tests compare it against
GET /images (the source of truth for order and visibility).
"""

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import event, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, ReportStatus, TagType, settings
from app.core.permissions import Permission
from app.core.security import create_access_token, get_password_hash
from app.models import Comments, Favorites, ImageReports, Images, TagLinks, Tags, Users
from app.services.image_filters import SEEK_UNBOUNDED_LOWER, SEEK_UNBOUNDED_UPPER
from tests.api.v1.conftest import grant_permission

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


async def _slow_comment_search(db: AsyncSession) -> Images:
    """The middle of three images, where `?commentsearch=zzzzqqqq` cannot finish in 1ms.

    Its neighbours carry enough comments that a search for a word none of them
    contain takes far longer than that, and far less than a second.
    """
    owner = await _user(db, "timeout_owner")
    low, middle, high = await _images(db, owner, [ImageStatus.ACTIVE] * 3)
    # The posts counter triggers would rewrite each image's row once per
    # comment, which is what makes a bulk insert crawl; they are not under test.
    await db.execute(text("ALTER TABLE posts DISABLE TRIGGER USER"))
    await db.execute(
        text(
            "INSERT INTO posts (image_id, user_id, post_text, deleted, update_count, ip) "
            "SELECT i, :user_id, 'lorem ipsum ' || g, false, 0, '127.0.0.1' "
            "FROM unnest(CAST(:image_ids AS integer[])) AS i, "
            "generate_series(1, 100000) AS g"
        ),
        {"user_id": owner.user_id, "image_ids": [low.image_id, high.image_id]},
    )
    await db.execute(text("ALTER TABLE posts ENABLE TRIGGER USER"))
    await db.commit()
    return middle


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
        active, repost, top = await _images(
            db_session,
            owner,
            [ImageStatus.ACTIVE, ImageStatus.REPOST, ImageStatus.ACTIVE],
        )
        headers = _hdr(hider)
        # Default visibility drops the repost between them for this viewer...
        assert (await _neighbors(client, top, headers=headers))["next_image_id"] == (
            active.image_id
        )
        # ...but an explicit status=-1 asks for reposts, and so excludes the active images.
        explicit = await _neighbors(client, top, f"status={ImageStatus.REPOST}", headers=headers)
        assert explicit == {"prev_image_id": None, "next_image_id": repost.image_id}

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

    async def test_comment_search_gets_the_listings_limit(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ):
        """GET /images bounds a comment search by COMMENT_SEARCH_TIMEOUT_SECONDS, so
        a search the listing can serve must not time out navigating at a tighter limit."""
        middle = await _slow_comment_search(db_session)
        monkeypatch.setattr(settings, "IMAGE_NEIGHBORS_TIMEOUT_SECONDS", 0.001)

        assert await _neighbors(client, middle, "commentsearch=zzzzqqqq") == {
            "prev_image_id": None,
            "next_image_id": None,
        }

    async def test_timeout_is_504(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ):
        middle = await _slow_comment_search(db_session)
        monkeypatch.setattr(settings, "IMAGE_NEIGHBORS_TIMEOUT_SECONDS", 0.001)
        monkeypatch.setattr("app.api.v1.images.COMMENT_SEARCH_TIMEOUT_SECONDS", 0.001)

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


# --- Seek bounds on the filter's link subqueries ------------------------------------
#
# The route builds the filter query seekable, so each neighbours statement repeats its
# own images.image_id bound on every link table's image_id (app/services/image_filters.py).
# The world below has twelve consecutive images. Every filter keeps the images at
# SEEK_LISTED and drops the rest, so each walked image has dropped images on both sides
# by id, and kept images sit at adjacent ids (1-2, 7-8): a link bound that is reversed
# or one id too tight changes some answer.
SEEK_LISTED = (1, 2, 4, 7, 8, 10)
# images.favorites by position: two tie groups, each mixing kept and dropped images.
SEEK_FAVORITES = [5, 5, 5, 5, 3, 3, 3, 3, 3, 3, 5, 5]
# images.last_post by position, as days after SEEK_BASE (None: NULL): NULLs and a tie
# group among both kept and dropped images, so a last_post walk runs every statement shape.
SEEK_LAST_POST_DAYS = [None, 2, 2, None, None, 1, 1, 2, None, 2, 1, None]
SEEK_BASE = datetime(2024, 1, 1, tzinfo=UTC)
SEEK_FILTERS = [
    "tags_any",
    "tags_all",
    "exclude_tags",
    "favorited_by_user_id",
    "exclude_favorited_by_user_id",
    "commenter",
    "commentsearch",
    "exclude_commenter",
    "reported",
]

# A comparison of some table's image_id with a bind parameter, as asyncpg receives it
# ("tag_links.image_id >= $4::INTEGER").
_ID_COMPARISON = re.compile(r"\b(\w+)\.image_id (<=|>=|<|>) \$(\d+)")


async def _seek_world(db: AsyncSession) -> tuple[list[Images], dict[str, str], dict[str, str]]:
    """Seed the world; return its images, each filter's query string, and mod headers."""
    owner = await _user(db, "seek_owner")
    fan, hater, commenter, critic = [
        await _user(db, f"seek_{name}") for name in ("fan", "hater", "commenter", "critic")
    ]
    mod = await _user(db, "seek_mod")
    mod_token = await grant_permission(db, mod.user_id, Permission.REPORT_VIEW)
    any_a, any_b, all_a, all_b, dropped_tag = [
        await _tag(db, f"Seek{name}") for name in ("AnyA", "AnyB", "AllA", "AllB", "Drop")
    ]
    images = await _images(db, owner, [ImageStatus.ACTIVE] * len(SEEK_FAVORITES))

    for position, image in enumerate(images):
        odd = position % 2 == 1
        if position in SEEK_LISTED:
            tag_ids = [(any_a if odd else any_b).tag_id, all_a.tag_id, all_b.tag_id]
            favoriter, author, comment = fan, commenter, "a kitten"
            report_status = ReportStatus.PENDING
        else:
            # Only one of the two tags_all tags, so each tag's subquery alone matches it.
            tag_ids = [dropped_tag.tag_id, (all_a if odd else all_b).tag_id]
            favoriter, author, comment = hater, critic, "a puppy"
            report_status = ReportStatus.DISMISSED
        db.add_all(
            [TagLinks(image_id=image.image_id, tag_id=t, user_id=owner.user_id) for t in tag_ids]
        )
        db.add(Favorites(user_id=favoriter.user_id, image_id=image.image_id))
        db.add(Comments(image_id=image.image_id, user_id=author.user_id, post_text=comment))
        db.add(
            ImageReports(
                image_id=image.image_id, user_id=owner.user_id, category=2, status=report_status
            )
        )
    await db.commit()
    # Counter triggers moved images.favorites and last_post while the rows went in; set
    # the sort values last.
    for image, favorites, days in zip(images, SEEK_FAVORITES, SEEK_LAST_POST_DAYS, strict=True):
        last_post = None if days is None else SEEK_BASE + timedelta(days=days)
        await db.execute(
            update(Images)
            .where(Images.image_id == image.image_id)  # type: ignore[arg-type]
            .values(favorites=favorites, last_post=last_post)
        )
    await db.commit()

    filters = {
        "tags_any": f"tags={any_a.tag_id},{any_b.tag_id}",
        "tags_all": f"tags={all_a.tag_id},{all_b.tag_id}&tags_mode=all",
        "exclude_tags": f"exclude_tags={dropped_tag.tag_id}",
        "favorited_by_user_id": f"favorited_by_user_id={fan.user_id}",
        "exclude_favorited_by_user_id": f"exclude_favorited_by_user_id={hater.user_id}",
        "commenter": f"commenter={commenter.user_id}",
        "commentsearch": "commentsearch=kitten",
        "exclude_commenter": f"exclude_commenter={critic.user_id}",
        "reported": "reported=true",
    }
    return images, filters, {"Authorization": f"Bearer {mod_token}"}


def _check_link_bounds(statement: str, parameters: Any, sort_by: str) -> str:
    """Assert a neighbours statement's link bounds equal its own image_id bound.

    Returns the statement's shape: "<" or ">" for its bound on images.image_id,
    else "null group" or "beyond".
    """
    own, lowers, uppers = [], [], []
    for table, operator, position in _ID_COMPARISON.findall(statement):
        value = parameters[int(position) - 1]
        if table == "images":
            own.append((operator, value))
        elif operator == ">=":
            lowers.append(value)
        else:
            uppers.append(value)
    # Both link sites (tags, exclude_tags) are bounded in every statement...
    assert len(lowers) == len(uppers) >= 1, statement
    # ...by exactly the statement's own restriction on images.image_id.
    assert len(own) <= 1, statement
    if not own:
        expected = (SEEK_UNBOUNDED_LOWER, SEEK_UNBOUNDED_UPPER)
    elif own[0][0] == "<":
        expected = (SEEK_UNBOUNDED_LOWER, own[0][1] - 1)
    else:
        assert own[0][0] == ">", statement
        expected = (own[0][1] + 1, SEEK_UNBOUNDED_UPPER)
    assert (set(lowers), set(uppers)) == ({expected[0]}, {expected[1]}), statement
    if own:
        return own[0][0]
    return "null group" if f"images.{sort_by} IS NULL" in statement else "beyond"


class TestSeekBoundedFilters:
    @pytest.mark.parametrize("sort_order", ["ASC", "DESC"])
    @pytest.mark.parametrize("sort_by", ["image_id", "favorites"])
    @pytest.mark.parametrize("bounded_filter", SEEK_FILTERS)
    async def test_walk_matches_listing(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        bounded_filter: str,
        sort_by: str,
        sort_order: str,
    ):
        images, filters, mod_headers = await _seek_world(db_session)
        # reported is honoured only for report_view holders.
        headers = mod_headers if bounded_filter == "reported" else None
        params = f"{filters[bounded_filter]}&sort_by={sort_by}&sort_order={sort_order}"

        listing = await client.get(f"/api/v1/images?{params}&per_page=100", headers=headers)
        assert listing.status_code == 200, listing.text
        ids = [image["image_id"] for image in listing.json()["images"]]
        assert sorted(ids) == [images[position].image_id for position in SEEK_LISTED]

        for position, image_id in enumerate(ids):
            response = await client.get(
                NEIGHBORS.format(image_id=image_id) + f"?{params}", headers=headers
            )
            assert response.status_code == 200, response.text
            assert response.json() == {
                "prev_image_id": ids[position - 1] if position > 0 else None,
                "next_image_id": ids[position + 1] if position < len(ids) - 1 else None,
            }

    @pytest.mark.parametrize("sort_order", ["ASC", "DESC"])
    @pytest.mark.parametrize("sort_by", ["image_id", "favorites", "last_post"])
    async def test_link_bounds_equal_each_statements_own_id_bound(
        self, client: AsyncClient, db_session: AsyncSession, sort_by: str, sort_order: str
    ):
        images, filters, _ = await _seek_world(db_session)
        params = (
            f"{filters['tags_any']}&{filters['exclude_tags']}"
            f"&sort_by={sort_by}&sort_order={sort_order}"
        )
        executed: list[tuple[str, Any]] = []

        def record(conn, cursor, statement, parameters, context, executemany):
            executed.append((statement, parameters))

        sync_engine = db_session.bind.sync_engine  # type: ignore[union-attr]
        event.listen(sync_engine, "before_cursor_execute", record)
        try:
            for image in images:
                await _neighbors(client, image, params)
        finally:
            event.remove(sync_engine, "before_cursor_execute", record)

        seeks = [(s, p) for s, p in executed if "FROM images" in s and "LIMIT" in s]
        assert seeks
        shapes = {_check_link_bounds(s, p, sort_by) for s, p in seeks}
        # Every statement shape ran: id-bounded both ways; for a non-id sort the
        # unbounded beyond step; for a nullable one the NULL group too.
        expected_shapes = {
            "image_id": {"<", ">"},
            "favorites": {"<", ">", "beyond"},
            "last_post": {"<", ">", "beyond", "null group"},
        }
        assert shapes == expected_shapes[sort_by]
