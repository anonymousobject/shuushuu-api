"""Tests for bulk_review_suggestions — cross-image batch review service.

These tests exercise the service layer directly against the real test DB.
No mocked behavior is asserted; all assertions target real DB rows written
by the service.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.image import Images
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.user import Users
from app.services.ml_suggestion_review import (
    approve_pending_suggestions_for_links,
    bulk_review_suggestions,
)


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


async def _make_chain(db: AsyncSession, user: Users, suffix: str) -> tuple[Tags, Tags, Tags]:
    """grandparent <- parent <- child via inheritedfrom_id."""
    grandparent = await _make_tag(db, user, f"gp_{suffix}")
    parent = Tags(
        title=f"bulk tag p_{suffix}", type=1, user_id=user.user_id,
        inheritedfrom_id=grandparent.tag_id,
    )
    db.add(parent)
    await db.flush()
    child = Tags(
        title=f"bulk tag c_{suffix}", type=1, user_id=user.user_id,
        inheritedfrom_id=parent.tag_id,
    )
    db.add(child)
    await db.flush()
    return grandparent, parent, child


class TestAncestorCleanupOnApprove:
    """Creating a TagLink deletes now-redundant pending ancestor suggestions."""

    async def test_review_approve_child_deletes_pending_ancestors(
        self, db_session: AsyncSession
    ):
        user = await _make_user(db_session, "anc1")
        image = await _make_image(db_session, user, "anc1")
        grandparent, parent, child = await _make_chain(db_session, user, "anc1")
        sugg_gp = await _make_suggestion(db_session, image, grandparent)
        sugg_p = await _make_suggestion(db_session, image, parent)
        sugg_c = await _make_suggestion(db_session, image, child)
        await db_session.commit()

        result = await bulk_review_suggestions(
            db_session,
            [{"suggestion_id": sugg_c.suggestion_id, "action": "approve"}],
            user.user_id,
        )
        assert result.approved == 1
        # Reports exactly the deleted pending ancestor rows, not the reviewed child.
        assert set(result.removed_suggestion_ids) == {
            sugg_gp.suggestion_id,
            sugg_p.suggestion_id,
        }
        assert sugg_c.suggestion_id not in result.removed_suggestion_ids

        rows = (
            (
                await db_session.execute(
                    select(MlTagSuggestions).where(
                        MlTagSuggestions.image_id == image.image_id
                    )
                )
            )
            .scalars()
            .all()
        )
        by_tag = {r.tag_id: r for r in rows}
        assert by_tag[child.tag_id].status == "approved"
        # Both pending ancestors are deleted — including the grandparent.
        assert parent.tag_id not in by_tag
        assert grandparent.tag_id not in by_tag

    async def test_reviewed_ancestor_rows_are_never_touched(
        self, db_session: AsyncSession
    ):
        user = await _make_user(db_session, "anc2")
        image = await _make_image(db_session, user, "anc2")
        grandparent, parent, child = await _make_chain(db_session, user, "anc2")
        await _make_suggestion(db_session, image, grandparent, status="rejected")
        await _make_suggestion(db_session, image, parent, status="approved")
        sugg_c = await _make_suggestion(db_session, image, child)
        await db_session.commit()

        await bulk_review_suggestions(
            db_session,
            [{"suggestion_id": sugg_c.suggestion_id, "action": "approve"}],
            user.user_id,
        )

        rows = (
            (
                await db_session.execute(
                    select(MlTagSuggestions).where(
                        MlTagSuggestions.image_id == image.image_id
                    )
                )
            )
            .scalars()
            .all()
        )
        by_tag = {r.tag_id: r.status for r in rows}
        assert by_tag[grandparent.tag_id] == "rejected"
        assert by_tag[parent.tag_id] == "approved"
        assert by_tag[child.tag_id] == "approved"

    async def test_reject_does_not_delete_ancestors(self, db_session: AsyncSession):
        user = await _make_user(db_session, "anc3")
        image = await _make_image(db_session, user, "anc3")
        _grandparent, parent, child = await _make_chain(db_session, user, "anc3")
        sugg_p = await _make_suggestion(db_session, image, parent)
        sugg_c = await _make_suggestion(db_session, image, child)
        await db_session.commit()

        result = await bulk_review_suggestions(
            db_session,
            [{"suggestion_id": sugg_c.suggestion_id, "action": "reject"}],
            user.user_id,
        )
        # A reject never deletes ancestors — nothing to report.
        assert result.removed_suggestion_ids == []

        refreshed = (
            (
                await db_session.execute(
                    select(MlTagSuggestions).where(
                        MlTagSuggestions.suggestion_id == sugg_p.suggestion_id
                    )
                )
            )
            .scalars()
            .one_or_none()
        )
        assert refreshed is not None and refreshed.status == "pending"

    async def test_out_of_band_link_approval_deletes_pending_ancestors(
        self, db_session: AsyncSession
    ):
        """approve_pending_suggestions_for_links (manual add / batch tagging
        path) also deletes pending ancestor rows."""
        user = await _make_user(db_session, "anc4")
        image = await _make_image(db_session, user, "anc4")
        grandparent, _parent, child = await _make_chain(db_session, user, "anc4")
        await _make_suggestion(db_session, image, grandparent)
        await _make_suggestion(db_session, image, child)
        db_session.add(
            TagLinks(image_id=image.image_id, tag_id=child.tag_id, user_id=user.user_id)
        )
        await db_session.flush()

        await approve_pending_suggestions_for_links(
            db_session, [(image.image_id, child.tag_id)], user.user_id
        )
        await db_session.commit()

        rows = (
            (
                await db_session.execute(
                    select(MlTagSuggestions).where(
                        MlTagSuggestions.image_id == image.image_id
                    )
                )
            )
            .scalars()
            .all()
        )
        by_tag = {r.tag_id: r.status for r in rows}
        assert by_tag[child.tag_id] == "approved"
        assert grandparent.tag_id not in by_tag

    async def test_pending_descendant_of_applied_tag_survives(
        self, db_session: AsyncSession
    ):
        """Applying a PARENT tag must not delete the more-specific pending
        child suggestion (cleanup goes up the chain only)."""
        user = await _make_user(db_session, "anc5")
        image = await _make_image(db_session, user, "anc5")
        _grandparent, parent, child = await _make_chain(db_session, user, "anc5")
        sugg_c = await _make_suggestion(db_session, image, child)
        db_session.add(
            TagLinks(image_id=image.image_id, tag_id=parent.tag_id, user_id=user.user_id)
        )
        await db_session.flush()

        await approve_pending_suggestions_for_links(
            db_session, [(image.image_id, parent.tag_id)], user.user_id
        )
        await db_session.commit()

        refreshed = (
            (
                await db_session.execute(
                    select(MlTagSuggestions).where(
                        MlTagSuggestions.suggestion_id == sugg_c.suggestion_id
                    )
                )
            )
            .scalars()
            .one_or_none()
        )
        assert refreshed is not None and refreshed.status == "pending"

    async def test_same_batch_reject_ancestor_approve_child_keeps_rejection(
        self, db_session: AsyncSession
    ):
        """A moderator rejecting the parent and approving the child in ONE
        request must keep the parent row as 'rejected' — the cleanup DELETE
        must see the same-batch status changes (autoflush=False session).

        The db_session fixture itself runs with autoflush=True (unlike the
        production sessionmaker in app/core/database.py), which would flush
        the pending 'rejected' status ahead of the DELETE regardless of the
        fix under test. no_autoflush reproduces the production autoflush=False
        setting for the call so this test actually exercises the bug.
        """
        user = await _make_user(db_session, "anc6")
        image = await _make_image(db_session, user, "anc6")
        _grandparent, parent, child = await _make_chain(db_session, user, "anc6")
        sugg_p = await _make_suggestion(db_session, image, parent)
        sugg_c = await _make_suggestion(db_session, image, child)
        await db_session.commit()

        with db_session.no_autoflush:
            result = await bulk_review_suggestions(
                db_session,
                [
                    {"suggestion_id": sugg_p.suggestion_id, "action": "reject"},
                    {"suggestion_id": sugg_c.suggestion_id, "action": "approve"},
                ],
                user.user_id,
            )
        assert result.approved == 1 and result.rejected == 1

        rows = (
            (
                await db_session.execute(
                    select(MlTagSuggestions).where(
                        MlTagSuggestions.image_id == image.image_id
                    )
                )
            )
            .scalars()
            .all()
        )
        by_tag = {r.tag_id: r.status for r in rows}
        assert by_tag[parent.tag_id] == "rejected"
        assert by_tag[child.tag_id] == "approved"
