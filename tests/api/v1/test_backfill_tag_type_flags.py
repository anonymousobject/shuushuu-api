"""Tests for scripts/backfill_tag_type_flags.py."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.image import Images
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.user import Users
from scripts.backfill_tag_type_flags import backfill_range


async def _create_user(db: AsyncSession) -> Users:
    user = Users(
        username="backfill_test_user",
        password="hashed",
        password_type="bcrypt",
        salt="saltsalt12345678",
        email="backfill@example.com",
        active=1,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def _create_image(db: AsyncSession, user_id: int, md5_suffix: str) -> Images:
    img = Images(
        user_id=user_id,
        filename=f"backfill-{md5_suffix}",
        ext="jpg",
        original_filename=f"backfill-{md5_suffix}.jpg",
        md5_hash=f"{md5_suffix:0<32}",
        filesize=1000,
        width=100,
        height=100,
        status=1,
        locked=0,
    )
    db.add(img)
    await db.flush()
    await db.refresh(img)
    return img


@pytest.mark.unit
class TestBackfillTagTypeFlags:
    """Tests for the backfill_range function in the backfill script."""

    async def test_backfill_sets_artist_flag_and_leaves_untagged_image_clean(
        self, db_session: AsyncSession
    ):
        """
        Image A with an ARTIST tag gets has_artist=True after backfill.
        Image B with no tags keeps all flags False after backfill.
        All other flags on image A remain False.
        """
        user = await _create_user(db_session)

        # Create two images — flags default to False (all 0)
        image_a = await _create_image(db_session, user.user_id, "backfillA1")
        image_b = await _create_image(db_session, user.user_id, "backfillB1")

        # Add an ARTIST tag (type=3) to image A only
        artist_tag = Tags(title="Test Backfill Artist", type=3)  # 3 = Artist
        db_session.add(artist_tag)
        await db_session.flush()
        await db_session.refresh(artist_tag)

        db_session.add(
            TagLinks(
                tag_id=artist_tag.tag_id,
                image_id=image_a.image_id,
                user_id=user.user_id,
            )
        )
        await db_session.flush()

        # Confirm precondition: flags are all False (not yet backfilled)
        pre_a = (
            await db_session.execute(
                select(Images)
                .where(Images.image_id == image_a.image_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert pre_a.has_artist is False, "precondition: has_artist should be False before backfill"

        # Run the backfill over the range covering both images
        lo = min(image_a.image_id, image_b.image_id)
        hi = max(image_a.image_id, image_b.image_id) + 1
        await backfill_range(db_session, lo=lo, hi=hi)
        await db_session.commit()

        # Fresh-read image A — should have has_artist=True, others False
        fresh_a = (
            await db_session.execute(
                select(Images)
                .where(Images.image_id == image_a.image_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert fresh_a.has_artist is True, "image A should have has_artist=True after backfill"
        assert fresh_a.has_theme is False, "image A should have has_theme=False (no theme tag)"
        assert fresh_a.has_source is False, "image A should have has_source=False (no source tag)"
        assert fresh_a.has_character is False, "image A should have has_character=False (no character tag)"

        # Fresh-read image B — all flags should remain False
        fresh_b = (
            await db_session.execute(
                select(Images)
                .where(Images.image_id == image_b.image_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert fresh_b.has_artist is False, "image B should have has_artist=False (no tags)"
        assert fresh_b.has_theme is False, "image B should have has_theme=False (no tags)"
        assert fresh_b.has_source is False, "image B should have has_source=False (no tags)"
        assert fresh_b.has_character is False, "image B should have has_character=False (no tags)"

    async def test_backfill_sets_all_four_flag_types(
        self, db_session: AsyncSession
    ):
        """Image with tags of all four types gets all four flags set after backfill."""
        user = await _create_user(db_session)
        image = await _create_image(db_session, user.user_id, "backfillAll1")

        # Add one tag of each type (1=Theme, 2=Source, 3=Artist, 4=Character)
        for tag_type, title in [(1, "BfTheme"), (2, "BfSource"), (3, "BfArtist"), (4, "BfChar")]:
            tag = Tags(title=title, type=tag_type)
            db_session.add(tag)
            await db_session.flush()
            await db_session.refresh(tag)
            db_session.add(TagLinks(tag_id=tag.tag_id, image_id=image.image_id, user_id=user.user_id))
        await db_session.flush()

        await backfill_range(db_session, lo=image.image_id, hi=image.image_id + 1)
        await db_session.commit()

        fresh = (
            await db_session.execute(
                select(Images)
                .where(Images.image_id == image.image_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert fresh.has_theme is True
        assert fresh.has_source is True
        assert fresh.has_artist is True
        assert fresh.has_character is True

    async def test_backfill_is_idempotent(
        self, db_session: AsyncSession
    ):
        """Running backfill_range twice produces the same result."""
        user = await _create_user(db_session)
        image = await _create_image(db_session, user.user_id, "backfillIdem1")

        source_tag = Tags(title="BfIdempotentSource", type=2)
        db_session.add(source_tag)
        await db_session.flush()
        await db_session.refresh(source_tag)
        db_session.add(TagLinks(tag_id=source_tag.tag_id, image_id=image.image_id, user_id=user.user_id))
        await db_session.flush()

        # Run twice
        await backfill_range(db_session, lo=image.image_id, hi=image.image_id + 1)
        await db_session.commit()
        await backfill_range(db_session, lo=image.image_id, hi=image.image_id + 1)
        await db_session.commit()

        fresh = (
            await db_session.execute(
                select(Images)
                .where(Images.image_id == image.image_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert fresh.has_source is True
        assert fresh.has_artist is False
