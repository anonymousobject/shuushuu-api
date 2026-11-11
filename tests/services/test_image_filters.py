"""The filter builder's seek bounds on link-table subqueries.

A neighbours seek (app/services/image_neighbors.py) restricts images.image_id to a
range. Postgres derives only equalities across ``images.image_id = link.image_id``,
so with ``seekable=True`` the builder repeats that range on every link table's
image_id, as two bind parameters the seek fills in. The listing builds with
``seekable=False`` and must compile with neither parameter.
"""

from dataclasses import fields

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.core.permissions import Permission
from app.models import Tags, Users
from app.services.image_filters import (
    SEEK_LOWER_PARAM,
    SEEK_UPPER_PARAM,
    ImageFilterParams,
    build_image_filter_query,
)
from tests.api.v1.conftest import grant_permission


async def _every_site_filters(db: AsyncSession, viewer: Users, tags_mode: str) -> ImageFilterParams:
    """Filters that reach every link-table site the builder has."""
    tags = [Tags(title=f"seek-{i}", desc="seek", type=TagType.THEME) for i in range(3)]
    db.add_all(tags)
    await db.commit()
    include_a, include_b, exclude = (tag.tag_id for tag in tags)
    return ImageFilterParams(
        tags=f"{include_a},{include_b}",
        tags_mode=tags_mode,
        exclude_tags=str(exclude),
        favorited_by_user_id=viewer.user_id,
        exclude_favorited_by_user_id="999",
        commenter=viewer.user_id,
        commentsearch="kitten",
        exclude_commenter="998",
        reported=True,
    )


def _sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


# Bounded link columns per site, for two include tags: tags_mode=all builds one
# subquery per tag, tags_mode=any one for both; exclude_tags adds one more.
# favorites: the favorited_by_user_id join and exclude_favorited_by_user_id.
# posts: the commenter/commentsearch EXISTS and exclude_commenter. image_reports: reported.
SITES = {
    "all": {"tag_links": 3, "favorites": 2, "posts": 2, "image_reports": 1},
    "any": {"tag_links": 2, "favorites": 2, "posts": 2, "image_reports": 1},
}


@pytest.mark.parametrize("tags_mode", ["all", "any"])
async def test_listing_query_has_no_seek_bounds(
    db_session: AsyncSession, test_user: Users, tags_mode: str
):
    await grant_permission(db_session, test_user.user_id, Permission.REPORT_VIEW)
    filters = await _every_site_filters(db_session, test_user, tags_mode)

    built = await build_image_filter_query(db_session, filters, test_user, None)
    sql = _sql(built.query)

    # The filters did reach every site; the listing just leaves them unbounded.
    assert "image_reports.image_id" in sql
    assert SEEK_LOWER_PARAM not in sql
    assert SEEK_UPPER_PARAM not in sql


@pytest.mark.parametrize("tags_mode", ["all", "any"])
async def test_seekable_query_bounds_every_link_site(
    db_session: AsyncSession, test_user: Users, tags_mode: str
):
    await grant_permission(db_session, test_user.user_id, Permission.REPORT_VIEW)
    filters = await _every_site_filters(db_session, test_user, tags_mode)

    built = await build_image_filter_query(db_session, filters, test_user, None, seekable=True)
    sql = _sql(built.query)

    sites = SITES[tags_mode]
    for table, count in sites.items():
        assert sql.count(f"{table}.image_id >= %({SEEK_LOWER_PARAM})s") == count, table
        assert sql.count(f"{table}.image_id <= %({SEEK_UPPER_PARAM})s") == count, table
    # ...and nowhere else, in particular not on images itself.
    assert sql.count(SEEK_LOWER_PARAM) == sum(sites.values())
    assert sql.count(SEEK_UPPER_PARAM) == sum(sites.values())


# --- has_content_filters: the bare-feed test behind GET /images' fast count ---------

# One value per filter parameter that makes it filter. Parameters in _MODIFIERS only
# refine another filter and never narrow the feed alone.
_FILTERING = {
    "user_id": 5,
    "favorited_by_user_id": 5,
    "exclude_user_id": "5",
    "exclude_favorited_by_user_id": "5",
    "image_status": [1],
    "tags": "1",
    "exclude_tags": "1",
    "missing_tag_types": "1",
    "date_from": "2026-01-01",
    "date_to": "2026-01-01",
    "min_width": 1,
    "max_width": 1,
    "min_height": 1,
    "max_height": 1,
    "min_rating": 1.0,
    "min_favorites": 1,
    "min_num_ratings": 1,
    "commenter": 5,
    "exclude_commenter": "5",
    "commentsearch": "kitten",
    "hascomments": False,
    "reported": True,
}
_MODIFIERS = {
    "tags_mode": "all",
    "tag_depth": 0,
    "exclude_descendants": True,
    "missing_tag_types_mode": "all",
    "commentsearch_mode": "like",
}


def test_every_filter_parameter_is_classified():
    """A new ImageFilterParams field must be added to one of the two maps above."""
    assert {field.name for field in fields(ImageFilterParams)} == {*_FILTERING, *_MODIFIERS}


async def test_no_filters_is_the_bare_feed(db_session: AsyncSession, test_user: Users):
    for viewer in (None, test_user):
        built = await build_image_filter_query(db_session, ImageFilterParams(), viewer, None)
        assert built.has_content_filters is False


@pytest.mark.parametrize("name", list(_FILTERING))
async def test_each_filter_alone_is_not_the_bare_feed(
    db_session: AsyncSession, test_user: Users, name: str
):
    # reported is honoured only for report_view holders.
    await grant_permission(db_session, test_user.user_id, Permission.REPORT_VIEW)
    filters = ImageFilterParams(**{name: _FILTERING[name]})

    built = await build_image_filter_query(db_session, filters, test_user, None)

    assert built.has_content_filters is True


@pytest.mark.parametrize(
    "ignored",
    [
        _MODIFIERS,
        {"tags": "abc"},  # no parseable tag id
        {"tags": "", "exclude_tags": "", "date_from": ""},
        {"commentsearch": "   "},
        {"reported": True},  # the viewer lacks report_view
    ],
)
async def test_parameters_that_filter_nothing_leave_the_bare_feed(
    db_session: AsyncSession, test_user: Users, ignored: dict
):
    built = await build_image_filter_query(
        db_session, ImageFilterParams(**ignored), test_user, None
    )

    assert built.has_content_filters is False
