"""Forum API endpoints: categories, threads, posts."""

from datetime import UTC, datetime
from typing import Annotated

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import PaginationParams
from app.core.auth import CurrentUser, OptionalCurrentUser
from app.core.database import get_db
from app.core.permission_cache import get_cached_user_permissions
from app.core.permission_deps import require_permission
from app.core.permissions import Permission
from app.core.redis import get_redis
from app.core.user_loader import build_user_summaries
from app.models.forum import ForumCategories, ForumPosts, ForumThreadReads, ForumThreads
from app.models.user import Users
from app.schemas.common import UserSummary
from app.schemas.forum import (
    ForumCategoryCreate,
    ForumCategoryListResponse,
    ForumCategoryResponse,
    ForumCategoryUpdate,
    ForumPostResponse,
    ForumThreadCreate,
    ForumThreadDetailResponse,
    ForumThreadListResponse,
    ForumThreadSummary,
    ForumThreadUpdate,
)
from app.services.forum import can_access, upsert_thread_read

router = APIRouter(prefix="/forum", tags=["forum"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[redis.Redis, Depends(get_redis)]


# ===== Shared helpers =====


async def _effective_perms(
    db: AsyncSession,
    redis_client: redis.Redis,  # type: ignore[type-arg]
    user: Users | None,
) -> set[str]:
    """Resolve the caller's permission set; empty for anonymous callers."""
    if user is None or user.user_id is None:
        return set()
    return await get_cached_user_permissions(db, redis_client, user.user_id)


async def _visible_category(db: AsyncSession, category_id: int, perms: set[str]) -> ForumCategories:
    """Load a category the caller may view; 404 (not 403) otherwise so gated
    categories don't leak existence."""
    category = await db.get(ForumCategories, category_id)
    if category is None or not can_access(perms, category.view_perm):
        raise HTTPException(status_code=404, detail="Category not found")
    return category


async def _first_post_id(db: AsyncSession, thread_id: int) -> int | None:
    """post_id of a thread's opening post (min post_id; opening posts can't
    be deleted alone, so this is stable)."""
    result = await db.execute(
        select(func.min(ForumPosts.post_id)).where(
            ForumPosts.thread_id == thread_id  # type: ignore[arg-type]
        )
    )
    return result.scalar()


def _thread_summary(
    thread: ForumThreads, summaries: dict[int, UserSummary], unread: bool
) -> ForumThreadSummary:
    return ForumThreadSummary(
        thread_id=thread.thread_id or 0,  # guaranteed to exist after flush/refresh
        category_id=thread.category_id,
        title=thread.title,
        user=summaries[thread.user_id],
        date=thread.date,
        pinned=thread.pinned,
        locked=thread.locked,
        deleted=thread.deleted,
        post_count=thread.post_count,
        last_post_at=thread.last_post_at,
        last_post_user=(
            summaries.get(thread.last_post_user_id) if thread.last_post_user_id else None
        ),
        unread=unread,
    )


def _post_response(post: ForumPosts, user: UserSummary, is_moderator: bool) -> ForumPostResponse:
    """Build a post response; tombstoned posts have their text blanked for
    callers without FORUM_MODERATE."""
    return ForumPostResponse(
        post_id=post.post_id or 0,  # guaranteed to exist after flush/refresh
        thread_id=post.thread_id,
        user_id=post.user_id,
        post_text="" if post.deleted and not is_moderator else post.post_text,
        date=post.date,
        deleted=post.deleted,
        update_count=post.update_count,
        last_updated=post.last_updated,
        last_updated_user_id=post.last_updated_user_id,
        user=user,
    )


def _category_response(
    category: ForumCategories,
    *,
    thread_count: int = 0,
    post_count: int = 0,
    last_post_at: datetime | None = None,
    last_thread_id: int | None = None,
    last_thread_title: str | None = None,
    last_post_user: UserSummary | None = None,
    can_create_thread: bool = False,
    can_reply: bool = False,
) -> ForumCategoryResponse:
    return ForumCategoryResponse(
        category_id=category.category_id or 0,  # guaranteed to exist after flush/refresh
        title=category.title,
        description=category.description,
        sort_order=category.sort_order,
        view_perm=category.view_perm,
        thread_create_perm=category.thread_create_perm,
        reply_perm=category.reply_perm,
        thread_count=thread_count,
        post_count=post_count,
        last_post_at=last_post_at,
        last_thread_id=last_thread_id,
        last_thread_title=last_thread_title,
        last_post_user=last_post_user,
        can_create_thread=can_create_thread,
        can_reply=can_reply,
    )


async def _check_duplicate_title(
    db: AsyncSession, title: str, exclude_category_id: int | None = None
) -> None:
    query = select(ForumCategories).where(
        ForumCategories.title == title  # type: ignore[arg-type]
    )
    if exclude_category_id is not None:
        query = query.where(
            ForumCategories.category_id != exclude_category_id  # type: ignore[arg-type]
        )
    existing = (await db.execute(query)).scalars().first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="A category with this title already exists")


# ===== Categories =====


@router.get("/categories", response_model=ForumCategoryListResponse)
async def list_categories(
    db: DbDep,
    redis_client: RedisDep,  # type: ignore[type-arg]
    current_user: OptionalCurrentUser,
) -> ForumCategoryListResponse:
    """List categories the caller may view, with stats and capabilities."""
    perms = await _effective_perms(db, redis_client, current_user)
    result = await db.execute(
        select(ForumCategories).order_by(
            ForumCategories.sort_order,  # type: ignore[arg-type]
            ForumCategories.category_id,  # type: ignore[arg-type]
        )
    )
    categories = [c for c in result.scalars().all() if can_access(perms, c.view_perm)]

    # Thread/post counts per category (live threads only)
    stats_rows = await db.execute(
        select(  # type: ignore[call-overload]
            ForumThreads.category_id,
            func.count(),
            func.coalesce(func.sum(ForumThreads.post_count), 0),
        )
        .where(ForumThreads.deleted == False)  # noqa: E712
        .group_by(ForumThreads.category_id)
    )
    stats = {cid: (threads, int(posts)) for cid, threads, posts in stats_rows.all()}

    # Latest-activity thread per category
    rn = (
        func.row_number()
        .over(
            partition_by=ForumThreads.category_id,  # type: ignore[arg-type]
            order_by=(
                ForumThreads.last_post_at.desc(),  # type: ignore[union-attr]
                ForumThreads.thread_id.desc(),  # type: ignore[union-attr]
            ),
        )
        .label("rn")
    )
    latest_sq = (
        select(  # type: ignore[call-overload]
            ForumThreads.category_id,
            ForumThreads.thread_id,
            ForumThreads.title,
            ForumThreads.last_post_at,
            ForumThreads.last_post_user_id,
            rn,
        )
        .where(ForumThreads.deleted == False)  # noqa: E712
        .subquery()
    )
    latest_rows = (await db.execute(select(latest_sq).where(latest_sq.c.rn == 1))).all()
    latest = {row.category_id: row for row in latest_rows}

    user_ids = {row.last_post_user_id for row in latest_rows if row.last_post_user_id}
    summaries = await build_user_summaries(db, user_ids)

    authed = current_user is not None
    entries = []
    for c in categories:
        thread_count, post_count = stats.get(c.category_id, (0, 0))
        last = latest.get(c.category_id)
        entries.append(
            _category_response(
                c,
                thread_count=thread_count,
                post_count=post_count,
                last_post_at=last.last_post_at if last else None,
                last_thread_id=last.thread_id if last else None,
                last_thread_title=last.title if last else None,
                last_post_user=(
                    summaries.get(last.last_post_user_id)
                    if last and last.last_post_user_id
                    else None
                ),
                can_create_thread=authed and can_access(perms, c.thread_create_perm),
                can_reply=authed and can_access(perms, c.reply_perm),
            )
        )
    return ForumCategoryListResponse(categories=entries)


@router.post(
    "/categories", response_model=ForumCategoryResponse, status_code=status.HTTP_201_CREATED
)
async def create_category(
    body: ForumCategoryCreate,
    current_user: CurrentUser,
    db: DbDep,
    _: Annotated[None, Depends(require_permission(Permission.FORUM_CATEGORY_MANAGE))],
) -> ForumCategoryResponse:
    """Create a category. Requires FORUM_CATEGORY_MANAGE."""
    await _check_duplicate_title(db, body.title)
    category = ForumCategories(**body.model_dump())
    db.add(category)
    await db.commit()
    await db.refresh(category)
    return _category_response(category)


@router.patch("/categories/{category_id}", response_model=ForumCategoryResponse)
async def update_category(
    category_id: int,
    body: ForumCategoryUpdate,
    current_user: CurrentUser,
    db: DbDep,
    _: Annotated[None, Depends(require_permission(Permission.FORUM_CATEGORY_MANAGE))],
) -> ForumCategoryResponse:
    """Update a category. Only provided fields change. Requires FORUM_CATEGORY_MANAGE."""
    category = await db.get(ForumCategories, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")

    updates = body.model_dump(exclude_unset=True)
    if "title" in updates:
        await _check_duplicate_title(db, updates["title"], exclude_category_id=category_id)
    for field, value in updates.items():
        setattr(category, field, value)
    await db.commit()
    await db.refresh(category)
    return _category_response(category)


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: int,
    current_user: CurrentUser,
    db: DbDep,
    _: Annotated[None, Depends(require_permission(Permission.FORUM_CATEGORY_MANAGE))],
) -> None:
    """Delete an empty category. 409 if it has any threads (even soft-deleted)."""
    category = await db.get(ForumCategories, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")

    thread_count = (
        await db.execute(
            select(func.count())
            .select_from(ForumThreads)
            .where(ForumThreads.category_id == category_id)  # type: ignore[arg-type]
        )
    ).scalar() or 0
    if thread_count:
        raise HTTPException(status_code=409, detail="Category has threads and cannot be deleted")
    await db.delete(category)
    await db.commit()


# ===== Threads =====


@router.get("/categories/{category_id}/threads", response_model=ForumThreadListResponse)
async def list_threads(
    category_id: int,
    pagination: Annotated[PaginationParams, Depends()],
    db: DbDep,
    redis_client: RedisDep,  # type: ignore[type-arg]
    current_user: OptionalCurrentUser,
) -> ForumThreadListResponse:
    """List live threads in a category: pinned first, then by last activity."""
    perms = await _effective_perms(db, redis_client, current_user)
    await _visible_category(db, category_id, perms)

    base = (
        select(ForumThreads)
        .where(ForumThreads.category_id == category_id)  # type: ignore[arg-type]
        .where(ForumThreads.deleted == False)  # type: ignore[arg-type]  # noqa: E712
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    rows = await db.execute(
        base.order_by(
            ForumThreads.pinned.desc(),  # type: ignore[attr-defined]
            ForumThreads.last_post_at.desc(),  # type: ignore[union-attr]
        )
        .offset(pagination.offset)
        .limit(pagination.per_page)
    )
    threads = list(rows.scalars().all())

    user_ids = {t.user_id for t in threads} | {
        t.last_post_user_id for t in threads if t.last_post_user_id
    }
    summaries = await build_user_summaries(db, user_ids)

    read_map: dict[int, datetime] = {}
    if current_user is not None and threads:
        read_rows = await db.execute(
            select(  # type: ignore[call-overload]
                ForumThreadReads.thread_id, ForumThreadReads.last_read_at
            )
            .where(ForumThreadReads.user_id == current_user.user_id)
            .where(ForumThreadReads.thread_id.in_([t.thread_id for t in threads]))  # type: ignore[union-attr]
        )
        read_map = dict(read_rows.all())  # type: ignore[arg-type]

    def is_unread(t: ForumThreads) -> bool:
        if current_user is None or t.last_post_at is None:
            return False
        last_read = read_map.get(t.thread_id or 0)
        return last_read is None or last_read < t.last_post_at

    return ForumThreadListResponse(
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        threads=[_thread_summary(t, summaries, is_unread(t)) for t in threads],
    )


@router.post(
    "/categories/{category_id}/threads",
    response_model=ForumThreadSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_thread(
    category_id: int,
    body: ForumThreadCreate,
    request: Request,
    current_user: CurrentUser,
    db: DbDep,
    redis_client: RedisDep,  # type: ignore[type-arg]
) -> ForumThreadSummary:
    """Create a thread with its opening post in one transaction."""
    assert current_user.user_id is not None
    perms = await _effective_perms(db, redis_client, current_user)
    category = await _visible_category(db, category_id, perms)
    if not can_access(perms, category.thread_create_perm):
        raise HTTPException(status_code=403, detail="You cannot create threads in this category")

    thread = ForumThreads(
        category_id=category.category_id, title=body.title, user_id=current_user.user_id
    )
    db.add(thread)
    await db.flush()
    post = ForumPosts(
        thread_id=thread.thread_id,
        user_id=current_user.user_id,
        post_text=body.post_text,
        ip=request.client.host if request.client else "",
    )
    db.add(post)
    await db.flush()
    await db.refresh(post)
    thread.post_count = 1
    thread.last_post_at = post.date
    thread.last_post_user_id = current_user.user_id
    # The author has obviously read their own thread
    await upsert_thread_read(db, current_user.user_id, thread.thread_id or 0, post.date)
    await db.commit()
    await db.refresh(thread)

    summaries = await build_user_summaries(db, {current_user.user_id})
    return _thread_summary(thread, summaries, unread=False)


@router.get("/threads/{thread_id}", response_model=ForumThreadDetailResponse)
async def get_thread(
    thread_id: int,
    pagination: Annotated[PaginationParams, Depends()],
    db: DbDep,
    redis_client: RedisDep,  # type: ignore[type-arg]
    current_user: OptionalCurrentUser,
) -> ForumThreadDetailResponse:
    """Thread meta + one page of posts (chronological). Marks the thread read
    for authenticated callers."""
    perms = await _effective_perms(db, redis_client, current_user)
    is_moderator = Permission.FORUM_MODERATE.value in perms

    thread = await db.get(ForumThreads, thread_id)
    if thread is None or (thread.deleted and not is_moderator):
        raise HTTPException(status_code=404, detail="Thread not found")
    category = await _visible_category(db, thread.category_id, perms)

    total = (
        await db.execute(
            select(func.count()).select_from(ForumPosts).where(ForumPosts.thread_id == thread_id)  # type: ignore[arg-type]
        )
    ).scalar() or 0
    posts = (
        (
            await db.execute(
                select(ForumPosts)
                .where(ForumPosts.thread_id == thread_id)  # type: ignore[arg-type]
                .order_by(ForumPosts.post_id)  # type: ignore[arg-type]
                .offset(pagination.offset)
                .limit(pagination.per_page)
            )
        )
        .scalars()
        .all()
    )

    user_ids = {p.user_id for p in posts} | {thread.user_id}
    if thread.last_post_user_id:
        user_ids.add(thread.last_post_user_id)
    summaries = await build_user_summaries(db, user_ids)

    if current_user is not None and current_user.user_id is not None:
        await upsert_thread_read(db, current_user.user_id, thread_id, datetime.now(UTC))
        await db.commit()

    return ForumThreadDetailResponse(
        thread=_thread_summary(thread, summaries, unread=False),
        can_reply=current_user is not None and can_access(perms, category.reply_perm),
        can_moderate=is_moderator,
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        posts=[_post_response(p, summaries[p.user_id], is_moderator) for p in posts],
    )


@router.patch("/threads/{thread_id}", response_model=ForumThreadSummary)
async def update_thread(
    thread_id: int,
    body: ForumThreadUpdate,
    current_user: CurrentUser,
    db: DbDep,
    redis_client: RedisDep,  # type: ignore[type-arg]
) -> ForumThreadSummary:
    """title: author or FORUM_MODERATE. pinned/locked/category_id/deleted:
    FORUM_MODERATE only."""
    assert current_user.user_id is not None
    perms = await _effective_perms(db, redis_client, current_user)
    is_moderator = Permission.FORUM_MODERATE.value in perms

    thread = await db.get(ForumThreads, thread_id)
    if thread is None or (thread.deleted and not is_moderator):
        raise HTTPException(status_code=404, detail="Thread not found")
    await _visible_category(db, thread.category_id, perms)

    updates = body.model_dump(exclude_unset=True)
    mod_fields = {"pinned", "locked", "category_id", "deleted"} & updates.keys()
    if mod_fields and not is_moderator:
        raise HTTPException(status_code=403, detail="FORUM_MODERATE permission required")
    if "title" in updates and not (is_moderator or thread.user_id == current_user.user_id):
        raise HTTPException(
            status_code=403,
            detail="Only the thread author or a moderator can edit the title",
        )
    if "category_id" in updates:
        target = await db.get(ForumCategories, updates["category_id"])
        if target is None:
            raise HTTPException(status_code=400, detail="Target category does not exist")

    for field, value in updates.items():
        setattr(thread, field, value)
    await db.commit()
    await db.refresh(thread)

    user_ids = {thread.user_id}
    if thread.last_post_user_id:
        user_ids.add(thread.last_post_user_id)
    summaries = await build_user_summaries(db, user_ids)
    return _thread_summary(thread, summaries, unread=False)


@router.delete("/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(
    thread_id: int,
    current_user: CurrentUser,
    db: DbDep,
    redis_client: RedisDep,  # type: ignore[type-arg]
) -> None:
    """Soft-delete. Author allowed only while the thread has no replies;
    otherwise FORUM_MODERATE."""
    assert current_user.user_id is not None
    perms = await _effective_perms(db, redis_client, current_user)
    is_moderator = Permission.FORUM_MODERATE.value in perms

    thread = await db.get(ForumThreads, thread_id)
    if thread is None or (thread.deleted and not is_moderator):
        raise HTTPException(status_code=404, detail="Thread not found")
    await _visible_category(db, thread.category_id, perms)

    if not is_moderator:
        if thread.user_id != current_user.user_id:
            raise HTTPException(
                status_code=403, detail="Only the author or a moderator can delete this thread"
            )
        if thread.post_count > 1:
            raise HTTPException(
                status_code=403,
                detail="Threads with replies can only be deleted by moderators",
            )
    thread.deleted = True
    await db.commit()
