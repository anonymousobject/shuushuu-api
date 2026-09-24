"""Concurrent PATCH /images/{id} requests on one image serialize.

Minor 1 of the image-metadata-history final review
(.superpowers/sdd/2026-09-24-image-info-editing-impl/final-review.md): without
a row lock, two requests in flight together can both read the same
pre-update miscmeta/source_url and both write a history row, and the second
row's old_value is stale. The fix is `.with_for_update()` on the image load
in `update_image` (app/api/v1/images.py) so the second request blocks until
the first commits, then diffs against the now-committed value.

Real cross-session concurrency, like test_db_retry_deadlock.py: the request
under test calls `update_image()` directly rather than through the `client`
fixture, because that fixture's `app` override binds every request in a test
to ONE shared AsyncSession (tests/conftest.py `app` fixture) — two
"concurrent" `client.patch()` calls would run on the same connection and
transaction, so they could never contend for the row lock at all.

A first session runs the real `update_image()` and is paused, via a patched
`db.commit`, after it has taken the row lock (the SELECT ... FOR UPDATE) but
before it releases it. A second session then runs the real `update_image()`
against the same image, with its `db.execute` wrapped only to flag the
moment its own SELECT ... FOR UPDATE is dispatched. Once that's dispatched,
the test asserts the second call has NOT finished within a short window —
proving it is genuinely blocked on the first side's row lock, not just slow —
before releasing the first side and letting both finish.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.images import update_image
from app.models.image import Images
from app.models.image_metadata_history import ImageMetadataHistory
from app.models.user import Users
from app.schemas.image import ImageUpdate

pytestmark = [pytest.mark.integration, pytest.mark.needs_commit]


async def test_second_patch_diffs_against_first_patchs_committed_value(db_session, engine):
    owner_result = await db_session.execute(select(Users).where(Users.user_id == 1))
    owner = owner_result.scalar_one()

    image = Images(
        filename="test-concurrent-patch",
        ext="jpg",
        original_filename="test.jpg",
        md5_hash="11112222333344445555666677778888",
        filesize=100000,
        width=800,
        height=600,
        caption="original caption",
        miscmeta="orig",
        user_id=owner.user_id,
        status=1,
    )
    db_session.add(image)
    await db_session.commit()
    await db_session.refresh(image)

    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    mock_redis = AsyncMock()
    first_paused_holding_lock = asyncio.Event()
    release_first = asyncio.Event()
    second_select_dispatched = asyncio.Event()

    async def first_patch() -> None:
        async with sessions() as db:
            real_commit = db.commit

            async def commit_after_release() -> None:
                # update_image has already issued its SELECT ... FOR UPDATE
                # (and queued its setattr updates) by the time it calls
                # commit; the row lock is held from here until real_commit().
                first_paused_holding_lock.set()
                await release_first.wait()
                await real_commit()

            db.commit = commit_after_release  # type: ignore[method-assign]
            await update_image(
                image.image_id, ImageUpdate(miscmeta="from-first"), owner, db, mock_redis
            )

    async def second_patch() -> None:
        await first_paused_holding_lock.wait()
        async with sessions() as db:
            real_execute = db.execute

            async def execute_and_flag_first_call(*args: object, **kwargs: object):
                # The first call is update_image's own SELECT ... FOR UPDATE;
                # flag it right before sending it so the test knows the
                # second side is now (about to be) waiting on Postgres, not
                # on Python-level scheduling.
                if not second_select_dispatched.is_set():
                    second_select_dispatched.set()
                return await real_execute(*args, **kwargs)

            db.execute = execute_and_flag_first_call  # type: ignore[method-assign]
            await update_image(
                image.image_id, ImageUpdate(miscmeta="from-second"), owner, db, mock_redis
            )

    first_task = asyncio.create_task(first_patch())
    second_task = asyncio.create_task(second_patch())

    await second_select_dispatched.wait()
    done, pending = await asyncio.wait([second_task], timeout=0.3)
    assert second_task in pending, (
        "second update_image() completed without blocking on the first's row lock"
    )

    release_first.set()
    await asyncio.gather(first_task, second_task)

    async with sessions() as db:
        result = await db.execute(
            select(ImageMetadataHistory)
            .where(ImageMetadataHistory.image_id == image.image_id)
            .order_by(ImageMetadataHistory.id)
        )
        rows = result.scalars().all()

    # Serialized: the second row's old_value is the first row's new_value,
    # not the original "orig" both requests read at their own select time.
    assert [(r.old_value, r.new_value) for r in rows] == [
        ("orig", "from-first"),
        ("from-first", "from-second"),
    ]
