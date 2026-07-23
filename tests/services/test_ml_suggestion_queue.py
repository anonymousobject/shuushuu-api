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
from app.models.tag_link import TagLinks
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


async def _apply_tag(db: AsyncSession, image: Images, tag: Tags, user: Users) -> None:
    """Apply a tag to an image directly (out-of-band of the ML review flow)."""
    db.add(TagLinks(image_id=image.image_id, tag_id=tag.tag_id, user_id=user.user_id))
    await db.flush()


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

        results, _total = await count_pending_by_tag(db_session)

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

        results, _total = await count_pending_by_tag(db_session)

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

        results, _total = await count_pending_by_tag(db_session, type_filter=TagType.CHARACTER)

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

        results, _total = await count_pending_by_tag(db_session, min_confidence=0.75)

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

        results, _total = await count_pending_by_tag(db_session)

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

    async def test_excludes_suggestions_whose_tag_is_already_applied(self, db_session: AsyncSession):
        """A pending suggestion whose tag was applied out of band is not listed.

        Covers the review-queue staleness bug: tags applied without going
        through the review flow left their suggestion rows 'pending', so the
        grid kept surfacing images that no longer needed review.
        """
        user = await _make_user(db_session, "lst7")
        image1 = await _make_image(db_session, user, "lst7a")
        image2 = await _make_image(db_session, user, "lst7b")
        tag = await _make_tag(db_session, user, "lst7_tag")
        await _make_suggestion(db_session, image1, tag, confidence=0.9)
        remaining = await _make_suggestion(db_session, image2, tag, confidence=0.8)
        # Tag applied to image1 out of band; suggestion row stays 'pending'.
        await _apply_tag(db_session, image1, tag, user)
        await db_session.commit()

        items, total = await list_pending_for_tag(db_session, tag.tag_id, 0.0, 1, 10)

        assert total == 1
        assert len(items) == 1
        assert items[0][0] == remaining.suggestion_id

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


class TestCountPendingByTagLimitAndSearch:
    """Tests for the per_page and search parameters of count_pending_by_tag."""

    async def test_per_page_returns_only_top_n_by_count(self, db_session: AsyncSession):
        """With per_page=2 and 3 tags seeded, only the top-2 by pending_count are returned."""
        user = await _make_user(db_session, "lim1")
        images = [await _make_image(db_session, user, f"lim1_{i}") for i in range(6)]
        # tag_x: 3 pending, tag_y: 2 pending, tag_z: 1 pending
        tag_x = await _make_tag(db_session, user, "lim1_x")
        tag_y = await _make_tag(db_session, user, "lim1_y")
        tag_z = await _make_tag(db_session, user, "lim1_z")
        for img in images[:3]:
            await _make_suggestion(db_session, img, tag_x)
        for img in images[3:5]:
            await _make_suggestion(db_session, img, tag_y)
        await _make_suggestion(db_session, images[5], tag_z)
        await db_session.commit()

        results, _total = await count_pending_by_tag(db_session, per_page=2)

        tag_ids = [r[0] for r in results]
        assert len([tid for tid in tag_ids if tid in {tag_x.tag_id, tag_y.tag_id, tag_z.tag_id}]) <= 2
        # The two returned tags from this seed must be tag_x and tag_y (highest counts)
        seeded_ids = {tid for tid in tag_ids if tid in {tag_x.tag_id, tag_y.tag_id, tag_z.tag_id}}
        assert tag_x.tag_id in seeded_ids
        assert tag_y.tag_id in seeded_ids
        assert tag_z.tag_id not in seeded_ids

    async def test_per_page_result_is_ordered_desc(self, db_session: AsyncSession):
        """Results with per_page are still ordered by pending_count DESC."""
        user = await _make_user(db_session, "lim2")
        images = [await _make_image(db_session, user, f"lim2_{i}") for i in range(3)]
        tag_a = await _make_tag(db_session, user, "lim2_a")
        tag_b = await _make_tag(db_session, user, "lim2_b")
        await _make_suggestion(db_session, images[0], tag_a)
        await _make_suggestion(db_session, images[1], tag_b)
        await _make_suggestion(db_session, images[2], tag_b)
        await db_session.commit()

        results, _total = await count_pending_by_tag(db_session, per_page=2)

        seeded = [r for r in results if r[0] in {tag_a.tag_id, tag_b.tag_id}]
        assert len(seeded) == 2
        # tag_b (count=2) must come before tag_a (count=1)
        seeded_ids = [r[0] for r in seeded]
        assert seeded_ids.index(tag_b.tag_id) < seeded_ids.index(tag_a.tag_id)

    async def test_search_filters_by_title(self, db_session: AsyncSession):
        """search='unique_xyz' returns only tags whose title contains that substring."""
        user = await _make_user(db_session, "srch1")
        image1 = await _make_image(db_session, user, "srch1a")
        image2 = await _make_image(db_session, user, "srch1b")
        tag_match = await _make_tag(db_session, user, "srch1_unique_xyz_tag")
        tag_nomatch = await _make_tag(db_session, user, "srch1_other_tag")
        await _make_suggestion(db_session, image1, tag_match)
        await _make_suggestion(db_session, image2, tag_nomatch)
        await db_session.commit()

        results, _total = await count_pending_by_tag(db_session, search="unique_xyz")

        tag_ids = [r[0] for r in results]
        assert tag_match.tag_id in tag_ids
        assert tag_nomatch.tag_id not in tag_ids

    async def test_search_is_case_insensitive(self, db_session: AsyncSession):
        """search='UPPER' matches a tag title stored as lower-case."""
        user = await _make_user(db_session, "srch2")
        image = await _make_image(db_session, user, "srch2a")
        tag = await _make_tag(db_session, user, "srch2_upper_case_tag")
        await _make_suggestion(db_session, image, tag)
        await db_session.commit()

        results, _total = await count_pending_by_tag(db_session, search="UPPER_CASE")

        tag_ids = [r[0] for r in results]
        assert tag.tag_id in tag_ids

    async def test_search_none_returns_all(self, db_session: AsyncSession):
        """search=None (default) does not filter by title."""
        user = await _make_user(db_session, "srch3")
        image1 = await _make_image(db_session, user, "srch3a")
        image2 = await _make_image(db_session, user, "srch3b")
        tag_a = await _make_tag(db_session, user, "srch3_alpha")
        tag_b = await _make_tag(db_session, user, "srch3_beta")
        await _make_suggestion(db_session, image1, tag_a)
        await _make_suggestion(db_session, image2, tag_b)
        await db_session.commit()

        results, _total = await count_pending_by_tag(db_session)

        tag_ids = [r[0] for r in results]
        assert tag_a.tag_id in tag_ids
        assert tag_b.tag_id in tag_ids


class TestCountPendingByTagPagination:
    """Tests for page/per_page pagination added to count_pending_by_tag."""

    async def test_returns_tuple_of_items_and_total(self, db_session: AsyncSession):
        """count_pending_by_tag returns a (items, total) tuple."""
        user = await _make_user(db_session, "pag0")
        image = await _make_image(db_session, user, "pag0a")
        tag = await _make_tag(db_session, user, "pag0_tag")
        await _make_suggestion(db_session, image, tag)
        await db_session.commit()

        result = await count_pending_by_tag(db_session, page=1, per_page=50)

        assert isinstance(result, tuple)
        assert len(result) == 2
        items, total = result
        assert isinstance(items, list)
        assert isinstance(total, int)

    async def test_page1_and_page2_return_different_items(self, db_session: AsyncSession):
        """With 12 tags seeded and per_page=5, page 1 and page 2 return disjoint sets."""
        user = await _make_user(db_session, "pag1")
        # 12 images so each tag gets exactly 1 pending suggestion
        images = [await _make_image(db_session, user, f"pag1_{i}") for i in range(12)]
        tags = [await _make_tag(db_session, user, f"pag1_t{i}") for i in range(12)]
        for img, tag in zip(images, tags):
            await _make_suggestion(db_session, img, tag)
        await db_session.commit()

        items_p1, total_p1 = await count_pending_by_tag(db_session, page=1, per_page=5)
        items_p2, total_p2 = await count_pending_by_tag(db_session, page=2, per_page=5)

        ids_p1 = {item[0] for item in items_p1}
        ids_p2 = {item[0] for item in items_p2}
        # Pages must not overlap
        assert ids_p1.isdisjoint(ids_p2)
        # Both pages must be non-empty (12 tags with per_page=5 gives 3 pages)
        assert len(items_p1) == 5
        assert len(items_p2) == 5

    async def test_total_is_distinct_tag_count_independent_of_page(self, db_session: AsyncSession):
        """total reflects all distinct pending tag ids, not just the current page."""
        user = await _make_user(db_session, "pag2")
        images = [await _make_image(db_session, user, f"pag2_{i}") for i in range(12)]
        tags = [await _make_tag(db_session, user, f"pag2_t{i}") for i in range(12)]
        seeded_tag_ids = {tag.tag_id for tag in tags}
        for img, tag in zip(images, tags):
            await _make_suggestion(db_session, img, tag)
        await db_session.commit()

        _items_p1, total_p1 = await count_pending_by_tag(db_session, page=1, per_page=5)
        _items_p2, total_p2 = await count_pending_by_tag(db_session, page=2, per_page=5)

        # total must be at least 12 (our seeded tags), same on both pages
        assert total_p1 >= 12
        assert total_p1 == total_p2

    async def test_items_ordered_by_pending_count_desc(self, db_session: AsyncSession):
        """Items on page 1 are ordered by pending_count DESC."""
        user = await _make_user(db_session, "pag3")
        images = [await _make_image(db_session, user, f"pag3_{i}") for i in range(6)]
        tag_hi = await _make_tag(db_session, user, "pag3_hi")  # 3 pending
        tag_lo = await _make_tag(db_session, user, "pag3_lo")  # 1 pending
        for img in images[:3]:
            await _make_suggestion(db_session, img, tag_hi)
        await _make_suggestion(db_session, images[3], tag_lo)
        await db_session.commit()

        items, _total = await count_pending_by_tag(db_session, page=1, per_page=50)

        seeded = [item for item in items if item[0] in {tag_hi.tag_id, tag_lo.tag_id}]
        assert len(seeded) == 2
        seeded_ids = [item[0] for item in seeded]
        assert seeded_ids.index(tag_hi.tag_id) < seeded_ids.index(tag_lo.tag_id)


async def _make_child_tag(
    db: AsyncSession, user: Users, suffix: str, parent: Tags
) -> Tags:
    tag = Tags(
        title=f"queue tag {suffix}", type=TagType.THEME, user_id=user.user_id,
        inheritedfrom_id=parent.tag_id,
    )
    db.add(tag)
    await db.flush()
    return tag


class TestListPendingDescendantHiding:
    """An ancestor's pending suggestion is hidden from its per-tag queue while
    a more-specific descendant suggestion is pending on the same image, so
    per-tag review is most-specific-first by construction."""

    async def test_pending_child_hides_parent_row(self, db_session: AsyncSession):
        user = await _make_user(db_session, "hide1")
        image = await _make_image(db_session, user, "hide1")
        parent = await _make_tag(db_session, user, "hide1_parent")
        child = await _make_child_tag(db_session, user, "hide1_child", parent)
        await _make_suggestion(db_session, image, parent)
        await _make_suggestion(db_session, image, child)
        await db_session.commit()

        items, total = await list_pending_for_tag(
            db_session, parent.tag_id, min_confidence=0.0, page=1, per_page=10
        )
        assert total == 0 and items == []

        # The child's own queue still shows the image.
        items, total = await list_pending_for_tag(
            db_session, child.tag_id, min_confidence=0.0, page=1, per_page=10
        )
        assert total == 1

    async def test_pending_grandchild_hides_grandparent_row(
        self, db_session: AsyncSession
    ):
        """Transitive: the intermediate tag has NO suggestion row at all."""
        user = await _make_user(db_session, "hide2")
        image = await _make_image(db_session, user, "hide2")
        grandparent = await _make_tag(db_session, user, "hide2_gp")
        parent = await _make_child_tag(db_session, user, "hide2_p", grandparent)
        grandchild = await _make_child_tag(db_session, user, "hide2_c", parent)
        await _make_suggestion(db_session, image, grandparent)
        await _make_suggestion(db_session, image, grandchild)
        await db_session.commit()

        items, total = await list_pending_for_tag(
            db_session, grandparent.tag_id, min_confidence=0.0, page=1, per_page=10
        )
        assert total == 0 and items == []

    async def test_rejected_descendant_does_not_hide(self, db_session: AsyncSession):
        """Rejecting the child resurfaces the parent — the cascade."""
        user = await _make_user(db_session, "hide3")
        image = await _make_image(db_session, user, "hide3")
        parent = await _make_tag(db_session, user, "hide3_parent")
        child = await _make_child_tag(db_session, user, "hide3_child", parent)
        await _make_suggestion(db_session, image, parent)
        await _make_suggestion(db_session, image, child, status="rejected")
        await db_session.commit()

        items, total = await list_pending_for_tag(
            db_session, parent.tag_id, min_confidence=0.0, page=1, per_page=10
        )
        assert total == 1

    async def test_unrelated_pending_does_not_hide(self, db_session: AsyncSession):
        user = await _make_user(db_session, "hide4")
        image = await _make_image(db_session, user, "hide4")
        parent = await _make_tag(db_session, user, "hide4_parent")
        unrelated = await _make_tag(db_session, user, "hide4_other")
        await _make_suggestion(db_session, image, parent)
        await _make_suggestion(db_session, image, unrelated)
        await db_session.commit()

        items, total = await list_pending_for_tag(
            db_session, parent.tag_id, min_confidence=0.0, page=1, per_page=10
        )
        assert total == 1

    async def test_hiding_is_per_image(self, db_session: AsyncSession):
        """Another image whose parent suggestion has no pending descendant
        stays listed while the blocked image is hidden."""
        user = await _make_user(db_session, "hide5")
        image_blocked = await _make_image(db_session, user, "hide5a")
        image_free = await _make_image(db_session, user, "hide5b")
        parent = await _make_tag(db_session, user, "hide5_parent")
        child = await _make_child_tag(db_session, user, "hide5_child", parent)
        await _make_suggestion(db_session, image_blocked, parent)
        await _make_suggestion(db_session, image_blocked, child)
        await _make_suggestion(db_session, image_free, parent)
        await db_session.commit()

        items, total = await list_pending_for_tag(
            db_session, parent.tag_id, min_confidence=0.0, page=1, per_page=10
        )
        assert total == 1
        assert [item[1] for item in items] == [image_free.image_id]
