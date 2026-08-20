"""
Repost data migration service.

When an image is marked as a repost, migrates favorites, ratings, and tags
from the repost to the original image, then cleans up the repost.
"""

from sqlalchemy import TextClause, delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.favorite import Favorites
from app.models.image import Images
from app.models.image_rating import ImageRatings
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.tag_link import TagLinks
from app.services.ml_suggestion_review import approve_pending_suggestions_for_links
from app.services.tag_type_flags import refresh_images_tag_type_flags


def _copy_to_original_sql(
    db: AsyncSession, table: str, insert_cols: str, select_cols: str
) -> TextClause:
    """INSERT-or-skip-duplicates, copying `table` rows from the repost to the original.

    MariaDB spells "skip duplicates" INSERT IGNORE; Postgres ON CONFLICT DO NOTHING.
    """
    base = (
        f"INTO {table} ({insert_cols}) "
        f"SELECT {select_cols} FROM {table} WHERE image_id = :repost_id"
    )
    if db.get_bind().dialect.name == "postgresql":
        return text(f"INSERT {base} ON CONFLICT DO NOTHING")
    return text(f"INSERT IGNORE {base}")


async def _tag_ids_for(db: AsyncSession, image_id: int) -> set[int]:
    """The tag ids currently linked to `image_id`."""
    result = await db.execute(
        select(TagLinks.tag_id).where(TagLinks.image_id == image_id)  # type: ignore[call-overload]
    )
    return set(result.scalars().all())


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
        _copy_to_original_sql(
            db, "favorites", "user_id, image_id, fav_date", "user_id, :original_id, fav_date"
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
        _copy_to_original_sql(
            db,
            "image_ratings",
            "user_id, image_id, rating, date",
            "user_id, :original_id, rating, date",
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
    # Read both sides' tag ids up front and diff them. tag_links is keyed on
    # (tag_id, image_id), so the INSERT IGNORE below adds exactly the repost's
    # tags that the original lacks — the difference IS the moved count, and it
    # is also the precise scope of the suggestion resolution further down.
    # (Replaces a COUNT-before/COUNT-after pair plus a third SELECT of the
    # original's tag ids.)
    original_tag_ids_before = await _tag_ids_for(db, original_id)
    repost_tag_ids = await _tag_ids_for(db, repost_id)
    moved_tag_ids = repost_tag_ids - original_tag_ids_before
    tags_moved = len(moved_tag_ids)

    await db.execute(
        _copy_to_original_sql(
            db,
            "tag_links",
            "tag_id, image_id, date_linked, user_id",
            "tag_id, :original_id, date_linked, user_id",
        ),
        {"original_id": original_id, "repost_id": repost_id},
    )

    await db.execute(
        delete(TagLinks).where(
            TagLinks.image_id == repost_id  # type: ignore[arg-type]
        )
    )

    # --- ML suggestions ---
    # The migrated tags are now applied to the original: resolve its matching
    # pending suggestions. Reviewer stays NULL — this is data movement, not a
    # human review (system resolution; see CONTEXT.md and ADR-0001).
    #
    # Scoped to the tags this migration actually added, matching every other
    # caller of approve_pending_suggestions_for_links (single tag add, batch
    # tagging, report resolution), which each pass only their own new links.
    # Passing the original's WHOLE tag set instead made the UPDATE's lock set
    # grow with the original's tag count and cover index gaps for tags with no
    # suggestion row — the gaps the ML pipeline inserts into, and the deadlock
    # in #335. A tag the original already carried is not this operation's to
    # resolve: whatever applied it owned that. (ADR-0005.)
    await approve_pending_suggestions_for_links(
        db, [(original_id, tag_id) for tag_id in sorted(moved_tag_ids)], None
    )

    # A repost is permanently out of review scope: wipe ALL its suggestion rows,
    # matching the favorites/ratings/tags wipe above (ADR-0002).
    await db.execute(
        delete(MlTagSuggestions).where(
            MlTagSuggestions.image_id == repost_id  # type: ignore[arg-type]
        )
    )

    await refresh_images_tag_type_flags(db, [original_id, repost_id])

    return {
        "favorites_moved": favorites_moved,
        "ratings_moved": ratings_moved,
        "tags_moved": tags_moved,
    }
