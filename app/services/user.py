"""
Shared user response builders used by auth + users endpoints.
"""

from __future__ import annotations

import redis.asyncio as redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.permission_cache import get_cached_user_permissions
from app.models import Privmsgs, Users
from app.models.permissions import UserGroups
from app.schemas.user import UserPrivateResponse
from app.services.upload import get_uploads_today


async def build_user_private_response(
    user_id: int,
    db: AsyncSession,
    redis_client: redis.Redis,  # type: ignore[type-arg]
) -> UserPrivateResponse | None:
    """
    Build the UserPrivateResponse for `user_id`, including cached permissions,
    unread PM count, and uploads-remaining-today.

    Returns None if the user doesn't exist. Callers that already know the user
    exists (e.g. immediately after auth) can treat None as an internal error.
    """
    user_result = await db.execute(
        select(Users)
        .options(
            selectinload(Users.user_groups).selectinload(UserGroups.group)  # type: ignore[arg-type]
        )
        .where(Users.user_id == user_id)  # type: ignore[arg-type]
    )
    user = user_result.scalar_one_or_none()
    if not user:
        return None

    permissions = await get_cached_user_permissions(db, redis_client, user_id)

    unread_result = await db.execute(
        select(func.count())
        .select_from(Privmsgs)
        .where(
            Privmsgs.to_user_id == user_id,  # type: ignore[arg-type]
            Privmsgs.viewed == 0,  # type: ignore[arg-type]
            Privmsgs.to_del == 0,  # type: ignore[arg-type]
        )
    )
    unread_pm_count = unread_result.scalar() or 0

    uploads_today = await get_uploads_today(user_id, db)

    response = UserPrivateResponse.model_validate(user)
    response.permissions = sorted(permissions)
    response.unread_pm_count = unread_pm_count
    response.uploads_remaining_today = max(0, user.maximgperday - uploads_today)
    return response
