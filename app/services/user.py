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
    db: AsyncSession,
    redis_client: redis.Redis,  # type: ignore[type-arg]
    *,
    user_id: int | None = None,
    user: Users | None = None,
) -> UserPrivateResponse | None:
    """
    Build the UserPrivateResponse, including cached permissions, unread PM
    count, and uploads-remaining-today.

    Pass either:
    - `user_id`: the helper fetches the row with `user_groups` eager-loaded
      (used by /users/me where only the authenticated user_id is in scope).
    - `user`: a pre-loaded ORM object with `user_groups` + groups already
      selectinloaded (used by /auth/login and /auth/refresh, which already
      hold the user from password verification — skipping the re-fetch saves
      a round trip on every auth).

    Returns None only when called with `user_id` and the row is missing.
    """
    if user is None:
        if user_id is None:
            raise ValueError("build_user_private_response requires user_id or user")
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

    assert user.user_id is not None
    uid = user.user_id

    permissions = await get_cached_user_permissions(db, redis_client, uid)

    unread_result = await db.execute(
        select(func.count())
        .select_from(Privmsgs)
        .where(
            Privmsgs.to_user_id == uid,  # type: ignore[arg-type]
            Privmsgs.viewed == 0,  # type: ignore[arg-type]
            Privmsgs.to_del == 0,  # type: ignore[arg-type]
        )
    )
    unread_pm_count = unread_result.scalar() or 0

    uploads_today = await get_uploads_today(uid, db)

    response = UserPrivateResponse.model_validate(user)
    response.permissions = sorted(permissions)
    response.unread_pm_count = unread_pm_count
    response.uploads_remaining_today = max(0, user.maximgperday - uploads_today)
    return response
