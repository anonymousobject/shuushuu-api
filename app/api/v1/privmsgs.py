"""
Privmsgs API endpoints
"""

import re
import uuid as uuid_mod
from typing import Annotated, Literal

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import and_, case, delete, desc, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import PaginationParams
from app.config import settings
from app.core.auth import VerifiedUser, get_current_user
from app.core.database import get_db
from app.core.permissions import Permission, has_permission
from app.core.redis import get_redis
from app.models import Privmsgs, Users
from app.models.permissions import Groups, UserGroups
from app.schemas.privmsg import (
    PrivmsgCreate,
    PrivmsgMessage,
    PrivmsgMessages,
    ThreadList,
    ThreadSummary,
)
from app.tasks.queue import enqueue_job

router = APIRouter(prefix="/privmsgs", tags=["privmsgs"])


async def get_user_groups_map(db: AsyncSession, user_ids: list[int]) -> dict[int, list[str]]:
    """
    Fetch groups for multiple user IDs in a single query.

    Returns a dict mapping user_id -> list of group names.
    """
    if not user_ids:
        return {}

    result = await db.execute(
        select(UserGroups.user_id, Groups.title)  # type: ignore[call-overload]
        .join(Groups, UserGroups.group_id == Groups.group_id)
        .where(UserGroups.user_id.in_(user_ids))  # type: ignore[attr-defined]
    )
    rows = result.all()

    groups_map: dict[int, list[str]] = {uid: [] for uid in user_ids}
    for user_id, group_title in rows:
        if group_title:
            groups_map[user_id].append(group_title)

    return groups_map


def _strip_re_prefix(subject: str) -> str:
    """Strip leading 'Re: ' prefixes from a subject line."""
    return re.sub(r"^(Re:\s*)+", "", subject, flags=re.IGNORECASE).strip()


@router.post(
    "/", response_model=PrivmsgMessage, status_code=status.HTTP_201_CREATED, include_in_schema=False
)
@router.post("", response_model=PrivmsgMessage, status_code=status.HTTP_201_CREATED)
async def send_privmsg(
    privmsg: PrivmsgCreate,
    current_user: VerifiedUser,
    db: AsyncSession = Depends(get_db),
) -> PrivmsgMessage:
    """
    Create a new private message.
    """

    thread_id = privmsg.thread_id or str(uuid_mod.uuid4())

    new_privmsg = Privmsgs(
        from_user_id=current_user.user_id,
        to_user_id=privmsg.to_user_id,
        subject=privmsg.subject,
        text=privmsg.message,
        thread_id=thread_id,
    )

    db.add(new_privmsg)

    # If replying to an existing thread, reset the recipient's soft-delete flags
    # so the thread reappears in their inbox
    if privmsg.thread_id:
        # Reset to_del for messages where recipient is the to_user
        await db.execute(
            update(Privmsgs)
            .where(
                Privmsgs.thread_id == thread_id,  # type: ignore[arg-type]
                Privmsgs.to_user_id == privmsg.to_user_id,  # type: ignore[arg-type]
                Privmsgs.to_del == 1,  # type: ignore[arg-type]
            )
            .values(to_del=0)
        )
        # Reset from_del for messages where recipient was the sender
        await db.execute(
            update(Privmsgs)
            .where(
                Privmsgs.thread_id == thread_id,  # type: ignore[arg-type]
                Privmsgs.from_user_id == privmsg.to_user_id,  # type: ignore[arg-type]
                Privmsgs.from_del == 1,  # type: ignore[arg-type]
            )
            .values(from_del=0)
        )

    await db.commit()
    await db.refresh(new_privmsg)

    # Queue background task to send email notification (non-blocking)
    await enqueue_job("send_pm_notification", privmsg_id=new_privmsg.privmsg_id)

    return PrivmsgMessage.model_validate(new_privmsg)


@router.get("/threads", response_model=ThreadList)
async def get_threads(
    pagination: Annotated[PaginationParams, Depends()],
    current_user: Annotated[Users, Depends(get_current_user)],
    filter: Annotated[Literal["all", "unread"], Query(description="Filter threads")] = "all",
    db: AsyncSession = Depends(get_db),
) -> ThreadList:
    """
    List conversation threads for the current user.

    Returns one entry per thread, sorted by most recent message date (newest first).
    Supports pagination and an optional filter query param (all or unread).
    Excludes threads the user has left (soft-deleted).
    """
    assert current_user.user_id is not None
    uid = current_user.user_id

    # Determine other_user_id via CASE expression:
    # If I sent the message, the other user is the recipient; otherwise it's the sender.
    other_user_id_expr = case(
        (Privmsgs.from_user_id == uid, Privmsgs.to_user_id),  # type: ignore[arg-type]
        else_=Privmsgs.from_user_id,
    )

    # Unread count: count messages TO the current user that are not viewed
    unread_expr = func.sum(
        case(
            (and_(Privmsgs.to_user_id == uid, Privmsgs.viewed == 0), 1),  # type: ignore[arg-type]
            else_=0,
        )
    )

    # Base filter: user is sender or recipient, and message not soft-deleted for them
    user_filter = or_(
        and_(Privmsgs.from_user_id == uid, Privmsgs.from_del == 0),  # type: ignore[arg-type]
        and_(Privmsgs.to_user_id == uid, Privmsgs.to_del == 0),  # type: ignore[arg-type]
    )

    # Only include messages with a thread_id
    thread_filter = Privmsgs.thread_id.isnot(None)  # type: ignore[union-attr]

    # Aggregation query grouped by thread_id
    thread_query = (
        select(  # type: ignore[call-overload]
            Privmsgs.thread_id,
            func.min(Privmsgs.subject).label("subject"),
            func.max(Privmsgs.date).label("latest_date"),
            func.count().label("message_count"),
            unread_expr.label("unread_count"),
            other_user_id_expr.label("other_user_id"),
        )
        .where(and_(user_filter, thread_filter))
        .group_by(Privmsgs.thread_id, other_user_id_expr)
        .order_by(desc("latest_date"))
    )

    # If filtering by unread, add HAVING clause
    if filter == "unread":
        thread_query = thread_query.having(unread_expr > 0)

    # Count total threads
    count_query = select(func.count()).select_from(thread_query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Paginate
    thread_query = thread_query.offset(pagination.offset).limit(pagination.per_page)
    result = await db.execute(thread_query)
    thread_rows = result.all()

    if not thread_rows:
        return ThreadList(
            total=total,
            page=pagination.page,
            per_page=pagination.per_page,
            threads=[],
        )

    # Collect other user IDs and thread IDs for batch lookups
    other_user_ids = list({row.other_user_id for row in thread_rows})
    thread_ids = [row.thread_id for row in thread_rows]

    # Fetch other user details (username, avatar)
    user_result = await db.execute(
        select(Users.user_id, Users.username, Users.avatar).where(Users.user_id.in_(other_user_ids))  # type: ignore[call-overload,union-attr]
    )
    user_rows = user_result.all()
    user_map: dict[int, tuple[str | None, str | None]] = {
        row[0]: (row[1], row[2]) for row in user_rows
    }

    # Fetch groups for other users
    groups_map = await get_user_groups_map(db, other_user_ids)

    # Fetch latest message text per thread using a correlated subquery approach:
    # For each thread, get the message with the max date.
    # We use a single query with a window function to get the latest message per thread.
    latest_msg_query = (
        select(Privmsgs.thread_id, Privmsgs.text)  # type: ignore[call-overload]
        .where(
            and_(
                Privmsgs.thread_id.in_(thread_ids),  # type: ignore[union-attr]
                or_(
                    and_(Privmsgs.from_user_id == uid, Privmsgs.from_del == 0),  # type: ignore[arg-type]
                    and_(Privmsgs.to_user_id == uid, Privmsgs.to_del == 0),  # type: ignore[arg-type]
                ),
            )
        )
        .order_by(Privmsgs.thread_id, desc(Privmsgs.date))  # type: ignore[arg-type]
    )
    latest_msg_result = await db.execute(latest_msg_query)
    latest_msg_rows = latest_msg_result.all()

    # Build a map of thread_id -> latest message text (first occurrence per thread_id due to ORDER BY)
    latest_text_map: dict[str, str] = {}
    for row in latest_msg_rows:
        if row[0] not in latest_text_map:
            latest_text_map[row[0]] = row[1] or ""

    # Build thread summaries
    threads: list[ThreadSummary] = []
    for row in thread_rows:
        username, avatar = user_map.get(row.other_user_id, (None, None))
        avatar_url: str | None = None
        if avatar:
            avatar_url = f"{settings.IMAGE_BASE_URL}/images/avatars/{avatar}"

        subject = _strip_re_prefix(row.subject or "")
        preview_text = latest_text_map.get(row.thread_id, "")
        if len(preview_text) > 80:
            preview_text = preview_text[:80]

        threads.append(
            ThreadSummary(
                thread_id=row.thread_id,
                subject=subject,
                other_user_id=row.other_user_id,
                other_username=username,
                other_avatar_url=avatar_url,
                other_groups=groups_map.get(row.other_user_id, []),
                latest_message_preview=preview_text,
                latest_message_date=row.latest_date.isoformat() if row.latest_date else "",
                unread_count=row.unread_count or 0,
                message_count=row.message_count or 0,
            )
        )

    return ThreadList(
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        threads=threads,
    )


@router.get("/threads/{thread_id}", response_model=PrivmsgMessages)
async def get_thread_messages(
    thread_id: Annotated[str, Path(description="Thread ID")],
    current_user: VerifiedUser,
    db: AsyncSession = Depends(get_db),
) -> PrivmsgMessages:
    """
    Retrieve all messages in a conversation thread.

    Verifies the current user is a participant (sender or recipient of any message
    in the thread). Marks unread messages addressed to the current user as viewed.
    Returns messages ordered chronologically (oldest first).
    """
    assert current_user.user_id is not None
    uid = current_user.user_id

    # Check if thread exists
    exists_result = await db.execute(
        select(func.count()).select_from(
            select(Privmsgs.privmsg_id).where(Privmsgs.thread_id == thread_id).subquery()  # type: ignore[arg-type]
        )
    )
    thread_count = exists_result.scalar() or 0

    if thread_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")

    # Check if user is a participant in the thread
    participant_result = await db.execute(
        select(func.count()).select_from(
            select(Privmsgs.privmsg_id)
            .where(
                Privmsgs.thread_id == thread_id,  # type: ignore[arg-type]
                or_(Privmsgs.from_user_id == uid, Privmsgs.to_user_id == uid),  # type: ignore[arg-type]
            )
            .subquery()
        )
    )
    participant_count = participant_result.scalar() or 0

    if participant_count == 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this thread",
        )

    # Mark unread messages addressed to the current user as viewed
    await db.execute(
        update(Privmsgs)
        .where(
            Privmsgs.thread_id == thread_id,  # type: ignore[arg-type]
            Privmsgs.to_user_id == uid,  # type: ignore[arg-type]
            Privmsgs.viewed == 0,  # type: ignore[arg-type]
        )
        .values(viewed=1)
    )
    await db.commit()

    # Fetch all messages in thread, excluding soft-deleted ones for this user
    # A message is visible if:
    # - User is sender and from_del == 0, OR
    # - User is recipient and to_del == 0
    visible_filter = or_(
        and_(Privmsgs.from_user_id == uid, Privmsgs.from_del == 0),  # type: ignore[arg-type]
        and_(Privmsgs.to_user_id == uid, Privmsgs.to_del == 0),  # type: ignore[arg-type]
    )

    # Alias for sender and recipient user joins
    SenderUser = Users.__table__.alias("sender_user")
    RecipientUser = Users.__table__.alias("recipient_user")

    query = (
        select(
            Privmsgs,
            SenderUser.c.username.label("sender_username"),
            SenderUser.c.avatar.label("sender_avatar"),
            RecipientUser.c.username.label("recipient_username"),
            RecipientUser.c.avatar.label("recipient_avatar"),
        )
        .join(SenderUser, Privmsgs.from_user_id == SenderUser.c.user_id, isouter=True)
        .join(RecipientUser, Privmsgs.to_user_id == RecipientUser.c.user_id, isouter=True)
        .where(Privmsgs.thread_id == thread_id, visible_filter)
        .order_by(Privmsgs.date)  # type: ignore[arg-type]
    )

    result = await db.execute(query)
    rows = result.all()

    # Collect all participant user IDs for group lookup
    all_user_ids = set()
    for row in rows:
        msg: Privmsgs = row[0]
        if msg.from_user_id:
            all_user_ids.add(msg.from_user_id)
        if msg.to_user_id:
            all_user_ids.add(msg.to_user_id)

    groups_map = await get_user_groups_map(db, list(all_user_ids))

    messages: list[PrivmsgMessage] = []
    for row in rows:
        msg = row[0]
        sender_username = row[1]
        sender_avatar = row[2]
        recipient_username = row[3]
        recipient_avatar = row[4]

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
            "from_groups": groups_map.get(msg.from_user_id, []),
            "to_groups": groups_map.get(msg.to_user_id, []),
        }
        messages.append(PrivmsgMessage.model_validate(data))

    return PrivmsgMessages(
        total=len(messages),
        page=1,
        per_page=len(messages),
        messages=messages,
    )


@router.delete("/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def leave_thread(
    thread_id: Annotated[str, Path(description="Thread ID to leave")],
    current_user: VerifiedUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Leave a conversation thread (soft-delete all messages for the current user).

    Sets from_del=1 for messages sent by this user in the thread, and to_del=1 for
    messages received by this user. Hard-deletes any messages where both parties
    have left (from_del=1 AND to_del=1).
    """
    assert current_user.user_id is not None
    uid = current_user.user_id

    # Verify user is a participant in the thread
    participant_result = await db.execute(
        select(func.count()).select_from(
            select(Privmsgs.privmsg_id)
            .where(
                Privmsgs.thread_id == thread_id,  # type: ignore[arg-type]
                or_(Privmsgs.from_user_id == uid, Privmsgs.to_user_id == uid),  # type: ignore[arg-type]
            )
            .subquery()
        )
    )
    participant_count = participant_result.scalar() or 0

    if participant_count == 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to leave this thread",
        )

    # Set from_del=1 for messages sent by this user in the thread
    await db.execute(
        update(Privmsgs)
        .where(
            Privmsgs.thread_id == thread_id,  # type: ignore[arg-type]
            Privmsgs.from_user_id == uid,  # type: ignore[arg-type]
        )
        .values(from_del=1)
    )

    # Set to_del=1 for messages received by this user in the thread
    await db.execute(
        update(Privmsgs)
        .where(
            Privmsgs.thread_id == thread_id,  # type: ignore[arg-type]
            Privmsgs.to_user_id == uid,  # type: ignore[arg-type]
        )
        .values(to_del=1)
    )

    # Hard-delete messages where both parties have left
    await db.execute(
        delete(Privmsgs).where(
            Privmsgs.thread_id == thread_id,  # type: ignore[arg-type]
            Privmsgs.from_del == 1,  # type: ignore[arg-type]
            Privmsgs.to_del == 1,  # type: ignore[arg-type]
        )
    )

    await db.commit()


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
        if not await has_permission(
            db, current_user.user_id, Permission.PRIVMSG_VIEW, redis_client
        ):
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

    # Collect sender user IDs to fetch groups in a single query
    sender_user_ids = [row[0].from_user_id for row in rows if row[0].from_user_id]
    groups_map = await get_user_groups_map(db, sender_user_ids)

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
            "from_groups": groups_map.get(msg.from_user_id, []),
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
        if not await has_permission(
            db, current_user.user_id, Permission.PRIVMSG_VIEW, redis_client
        ):
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

    # Collect recipient user IDs to fetch groups in a single query
    recipient_user_ids = [row[0].to_user_id for row in rows if row[0].to_user_id]
    groups_map = await get_user_groups_map(db, recipient_user_ids)

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
            "to_groups": groups_map.get(msg.to_user_id, []),
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

    # Fetch groups for both sender and recipient
    user_ids = [uid for uid in [msg.from_user_id, msg.to_user_id] if uid]
    groups_map = await get_user_groups_map(db, user_ids)

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
        "from_groups": groups_map.get(msg.from_user_id, []),
        "to_groups": groups_map.get(msg.to_user_id, []),
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
