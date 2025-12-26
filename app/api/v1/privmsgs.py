"""
Privmsgs API endpoints
"""

from typing import Annotated

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import PaginationParams
from app.config import settings
from app.core.auth import VerifiedUser, get_current_user
from app.core.database import get_db
from app.core.permissions import Permission, has_permission
from app.core.redis import get_redis
from app.models import Privmsgs, Users
from app.schemas.privmsg import PrivmsgCreate, PrivmsgMessage, PrivmsgMessages
from app.tasks.queue import enqueue_job

router = APIRouter(prefix="/privmsgs", tags=["privmsgs"])


@router.post("/", response_model=PrivmsgMessage, status_code=status.HTTP_201_CREATED)
async def send_privmsg(
    privmsg: PrivmsgCreate,
    current_user: VerifiedUser,
    db: AsyncSession = Depends(get_db),
) -> PrivmsgMessage:
    """
    Create a new private message.
    """

    new_privmsg = Privmsgs(
        from_user_id=current_user.user_id,
        to_user_id=privmsg.to_user_id,
        subject=privmsg.subject,
        text=privmsg.message,
    )

    db.add(new_privmsg)
    await db.commit()
    await db.refresh(new_privmsg)

    # Queue background task to send email notification (non-blocking)
    await enqueue_job("send_pm_notification", privmsg_id=new_privmsg.privmsg_id)

    return PrivmsgMessage.model_validate(new_privmsg)


@router.get("/received", response_model=PrivmsgMessages)
async def get_received_privmsgs(
    pagination: Annotated[PaginationParams, Depends()],
    current_user: Annotated[Users, Depends(get_current_user)],
    user_id: Annotated[int | None, Query(description="Filter by user ID (admin only)")] = None,
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> PrivmsgMessages:
    """
    Retrieve received private messages.

    Regular users can only see their own received messages.
    Users with PRIVMSG_VIEW permission can view received messages for any user by specifying user_id parameter.

    **Examples:**
    - `/privmsgs/received` - Get your own received messages
    - `/privmsgs/received?user_id=5` - (With PRIVMSG_VIEW permission) Get received messages for user 5
    """
    assert current_user.user_id is not None
    # Determine which user's messages to retrieve
    target_user_id = current_user.user_id

    # Users with PRIVMSG_VIEW permission can filter by any user_id
    if user_id is not None:
        if not await has_permission(db, current_user.user_id, Permission.PRIVMSG_VIEW, redis_client):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view other users' messages",
            )
        target_user_id = user_id

    # Base query of messages received by target user (used for counting)
    base_query = select(Privmsgs).where(Privmsgs.to_user_id == target_user_id)  # type: ignore[arg-type]

    # Count total
    count_query = select(func.count()).select_from(base_query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Query messages with sender username and avatar joined (outer join to be safe)
    query = (
        select(Privmsgs, Users.username, Users.avatar)  # type: ignore[call-overload]
        .join(Users, Privmsgs.from_user_id == Users.user_id, isouter=True)
        .where(Privmsgs.to_user_id == target_user_id)
        .order_by(desc(Privmsgs.date))  # type: ignore[arg-type]
        .offset(pagination.offset)
        .limit(pagination.per_page)
    )

    result = await db.execute(query)
    rows = result.all()

    messages: list[PrivmsgMessage] = []

    for row in rows:
        msg: Privmsgs = row[0]
        sender_username: str | None = row[1] if len(row) > 1 else None
        sender_avatar: str | None = row[2] if len(row) > 2 else None
        from_avatar_url: str | None = None
        if sender_avatar:
            from_avatar_url = f"{settings.IMAGE_BASE_URL}/images/avatars/{sender_avatar}"

        data = {
            "privmsg_id": msg.privmsg_id,
            "subject": msg.subject,
            "text": msg.text,
            "from_user_id": msg.from_user_id,
            "to_user_id": msg.to_user_id,
            "date": msg.date,
            "viewed": msg.viewed,
            "from_username": sender_username,
            "from_avatar_url": from_avatar_url,
        }
        messages.append(PrivmsgMessage.model_validate(data))

    return PrivmsgMessages(
        total=total or 0,
        page=pagination.page,
        per_page=pagination.per_page,
        messages=messages,
    )


@router.get("/sent", response_model=PrivmsgMessages)
async def get_sent_privmsgs(
    pagination: Annotated[PaginationParams, Depends()],
    current_user: Annotated[Users, Depends(get_current_user)],
    user_id: Annotated[int | None, Query(description="Filter by user ID (admin only)")] = None,
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> PrivmsgMessages:
    """
    Retrieve sent private messages.

    Regular users can only see their own sent messages.
    Users with PRIVMSG_VIEW permission can view sent messages for any user by specifying user_id parameter.

    **Examples:**
    - `/privmsgs/sent` - Get your own sent messages
    - `/privmsgs/sent?user_id=5` - (With PRIVMSG_VIEW permission) Get sent messages for user 5
    """
    assert current_user.user_id is not None
    # Determine which user's messages to retrieve
    target_user_id = current_user.user_id

    # Users with PRIVMSG_VIEW permission can filter by any user_id
    if user_id is not None:
        if not await has_permission(db, current_user.user_id, Permission.PRIVMSG_VIEW, redis_client):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view other users' messages",
            )
        target_user_id = user_id

    # Base query of messages sent by target user (used for counting)
    base_query = select(Privmsgs).where(Privmsgs.from_user_id == target_user_id)  # type: ignore[arg-type]

    # Count total
    count_query = select(func.count()).select_from(base_query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Query messages with recipient username and avatar joined (outer join to be safe)
    query = (
        select(Privmsgs, Users.username, Users.avatar)  # type: ignore[call-overload]
        .join(Users, Privmsgs.to_user_id == Users.user_id, isouter=True)
        .where(Privmsgs.from_user_id == target_user_id)
        .order_by(desc(Privmsgs.date))  # type: ignore[arg-type]
        .offset(pagination.offset)
        .limit(pagination.per_page)
    )

    result = await db.execute(query)
    rows = result.all()

    messages: list[PrivmsgMessage] = []

    for row in rows:
        msg: Privmsgs = row[0]
        recipient_username: str | None = row[1] if len(row) > 1 else None
        recipient_avatar: str | None = row[2] if len(row) > 2 else None
        to_avatar_url: str | None = None
        if recipient_avatar:
            to_avatar_url = f"{settings.IMAGE_BASE_URL}/images/avatars/{recipient_avatar}"

        data = {
            "privmsg_id": msg.privmsg_id,
            "subject": msg.subject,
            "text": msg.text,
            "from_user_id": msg.from_user_id,
            "to_user_id": msg.to_user_id,
            "date": msg.date,
            "viewed": msg.viewed,
            "to_username": recipient_username,
            "to_avatar_url": to_avatar_url,
        }
        messages.append(PrivmsgMessage.model_validate(data))

    return PrivmsgMessages(
        total=total or 0,
        page=pagination.page,
        per_page=pagination.per_page,
        messages=messages,
    )


@router.get("/{privmsg_id}", response_model=PrivmsgMessage)
async def get_privmsg(
    privmsg_id: Annotated[int, Path(description="Privmsg ID to fetch")],
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> PrivmsgMessage:
    """Retrieve a single private message by id. Only sender or recipient may view it.

    If the requesting user is the recipient and the message is not yet viewed, mark it as viewed.
    """
    result = await db.execute(select(Privmsgs).where(Privmsgs.privmsg_id == privmsg_id))  # type: ignore[arg-type]
    msg = result.scalar_one_or_none()

    if not msg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

    # Check ownership
    is_recipient = current_user.user_id == msg.to_user_id
    is_sender = current_user.user_id == msg.from_user_id

    if not (is_recipient or is_sender):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view this message"
        )

    # If recipient views and not yet marked viewed, mark it
    if is_recipient and msg.viewed == 0:
        msg.viewed = 1
        db.add(msg)
        await db.commit()
        await db.refresh(msg)

    # Try to fetch usernames and avatars for sender/recipient
    sender_res = await db.execute(
        select(Users.username, Users.avatar).where(Users.user_id == msg.from_user_id)  # type: ignore[call-overload]
    )
    sender_row = sender_res.first()
    recipient_res = await db.execute(
        select(Users.username, Users.avatar).where(Users.user_id == msg.to_user_id)  # type: ignore[call-overload]
    )
    recipient_row = recipient_res.first()

    sender_username = sender_row[0] if sender_row else None
    sender_avatar = sender_row[1] if sender_row and len(sender_row) > 1 else None
    recipient_username = recipient_row[0] if recipient_row else None
    recipient_avatar = recipient_row[1] if recipient_row and len(recipient_row) > 1 else None

    from_avatar_url: str | None = None
    to_avatar_url: str | None = None
    if sender_avatar:
        from_avatar_url = f"{settings.IMAGE_BASE_URL}/images/avatars/{sender_avatar}"
    if recipient_avatar:
        to_avatar_url = f"{settings.IMAGE_BASE_URL}/images/avatars/{recipient_avatar}"

    data = {
        "privmsg_id": msg.privmsg_id,
        "subject": msg.subject,
        "text": msg.text,
        "from_user_id": msg.from_user_id,
        "to_user_id": msg.to_user_id,
        "date": msg.date,
        "viewed": msg.viewed,
        "from_username": sender_username,
        "to_username": recipient_username,
        "from_avatar_url": from_avatar_url,
        "to_avatar_url": to_avatar_url,
    }

    return PrivmsgMessage.model_validate(data)


@router.delete("/{privmsg_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_privmsg(
    privmsg_id: Annotated[int, Path(description="Privmsg ID to delete")],
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> None:
    """Mark a private message as deleted for the requesting user.

    - If the current user is the recipient, set `to_del = 1`.
    - If the current user is the sender, set `from_del = 1`.
    - If both `to_del` and `from_del` are 1, remove the row from the DB.
    """
    result = await db.execute(select(Privmsgs).where(Privmsgs.privmsg_id == privmsg_id))  # type: ignore[arg-type]
    msg = result.scalar_one_or_none()

    if not msg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

    # Check ownership
    is_recipient = current_user.user_id == msg.to_user_id
    is_sender = current_user.user_id == msg.from_user_id

    if not (is_recipient or is_sender):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to delete this message"
        )

    if is_recipient:
        msg.to_del = 1

    if is_sender:
        msg.from_del = 1

    # If both parties have deleted, delete the row
    if msg.to_del == 1 and msg.from_del == 1:
        await db.delete(msg)
    else:
        db.add(msg)

    await db.commit()
