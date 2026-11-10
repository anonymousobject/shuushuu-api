"""Tests for seeking to the adjacent results of a filtered image listing."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import asc, desc, event, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import ImageSortParams
from app.config import TagType
from app.models import TagLinks, Tags
from app.models.image import Images, ImageSortBy
from app.services.image_filters import (
    SEEK_UNBOUNDED_UPPER,
    ImageFilterParams,
    build_image_filter_query,
)
from app.services.image_neighbors import NeighborsTimeoutError, find_neighbors

# Per-sort values for 8 images, in creation order. Each has two tied values;
# the nullable columns (last_post, total_pixels) also have two NULLs.
_NON_NULLABLE_VALUES = [5, 3, 5, 1, 3, 9, 7, 2]
_NULLABLE_VALUES = [5, None, 3, 1, None, 3, 9, 5]


def _sort_value(sort_by: ImageSortBy, index: int) -> object:
    """Value the image created at `index` gets for the column `sort_by` sorts on."""
    if sort_by == ImageSortBy.last_post:
        step = _NULLABLE_VALUES[index]
        return None if step is None else datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=step)
    if sort_by == ImageSortBy.total_pixels:
        step = _NULLABLE_VALUES[index]
        return None if step is None else Decimal(step * 1000)
    if sort_by == ImageSortBy.bayesian_rating:
        return float(_NON_NULLABLE_VALUES[index])
    if sort_by == ImageSortBy.favorites:
        return _NON_NULLABLE_VALUES[index]
    return None


async def _create_images(
    db_session: AsyncSession, user_id: int, sort_by: ImageSortBy
) -> list[Images]:
    images = []
    for index in range(8):
        image = Images(
            filename=f"neighbors-{index}",
            ext="jpg",
            original_filename=f"neighbors{index}.jpg",
            md5_hash=f"{index + 1:032x}",
            filesize=1000,
            width=100,
            height=100,
            user_id=user_id,
            status=1,
            locked=False,
        )
        if sort_by not in (ImageSortBy.image_id, ImageSortBy.date_added):
            setattr(image, sort_by.value, _sort_value(sort_by, index))
        images.append(image)
    db_session.add_all(images)
    await db_session.commit()
    return images


def _sorting(sort_by: ImageSortBy, sort_order: str) -> ImageSortParams:
    return ImageSortParams(sort_by=sort_by, sort_order=sort_order)  # type: ignore[arg-type]


async def _listing_order(
    db_session: AsyncSession, query, sort_by: ImageSortBy, sort_order: str
) -> list[int]:
    column = sort_by.get_column(Images)
    direction = desc if sort_order == "DESC" else asc
    result = await db_session.execute(
        query.order_by(direction(column), desc(Images.image_id)).with_only_columns(Images.image_id)
    )
    return list(result.scalars().all())


async def _walk(
    db_session: AsyncSession,
    query,
    sorting: ImageSortParams,
    start_id: int,
    side: int,
) -> list[int]:
    """Follow `side` (0 = prev, 1 = next) from `start_id` until it runs out."""
    visited = [start_id]
    while True:
        image = await db_session.get(Images, visited[-1])
        assert image is not None
        neighbours = await find_neighbors(db_session, query, image, sorting, 5.0)
        following = neighbours[side]
        if following is None:
            return visited
        assert following not in visited, "walk looped"
        visited.append(following)


@pytest.mark.parametrize("sort_order", ["ASC", "DESC"])
@pytest.mark.parametrize("sort_by", list(ImageSortBy))
async def test_walk_matches_listing_order(db_session, test_user, sort_by, sort_order):
    await _create_images(db_session, test_user.user_id, sort_by)
    query = select(Images)
    expected = await _listing_order(db_session, query, sort_by, sort_order)
    assert len(expected) == 8
    sorting = _sorting(sort_by, sort_order)

    assert await _walk(db_session, query, sorting, expected[0], side=1) == expected
    assert await _walk(db_session, query, sorting, expected[-1], side=0) == expected[::-1]


async def test_ends_return_none(db_session, test_user):
    await _create_images(db_session, test_user.user_id, ImageSortBy.favorites)
    query = select(Images)
    sorting = _sorting(ImageSortBy.favorites, "DESC")
    expected = await _listing_order(db_session, query, ImageSortBy.favorites, "DESC")

    first = await db_session.get(Images, expected[0])
    last = await db_session.get(Images, expected[-1])
    assert first is not None and last is not None
    first_prev, first_next = await find_neighbors(db_session, query, first, sorting, 5.0)
    last_prev, last_next = await find_neighbors(db_session, query, last, sorting, 5.0)

    assert first_prev is None
    assert first_next == expected[1]
    assert last_next is None
    assert last_prev == expected[-2]


async def test_respects_the_filtered_query(db_session, test_user):
    images = await _create_images(db_session, test_user.user_id, ImageSortBy.favorites)
    kept = {images[1].image_id, images[4].image_id, images[6].image_id}
    query = select(Images).where(Images.image_id.in_(kept))  # type: ignore[attr-defined]
    sorting = _sorting(ImageSortBy.favorites, "DESC")
    expected = await _listing_order(db_session, query, ImageSortBy.favorites, "DESC")
    assert set(expected) == kept

    assert await _walk(db_session, query, sorting, expected[0], side=1) == expected


async def test_timeout_raises_neighbors_timeout_error(db_session, test_user):
    images = await _create_images(db_session, test_user.user_id, ImageSortBy.favorites)
    slow_query = select(Images).where(func.pg_sleep(0.5).is_not(None))
    sorting = _sorting(ImageSortBy.favorites, "DESC")

    with pytest.raises(NeighborsTimeoutError):
        await find_neighbors(db_session, slow_query, images[0], sorting, 0.05)

    assert (await db_session.execute(text("SELECT 1"))).scalar() == 1
    # Rows created before the cancelled statement survive the rollback.
    count = await db_session.execute(select(func.count()).select_from(Images))
    assert count.scalar() == 8


async def _executed_statements(
    db_session: AsyncSession, user_id: int, sort_by: ImageSortBy, sort_order: str
):
    """SQL of every statement find_neighbors runs, for each image in the listing."""
    await _create_images(db_session, user_id, sort_by)
    query = select(Images)
    sorting = _sorting(sort_by, sort_order)
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    sync_engine = db_session.bind.sync_engine  # type: ignore[union-attr]
    event.listen(sync_engine, "before_cursor_execute", record)
    try:
        for image in (await db_session.execute(query)).scalars().all():
            await find_neighbors(db_session, query, image, sorting, 5.0)
    finally:
        event.remove(sync_engine, "before_cursor_execute", record)
    return [s for s in statements if "FROM images" in s]


@pytest.mark.parametrize("sort_order", ["ASC", "DESC"])
@pytest.mark.parametrize(
    "sort_by", [ImageSortBy.image_id, ImageSortBy.favorites, ImageSortBy.bayesian_rating]
)
async def test_not_null_columns_never_test_for_null(db_session, test_user, sort_by, sort_order):
    statements = await _executed_statements(db_session, test_user.user_id, sort_by, sort_order)

    assert statements
    assert not [s for s in statements if "IS NULL" in s]


@pytest.mark.parametrize("sort_order", ["ASC", "DESC"])
@pytest.mark.parametrize("sort_by", [ImageSortBy.last_post, ImageSortBy.total_pixels])
async def test_nullable_columns_never_combine_range_and_null(
    db_session, test_user, sort_by, sort_order
):
    statements = await _executed_statements(db_session, test_user.user_id, sort_by, sort_order)
    column = f"images.{sort_by.value}"

    assert [s for s in statements if f"{column} IS NULL" in s]
    assert not [s for s in statements if f"{column} >" in s and f"{column} IS NULL" in s]


async def _plan_cache_mode(db_session: AsyncSession) -> str:
    return (await db_session.execute(text("SHOW plan_cache_mode"))).scalar_one()


@pytest.mark.parametrize("sort_order", ["ASC", "DESC"])
@pytest.mark.parametrize(
    "sort_by", [ImageSortBy.image_id, ImageSortBy.favorites, ImageSortBy.last_post]
)
async def test_statements_are_planned_for_their_parameter_values(
    db_session, test_user, sort_by, sort_order
):
    """Every statement runs with plan_cache_mode = force_custom_plan.

    Postgres may switch a prepared statement to a generic plan from its sixth
    execution, and a generic plan cannot see that the seek's link bounds
    (image_filters._seek_bounds) are often the always-true sentinels. The query
    below matches nothing unless the setting is in force while the statement
    itself executes, so any statement run without it changes the walk.
    """
    await _create_images(db_session, test_user.user_id, sort_by)
    forced = select(Images).where(func.current_setting("plan_cache_mode") == "force_custom_plan")
    expected = await _listing_order(db_session, select(Images), sort_by, sort_order)
    sorting = _sorting(sort_by, sort_order)

    assert await _walk(db_session, forced, sorting, expected[0], side=1) == expected
    assert await _walk(db_session, forced, sorting, expected[-1], side=0) == expected[::-1]


async def test_plan_cache_mode_is_restored_after_returning(db_session, test_user):
    images = await _create_images(db_session, test_user.user_id, ImageSortBy.favorites)
    assert await _plan_cache_mode(db_session) == "auto"

    await find_neighbors(
        db_session, select(Images), images[0], _sorting(ImageSortBy.favorites, "DESC"), 5.0
    )

    assert await _plan_cache_mode(db_session) == "auto"
    assert (await db_session.execute(text("SELECT 1"))).scalar() == 1


async def test_plan_cache_mode_is_restored_after_timeout(db_session, test_user):
    images = await _create_images(db_session, test_user.user_id, ImageSortBy.favorites)
    slow_query = select(Images).where(func.pg_sleep(0.5).is_not(None))
    assert await _plan_cache_mode(db_session) == "auto"

    with pytest.raises(NeighborsTimeoutError):
        await find_neighbors(
            db_session, slow_query, images[0], _sorting(ImageSortBy.favorites, "DESC"), 0.05
        )

    assert await _plan_cache_mode(db_session) == "auto"
    assert (await db_session.execute(text("SELECT 1"))).scalar() == 1


async def test_last_possible_image_id_does_not_overflow_the_link_bound(db_session, test_user):
    """The image at int4's maximum id: "prev" (image_id > cur) has no int4 cur + 1."""
    tag = Tags(title="seek-int4-max", desc="seek", type=TagType.THEME)
    db_session.add(tag)
    await db_session.commit()
    (lower,) = await _create_images_at(db_session, test_user.user_id, [None])
    (top,) = await _create_images_at(db_session, test_user.user_id, [SEEK_UNBOUNDED_UPPER])
    db_session.add_all(
        [
            TagLinks(image_id=image.image_id, tag_id=tag.tag_id, user_id=test_user.user_id)
            for image in (lower, top)
        ]
    )
    await db_session.commit()
    built = await build_image_filter_query(
        db_session, ImageFilterParams(tags=str(tag.tag_id)), None, None, seekable=True
    )

    neighbours = await find_neighbors(
        db_session, built.query, top, _sorting(ImageSortBy.image_id, "DESC"), 5.0
    )

    assert neighbours == (None, lower.image_id)


async def _create_images_at(
    db_session: AsyncSession, user_id: int, image_ids: list[int | None]
) -> list[Images]:
    """Images with the given ids (None: the next serial id)."""
    images = [
        Images(
            image_id=image_id,
            filename=f"neighbors-at-{index}",
            ext="jpg",
            original_filename=f"neighbors-at{index}.jpg",
            md5_hash=f"{index + 1:016x}{image_id or 0:016x}",
            filesize=1000,
            width=100,
            height=100,
            user_id=user_id,
            status=1,
            locked=False,
        )
        for index, image_id in enumerate(image_ids)
    ]
    db_session.add_all(images)
    await db_session.commit()
    return images


DIVISION_BY_ZERO_SQLSTATE = "22012"


async def _statement_timeout(db_session: AsyncSession) -> str:
    return (await db_session.execute(text("SHOW statement_timeout"))).scalar_one()


async def test_other_database_errors_surface_as_themselves(db_session, test_user):
    """A failing statement that is not a timeout propagates as its own error.

    Without a rollback first, both cleanup SETs would fail in the aborted
    transaction, and InFailedSQLTransaction would replace the real error.
    """
    images = await _create_images(db_session, test_user.user_id, ImageSortBy.favorites)
    failing = select(Images).where(text("1 / 0 = 1"))
    timeout_before = await _statement_timeout(db_session)

    with pytest.raises(DBAPIError) as raised:
        await find_neighbors(
            db_session, failing, images[0], _sorting(ImageSortBy.favorites, "DESC"), 5.0
        )

    assert raised.value.orig.sqlstate == DIVISION_BY_ZERO_SQLSTATE  # type: ignore[union-attr]
    assert await _plan_cache_mode(db_session) == "auto"
    assert await _statement_timeout(db_session) == timeout_before
    assert (await db_session.execute(text("SELECT 1"))).scalar() == 1
