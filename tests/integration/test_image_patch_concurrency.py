"""Concurrent PATCH /images/{id} requests on one image serialize.

Minor 1 of the image-metadata-history final review
(.superpowers/sdd/2026-09-24-image-info-editing-impl/final-review.md): without
a row lock, two requests in flight together can both read the same
pre-update miscmeta/source_url and both write a history row, and the second
row's old_value is stale. The fix is `.with_for_update(key_share=True)`
(SELECT ... FOR NO KEY UPDATE) on the image load in `update_image`
(app/api/v1/images.py) so the second request blocks until the first commits,
then diffs against the now-committed value.

Real cross-session concurrency, like test_db_retry_deadlock.py: the request
under test calls `update_image()` directly rather than through the `client`
fixture, because that fixture's `app` override binds every request in a test
to ONE shared AsyncSession (tests/conftest.py `app` fixture) — two
"concurrent" `client.patch()` calls would run on the same connection and
transaction, so they could never contend for the row lock at all.

A first session runs the real `update_image()` and is paused, via a patched
`db.commit`, after it has taken the row lock but before it releases it. A
second session then runs the real `update_image()` against the same image,
with its `db.execute` wrapped only to flag the moment its own locking SELECT
is dispatched. Once that's dispatched,
the test asserts the second call has NOT finished within a short window —
proving it is genuinely blocked on the first side's row lock, not just slow —
before releasing the first side and letting both finish.

Every wait is bounded and every exit path releases the first side and ends
both sessions: this file needs committed rows, and the needs_commit
teardown's TRUNCATE waits forever on a session left holding its transaction
open. With no pytest-timeout and no CI job timeout, a regressed lock would
otherwise hang the run instead of failing it.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.images import update_image
from app.models.image import Images
from app.models.image_metadata_history import ImageMetadataHistory
from app.models.image_rating import ImageRatings
from app.models.user import Users
from app.schemas.image import ImageUpdate

pytestmark = [pytest.mark.integration, pytest.mark.needs_commit]

# Bound on every wait that could otherwise block forever. The work waited on
# takes milliseconds; this only has to be long enough never to fire spuriously.
_GUARD_SECONDS = 5


async def _finish(*tasks: asyncio.Task[None]) -> None:
    """Wait for `tasks` to end, then re-raise the first error one of them hit.

    A task still running after the guard is cancelled: unwinding its
    `async with sessions()` closes the session, which ends its transaction.
    """
    _, still_running = await asyncio.wait(tasks, timeout=_GUARD_SECONDS)
    for task in still_running:
        task.cancel()
    outcomes = await asyncio.wait_for(
        asyncio.gather(*tasks, return_exceptions=True), _GUARD_SECONDS
    )
    for outcome in outcomes:
        if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError):
            raise outcome
    if still_running:
        names = ", ".join(task.get_name() for task in still_running)
        pytest.fail(f"{names} still running after {_GUARD_SECONDS}s; cancelled")


async def _patch_parked_at_commit(
    sessions: async_sessionmaker[AsyncSession],
    image_id: int,
    user: Users,
    parked: asyncio.Event,
    release: asyncio.Event,
) -> None:
    """Run the real `update_image()`, pausing at its commit until `release`.

    By the time update_image calls commit it has issued its locking SELECT
    (and queued its setattr updates), so the row lock is held from `parked`
    until `release`.
    """
    async with sessions() as db:
        real_commit = db.commit

        async def commit_after_release() -> None:
            parked.set()
            await release.wait()
            await real_commit()

        db.commit = commit_after_release  # type: ignore[method-assign]
        await update_image(image_id, ImageUpdate(miscmeta="from-first"), user, db, AsyncMock())


async def _seed_image(db_session: AsyncSession) -> tuple[Users, Images]:
    """A committed image with miscmeta "orig", and its owner (user 1)."""
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
    return owner, image


async def test_second_patch_diffs_against_first_patchs_committed_value(db_session, engine):
    owner, image = await _seed_image(db_session)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    first_paused_holding_lock = asyncio.Event()
    release_first = asyncio.Event()
    second_select_dispatched = asyncio.Event()

    async def second_patch() -> None:
        await first_paused_holding_lock.wait()
        async with sessions() as db:
            real_execute = db.execute

            async def execute_and_flag_first_call(*args: object, **kwargs: object):
                # The first call is update_image's own locking SELECT;
                # flag it right before sending it so the test knows the
                # second side is now (about to be) waiting on Postgres, not
                # on Python-level scheduling.
                if not second_select_dispatched.is_set():
                    second_select_dispatched.set()
                return await real_execute(*args, **kwargs)

            db.execute = execute_and_flag_first_call  # type: ignore[method-assign]
            await update_image(
                image.image_id, ImageUpdate(miscmeta="from-second"), owner, db, AsyncMock()
            )

    first_task = asyncio.create_task(
        _patch_parked_at_commit(
            sessions, image.image_id, owner, first_paused_holding_lock, release_first
        ),
        name="first PATCH",
    )
    second_task = asyncio.create_task(second_patch(), name="second PATCH")
    try:
        await asyncio.wait_for(second_select_dispatched.wait(), _GUARD_SECONDS)
        done, _ = await asyncio.wait([second_task], timeout=0.3)
        assert second_task not in done, (
            "second update_image() completed without blocking on the first's row lock"
        )
    finally:
        release_first.set()
        await _finish(first_task, second_task)

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


async def test_fk_insert_referencing_the_image_does_not_wait_on_a_patch(db_session, engine):
    """An insert that only references the image passes a PATCH in flight.

    Its FK check takes FOR KEY SHARE on the image row. The PATCH's row lock
    must be FOR NO KEY UPDATE, which lets that through; FOR UPDATE would queue
    every such insert (tag_history, image_reports, ml_tag_suggestions, ...)
    behind the whole PATCH. image_ratings stands in for them because nothing
    on it writes the image row: favorites and posts have counter triggers that
    UPDATE images, so those wait on any PATCH whichever lock it takes.
    """
    owner, image = await _seed_image(db_session)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    patch_holding_lock = asyncio.Event()
    release_patch = asyncio.Event()

    patch_task = asyncio.create_task(
        _patch_parked_at_commit(sessions, image.image_id, owner, patch_holding_lock, release_patch),
        name="PATCH",
    )
    try:
        await asyncio.wait_for(patch_holding_lock.wait(), _GUARD_SECONDS)
        async with sessions() as db:
            db.add(ImageRatings(user_id=owner.user_id, image_id=image.image_id, rating=8))
            try:
                await asyncio.wait_for(db.commit(), _GUARD_SECONDS)
            except TimeoutError:
                pytest.fail("the image_ratings insert waited on the PATCH's row lock")
    finally:
        release_patch.set()
        await _finish(patch_task)
