"""
Privmsgs API endpoints
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import PaginationParams
from app.core.auth import get_current_user
from app.core.database import get_db
from app.core.permissions import Permission, has_permission
from app.models import Privmsgs, Users
from app.schemas.privmsg import PrivmsgCreate, PrivmsgMessage, PrivmsgMessages

router = APIRouter(prefix="/privmsgs", tags=["privmsgs"])


@router.post("/", response_model=PrivmsgMessage, status_code=status.HTTP_201_CREATED)
async def send_privmsg(
    privmsg: PrivmsgCreate,
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> PrivmsgMessage:
    """
    Create a new private message.
    """

    # Create Privmsgs model instance (note: 'message' field maps to 'text' column)
    new_privmsg = Privmsgs(
        from_user_id=current_user.user_id,
        to_user_id=privmsg.to_user_id,
        subject=privmsg.subject,
        text=privmsg.message,
    )

    db.add(new_privmsg)
    await db.commit()
    await db.refresh(new_privmsg)
    return PrivmsgMessage.model_validate(new_privmsg)


@router.get("/received", response_model=PrivmsgMessages)
async def get_received_privmsgs(
    pagination: Annotated[PaginationParams, Depends()],
    current_user: Annotated[Users, Depends(get_current_user)],
    user_id: Annotated[int | None, Query(description="Filter by user ID (admin only)")] = None,
    db: AsyncSession = Depends(get_db),
) -> PrivmsgMessages:
    """
    Retrieve received private messages.

    Regular users can only see their own received messages.
    Users with PRIVMSG_VIEW permission can view received messages for any user by specifying user_id parameter.

    **Examples:**
    - `/privmsgs/received` - Get your own received messages
    - `/privmsgs/received?user_id=5` - (With PRIVMSG_VIEW permission) Get received messages for user 5
    """
    # Determine which user's messages to retrieve
    target_user_id = current_user.user_id

    # Users with PRIVMSG_VIEW permission can filter by any user_id
    if user_id is not None:
        if not await has_permission(db, current_user.user_id, Permission.PRIVMSG_VIEW):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view other users' messages",
            )
        target_user_id = user_id

    # Query messages received by target user
    query = select(Privmsgs).where(Privmsgs.to_user_id == target_user_id)  # type: ignore[arg-type]

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


@router.get("/sent", response_model=PrivmsgMessages)
async def get_sent_privmsgs(
    pagination: Annotated[PaginationParams, Depends()],
    current_user: Annotated[Users, Depends(get_current_user)],
    user_id: Annotated[int | None, Query(description="Filter by user ID (admin only)")] = None,
    db: AsyncSession = Depends(get_db),
) -> PrivmsgMessages:
    """
    Retrieve sent private messages.

    Regular users can only see their own sent messages.
    Users with PRIVMSG_VIEW permission can view sent messages for any user by specifying user_id parameter.

    **Examples:**
    - `/privmsgs/sent` - Get your own sent messages
    - `/privmsgs/sent?user_id=5` - (With PRIVMSG_VIEW permission) Get sent messages for user 5
    """
    # Determine which user's messages to retrieve
    target_user_id = current_user.user_id

    # Users with PRIVMSG_VIEW permission can filter by any user_id
    if user_id is not None:
        if not await has_permission(db, current_user.user_id, Permission.PRIVMSG_VIEW):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view other users' messages",
            )
        target_user_id = user_id

    # Query messages sent by target user
    query = select(Privmsgs).where(Privmsgs.from_user_id == target_user_id)  # type: ignore[arg-type]

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
