"""
Repost data migration service.

When an image is marked as a repost, migrates favorites, ratings, and tags
from the repost to the original image, then cleans up the repost.
"""

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.favorite import Favorites
from app.models.image import Images
from app.models.image_rating import ImageRatings
from app.models.tag_link import TagLinks


async def migrate_repost_data(repost_id: int, original_id: int, db: AsyncSession) -> dict[str, int]:
    """
    Migrate favorites, ratings, and tags from a repost to the original image.

    Uses INSERT IGNORE to handle duplicates: if a user already favorited/rated
    the original, the repost's record is silently discarded.

    Args:
        repost_id: Image ID of the repost being marked
        original_id: Image ID of the original (replacement) image
        db: Database session (caller manages transaction)

    Returns:
        Dict with counts: favorites_moved, ratings_moved, tags_moved
    """
    # --- Favorites ---
    before_fav = await db.execute(
        select(func.count())
        .select_from(Favorites)
        .where(
            Favorites.image_id == original_id  # type: ignore[arg-type]
        )
    )
    fav_count_before = before_fav.scalar() or 0

    await db.execute(
        text(
            "INSERT IGNORE INTO favorites (user_id, image_id, fav_date) "
            "SELECT user_id, :original_id, fav_date FROM favorites "
            "WHERE image_id = :repost_id"
        ),
        {"original_id": original_id, "repost_id": repost_id},
    )

    after_fav = await db.execute(
        select(func.count())
        .select_from(Favorites)
        .where(
            Favorites.image_id == original_id  # type: ignore[arg-type]
        )
    )
    fav_count_after = after_fav.scalar() or 0
    favorites_moved = fav_count_after - fav_count_before

    await db.execute(
        delete(Favorites).where(Favorites.image_id == repost_id)  # type: ignore[arg-type]
    )

    # Update favorites counts on both images
    await db.execute(
        update(Images)
        .where(Images.image_id == original_id)  # type: ignore[arg-type]
        .values(favorites=fav_count_after)
    )
    await db.execute(
        update(Images)
        .where(Images.image_id == repost_id)  # type: ignore[arg-type]
        .values(favorites=0)
    )

    # --- Ratings ---
    before_rat = await db.execute(
        select(func.count())
        .select_from(ImageRatings)
        .where(
            ImageRatings.image_id == original_id  # type: ignore[arg-type]
        )
    )
    rat_count_before = before_rat.scalar() or 0

    await db.execute(
        text(
            "INSERT IGNORE INTO image_ratings (user_id, image_id, rating, date) "
            "SELECT user_id, :original_id, rating, date FROM image_ratings "
            "WHERE image_id = :repost_id"
        ),
        {"original_id": original_id, "repost_id": repost_id},
    )

    after_rat = await db.execute(
        select(func.count())
        .select_from(ImageRatings)
        .where(
            ImageRatings.image_id == original_id  # type: ignore[arg-type]
        )
    )
    rat_count_after = after_rat.scalar() or 0
    ratings_moved = rat_count_after - rat_count_before

    await db.execute(
        delete(ImageRatings).where(
            ImageRatings.image_id == repost_id  # type: ignore[arg-type]
        )
    )

    # Reset repost rating fields
    await db.execute(
        update(Images)
        .where(Images.image_id == repost_id)  # type: ignore[arg-type]
        .values(num_ratings=0, rating=0, bayesian_rating=0)
    )

    # --- Tags ---
    before_tag = await db.execute(
        select(func.count())
        .select_from(TagLinks)
        .where(
            TagLinks.image_id == original_id  # type: ignore[arg-type]
        )
    )
    tag_count_before = before_tag.scalar() or 0

    await db.execute(
        text(
            "INSERT IGNORE INTO tag_links (tag_id, image_id, date_linked, user_id) "
            "SELECT tag_id, :original_id, date_linked, user_id FROM tag_links "
            "WHERE image_id = :repost_id"
        ),
        {"original_id": original_id, "repost_id": repost_id},
    )

    after_tag = await db.execute(
        select(func.count())
        .select_from(TagLinks)
        .where(
            TagLinks.image_id == original_id  # type: ignore[arg-type]
        )
    )
    tag_count_after = after_tag.scalar() or 0
    tags_moved = tag_count_after - tag_count_before

    await db.execute(
        delete(TagLinks).where(
            TagLinks.image_id == repost_id  # type: ignore[arg-type]
        )
    )

    return {
        "favorites_moved": favorites_moved,
        "ratings_moved": ratings_moved,
        "tags_moved": tags_moved,
    }
