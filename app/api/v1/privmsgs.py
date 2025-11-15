"""
Privmsgs API endpoints
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import PaginationParams
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import Privmsgs, Users
from app.schemas.privmsg import PrivmsgMessage, PrivmsgMessages

router = APIRouter(prefix="/privmsgs", tags=["privmsgs"])


@router.get("/", response_model=PrivmsgMessages)
async def get_user_privmsgs(
    pagination: Annotated[PaginationParams, Depends()],
    current_user: Annotated[Users, Depends(get_current_user)],
    to_user_id: Annotated[int | None, Query(description="Filter by recipient user ID")] = None,
    from_user_id: Annotated[int | None, Query(description="Filter by sender user ID")] = None,
    db: AsyncSession = Depends(get_db),
) -> PrivmsgMessages:
    """
    Retrieve private messages with pagination.

    Regular users can only see messages they sent or received.
    Admins can view any messages and use filters to see specific conversations.
    """
    query = select(Privmsgs)

    # Permission check: regular users can only see their own messages
    if not current_user.admin:
        # User can see messages they sent OR received
        query = query.where(
            or_(
                Privmsgs.to_user_id == current_user.user_id,  # type: ignore[arg-type]
                Privmsgs.from_user_id == current_user.user_id,  # type: ignore[arg-type]
            )
        )

    # Apply optional filters (typically used by admins)
    if to_user_id is not None:
        # Non-admins trying to filter by other users' messages
        if not current_user.admin and to_user_id != current_user.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view messages for this user",
            )
        query = query.where(Privmsgs.to_user_id == to_user_id)  # type: ignore[arg-type]

    if from_user_id is not None:
        # Non-admins trying to filter by other users' messages
        if not current_user.admin and from_user_id != current_user.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view messages from this user",
            )
        query = query.where(Privmsgs.from_user_id == from_user_id)  # type: ignore[arg-type]

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply sorting (newest first)
    query = query.order_by(desc(Privmsgs.date))  # type: ignore[arg-type]

    # Apply pagination
    query = query.offset(pagination.offset).limit(pagination.per_page)

    result = await db.execute(query)
    messages = result.scalars().all()

    return PrivmsgMessages(
        total=total or 0,
        page=pagination.page,
        per_page=pagination.per_page,
        messages=[PrivmsgMessage.model_validate(msg) for msg in messages],
    )
