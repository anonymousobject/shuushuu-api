"""Tests for the suggestion-lifecycle hook (ADR-0002).

Pending rows are deleted when an image leaves suggestion-eligible status;
pending rows are re-seeded from the raw store when it returns. Reviewed rows
survive non-repost transitions. All against the real test DB.
"""

from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, settings
from app.models.image import Images
from app.models.ml_raw_prediction import MlExternalTags, MlModels, MlRawPredictions
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.tag import Tags
from app.models.user import Users
from app.services.ml_suggestion_lifecycle import sync_suggestions_for_status_transition

PIPELINE = "app.services.ml_suggestion_pipeline"
MODEL = "caformer_b36.dbv4-full"


async def _make_user(db: AsyncSession, suffix: str) -> Users:
    user = Users(
        username=f"lifecycle_{suffix}",
        email=f"lifecycle_{suffix}@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="testsalt12345678",
        active=1,
    )
    db.add(user)
    await db.flush()
    return user


async def _make_image(db: AsyncSession, user: Users, suffix: str, status: int) -> Images:
    image = Images(
        filename=f"2024-01-01-lifecycle-{suffix}",
        ext="jpg",
        user_id=user.user_id,
        md5_hash=f"lifecycle_hash_{suffix}",
        filesize=1024,
        width=800,
        height=600,
        status=status,
    )
    db.add(image)
    await db.flush()
    return image


async def _make_tag(db: AsyncSession, user: Users, suffix: str) -> Tags:
    tag = Tags(title=f"lifecycle tag {suffix}", type=1, user_id=user.user_id)
    db.add(tag)
    await db.flush()
    return tag


async def _make_suggestion(
    db: AsyncSession, image: Images, tag: Tags, status: str = "pending"
) -> MlTagSuggestions:
    suggestion = MlTagSuggestions(
        image_id=image.image_id,
        tag_id=tag.tag_id,
        confidence=0.9,
        model_version=MODEL,
        status=status,
    )
    db.add(suggestion)
    await db.flush()
    return suggestion


async def _suggestion_rows(db: AsyncSession, image_id: int) -> list[MlTagSuggestions]:
    return list(
        (
            await db.execute(
                select(MlTagSuggestions).where(MlTagSuggestions.image_id == image_id)
            )
        )
        .scalars()
        .all()
    )


async def _resolver_passthrough(db, suggestions):
    return suggestions


class TestEligibleToIneligible:
    async def test_deletes_pending_keeps_reviewed(self, db_session: AsyncSession):
        """ACTIVE -> DEACTIVATED deletes pending rows but keeps approved/rejected."""
        user = await _make_user(db_session, "e2i")
        image = await _make_image(db_session, user, "e2i", ImageStatus.ACTIVE)
        t1 = await _make_tag(db_session, user, "e2i_p")
        t2 = await _make_tag(db_session, user, "e2i_a")
        t3 = await _make_tag(db_session, user, "e2i_r")
        await _make_suggestion(db_session, image, t1, status="pending")
        await _make_suggestion(db_session, image, t2, status="approved")
        await _make_suggestion(db_session, image, t3, status="rejected")
        await db_session.commit()

        await sync_suggestions_for_status_transition(
            db_session, image.image_id, ImageStatus.ACTIVE, ImageStatus.DEACTIVATED
        )
        await db_session.commit()

        rows = await _suggestion_rows(db_session, image.image_id)
        statuses = sorted(r.status for r in rows)
        assert statuses == ["approved", "rejected"]


class TestNoOpTransitions:
    async def test_eligible_to_eligible_is_noop(self, db_session: AsyncSession):
        """ACTIVE -> SPOILER leaves pending rows alone."""
        user = await _make_user(db_session, "e2e")
        image = await _make_image(db_session, user, "e2e", ImageStatus.ACTIVE)
        tag = await _make_tag(db_session, user, "e2e")
        await _make_suggestion(db_session, image, tag, status="pending")
        await db_session.commit()

        await sync_suggestions_for_status_transition(
            db_session, image.image_id, ImageStatus.ACTIVE, ImageStatus.SPOILER
        )
        await db_session.commit()

        rows = await _suggestion_rows(db_session, image.image_id)
        assert len(rows) == 1
        assert rows[0].status == "pending"

    async def test_ineligible_to_ineligible_is_noop(self, db_session: AsyncSession):
        """DEACTIVATED -> REVIEW does not touch rows and does not re-seed."""
        user = await _make_user(db_session, "i2i")
        image = await _make_image(db_session, user, "i2i", ImageStatus.DEACTIVATED)
        tag = await _make_tag(db_session, user, "i2i")
        await _make_suggestion(db_session, image, tag, status="approved")
        await db_session.commit()

        await sync_suggestions_for_status_transition(
            db_session, image.image_id, ImageStatus.DEACTIVATED, ImageStatus.REVIEW
        )
        await db_session.commit()

        rows = await _suggestion_rows(db_session, image.image_id)
        assert len(rows) == 1
        assert rows[0].status == "approved"


class TestIneligibleToEligible:
    async def test_reseeds_pending_from_raw_store(
        self, db_session: AsyncSession, monkeypatch
    ):
        """DEACTIVATED -> ACTIVE re-seeds pending rows from ml_raw_predictions."""
        monkeypatch.setattr(settings, "ML_MODEL_NAME", MODEL)
        monkeypatch.setattr(settings, "ML_MIN_CONFIDENCE", 0.35)

        user = await _make_user(db_session, "i2e")
        image = await _make_image(db_session, user, "i2e", ImageStatus.ACTIVE)
        tag = await _make_tag(db_session, user, "i2e")

        # Seed the raw store: model -> external tag -> raw prediction.
        model_row = MlModels(name=MODEL)
        db_session.add(model_row)
        await db_session.flush()
        ext_tag = MlExternalTags(name="lifecycle_ext", category=0)
        db_session.add(ext_tag)
        await db_session.flush()
        db_session.add(
            MlRawPredictions(
                image_id=image.image_id,
                external_tag_id=ext_tag.id,
                model_id=model_row.id,
                confidence=0.9,
            )
        )
        await db_session.commit()

        # Patch resolvers so the external tag maps straight to our internal tag
        # (established pattern from tests/services/test_ml_remap.py).
        resolved = [{"tag_id": tag.tag_id, "confidence": 0.9, "model_version": MODEL}]

        async def _resolve_to_internal(db, suggestions):
            return [dict(r) for r in resolved]

        with (
            patch(f"{PIPELINE}.resolve_external_tags", _resolve_to_internal),
            patch(f"{PIPELINE}.resolve_tag_relationships", _resolver_passthrough),
        ):
            await sync_suggestions_for_status_transition(
                db_session, image.image_id, ImageStatus.DEACTIVATED, ImageStatus.ACTIVE
            )
        await db_session.commit()

        rows = await _suggestion_rows(db_session, image.image_id)
        assert len(rows) == 1
        assert rows[0].status == "pending"
        assert rows[0].tag_id == tag.tag_id
