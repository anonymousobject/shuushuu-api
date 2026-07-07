"""Import the retired phpBB3 forum into the new forum tables.

Reads two live source databases (never .sql): the phpBB dump and the legacy
php_shuu dump. Converts s9e post XML to markdown, resolves posters to current
accounts, rehosts attachments, and inserts categories/threads/posts as locked,
tier-gated threads. Idempotent via legacy_* unique keys.

Usage:
    uv run python -m scripts.import_forum_archive [--dry-run] [--remap] \
        [--phpbb-url URL] [--site-url URL]
"""

import argparse
import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine

from app.core.archived_user import ensure_archived_user
from app.core.database import AsyncSessionLocal
from app.models.forum import ForumCategories, ForumPosts, ForumThreads
from app.services.forum_import.attachments import forum_attachment_url, rehost_attachment
from app.services.forum_import.s9e_convert import s9e_to_markdown
from app.services.forum_import.user_map import PosterResolution, resolve_posters

DEFAULT_PHPBB_URL = "mysql+aiomysql://root:{pw}@localhost:3306/shuushuuphpbb3"
DEFAULT_SITE_URL = "mysql+aiomysql://root:{pw}@localhost:3306/php_shuu"
BACKUP_FILES_DIR = Path("/sakura/backups/forums-2026-02-20/files")

# phpBB forum_id → access-tier permission (others public/NULL)
TIER_BY_FORUM_ID = {7: "forum_access_staff", 10: "forum_access_tagger"}


@dataclass
class ImportStats:
    categories: int = 0
    threads: int = 0
    posts: int = 0
    resolved: int = 0
    archived: int = 0
    attachments: int = 0
    skipped_existing: int = 0
    remapped: int = 0
    notes: list[str] = field(default_factory=list)


async def _build_resolution(
    target: AsyncSession, phpbb_url: str, legacy_url: str
) -> dict[int, PosterResolution]:
    phpbb = create_async_engine(phpbb_url)
    legacy = create_async_engine(legacy_url)
    try:
        async with phpbb.connect() as pc:
            posters = {
                int(r[0]): (r[1], (r[2] or "").lower().strip())
                for r in await pc.execute(
                    text(
                        "SELECT u.user_id, u.username, u.user_email FROM phpbb_users u "
                        "JOIN (SELECT DISTINCT poster_id FROM phpbb_posts WHERE poster_id>1) p "
                        "ON p.poster_id=u.user_id"
                    )
                )
            }
        async with legacy.connect() as lc:
            forum_id_map = {
                int(r[0]): int(r[1])
                for r in await lc.execute(
                    text("SELECT forum_id, user_id FROM users WHERE forum_id IS NOT NULL AND forum_id>0")
                )
            }
    finally:
        await phpbb.dispose()
        await legacy.dispose()

    rows = await target.execute(text("SELECT user_id, LOWER(email) FROM users"))
    target_user_ids: set[int] = set()
    target_email_to_id: dict[str, int] = {}
    for uid, email in rows:
        target_user_ids.add(int(uid))
        if email:
            target_email_to_id[email.strip()] = int(uid)
    return resolve_posters(posters, forum_id_map, target_user_ids, target_email_to_id)


async def _post_attachment_links(
    pc: AsyncConnection, post_id: int, backup_dir: Path, stats: ImportStats
) -> str:
    rows = list(
        await pc.execute(
            text(
                "SELECT physical_filename, real_filename, mimetype FROM phpbb_attachments "
                "WHERE post_msg_id=:pid AND is_orphan=0 AND in_message=0 ORDER BY attach_id"
            ),
            {"pid": post_id},
        )
    )
    lines = []
    for physical, real, mime in rows:
        src = backup_dir / physical
        if src.exists():
            await rehost_attachment(physical, src, mime or "application/octet-stream")
            lines.append(f"\U0001F4CE [{real}]({forum_attachment_url(physical)})")
            stats.attachments += 1
        else:
            stats.notes.append(f"missing attachment file {physical} (post {post_id})")
    return ("\n\n" + "\n".join(lines)) if lines else ""


async def run_import(
    target: AsyncSession,
    phpbb_url: str,
    legacy_url: str,
    backup_files_dir: Path,
    *,
    only_forum_ids: set[int] | None = None,
    dry_run: bool = False,
    remap_only: bool = False,
) -> ImportStats:
    stats = ImportStats()
    resolution = await _build_resolution(target, phpbb_url, legacy_url)
    archived_id = await ensure_archived_user(target)

    def author_of(poster_id: int) -> tuple[int, str]:
        res = resolution.get(poster_id)
        if res is None:  # e.g. guest (poster_id=1) — attribute to Archived
            return archived_id, "Guest"
        if res.site_user_id is None:
            return archived_id, res.legacy_username
        return res.site_user_id, res.legacy_username

    if remap_only:
        for phpbb_id, res in resolution.items():
            uid = res.site_user_id if res.site_user_id is not None else archived_id
            r = await target.execute(
                text("UPDATE forum_posts SET user_id=:u WHERE legacy_poster_id=:p"),
                {"u": uid, "p": phpbb_id},
            )
            stats.remapped += r.rowcount or 0  # type: ignore[attr-defined]
            await target.execute(
                text("UPDATE forum_threads SET user_id=:u WHERE legacy_topic_id IN "
                     "(SELECT DISTINCT thread_id FROM forum_posts WHERE legacy_poster_id=:p) "
                     "AND user_id<>:u"),
                {"u": uid, "p": phpbb_id},
            )
        if not dry_run:
            await target.commit()
        return stats

    phpbb = create_async_engine(phpbb_url)
    try:
        async with phpbb.connect() as pc:
            forums = list(
                await pc.execute(
                    text(
                        "SELECT forum_id, forum_name, forum_desc, left_id FROM phpbb_forums "
                        "WHERE forum_type=1 ORDER BY left_id"
                    )
                )
            )
            for fid, fname, fdesc, left_id in forums:
                if only_forum_ids is not None and fid not in only_forum_ids:
                    continue
                cat = await _upsert_category(target, fid, fname, fdesc, left_id, stats, dry_run)
                topics = list(
                    await pc.execute(
                        text(
                            "SELECT topic_id, topic_title, topic_time FROM phpbb_topics "
                            "WHERE forum_id=:fid AND topic_visibility=1 ORDER BY topic_time"
                        ),
                        {"fid": fid},
                    )
                )
                for tid, title, ttime in topics:
                    await _import_topic(
                        target, pc, cat, tid, title, ttime, author_of,
                        backup_files_dir, stats, dry_run,
                    )
    finally:
        await phpbb.dispose()

    if dry_run:
        await target.rollback()

    for res in resolution.values():
        if res.site_user_id is None:
            stats.archived += 1
        else:
            stats.resolved += 1
    return stats


async def _upsert_category(
    target: AsyncSession,
    fid: int,
    fname: str,
    fdesc: str | None,
    left_id: int,
    stats: ImportStats,
    dry_run: bool,
) -> ForumCategories:
    existing = (
        await target.execute(
            select(ForumCategories).where(
                ForumCategories.legacy_forum_id == fid  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    tier = TIER_BY_FORUM_ID.get(fid)
    cat = ForumCategories(
        title=fname,
        description=fdesc or None,
        sort_order=int(left_id),
        legacy_forum_id=fid,
        view_perm=tier,
        thread_create_perm=tier,
        reply_perm=tier,
    )
    target.add(cat)
    await target.flush()
    stats.categories += 1
    return cat


async def _import_topic(
    target: AsyncSession,
    pc: AsyncConnection,
    cat: ForumCategories,
    tid: int,
    title: str,
    ttime: int,
    author_of: Callable[[int], tuple[int, str]],
    backup_dir: Path,
    stats: ImportStats,
    dry_run: bool,
) -> None:
    if (
        await target.execute(
            select(ForumThreads.thread_id).where(  # type: ignore[call-overload]
                ForumThreads.legacy_topic_id == tid
            )
        )
    ).scalar_one_or_none() is not None:
        stats.skipped_existing += 1
        return
    posts = list(
        await pc.execute(
            text(
                "SELECT post_id, poster_id, post_time, post_text FROM phpbb_posts "
                "WHERE topic_id=:tid AND post_visibility=1 ORDER BY post_time, post_id"
            ),
            {"tid": tid},
        )
    )
    if not posts:
        return
    first_uid, _ = author_of(int(posts[0][1]))
    thread = ForumThreads(
        category_id=cat.category_id,
        title=title[:255],
        user_id=first_uid,
        date=datetime.fromtimestamp(int(ttime), UTC),
        locked=True,
        pinned=False,
        legacy_topic_id=int(tid),
    )
    target.add(thread)
    await target.flush()
    stats.threads += 1

    last_at = None
    last_uid = first_uid
    for post_id, poster_id, ptime, ptext in posts:
        uid, legacy_name = author_of(int(poster_id))
        body = s9e_to_markdown(ptext or "")
        body += await _post_attachment_links(pc, int(post_id), backup_dir, stats)
        pdate = datetime.fromtimestamp(int(ptime), UTC)
        target.add(
            ForumPosts(
                thread_id=thread.thread_id,
                user_id=uid,
                post_text=body.strip() or "(empty)",
                date=pdate,
                legacy_post_id=int(post_id),
                legacy_poster_id=int(poster_id),
                legacy_username=legacy_name,
            )
        )
        stats.posts += 1
        last_at, last_uid = pdate, uid
    await target.flush()
    thread.post_count = len(posts)
    thread.last_post_at = last_at
    thread.last_post_user_id = last_uid
    # Real runs commit per topic (idempotent + resumable). Dry runs stay in one
    # transaction and are rolled back wholesale at the end of run_import.
    if not dry_run:
        await target.commit()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--remap", action="store_true")
    ap.add_argument("--phpbb-url", default=None)
    ap.add_argument("--site-url", default=None)
    args = ap.parse_args()
    import os

    pw = os.environ.get("MARIADB_ROOT_PASSWORD", "root_password")
    phpbb_url = args.phpbb_url or DEFAULT_PHPBB_URL.format(pw=pw)
    legacy_url = args.site_url or DEFAULT_SITE_URL.format(pw=pw)

    async with AsyncSessionLocal() as session:
        stats = await run_import(
            session, phpbb_url, legacy_url, BACKUP_FILES_DIR,
            dry_run=args.dry_run, remap_only=args.remap,
        )
    print(stats)


if __name__ == "__main__":
    asyncio.run(main())
