"""Tests for approve_pending_suggestions_for_links — out-of-band invalidation.

When a tag is applied to an image without going through the ML review flow
(manual tag add, batch tagging, report resolution), the matching pending
suggestion row must be marked approved so the review queue and pending counts
stay honest. These tests exercise the service helper directly against the
real test DB.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.models.image import Images
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.tag import Tags
from app.models.user import Users
from app.services.ml_suggestion_review import approve_pending_suggestions_for_links


async def _make_user(db: AsyncSession, suffix: str) -> Users:
    user = Users(
        username=f"inval_svc_{suffix}",
        email=f"inval_svc_{suffix}@example.com",
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
        filename=f"2024-01-01-inval-{suffix}",
        ext="jpg",
        user_id=user.user_id,
        md5_hash=f"inval_hash_{suffix}",
        filesize=1024,
        width=800,
        height=600,
    )
    db.add(image)
    await db.flush()
    return image


async def _make_tag(db: AsyncSession, user: Users, suffix: str) -> Tags:
    tag = Tags(title=f"inval tag {suffix}", type=TagType.THEME, user_id=user.user_id)
    db.add(tag)
    await db.flush()
    return tag


async def _make_suggestion(
    db: AsyncSession,
    image: Images,
    tag: Tags,
    status: str = "pending",
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


class TestApprovePendingSuggestionsForLinks:
    """Tests for the out-of-band suggestion invalidation helper."""

    async def test_marks_matching_pending_suggestion_approved(self, db_session: AsyncSession):
        """A pending suggestion matching an applied (image, tag) pair is approved."""
        user = await _make_user(db_session, "m1")
        tagger = await _make_user(db_session, "m1_tagger")
        image = await _make_image(db_session, user, "m1a")
        tag = await _make_tag(db_session, user, "m1_tag")
        suggestion = await _make_suggestion(db_session, image, tag)

        await approve_pending_suggestions_for_links(
            db_session, [(image.image_id, tag.tag_id)], tagger.user_id
        )
        await db_session.commit()

        await db_session.refresh(suggestion)
        assert suggestion.status == "approved"
        assert suggestion.reviewed_at is not None
        assert suggestion.reviewed_by_user_id == tagger.user_id

    async def test_ignores_non_matching_pairs(self, db_session: AsyncSession):
        """Suggestions for other tags or other images are left pending."""
        user = await _make_user(db_session, "m2")
        image1 = await _make_image(db_session, user, "m2a")
        image2 = await _make_image(db_session, user, "m2b")
        tag1 = await _make_tag(db_session, user, "m2_t1")
        tag2 = await _make_tag(db_session, user, "m2_t2")
        other_tag = await _make_suggestion(db_session, image1, tag2)
        other_image = await _make_suggestion(db_session, image2, tag1)

        await approve_pending_suggestions_for_links(
            db_session, [(image1.image_id, tag1.tag_id)], user.user_id
        )
        await db_session.commit()

        await db_session.refresh(other_tag)
        await db_session.refresh(other_image)
        assert other_tag.status == "pending"
        assert other_image.status == "pending"

    async def test_does_not_touch_rejected_suggestions(self, db_session: AsyncSession):
        """A rejected suggestion stays rejected even if its tag gets applied."""
        user = await _make_user(db_session, "m3")
        image = await _make_image(db_session, user, "m3a")
        tag = await _make_tag(db_session, user, "m3_tag")
        rejected = await _make_suggestion(db_session, image, tag, status="rejected")

        await approve_pending_suggestions_for_links(
            db_session, [(image.image_id, tag.tag_id)], user.user_id
        )
        await db_session.commit()

        await db_session.refresh(rejected)
        assert rejected.status == "rejected"
        assert rejected.reviewed_by_user_id is None

    async def test_handles_multiple_pairs(self, db_session: AsyncSession):
        """All matching pending suggestions across several pairs are approved."""
        user = await _make_user(db_session, "m4")
        image1 = await _make_image(db_session, user, "m4a")
        image2 = await _make_image(db_session, user, "m4b")
        tag1 = await _make_tag(db_session, user, "m4_t1")
        tag2 = await _make_tag(db_session, user, "m4_t2")
        s1 = await _make_suggestion(db_session, image1, tag1)
        s2 = await _make_suggestion(db_session, image2, tag2)

        await approve_pending_suggestions_for_links(
            db_session,
            [(image1.image_id, tag1.tag_id), (image2.image_id, tag2.tag_id)],
            user.user_id,
        )
        await db_session.commit()

        await db_session.refresh(s1)
        await db_session.refresh(s2)
        assert s1.status == "approved"
        assert s2.status == "approved"

    async def test_empty_pairs_is_a_noop(self, db_session: AsyncSession):
        """Calling with no pairs issues no update and raises nothing."""
        user = await _make_user(db_session, "m5")
        image = await _make_image(db_session, user, "m5a")
        tag = await _make_tag(db_session, user, "m5_tag")
        suggestion = await _make_suggestion(db_session, image, tag)

        await approve_pending_suggestions_for_links(db_session, [], user.user_id)
        await db_session.commit()

        await db_session.refresh(suggestion)
        assert suggestion.status == "pending"
