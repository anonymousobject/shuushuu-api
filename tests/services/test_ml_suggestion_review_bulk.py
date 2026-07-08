"""Tests for bulk_review_suggestions — cross-image batch review service.

These tests exercise the service layer directly against the real test DB.
No mocked behavior is asserted; all assertions target real DB rows written
by the service.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.image import Images
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.user import Users
from app.services.ml_suggestion_review import bulk_review_suggestions


async def _make_user(db: AsyncSession, suffix: str) -> Users:
    user = Users(
        username=f"bulk_rev_{suffix}",
        email=f"bulk_rev_{suffix}@example.com",
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
        filename=f"2024-01-01-bulk-{suffix}",
        ext="jpg",
        user_id=user.user_id,
        md5_hash=f"bulk_hash_{suffix}",
        filesize=1024,
        width=800,
        height=600,
    )
    db.add(image)
    await db.flush()
    return image


async def _make_tag(db: AsyncSession, user: Users, suffix: str) -> Tags:
    tag = Tags(title=f"bulk tag {suffix}", type=1, user_id=user.user_id)
    db.add(tag)
    await db.flush()
    return tag


async def _make_suggestion(
    db: AsyncSession, image: Images, tag: Tags, status: str = "pending"
) -> MlTagSuggestions:
    suggestion = MlTagSuggestions(
        image_id=image.image_id,
        tag_id=tag.tag_id,
        confidence=0.88,
        model_version="v3",
        status=status,
    )
    db.add(suggestion)
    await db.flush()
    return suggestion


class TestBulkReviewSuggestions:
    """Direct service-layer tests for bulk_review_suggestions."""

    async def test_approve_and_reject_across_two_images(self, db_session: AsyncSession):
        """Approving from image1 and rejecting from image2 in one bulk call.

        Asserts:
        - s1 (approve): TagLink created, status=approved
        - s2 (reject): no TagLink, status=rejected
        - return value: {approved:1, rejected:1, errors:[]}
        """
        user = await _make_user(db_session, "two_images")
        image1 = await _make_image(db_session, user, "a")
        image2 = await _make_image(db_session, user, "b")
        tag1 = await _make_tag(db_session, user, "a")
        tag2 = await _make_tag(db_session, user, "b")
        s1 = await _make_suggestion(db_session, image1, tag1)
        s2 = await _make_suggestion(db_session, image2, tag2)
        await db_session.commit()

        reviews = [
            {"suggestion_id": s1.suggestion_id, "action": "approve"},
            {"suggestion_id": s2.suggestion_id, "action": "reject"},
        ]
        result = await bulk_review_suggestions(db_session, reviews, user.user_id)

        assert result.approved == 1
        assert result.rejected == 1
        assert result.errors == []

        # s1: TagLink must exist on image1/tag1
        link_result = await db_session.execute(
            select(TagLinks).where(
                TagLinks.image_id == image1.image_id,
                TagLinks.tag_id == tag1.tag_id,
            )
        )
        assert link_result.scalar_one_or_none() is not None

        # s2: no TagLink on image2/tag2
        no_link_result = await db_session.execute(
            select(TagLinks).where(
                TagLinks.image_id == image2.image_id,
                TagLinks.tag_id == tag2.tag_id,
            )
        )
        assert no_link_result.scalar_one_or_none() is None

        # Suggestion statuses updated
        await db_session.refresh(s1)
        await db_session.refresh(s2)
        assert s1.status == "approved"
        assert s1.reviewed_by_user_id == user.user_id
        assert s1.reviewed_at is not None
        assert s2.status == "rejected"
        assert s2.reviewed_by_user_id == user.user_id
        assert s2.reviewed_at is not None

    async def test_nonexistent_suggestion_goes_to_errors(self, db_session: AsyncSession):
        """A suggestion_id that doesn't exist lands in errors; valid ones still process."""
        user = await _make_user(db_session, "errors")
        image = await _make_image(db_session, user, "err")
        tag = await _make_tag(db_session, user, "err")
        valid = await _make_suggestion(db_session, image, tag)
        await db_session.commit()

        nonexistent_id = 999_888_777

        reviews = [
            {"suggestion_id": valid.suggestion_id, "action": "approve"},
            {"suggestion_id": nonexistent_id, "action": "approve"},
        ]
        result = await bulk_review_suggestions(db_session, reviews, user.user_id)

        assert result.approved == 1
        assert result.rejected == 0
        assert len(result.errors) == 1
        assert str(nonexistent_id) in result.errors[0]

        # Valid suggestion was still processed
        await db_session.refresh(valid)
        assert valid.status == "approved"

    async def test_approve_does_not_duplicate_existing_tag_link(self, db_session: AsyncSession):
        """Approving a suggestion whose TagLink already exists is idempotent.

        Asserts:
        - No error is raised (returns approved:1 / errors:[])
        - Exactly one TagLink for (image_id, tag_id) exists afterward
        """
        user = await _make_user(db_session, "idem")
        image = await _make_image(db_session, user, "idem")
        tag = await _make_tag(db_session, user, "idem")
        suggestion = await _make_suggestion(db_session, image, tag)

        # Pre-create the TagLink that the approval would normally create.
        existing_link = TagLinks(
            image_id=image.image_id,
            tag_id=tag.tag_id,
            user_id=user.user_id,
        )
        db_session.add(existing_link)
        await db_session.commit()

        result = await bulk_review_suggestions(
            db_session,
            [{"suggestion_id": suggestion.suggestion_id, "action": "approve"}],
            user.user_id,
        )

        assert result.approved == 1
        assert result.errors == []

        # Exactly one TagLink must exist — no duplicate created.
        links_result = await db_session.execute(
            select(TagLinks).where(
                TagLinks.image_id == image.image_id,
                TagLinks.tag_id == tag.tag_id,
            )
        )
        assert len(links_result.scalars().all()) == 1
