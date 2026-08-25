# Repost Data Cleanup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** When marking an image as a repost, migrate favorites, ratings, and tags to the original image and clean up the repost.

**Architecture:** New service function `migrate_repost_data()` in `app/services/repost.py`, called from `change_image_status` in `app/api/v1/admin.py`. Uses raw SQL `INSERT IGNORE` for atomic upsert of junction table rows, then deletes from repost. Returns migration summary for audit logging.

**Tech Stack:** SQLAlchemy async, raw SQL for `INSERT IGNORE` (not expressible via ORM), existing `schedule_rating_recalculation` for background rating update.

---

### Task 1: Create repost service with migrate_repost_data

**Files:**
- Create: `app/services/repost.py`
- Test: `tests/unit/test_repost_cleanup.py`

**Step 1: Write the failing test**

Create `tests/unit/test_repost_cleanup.py`:

```python
"""Tests for repost data migration service."""

from datetime import UTC, datetime

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

        # User favorited both
        db_session.add(Favorites(user_id=user.user_id, image_id=original.image_id))
        db_session.add(Favorites(user_id=user.user_id, image_id=repost.image_id))
        await db_session.flush()

        result = await migrate_repost_data(repost.image_id, original.image_id, db_session)

        # Duplicate skipped, so 0 moved
        assert result["favorites_moved"] == 0

        # Original still has exactly 1 favorite
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

        # User rated both - original keeps its rating
        db_session.add(ImageRatings(
            user_id=user.user_id, image_id=original.image_id, rating=9
        ))
        db_session.add(ImageRatings(
            user_id=user.user_id, image_id=repost.image_id, rating=7
        ))
        await db_session.flush()

        result = await migrate_repost_data(repost.image_id, original.image_id, db_session)

        assert result["ratings_moved"] == 0

        # Original still has rating=9 (not overwritten)
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

        # Tag linked to both
        db_session.add(TagLinks(
            tag_id=tag.tag_id, image_id=original.image_id, user_id=user.user_id
        ))
        db_session.add(TagLinks(
            tag_id=tag.tag_id, image_id=repost.image_id, user_id=user.user_id
        ))
        await db_session.flush()

        result = await migrate_repost_data(repost.image_id, original.image_id, db_session)

        assert result["tags_moved"] == 0

        # Original still has exactly 1 tag link
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

        # Set up some data on the repost
        repost.favorites = 3
        repost.num_ratings = 2
        repost.rating = 7.5
        repost.bayesian_rating = 6.8
        db_session.add(Favorites(user_id=user.user_id, image_id=repost.image_id))
        db_session.add(ImageRatings(
            user_id=user.user_id, image_id=repost.image_id, rating=8
        ))
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

        # user1 favorited original, user2 favorited repost
        db_session.add(Favorites(user_id=user1.user_id, image_id=original.image_id))
        db_session.add(Favorites(user_id=user2.user_id, image_id=repost.image_id))
        original.favorites = 1
        await db_session.flush()

        await migrate_repost_data(repost.image_id, original.image_id, db_session)

        await db_session.refresh(original)
        assert original.favorites == 2  # user1 + user2

    async def test_no_data_returns_zero_counts(self, db_session: AsyncSession):
        """When repost has no favorites/ratings/tags, counts should be 0."""
        user = await _create_user(db_session, "emptyuser")
        original = await _create_image(db_session, user.user_id, "orig9")
        repost = await _create_image(db_session, user.user_id, "repo9")

        result = await migrate_repost_data(repost.image_id, original.image_id, db_session)

        assert result["favorites_moved"] == 0
        assert result["ratings_moved"] == 0
        assert result["tags_moved"] == 0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_repost_cleanup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.repost'`

**Step 3: Write the implementation**

Create `app/services/repost.py`:

```python
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


async def migrate_repost_data(
    repost_id: int, original_id: int, db: AsyncSession
) -> dict[str, int]:
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
    # Count before insert to calculate moved count
    before_fav = await db.execute(
        select(func.count()).select_from(Favorites).where(
            Favorites.image_id == original_id  # type: ignore[arg-type]
        )
    )
    fav_count_before = before_fav.scalar() or 0

    await db.execute(
        text(
            "INSERT IGNORE INTO favorites (user_id, image_id, fav_date) "
            "SELECT user_id, :original_id, NOW() FROM favorites "
            "WHERE image_id = :repost_id"
        ),
        {"original_id": original_id, "repost_id": repost_id},
    )

    after_fav = await db.execute(
        select(func.count()).select_from(Favorites).where(
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
        select(func.count()).select_from(ImageRatings).where(
            ImageRatings.image_id == original_id  # type: ignore[arg-type]
        )
    )
    rat_count_before = before_rat.scalar() or 0

    await db.execute(
        text(
            "INSERT IGNORE INTO image_ratings (user_id, image_id, rating) "
            "SELECT user_id, :original_id, rating FROM image_ratings "
            "WHERE image_id = :repost_id"
        ),
        {"original_id": original_id, "repost_id": repost_id},
    )

    after_rat = await db.execute(
        select(func.count()).select_from(ImageRatings).where(
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
        select(func.count()).select_from(TagLinks).where(
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
        select(func.count()).select_from(TagLinks).where(
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
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_repost_cleanup.py -v`
Expected: All 10 tests PASS

**Step 5: Commit**

```bash
git add app/services/repost.py tests/unit/test_repost_cleanup.py
git commit -m "feat: add repost data migration service"
```

---

### Task 2: Integrate into change_image_status endpoint

**Files:**
- Modify: `app/api/v1/admin.py:715-775`
- Test: `tests/api/v1/test_admin_images.py`

**Step 1: Write the failing test**

Add to `tests/api/v1/test_admin_images.py`:

```python
    async def test_repost_migrates_favorites_ratings_tags(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Marking as repost should migrate favorites, ratings, and tags to original."""
        admin, admin_password = await create_admin_user(
            db_session, username="repostadmin", email="repostadmin@example.com"
        )
        await grant_permission(db_session, admin.user_id, "image_edit")

        user = await create_regular_user(
            db_session, username="repostfan", email="repostfan@example.com"
        )

        original_image = await create_test_image(db_session, admin.user_id)
        repost_image = Images(
            user_id=admin.user_id,
            filename="repost_migrate",
            ext="jpg",
            md5_hash="migrate123456789012345678901234",
            status=ImageStatus.ACTIVE,
        )
        db_session.add(repost_image)
        await db_session.commit()
        await db_session.refresh(repost_image)

        # Add data to repost
        db_session.add(Favorites(user_id=user.user_id, image_id=repost_image.image_id))
        db_session.add(ImageRatings(
            user_id=user.user_id, image_id=repost_image.image_id, rating=8
        ))
        tag = Tags(title="repost test tag", type=1)
        db_session.add(tag)
        await db_session.flush()
        db_session.add(TagLinks(
            tag_id=tag.tag_id, image_id=repost_image.image_id, user_id=user.user_id
        ))
        repost_image.favorites = 1
        await db_session.commit()

        token = await login_user(client, admin.username, admin_password)

        response = await client.patch(
            f"/api/v1/admin/images/{repost_image.image_id}",
            json={
                "status": ImageStatus.REPOST,
                "replacement_id": original_image.image_id,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200

        # Verify data migrated to original
        fav_result = await db_session.execute(
            select(func.count()).select_from(Favorites).where(
                Favorites.image_id == original_image.image_id
            )
        )
        assert fav_result.scalar() == 1

        rating_result = await db_session.execute(
            select(func.count()).select_from(ImageRatings).where(
                ImageRatings.image_id == original_image.image_id
            )
        )
        assert rating_result.scalar() == 1

        tag_result = await db_session.execute(
            select(func.count()).select_from(TagLinks).where(
                TagLinks.image_id == original_image.image_id
            )
        )
        assert tag_result.scalar() == 1

        # Verify repost is cleaned up
        fav_result = await db_session.execute(
            select(func.count()).select_from(Favorites).where(
                Favorites.image_id == repost_image.image_id
            )
        )
        assert fav_result.scalar() == 0

        # Verify audit trail includes migration summary
        action_result = await db_session.execute(
            select(AdminActions).where(
                AdminActions.image_id == repost_image.image_id,
                AdminActions.action_type == AdminActionType.IMAGE_STATUS_CHANGE,
            )
        )
        action = action_result.scalar_one()
        assert action.details["favorites_moved"] == 1
        assert action.details["ratings_moved"] == 1
        assert action.details["tags_moved"] == 1
```

This requires new imports at the top of the test file:

```python
from sqlalchemy import func, select  # add func
from app.models.favorite import Favorites
from app.models.image_rating import ImageRatings
from app.models.tag import Tags
from app.models.tag_link import TagLinks
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/v1/test_admin_images.py::TestChangeImageStatus::test_repost_migrates_favorites_ratings_tags -v`
Expected: FAIL — no migration happens, favorites/ratings/tags stay on repost

**Step 3: Integrate into admin.py**

In `app/api/v1/admin.py`, add import at top:

```python
from app.services.repost import migrate_repost_data
```

Then modify the repost handling block (after `image.replacement_id = status_data.replacement_id`, around line 736):

```python
            image.replacement_id = status_data.replacement_id

            # Migrate favorites, ratings, and tags to the original image
            migration_result = await migrate_repost_data(
                image_id, status_data.replacement_id, db
            )
```

And update the admin action details dict (around line 765) to include migration info:

```python
    action = AdminActions(
        user_id=current_user.user_id,
        action_type=AdminActionType.IMAGE_STATUS_CHANGE,
        image_id=image_id,
        details={
            "previous_status": previous_status,
            "new_status": image.status,
            "previous_locked": previous_locked,
            "new_locked": image.locked,
            "replacement_id": image.replacement_id,
            **(migration_result if status_data.status == ImageStatus.REPOST else {}),
        },
    )
```

Also schedule rating recalculation for the original (after commit):

```python
    await db.commit()
    await db.refresh(image)

    # Schedule rating recalculation for original image after repost migration
    if status_data.status == ImageStatus.REPOST and status_data.replacement_id:
        from app.services.rating import schedule_rating_recalculation
        await schedule_rating_recalculation(status_data.replacement_id)

    return ImageStatusResponse.model_validate(image)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_admin_images.py -v`
Expected: All tests PASS (both new and existing)

**Step 5: Commit**

```bash
git add app/api/v1/admin.py tests/api/v1/test_admin_images.py
git commit -m "feat: integrate repost data migration into status change endpoint"
```
