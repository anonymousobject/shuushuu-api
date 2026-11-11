"""Seek to the results adjacent to an image in a filtered image listing.

The listing's order is ``(sort_column <direction>, image_id DESC)``. Rather than
number the whole result set, each side is found with at most three ``LIMIT 1``
statements (two for a non-nullable sort): first the rows tied with the image on
the sort column, then, only if there are none, the rows strictly past it, and
last, for a nullable column whose non-NULL values ran out, the NULL group as a
statement of its own. No statement has an ``OR`` across columns, so a
``(sort_column, image_id)`` index can serve each.

NULL sorts as the largest value (Postgres's default): first under DESC, last
under ASC.
"""

import time
from typing import Any

from sqlalchemy import Select, asc, desc
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.api.dependencies import ImageSortParams
from app.core.database import force_custom_plans, set_statement_timeout, statement_timeout
from app.models.image import Images
from app.services.image_filters import (
    SEEK_LOWER_PARAM,
    SEEK_UNBOUNDED_LOWER,
    SEEK_UNBOUNDED_UPPER,
    SEEK_UPPER_PARAM,
)

# SQLSTATE for a statement cancelled by statement_timeout (asyncpg QueryCanceledError).
QUERY_CANCELED_SQLSTATE = "57014"


class NeighborsTimeoutError(Exception):
    """Finding the adjacent results ran past its time limit."""


def _less_than(column: Any, value: Any) -> Any:
    """Rows before `value` in ascending order; NULL is the largest value."""
    return column.is_not(None) if value is None else column < value


# Link bounds for a statement that puts no bound on images.image_id.
_UNBOUNDED_LINKS = {SEEK_LOWER_PARAM: SEEK_UNBOUNDED_LOWER, SEEK_UPPER_PARAM: SEEK_UNBOUNDED_UPPER}


def _past(column: Any, value: Any, *, greater: bool) -> tuple[Any, dict[str, int]]:
    """Rows strictly past `value` on `column`, and the link bounds that match them.

    A seekable filter query repeats a statement's range on images.image_id inside its
    link subqueries (image_filters._seek_bounds), which is correct only while the two
    ranges are equal. So both come from here: a comparison on image_id gives the link
    bounds its exact integer range; one on any other column leaves them unbounded.
    """
    clause = column > value if greater else _less_than(column, value)
    if column is not col(Images.image_id):
        return clause, _UNBOUNDED_LINKS
    if greater:  # image_id > value; min(): no int4 above the last id (a looser bound is safe)
        lower = min(value + 1, SEEK_UNBOUNDED_UPPER)
        return clause, {SEEK_LOWER_PARAM: lower, SEEK_UPPER_PARAM: SEEK_UNBOUNDED_UPPER}
    # image_id < value
    return clause, {SEEK_LOWER_PARAM: SEEK_UNBOUNDED_LOWER, SEEK_UPPER_PARAM: value - 1}


async def _first_id(
    db: AsyncSession,
    query: Select[Any],
    *clauses: Any,
    order_by: tuple[Any, ...],
    link_bounds: dict[str, int],
    deadline: float,
) -> int | None:
    # The server's limit is per statement, so each one gets what is left of the lookup's.
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise NeighborsTimeoutError
    await set_statement_timeout(db, remaining)
    statement = (
        query.where(*clauses).order_by(*order_by).with_only_columns(col(Images.image_id)).limit(1)
    )
    # `link_bounds` must be this statement's own range on images.image_id (see _past).
    # A query without the seek parameters ignores them.
    return (await db.execute(statement, link_bounds)).scalar()


async def _find_side(
    db: AsyncSession,
    query: Select[Any],
    column: Any,
    value: Any,
    current_id: int,
    sorting: ImageSortParams,
    deadline: float,
    *,
    is_next: bool,
) -> int | None:
    # The tie-break is image_id DESC in the listing, so "next" walks ids down
    # and "prev" walks them up, whatever the sort direction.
    id_column = col(Images.image_id)
    id_order = desc(id_column) if is_next else asc(id_column)

    if column is not id_column:
        tied = column.is_(None) if value is None else column == value
        id_clause, link_bounds = _past(id_column, current_id, greater=not is_next)
        found = await _first_id(
            db,
            query,
            tied,
            id_clause,
            order_by=(id_order,),
            link_bounds=link_bounds,
            deadline=deadline,
        )
        if found is not None:
            return found

    # Strictly past `value`: "next" is further along the sort direction, "prev" back.
    beyond_is_greater = is_next == (sorting.sort_order == "ASC")
    if not beyond_is_greater:
        clause, link_bounds = _past(column, value, greater=False)
        return await _first_id(
            db,
            query,
            clause,
            order_by=(desc(column), id_order),
            link_bounds=link_bounds,
            deadline=deadline,
        )

    # NULL is past every value, but `col > v OR col IS NULL` cannot drive an
    # ordered index scan, so the NULL group is its own statement. It is a tie
    # group, hence ordered by id alone.
    if value is None:
        return None
    clause, link_bounds = _past(column, value, greater=True)
    found = await _first_id(
        db,
        query,
        clause,
        order_by=(asc(column), id_order),
        link_bounds=link_bounds,
        deadline=deadline,
    )
    if found is None and column.expression.nullable:
        # The NULL group has no bound on images.image_id, so neither do its links.
        found = await _first_id(
            db,
            query,
            column.is_(None),
            order_by=(id_order,),
            link_bounds=_UNBOUNDED_LINKS,
            deadline=deadline,
        )
    return found


async def find_neighbors(
    db: AsyncSession,
    query: Select[Any],
    image: Images,
    sorting: ImageSortParams,
    timeout_seconds: float,
) -> tuple[int | None, int | None]:
    """Return ``(prev_image_id, next_image_id)`` for `image` within `query`.

    `query` is a filtered ``Select`` over ``Images`` with no ordering. "next" is
    the result that follows `image` in the listing's order, "prev" the one before.

    `timeout_seconds` bounds the whole lookup, not each of its statements. Raises
    NeighborsTimeoutError past it, and re-raises any other database error as itself;
    after a database error the session is rolled back and usable.
    """
    assert image.image_id is not None
    column = sorting.sort_by.get_column(Images)
    value = getattr(image, column.key)
    deadline = time.monotonic() + timeout_seconds
    # Custom plans: a generic plan (a prepared statement's sixth execution onward)
    # cannot see that the link bounds are often the always-true sentinels, and so
    # reads a popular tag's whole link list.
    try:
        async with statement_timeout(db, timeout_seconds), force_custom_plans(db):
            next_id = await _find_side(
                db, query, column, value, image.image_id, sorting, deadline, is_next=True
            )
            prev_id = await _find_side(
                db, query, column, value, image.image_id, sorting, deadline, is_next=False
            )
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) != QUERY_CANCELED_SQLSTATE:
            raise
        raise NeighborsTimeoutError from exc
    return prev_id, next_id
