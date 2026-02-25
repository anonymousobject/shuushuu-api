"""Tests for repost data migration service."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.favorite import Favorites
from app.models.image import Images
from app.models.image_rating import ImageRatings
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.user import Users
from app.services.repost import migrate_repost_data


async def _create_user(db: AsyncSession, username: str) -> Users:
    user = Users(
        username=username,
        password="hashed",
        password_type="bcrypt",
        salt="saltsalt12345678",
        email=f"{username}@example.com",
        active=1,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def _create_image(db: AsyncSession, user_id: int, md5_suffix: str) -> Images:
    img = Images(
        user_id=user_id,
        filename=f"img-{md5_suffix}",
        ext="jpg",
        original_filename=f"img-{md5_suffix}.jpg",
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


async def _create_tag(db: AsyncSession, title: str) -> Tags:
    tag = Tags(title=title, type=1)
    db.add(tag)
    await db.flush()
    await db.refresh(tag)
    return tag


@pytest.mark.unit
class TestMigrateRepostData:
    """Tests for migrate_repost_data service function."""

    async def test_migrates_favorites(self, db_session: AsyncSession):
        """Favorites on the repost should move to the original."""
        user = await _create_user(db_session, "favuser")
        original = await _create_image(db_session, user.user_id, "orig1")
        repost = await _create_image(db_session, user.user_id, "repo1")

        db_session.add(Favorites(user_id=user.user_id, image_id=repost.image_id))
        await db_session.flush()

        result = await migrate_repost_data(repost.image_id, original.image_id, db_session)

        assert result["favorites_moved"] == 1

        # Favorite now on original
        fav_count = await db_session.execute(
            select(func.count()).select_from(Favorites).where(
                Favorites.image_id == original.image_id
            )
        )
        assert fav_count.scalar() == 1

        # No favorites on repost
        fav_count = await db_session.execute(
            select(func.count()).select_from(Favorites).where(
                Favorites.image_id == repost.image_id
            )
        )
        assert fav_count.scalar() == 0

    async def test_migrates_ratings(self, db_session: AsyncSession):
        """Ratings on the repost should move to the original."""
        user = await _create_user(db_session, "rateuser")
        original = await _create_image(db_session, user.user_id, "orig2")
        repost = await _create_image(db_session, user.user_id, "repo2")

        db_session.add(ImageRatings(user_id=user.user_id, image_id=repost.image_id, rating=8))
        await db_session.flush()

        result = await migrate_repost_data(repost.image_id, original.image_id, db_session)

        assert result["ratings_moved"] == 1

        # Rating now on original
        rating_count = await db_session.execute(
            select(func.count()).select_from(ImageRatings).where(
                ImageRatings.image_id == original.image_id
            )
        )
        assert rating_count.scalar() == 1

        # No ratings on repost
        rating_count = await db_session.execute(
            select(func.count()).select_from(ImageRatings).where(
                ImageRatings.image_id == repost.image_id
            )
        )
        assert rating_count.scalar() == 0

    async def test_migrates_tags(self, db_session: AsyncSession):
        """Tag links on the repost should move to the original."""
        user = await _create_user(db_session, "taguser")
        original = await _create_image(db_session, user.user_id, "orig3")
        repost = await _create_image(db_session, user.user_id, "repo3")
        tag = await _create_tag(db_session, "test tag")

        db_session.add(TagLinks(
            tag_id=tag.tag_id, image_id=repost.image_id, user_id=user.user_id
        ))
        await db_session.flush()

        result = await migrate_repost_data(repost.image_id, original.image_id, db_session)

        assert result["tags_moved"] == 1

        # Tag link now on original
        tag_count = await db_session.execute(
            select(func.count()).select_from(TagLinks).where(
                TagLinks.image_id == original.image_id
            )
        )
        assert tag_count.scalar() == 1

        # No tag links on repost
        tag_count = await db_session.execute(
            select(func.count()).select_from(TagLinks).where(
                TagLinks.image_id == repost.image_id
            )
        )
        assert tag_count.scalar() == 0

    async def test_skips_duplicate_favorites(self, db_session: AsyncSession):
        """If user already favorited the original, repost favorite is discarded."""
        user = await _create_user(db_session, "dupfavuser")
        original = await _create_image(db_session, user.user_id, "orig4")
        repost = await _create_image(db_session, user.user_id, "repo4")

        db_session.add(Favorites(user_id=user.user_id, image_id=original.image_id))
        db_session.add(Favorites(user_id=user.user_id, image_id=repost.image_id))
        await db_session.flush()

        result = await migrate_repost_data(repost.image_id, original.image_id, db_session)

        assert result["favorites_moved"] == 0

        fav_count = await db_session.execute(
            select(func.count()).select_from(Favorites).where(
                Favorites.image_id == original.image_id
            )
        )
        assert fav_count.scalar() == 1

    async def test_skips_duplicate_ratings(self, db_session: AsyncSession):
        """If user already rated the original, repost rating is discarded."""
        user = await _create_user(db_session, "duprateuser")
        original = await _create_image(db_session, user.user_id, "orig5")
        repost = await _create_image(db_session, user.user_id, "repo5")

        db_session.add(ImageRatings(user_id=user.user_id, image_id=original.image_id, rating=9))
        db_session.add(ImageRatings(user_id=user.user_id, image_id=repost.image_id, rating=7))
        await db_session.flush()

        result = await migrate_repost_data(repost.image_id, original.image_id, db_session)

        assert result["ratings_moved"] == 0

        rating = await db_session.execute(
            select(ImageRatings.rating).where(
                ImageRatings.image_id == original.image_id,
                ImageRatings.user_id == user.user_id,
            )
        )
        assert rating.scalar() == 9

    async def test_skips_duplicate_tags(self, db_session: AsyncSession):
        """If tag already linked to original, repost tag link is discarded."""
        user = await _create_user(db_session, "duptaguser")
        original = await _create_image(db_session, user.user_id, "orig6")
        repost = await _create_image(db_session, user.user_id, "repo6")
        tag = await _create_tag(db_session, "duptag")

        db_session.add(TagLinks(tag_id=tag.tag_id, image_id=original.image_id, user_id=user.user_id))
        db_session.add(TagLinks(tag_id=tag.tag_id, image_id=repost.image_id, user_id=user.user_id))
        await db_session.flush()

        result = await migrate_repost_data(repost.image_id, original.image_id, db_session)

        assert result["tags_moved"] == 0

        tag_count = await db_session.execute(
            select(func.count()).select_from(TagLinks).where(
                TagLinks.image_id == original.image_id
            )
        )
        assert tag_count.scalar() == 1

    async def test_resets_repost_image_counters(self, db_session: AsyncSession):
        """Repost image should have favorites=0 and rating fields reset."""
        user = await _create_user(db_session, "resetuser")
        original = await _create_image(db_session, user.user_id, "orig7")
        repost = await _create_image(db_session, user.user_id, "repo7")

        repost.favorites = 3
        repost.num_ratings = 2
        repost.rating = 7.5
        repost.bayesian_rating = 6.8
        db_session.add(Favorites(user_id=user.user_id, image_id=repost.image_id))
        db_session.add(ImageRatings(user_id=user.user_id, image_id=repost.image_id, rating=8))
        await db_session.flush()

        await migrate_repost_data(repost.image_id, original.image_id, db_session)

        await db_session.refresh(repost)
        assert repost.favorites == 0
        assert repost.num_ratings == 0
        assert repost.rating == 0
        assert repost.bayesian_rating == 0

    async def test_updates_original_favorites_count(self, db_session: AsyncSession):
        """Original image favorites count should reflect migrated favorites."""
        user1 = await _create_user(db_session, "favcount1")
        user2 = await _create_user(db_session, "favcount2")
        original = await _create_image(db_session, user1.user_id, "orig8")
        repost = await _create_image(db_session, user1.user_id, "repo8")

        db_session.add(Favorites(user_id=user1.user_id, image_id=original.image_id))
        db_session.add(Favorites(user_id=user2.user_id, image_id=repost.image_id))
        original.favorites = 1
        await db_session.flush()

        await migrate_repost_data(repost.image_id, original.image_id, db_session)

        await db_session.refresh(original)
        assert original.favorites == 2

    async def test_no_data_returns_zero_counts(self, db_session: AsyncSession):
        """When repost has no favorites/ratings/tags, counts should be 0."""
        user = await _create_user(db_session, "emptyuser")
        original = await _create_image(db_session, user.user_id, "orig9")
        repost = await _create_image(db_session, user.user_id, "repo9")

        result = await migrate_repost_data(repost.image_id, original.image_id, db_session)

        assert result["favorites_moved"] == 0
        assert result["ratings_moved"] == 0
        assert result["tags_moved"] == 0
