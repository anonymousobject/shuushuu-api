"""Tests for ml_suggestion_queue service — worklist counts and per-tag listing.

These tests exercise the service layer directly against the real test DB.
No mocked behavior is asserted; all assertions target real DB rows.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.models.image import Images
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.tag import Tags
from app.models.user import Users
from app.services.ml_suggestion_queue import count_pending_by_tag, list_pending_for_tag


async def _make_user(db: AsyncSession, suffix: str) -> Users:
    user = Users(
        username=f"queue_svc_{suffix}",
        email=f"queue_svc_{suffix}@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="testsalt12345678",
        active=1,
    )
    db.add(user)
    await db.flush()
    return user


async def _make_image(db: AsyncSession, user: Users, suffix: str) -> Images:
    image = Images(
        filename=f"2024-01-01-queue-{suffix}",
        ext="jpg",
        user_id=user.user_id,
        md5_hash=f"queue_hash_{suffix}",
        filesize=1024,
        width=800,
        height=600,
    )
    db.add(image)
    await db.flush()
    return image


async def _make_tag(db: AsyncSession, user: Users, suffix: str, tag_type: int = TagType.THEME) -> Tags:
    tag = Tags(title=f"queue tag {suffix}", type=tag_type, user_id=user.user_id)
    db.add(tag)
    await db.flush()
    return tag


async def _make_suggestion(
    db: AsyncSession,
    image: Images,
    tag: Tags,
    confidence: float = 0.88,
    status: str = "pending",
) -> MlTagSuggestions:
    suggestion = MlTagSuggestions(
        image_id=image.image_id,
        tag_id=tag.tag_id,
        confidence=confidence,
        model_version="v3",
        status=status,
    )
    db.add(suggestion)
    await db.flush()
    return suggestion


class TestCountPendingByTag:
    """Tests for count_pending_by_tag service function."""

    async def test_returns_counts_for_pending_suggestions(self, db_session: AsyncSession):
        """Seeds two tags with different pending counts; asserts both appear in DESC order."""
        user = await _make_user(db_session, "cnt1")
        image1 = await _make_image(db_session, user, "cnt1a")
        image2 = await _make_image(db_session, user, "cnt1b")
        image3 = await _make_image(db_session, user, "cnt1c")
        tag_a = await _make_tag(db_session, user, "cnt_a")  # will have 2 pending
        tag_b = await _make_tag(db_session, user, "cnt_b")  # will have 1 pending
        await _make_suggestion(db_session, image1, tag_a)
        await _make_suggestion(db_session, image2, tag_a)
        await _make_suggestion(db_session, image3, tag_b)
        await db_session.commit()

        results = await count_pending_by_tag(db_session)

        tag_ids = [r[0] for r in results]
        assert tag_a.tag_id in tag_ids
        assert tag_b.tag_id in tag_ids

        # Verify structure: (tag_id, title, type, pending_count)
        row_a = next(r for r in results if r[0] == tag_a.tag_id)
        row_b = next(r for r in results if r[0] == tag_b.tag_id)
        assert row_a[1] == tag_a.title
        assert row_a[2] == TagType.THEME
        assert row_a[3] == 2
        assert row_b[3] == 1

        # DESC order: tag_a (count=2) must come before tag_b (count=1)
        idx_a = tag_ids.index(tag_a.tag_id)
        idx_b = tag_ids.index(tag_b.tag_id)
        assert idx_a < idx_b

    async def test_excludes_non_pending_suggestions(self, db_session: AsyncSession):
        """Approved and rejected suggestions are NOT counted as pending."""
        user = await _make_user(db_session, "cnt2")
        image1 = await _make_image(db_session, user, "cnt2a")
        image2 = await _make_image(db_session, user, "cnt2b")
        image3 = await _make_image(db_session, user, "cnt2c")
        tag = await _make_tag(db_session, user, "cnt2_tag")
        # 1 pending + 1 approved + 1 rejected
        await _make_suggestion(db_session, image1, tag, status="pending")
        await _make_suggestion(db_session, image2, tag, status="approved")
        await _make_suggestion(db_session, image3, tag, status="rejected")
        await db_session.commit()

        results = await count_pending_by_tag(db_session)

        row = next((r for r in results if r[0] == tag.tag_id), None)
        assert row is not None
        assert row[3] == 1  # only the pending one

    async def test_type_filter_returns_only_matching_type(self, db_session: AsyncSession):
        """type_filter=CHARACTER returns only character-type tags."""
        user = await _make_user(db_session, "cnt3")
        image1 = await _make_image(db_session, user, "cnt3a")
        image2 = await _make_image(db_session, user, "cnt3b")
        theme_tag = await _make_tag(db_session, user, "cnt3_theme", tag_type=TagType.THEME)
        char_tag = await _make_tag(db_session, user, "cnt3_char", tag_type=TagType.CHARACTER)
        await _make_suggestion(db_session, image1, theme_tag)
        await _make_suggestion(db_session, image2, char_tag)
        await db_session.commit()

        results = await count_pending_by_tag(db_session, type_filter=TagType.CHARACTER)

        tag_ids = [r[0] for r in results]
        assert char_tag.tag_id in tag_ids
        assert theme_tag.tag_id not in tag_ids
        # All returned rows must be character type
        for row in results:
            assert row[2] == TagType.CHARACTER

    async def test_min_confidence_excludes_low_confidence_rows(self, db_session: AsyncSession):
        """min_confidence filters out suggestions below the threshold."""
        user = await _make_user(db_session, "cnt4")
        image1 = await _make_image(db_session, user, "cnt4a")
        image2 = await _make_image(db_session, user, "cnt4b")
        image3 = await _make_image(db_session, user, "cnt4c")
        tag = await _make_tag(db_session, user, "cnt4_tag")
        await _make_suggestion(db_session, image1, tag, confidence=0.9)
        await _make_suggestion(db_session, image2, tag, confidence=0.8)
        await _make_suggestion(db_session, image3, tag, confidence=0.5)
        await db_session.commit()

        results = await count_pending_by_tag(db_session, min_confidence=0.75)

        row = next((r for r in results if r[0] == tag.tag_id), None)
        assert row is not None
        assert row[3] == 2  # only 0.9 and 0.8 qualify

    async def test_tag_with_no_pending_not_returned(self, db_session: AsyncSession):
        """A tag where all suggestions are approved/rejected does not appear."""
        user = await _make_user(db_session, "cnt5")
        image1 = await _make_image(db_session, user, "cnt5a")
        tag = await _make_tag(db_session, user, "cnt5_tag")
        await _make_suggestion(db_session, image1, tag, status="approved")
        await db_session.commit()

        results = await count_pending_by_tag(db_session)

        tag_ids = [r[0] for r in results]
        assert tag.tag_id not in tag_ids


class TestListPendingForTag:
    """Tests for list_pending_for_tag service function."""

    async def test_returns_items_ordered_by_confidence_desc(self, db_session: AsyncSession):
        """Items for a given tag are returned in confidence DESC order."""
        user = await _make_user(db_session, "lst1")
        image1 = await _make_image(db_session, user, "lst1a")
        image2 = await _make_image(db_session, user, "lst1b")
        image3 = await _make_image(db_session, user, "lst1c")
        tag = await _make_tag(db_session, user, "lst1_tag")
        s_low = await _make_suggestion(db_session, image1, tag, confidence=0.5)
        s_mid = await _make_suggestion(db_session, image2, tag, confidence=0.75)
        s_high = await _make_suggestion(db_session, image3, tag, confidence=0.9)
        await db_session.commit()

        items, total = await list_pending_for_tag(db_session, tag.tag_id, 0.0, 1, 10)

        assert total == 3
        assert len(items) == 3
        # Each item is (suggestion_id, image_id, confidence)
        confidences = [item[2] for item in items]
        assert confidences == sorted(confidences, reverse=True)
        # Highest confidence first
        assert items[0][0] == s_high.suggestion_id
        assert items[2][0] == s_low.suggestion_id

    async def test_pagination_page_2(self, db_session: AsyncSession):
        """Page 2 with per_page=2 returns the correct slice."""
        user = await _make_user(db_session, "lst2")
        images = [await _make_image(db_session, user, f"lst2_{i}") for i in range(5)]
        tag = await _make_tag(db_session, user, "lst2_tag")
        # Create 5 suggestions with distinct confidences
        suggestions = []
        for i, img in enumerate(images):
            s = await _make_suggestion(db_session, img, tag, confidence=0.9 - i * 0.1)
            suggestions.append(s)
        await db_session.commit()

        items, total = await list_pending_for_tag(db_session, tag.tag_id, 0.0, 2, 2)

        assert total == 5
        assert len(items) == 2
        # Page 2 (1-based) with per_page=2 → items at positions [2,3] in 0-based DESC order
        # Confidences: 0.9, 0.8, 0.7, 0.6, 0.5 → page 2 = 0.7, 0.6
        confidences = [item[2] for item in items]
        assert abs(confidences[0] - 0.7) < 1e-6
        assert abs(confidences[1] - 0.6) < 1e-6

    async def test_min_confidence_filters_items_and_total(self, db_session: AsyncSession):
        """min_confidence filters both returned items AND the total count."""
        user = await _make_user(db_session, "lst3")
        image1 = await _make_image(db_session, user, "lst3a")
        image2 = await _make_image(db_session, user, "lst3b")
        image3 = await _make_image(db_session, user, "lst3c")
        tag = await _make_tag(db_session, user, "lst3_tag")
        await _make_suggestion(db_session, image1, tag, confidence=0.9)
        await _make_suggestion(db_session, image2, tag, confidence=0.8)
        await _make_suggestion(db_session, image3, tag, confidence=0.3)
        await db_session.commit()

        items, total = await list_pending_for_tag(db_session, tag.tag_id, 0.75, 1, 10)

        assert total == 2  # only 0.9 and 0.8 qualify
        assert len(items) == 2
        for item in items:
            assert item[2] >= 0.75

    async def test_excludes_non_pending_suggestions(self, db_session: AsyncSession):
        """Approved and rejected suggestions are NOT returned."""
        user = await _make_user(db_session, "lst4")
        image1 = await _make_image(db_session, user, "lst4a")
        image2 = await _make_image(db_session, user, "lst4b")
        image3 = await _make_image(db_session, user, "lst4c")
        tag = await _make_tag(db_session, user, "lst4_tag")
        pending = await _make_suggestion(db_session, image1, tag, status="pending")
        await _make_suggestion(db_session, image2, tag, status="approved")
        await _make_suggestion(db_session, image3, tag, status="rejected")
        await db_session.commit()

        items, total = await list_pending_for_tag(db_session, tag.tag_id, 0.0, 1, 10)

        assert total == 1
        assert len(items) == 1
        assert items[0][0] == pending.suggestion_id

    async def test_item_structure_has_suggestion_id_image_id_confidence(self, db_session: AsyncSession):
        """Each item is a (suggestion_id, image_id, confidence) tuple."""
        user = await _make_user(db_session, "lst5")
        image = await _make_image(db_session, user, "lst5a")
        tag = await _make_tag(db_session, user, "lst5_tag")
        suggestion = await _make_suggestion(db_session, image, tag, confidence=0.77)
        await db_session.commit()

        items, total = await list_pending_for_tag(db_session, tag.tag_id, 0.0, 1, 10)

        assert total == 1
        assert len(items) == 1
        item = items[0]
        assert item[0] == suggestion.suggestion_id
        assert item[1] == image.image_id
        assert abs(item[2] - 0.77) < 1e-6

    async def test_returns_empty_for_other_tags_suggestions(self, db_session: AsyncSession):
        """Suggestions for a different tag_id are not returned."""
        user = await _make_user(db_session, "lst6")
        image = await _make_image(db_session, user, "lst6a")
        tag_a = await _make_tag(db_session, user, "lst6_a")
        tag_b = await _make_tag(db_session, user, "lst6_b")
        await _make_suggestion(db_session, image, tag_a)
        await db_session.commit()

        items, total = await list_pending_for_tag(db_session, tag_b.tag_id, 0.0, 1, 10)

        assert total == 0
        assert items == []
