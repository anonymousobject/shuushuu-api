"""Seek to the results adjacent to an image in a filtered image listing.

The listing's order is ``(sort_column <direction>, image_id DESC)``. Rather than
number the whole result set, each side is found with at most two ``LIMIT 1``
statements: first the rows tied with the image on the sort column, then, only
if there are none, the rows strictly past it. Neither statement has an ``OR``
across columns, so a ``(sort_column, image_id)`` index can serve each.

NULL sorts as the largest value (Postgres's default): first under DESC, last
under ASC.
"""

from typing import Any

from sqlalchemy import Select, asc, desc
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.api.dependencies import ImageSortParams
from app.core.database import statement_timeout
from app.models.image import Images

# SQLSTATE for a statement cancelled by statement_timeout (asyncpg QueryCanceledError).
QUERY_CANCELED_SQLSTATE = "57014"


class NeighborsTimeoutError(Exception):
    """Finding the adjacent results ran past the statement timeout."""


def _less_than(column: Any, value: Any) -> Any:
    """Rows before `value` in ascending order; NULL is the largest value."""
    return column.is_not(None) if value is None else column < value


async def _first_id(
    db: AsyncSession, query: Select[Any], *clauses: Any, order_by: tuple[Any, ...]
) -> int | None:
    statement = (
        query.where(*clauses).order_by(*order_by).with_only_columns(col(Images.image_id)).limit(1)
    )
    return (await db.execute(statement)).scalar()


async def _find_side(
    db: AsyncSession,
    query: Select[Any],
    column: Any,
    value: Any,
    current_id: int,
    sorting: ImageSortParams,
    *,
    is_next: bool,
) -> int | None:
    # The tie-break is image_id DESC in the listing, so "next" walks ids down
    # and "prev" walks them up, whatever the sort direction.
    id_column = col(Images.image_id)
    id_order = desc(id_column) if is_next else asc(id_column)

    if column is not id_column:
        tied = column.is_(None) if value is None else column == value
        id_clause = id_column < current_id if is_next else id_column > current_id
        found = await _first_id(db, query, tied, id_clause, order_by=(id_order,))
        if found is not None:
            return found

    # Strictly past `value`: "next" is further along the sort direction, "prev" back.
    beyond_is_greater = is_next == (sorting.sort_order == "ASC")
    if not beyond_is_greater:
        clause = _less_than(column, value)
        return await _first_id(db, query, clause, order_by=(desc(column), id_order))

    # NULL is past every value, but `col > v OR col IS NULL` cannot drive an
    # ordered index scan, so the NULL group is its own statement. It is a tie
    # group, hence ordered by id alone.
    if value is None:
        return None
    found = await _first_id(db, query, column > value, order_by=(asc(column), id_order))
    if found is None and column.expression.nullable:
        found = await _first_id(db, query, column.is_(None), order_by=(id_order,))
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

    Raises NeighborsTimeoutError when a statement exceeds `timeout_seconds`; the
    session is rolled back and usable afterwards.
    """
    assert image.image_id is not None
    column = sorting.sort_by.get_column(Images)
    value = getattr(image, column.key)
    async with statement_timeout(db, timeout_seconds):
        try:
            next_id = await _find_side(
                db, query, column, value, image.image_id, sorting, is_next=True
            )
            prev_id = await _find_side(
                db, query, column, value, image.image_id, sorting, is_next=False
            )
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) != QUERY_CANCELED_SQLSTATE:
                raise
            # Roll back before statement_timeout's cleanup SET: that cannot
            # run in a failed transaction.
            await db.rollback()
            raise NeighborsTimeoutError from exc
    return prev_id, next_id
