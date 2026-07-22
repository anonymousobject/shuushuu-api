"""Shared fixtures for forum API tests.

Test personas (users 1-3 are pre-seeded by the root conftest):
- user 1 "testuser": content author
- user 2 "testuser2": privileged (grants stacked per fixture)
- user 3 "testuser3": plain authenticated user, no forum permissions
- user 4 "testtagger": FORUM_ACCESS_TAGGER only (created here)
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import Permission
from app.core.security import create_access_token
from app.models.forum import ForumCategories, ForumPosts, ForumThreads
from app.models.permissions import Perms, UserPerms
from app.models.user import Users


async def grant_permission(
    db_session: AsyncSession, user_id: int, permission: Permission
) -> str:
    """Grant a permission to a user via user_perms and return a bearer token.

    Looks up the Perms row by title first — sync_permissions() has already
    seeded all enum permissions at session setup.
    """
    user = await db_session.get(Users, user_id)
    user.active = 1
    result = await db_session.execute(select(Perms).where(Perms.title == permission.value))
    perm = result.scalars().first()
    if perm is None:
        perm = Perms(title=permission.value, desc=permission.description)
        db_session.add(perm)
        await db_session.flush()
    db_session.add(UserPerms(user_id=user_id, perm_id=perm.perm_id, permvalue=1))
    await db_session.commit()
    return create_access_token(user_id)


async def activate_user(db_session: AsyncSession, user_id: int) -> str:
    """Mark a pre-seeded user active and return a bearer token."""
    user = await db_session.get(Users, user_id)
    user.active = 1
    await db_session.commit()
    return create_access_token(user_id)


async def make_thread(
    db_session: AsyncSession,
    category: ForumCategories,
    user_id: int = 1,
    title: str = "Test thread",
    post_text: str = "Opening post",
) -> ForumThreads:
    """Create a thread + opening post with correct denormalized fields."""
    thread = ForumThreads(category_id=category.category_id, title=title, user_id=user_id)
    db_session.add(thread)
    await db_session.flush()
    post = ForumPosts(thread_id=thread.thread_id, user_id=user_id, post_text=post_text)
    db_session.add(post)
    await db_session.flush()
    await db_session.refresh(post)
    thread.post_count = 1
    thread.last_post_at = post.date
    thread.last_post_user_id = user_id
    await db_session.commit()
    await db_session.refresh(thread)
    return thread


@pytest.fixture
async def public_category(db_session: AsyncSession) -> ForumCategories:
    cat = ForumCategories(title="Site Discussion", description="General site talk", sort_order=1)
    db_session.add(cat)
    await db_session.commit()
    await db_session.refresh(cat)
    return cat


@pytest.fixture
async def announce_category(db_session: AsyncSession) -> ForumCategories:
    """Public view/reply, staff-only thread creation."""
    cat = ForumCategories(
        title="Announcements",
        sort_order=0,
        thread_create_perm=Permission.FORUM_ACCESS_STAFF.value,
    )
    db_session.add(cat)
    await db_session.commit()
    await db_session.refresh(cat)
    return cat


@pytest.fixture
async def staff_category(db_session: AsyncSession) -> ForumCategories:
    """Fully staff-gated."""
    cat = ForumCategories(
        title="Mod Board",
        sort_order=2,
        view_perm=Permission.FORUM_ACCESS_STAFF.value,
        thread_create_perm=Permission.FORUM_ACCESS_STAFF.value,
        reply_perm=Permission.FORUM_ACCESS_STAFF.value,
    )
    db_session.add(cat)
    await db_session.commit()
    await db_session.refresh(cat)
    return cat


@pytest.fixture
async def tagger_category(db_session: AsyncSession) -> ForumCategories:
    """Fully tagger-gated (staff hold FORUM_ACCESS_TAGGER too via grants)."""
    cat = ForumCategories(
        title="Tagger Board",
        sort_order=3,
        view_perm=Permission.FORUM_ACCESS_TAGGER.value,
        thread_create_perm=Permission.FORUM_ACCESS_TAGGER.value,
        reply_perm=Permission.FORUM_ACCESS_TAGGER.value,
    )
    db_session.add(cat)
    await db_session.commit()
    await db_session.refresh(cat)
    return cat


@pytest.fixture
async def public_thread(db_session: AsyncSession, public_category: ForumCategories) -> ForumThreads:
    return await make_thread(db_session, public_category)


@pytest.fixture
async def user_token(db_session: AsyncSession) -> str:
    """Plain authenticated user (user 3), no forum permissions."""
    return await activate_user(db_session, 3)


@pytest.fixture
async def author_token(db_session: AsyncSession) -> str:
    """Token for user 1, the default content author."""
    return await activate_user(db_session, 1)


@pytest.fixture
async def staff_token(db_session: AsyncSession) -> str:
    """User 2 with both access tiers + FORUM_MODERATE."""
    await grant_permission(db_session, 2, Permission.FORUM_ACCESS_STAFF)
    await grant_permission(db_session, 2, Permission.FORUM_ACCESS_TAGGER)
    return await grant_permission(db_session, 2, Permission.FORUM_MODERATE)


@pytest.fixture
async def category_manager_token(db_session: AsyncSession) -> str:
    """User 2 with FORUM_CATEGORY_MANAGE."""
    return await grant_permission(db_session, 2, Permission.FORUM_CATEGORY_MANAGE)


@pytest.fixture
async def tagger_token(db_session: AsyncSession) -> str:
    """User 4 with FORUM_ACCESS_TAGGER only."""
    user = Users(
        user_id=4,
        username="testtagger",
        password="testpassword",
        password_type="bcrypt",
        salt="testsalt00000004",
        email="tagger@example.com",
        active=1,
    )
    db_session.add(user)
    await db_session.commit()
    return await grant_permission(db_session, 4, Permission.FORUM_ACCESS_TAGGER)
