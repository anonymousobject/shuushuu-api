"""
Comment query services shared by the comments and images endpoints.
"""

from collections.abc import Sequence

from sqlalchemy import asc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.interfaces import LoaderOption

from app.models import Comments, Users
from app.models.permissions import UserGroups
from app.schemas.comment import CommentResponse


def comment_user_eager_load() -> LoaderOption:
    """Eager-load chain for a comment's author (user -> user_groups -> group)."""
    return (
        selectinload(Comments.user)  # type: ignore[arg-type]
        .selectinload(Users.user_groups)  # type: ignore[arg-type]
        .selectinload(UserGroups.group)  # type: ignore[arg-type]
    )


async def comments_for_images(
    db: AsyncSession, image_ids: Sequence[int]
) -> dict[int, list[CommentResponse]]:
    """
    Every non-deleted comment on the given images, oldest first, grouped by
    image id. Feeds include_comments=true on the images list; images without
    comments are absent from the result.
    """
    if not image_ids:
        return {}
    result = await db.execute(
        select(Comments)
        .where(
            Comments.deleted == False,  # type: ignore[arg-type]  # noqa: E712
            Comments.image_id.in_(image_ids),  # type: ignore[union-attr]
        )
        .order_by(asc(Comments.date), asc(Comments.post_id))  # type: ignore[arg-type]
        .options(comment_user_eager_load())
    )
    grouped: dict[int, list[CommentResponse]] = {}
    for comment in result.scalars().all():
        if comment.image_id is None:
            continue
        grouped.setdefault(comment.image_id, []).append(CommentResponse.model_validate(comment))
    return grouped
