"""End-to-end import of one phpBB forum into the forum tables (real source DBs)."""

import os
from pathlib import Path

import pytest
from sqlalchemy import func, select
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
