"""End-to-end import of one phpBB forum into the forum tables (real source DBs)."""

import os
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.archived_user import get_archived_user_id
from app.models.forum import ForumCategories, ForumPosts, ForumThreads

pytestmark = pytest.mark.forum_import

ROOT = os.environ.get("MARIADB_ROOT_PASSWORD", "root_password")
PHPBB_URL = f"mysql+aiomysql://root:{ROOT}@localhost:3306/shuushuuphpbb3"
LEGACY_URL = f"mysql+aiomysql://root:{ROOT}@localhost:3306/php_shuu"
BACKUP = Path("/sakura/backups/forums-2026-02-20/files")
GAMING_FORUM_ID = 16  # small: 3 topics / 15 posts


def _sources_available() -> bool:
    return BACKUP.exists()


@pytest.mark.skipif(not _sources_available(), reason="phpBB backup files not present")
async def test_import_one_forum(db_session: AsyncSession, monkeypatch, tmp_path):
    from app.config import settings
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))  # attachments → tmp
    monkeypatch.setattr(settings, "R2_ENABLED", False)
    from scripts.import_forum_archive import run_import

    stats = await run_import(
        db_session, PHPBB_URL, LEGACY_URL, BACKUP, only_forum_ids={GAMING_FORUM_ID}
    )
    assert stats.categories == 1
    assert stats.threads == 3
    assert stats.posts == 15

    cat = (
        await db_session.execute(
            select(ForumCategories).where(ForumCategories.legacy_forum_id == GAMING_FORUM_ID)
        )
    ).scalar_one()
    assert cat.title == "Gaming"
    assert cat.view_perm is None  # public forum

    threads = (
        await db_session.execute(
            select(ForumThreads).where(ForumThreads.category_id == cat.category_id)
        )
    ).scalars().all()
    assert len(threads) == 3
    assert all(t.locked for t in threads)
    assert all(not t.pinned for t in threads)
    # denorm set
    for t in threads:
        assert t.post_count > 0
        assert t.last_post_at is not None

    # every post carries provenance
    posts = (await db_session.execute(select(ForumPosts))).scalars().all()
    assert all(p.legacy_post_id is not None for p in posts)
    assert all(p.legacy_poster_id is not None for p in posts)
    assert all(p.legacy_username for p in posts)
    # archived-user posts (if any) have a legacy name preserved
    archived_id = await get_archived_user_id(db_session)
    for p in posts:
        if p.user_id == archived_id:
            assert p.legacy_username


@pytest.mark.skipif(not _sources_available(), reason="phpBB backup files not present")
async def test_import_is_idempotent(db_session: AsyncSession, monkeypatch, tmp_path):
    from app.config import settings
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
    monkeypatch.setattr(settings, "R2_ENABLED", False)
    from scripts.import_forum_archive import run_import

    await run_import(db_session, PHPBB_URL, LEGACY_URL, BACKUP, only_forum_ids={GAMING_FORUM_ID})
    total1 = (await db_session.execute(select(func.count()).select_from(ForumPosts))).scalar()
    await run_import(db_session, PHPBB_URL, LEGACY_URL, BACKUP, only_forum_ids={GAMING_FORUM_ID})
    total2 = (await db_session.execute(select(func.count()).select_from(ForumPosts))).scalar()
    assert total1 == total2  # re-run created nothing new


@pytest.mark.skipif(not _sources_available(), reason="phpBB backup files not present")
async def test_dry_run_writes_nothing(db_session: AsyncSession, monkeypatch, tmp_path):
    from app.config import settings
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
    monkeypatch.setattr(settings, "R2_ENABLED", False)
    from scripts.import_forum_archive import run_import

    await run_import(
        db_session, PHPBB_URL, LEGACY_URL, BACKUP,
        only_forum_ids={GAMING_FORUM_ID}, dry_run=True,
    )

    # Nothing persisted: the Archived User row may exist (needed so dry-run
    # inserts don't FK-fail), but the forum tables themselves must be empty.
    cat_count = (
        await db_session.execute(select(func.count()).select_from(ForumCategories))
    ).scalar()
    thread_count = (
        await db_session.execute(select(func.count()).select_from(ForumThreads))
    ).scalar()
    post_count = (
        await db_session.execute(select(func.count()).select_from(ForumPosts))
    ).scalar()
    assert cat_count == 0
    assert thread_count == 0
    assert post_count == 0


@pytest.mark.skipif(not _sources_available(), reason="phpBB backup files not present")
async def test_remap_recovers_thread_author(db_session: AsyncSession, monkeypatch, tmp_path):
    from app.config import settings
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
    monkeypatch.setattr(settings, "R2_ENABLED", False)
    from scripts.import_forum_archive import run_import

    await run_import(db_session, PHPBB_URL, LEGACY_URL, BACKUP, only_forum_ids={GAMING_FORUM_ID})

    thread = (
        await db_session.execute(select(ForumThreads).order_by(ForumThreads.thread_id).limit(1))
    ).scalars().first()
    assert thread is not None
    opening_post = (
        await db_session.execute(
            select(ForumPosts)
            .where(ForumPosts.thread_id == thread.thread_id)
            .order_by(ForumPosts.post_id)
            .limit(1)
        )
    ).scalars().first()
    assert opening_post is not None

    # Corrupt the opening post's + thread's authorship to a different existing
    # user (test-DB user_id=2), simulating a bad prior remap/attribution.
    await db_session.execute(
        text("UPDATE forum_posts SET user_id=:u WHERE post_id=:pid"),
        {"u": 2, "pid": opening_post.post_id},
    )
    await db_session.execute(
        text("UPDATE forum_threads SET user_id=:u WHERE thread_id=:tid"),
        {"u": 2, "tid": thread.thread_id},
    )
    await db_session.commit()

    await run_import(
        db_session, PHPBB_URL, LEGACY_URL, BACKUP,
        only_forum_ids={GAMING_FORUM_ID}, remap_only=True,
    )

    # Test-DB users don't match the real phpBB forum_id map, so the resolved
    # author is the Archived User — proving the opening post got re-attributed
    # by legacy_poster_id and the thread's author was re-derived from it.
    archived_id = await get_archived_user_id(db_session)
    await db_session.refresh(opening_post)
    await db_session.refresh(thread)
    assert opening_post.user_id == archived_id
    assert thread.user_id == opening_post.user_id
