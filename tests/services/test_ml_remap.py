"""
Tests for the ML remap service.

``remap_image`` re-maps raw predictions to pending MlTagSuggestions rows:
- adds pending for a newly-implied tag with no existing suggestion row
- deletes stale pending rows for the SAME model when a tag is no longer implied
- preserves pending rows from a DIFFERENT model (cross-source clobber guard)
- preserves approved/rejected rows and does not re-add them
- does NOT reset approved rows whose image-tag was removed (unlike store_predictions)

``remap_image_from_store`` reads ml_raw_predictions for a given image+model and
calls remap_image, exercising the 3-table join and the model-name filter.

The seeding approach mirrors test_ml_suggestion_pipeline.py: resolve_external_tags
and resolve_tag_relationships are patched to pass tag_id-carrying dicts straight
through, so each test fully controls the implied set and focuses on the reconcile
logic rather than on the mapping/resolution path.
"""

from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.image import Images
from app.models.ml_raw_prediction import MlExternalTags, MlModels, MlRawPredictions
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.tag import Tags
from app.models.tag_mapping import TagMappings
from app.services.ml_remap import remap_image, remap_image_from_store, remap_images_for_tag

PIPELINE = "app.services.ml_suggestion_pipeline"

CAFORMER = "caformer_b36.dbv4-full"
SWINV2 = "wd-swinv2-tagger-v3"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _preds(*tag_ids: int, model: str = CAFORMER, confidence: float = 0.9) -> list[dict[str, Any]]:
    """Build canned predictions already carrying internal tag_ids (resolvers will be patched out)."""
    return [
        {"tag_id": tid, "confidence": confidence, "model_version": model}
        for tid in tag_ids
    ]


async def _resolver_passthrough(db, suggestions):
    return suggestions


def _resolver_to_tag_ids(rows: list[dict[str, Any]]):
    """Fake resolve_external_tags that returns the given tag_id rows unchanged."""

    async def _resolver(db, suggestions):
        return [dict(r) for r in rows]

    return _resolver


async def _make_user(db_session, suffix: str):
    from app.models.user import Users

    user = Users(
        username=f"remap_{suffix}",
        email=f"remap_{suffix}@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="testsalt12345678",
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _make_image(db_session, user, suffix: str) -> Images:
    image = Images(
        filename=f"2024-01-01-remap-{suffix}",
        ext="jpg",
        user_id=user.user_id,
        md5_hash=f"hash_remap_{suffix}",
        filesize=1024,
        width=800,
        height=600,
    )
    db_session.add(image)
    await db_session.flush()
    return image


async def _make_tags(db_session, *tag_ids: int) -> None:
    """Ensure Tags rows exist for each id (idempotent flush, no commit)."""
    for tid in tag_ids:
        db_session.add(Tags(tag_id=tid, title=f"tag_{tid}"))
    await db_session.flush()


# ---------------------------------------------------------------------------
# Case 1: adds pending for a newly-implied tag with no existing row
# ---------------------------------------------------------------------------


async def test_adds_pending_for_new_implied_tag(db_session, monkeypatch):
    """remap_image inserts a pending suggestion for an implied tag with no prior row."""
    monkeypatch.setattr(settings, "ML_MIN_CONFIDENCE", 0.35)

    user = await _make_user(db_session, "case1")
    image = await _make_image(db_session, user, "case1")
    await _make_tags(db_session, 101)
    await db_session.commit()

    predictions = _preds(101, model=CAFORMER)

    with (
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(predictions)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _resolver_passthrough),
    ):
        added = await remap_image(db_session, image.image_id, predictions, CAFORMER)

    assert added == 1

    rows = (
        await db_session.execute(
            select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.tag_id == 101
    assert row.status == "pending"
    assert row.model_version == CAFORMER
    assert row.confidence == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# Case 2: deletes ONLY the stale same-model pending row for a tag no longer implied
# ---------------------------------------------------------------------------


async def test_deletes_stale_same_model_pending(db_session, monkeypatch):
    """remap_image deletes pending rows (same model) for tags no longer implied,
    but keeps pending rows (same model) for tags that ARE still implied."""
    monkeypatch.setattr(settings, "ML_MIN_CONFIDENCE", 0.35)

    user = await _make_user(db_session, "case2")
    image = await _make_image(db_session, user, "case2")
    await _make_tags(db_session, 201, 202)

    # tag 201 — stale pending (same model, NOT in new implied set)
    db_session.add(
        MlTagSuggestions(
            image_id=image.image_id, tag_id=201, confidence=0.8,
            model_version=CAFORMER, status="pending",
        )
    )
    # tag 202 — still-implied pending (same model, IS in new implied set)
    db_session.add(
        MlTagSuggestions(
            image_id=image.image_id, tag_id=202, confidence=0.8,
            model_version=CAFORMER, status="pending",
        )
    )
    await db_session.commit()

    # New implied set: only tag 202 (tag 201 is gone)
    predictions = _preds(202, model=CAFORMER)

    with (
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(predictions)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _resolver_passthrough),
    ):
        added = await remap_image(db_session, image.image_id, predictions, CAFORMER)

    # tag 202 already had a row → nothing new added
    assert added == 0

    rows = (
        await db_session.execute(
            select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
        )
    ).scalars().all()

    tag_ids = {r.tag_id for r in rows}
    # Stale tag 201 must be gone
    assert 201 not in tag_ids, "stale same-model pending should have been deleted"
    # Still-implied tag 202 must survive
    assert 202 in tag_ids, "still-implied same-model pending should be kept"


# ---------------------------------------------------------------------------
# Case 3: preserves pending rows from a DIFFERENT model (cross-source clobber guard)
# ---------------------------------------------------------------------------


async def test_preserves_different_model_pending(db_session, monkeypatch):
    """remap_image with model=caformer must NOT delete pending rows from swinv2,
    even if those swinv2 rows refer to tags that are NOT in the caformer implied set."""
    monkeypatch.setattr(settings, "ML_MIN_CONFIDENCE", 0.35)

    user = await _make_user(db_session, "case3")
    image = await _make_image(db_session, user, "case3")
    await _make_tags(db_session, 301, 302)

    # tag 301 — pending from swinv2, NOT in caformer implied set
    db_session.add(
        MlTagSuggestions(
            image_id=image.image_id, tag_id=301, confidence=0.85,
            model_version=SWINV2, status="pending",
        )
    )
    await db_session.commit()

    # Caformer re-map implies only tag 302 — tag 301 is absent but belongs to swinv2
    predictions = _preds(302, model=CAFORMER)

    with (
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(predictions)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _resolver_passthrough),
    ):
        added = await remap_image(db_session, image.image_id, predictions, CAFORMER)

    # tag 302 has no prior row → 1 added
    assert added == 1

    rows = (
        await db_session.execute(
            select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
        )
    ).scalars().all()
    by_tag = {r.tag_id: r for r in rows}

    # swinv2 row for tag 301 must still be there — cross-source clobber guard
    assert 301 in by_tag, "swinv2 pending row must NOT be deleted by caformer remap"
    assert by_tag[301].model_version == SWINV2
    assert by_tag[301].status == "pending"

    # caformer added tag 302
    assert 302 in by_tag
    assert by_tag[302].model_version == CAFORMER


# ---------------------------------------------------------------------------
# Case 4: preserves approved/rejected rows; does not re-add them
# ---------------------------------------------------------------------------


async def test_preserves_approved_and_rejected(db_session, monkeypatch):
    """Approved and rejected suggestions are left as-is; no new pending is added
    for the same tag (dismissed stays dismissed)."""
    monkeypatch.setattr(settings, "ML_MIN_CONFIDENCE", 0.35)

    user = await _make_user(db_session, "case4")
    image = await _make_image(db_session, user, "case4")
    await _make_tags(db_session, 401, 402)

    approved_row = MlTagSuggestions(
        image_id=image.image_id, tag_id=401, confidence=0.9,
        model_version=CAFORMER, status="approved",
    )
    rejected_row = MlTagSuggestions(
        image_id=image.image_id, tag_id=402, confidence=0.9,
        model_version=CAFORMER, status="rejected",
    )
    db_session.add_all([approved_row, rejected_row])
    await db_session.commit()

    # Both tags are in the implied set — but they already have rows → skip both
    predictions = _preds(401, 402, model=CAFORMER)

    with (
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(predictions)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _resolver_passthrough),
    ):
        added = await remap_image(db_session, image.image_id, predictions, CAFORMER)

    assert added == 0

    rows = (
        await db_session.execute(
            select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
        )
    ).scalars().all()
    assert len(rows) == 2
    by_tag = {r.tag_id: r for r in rows}

    assert by_tag[401].status == "approved", "approved row must remain approved"
    assert by_tag[402].status == "rejected", "rejected row must remain rejected"


# ---------------------------------------------------------------------------
# Case 5: does NOT reset approved rows whose image-tag was removed
# ---------------------------------------------------------------------------


async def test_does_not_reset_approved_when_tag_removed(db_session, monkeypatch):
    """remap_image has NO reset logic: an approved suggestion whose tag is no
    longer applied to the image stays approved. This is a deliberate divergence
    from store_predictions, which WOULD reset it."""
    monkeypatch.setattr(settings, "ML_MIN_CONFIDENCE", 0.35)

    user = await _make_user(db_session, "case5")
    image = await _make_image(db_session, user, "case5")
    await _make_tags(db_session, 501)

    # Approved suggestion; tag 501 is NOT in TagLinks (tag was removed from image)
    approved_row = MlTagSuggestions(
        image_id=image.image_id, tag_id=501, confidence=0.9,
        model_version=CAFORMER, status="approved",
    )
    db_session.add(approved_row)
    await db_session.commit()

    # The current implied set does NOT include tag 501 (e.g. model no longer predicts it)
    predictions: list[dict[str, Any]] = []

    with (
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(predictions)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _resolver_passthrough),
    ):
        added = await remap_image(db_session, image.image_id, predictions, CAFORMER)

    assert added == 0

    rows = (
        await db_session.execute(
            select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    # remap_image must not have reset the approved row — it stays approved
    assert rows[0].status == "approved", "approved row must NOT be reset by remap_image"
    assert rows[0].tag_id == 501


# ---------------------------------------------------------------------------
# remap_image_from_store: 3-table join + model-name filter
# ---------------------------------------------------------------------------


async def test_remap_image_from_store_creates_suggestion(db_session, monkeypatch):
    """remap_image_from_store reads ml_raw_predictions for the given model+image
    and creates a pending suggestion for the implied tag."""
    monkeypatch.setattr(settings, "ML_MIN_CONFIDENCE", 0.35)

    user = await _make_user(db_session, "store1")
    image = await _make_image(db_session, user, "store1")
    await _make_tags(db_session, 601)
    await db_session.commit()

    # Seed ml_models + ml_external_tags + ml_raw_predictions
    model_row = MlModels(name=CAFORMER)
    db_session.add(model_row)
    await db_session.flush()

    ext_tag = MlExternalTags(name="long_hair", category=0)
    db_session.add(ext_tag)
    await db_session.flush()

    db_session.add(
        MlRawPredictions(
            image_id=image.image_id,
            model_id=model_row.id,
            external_tag_id=ext_tag.id,
            confidence=0.9,
        )
    )
    await db_session.commit()

    # Patch resolvers so "long_hair" → tag_id=601
    resolved = [{"tag_id": 601, "confidence": 0.9, "model_version": CAFORMER}]

    with (
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(resolved)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _resolver_passthrough),
    ):
        added = await remap_image_from_store(db_session, image.image_id, CAFORMER)

    assert added == 1

    rows = (
        await db_session.execute(
            select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].tag_id == 601
    assert rows[0].status == "pending"
    assert rows[0].model_version == CAFORMER


# ---------------------------------------------------------------------------
# Case 6: cross-model ADD guard — never inserts a duplicate (image, tag) row
# ---------------------------------------------------------------------------


async def test_does_not_duplicate_tag_held_by_another_model(db_session, monkeypatch):
    """remap_image with caformer must NOT insert a second MlTagSuggestions row for
    a tag that already has a pending row from swinv2.

    The UNIQUE(image_id, tag_id) constraint means a second insert would raise an
    integrity error. The add-guard (existing_tag_ids covers all statuses/models)
    must skip T silently and only insert U. The swinv2 row for T must be
    untouched — not duplicated, not overwritten, not deleted."""
    monkeypatch.setattr(settings, "ML_MIN_CONFIDENCE", 0.35)

    SWINV2_FULL = "swinv2_base_window8_256.dbv4-full"

    user = await _make_user(db_session, "case6")
    image = await _make_image(db_session, user, "case6")
    await _make_tags(db_session, 601, 602)

    # tag 601 (T) — existing pending row from a DIFFERENT model (swinv2)
    db_session.add(
        MlTagSuggestions(
            image_id=image.image_id, tag_id=601, confidence=0.75,
            model_version=SWINV2_FULL, status="pending",
        )
    )
    await db_session.commit()

    # caformer re-map implies BOTH T (601) and U (602)
    predictions = _preds(601, 602, model=CAFORMER)

    # No exception must be raised (no UNIQUE violation)
    with (
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(predictions)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _resolver_passthrough),
    ):
        added = await remap_image(db_session, image.image_id, predictions, CAFORMER)

    # Only U (602) was added; T (601) was skipped by the add-guard
    assert added == 1

    rows = (
        await db_session.execute(
            select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
        )
    ).scalars().all()

    # Exactly one row for tag T — no duplication
    t_rows = [r for r in rows if r.tag_id == 601]
    assert len(t_rows) == 1, "tag T must have exactly one MlTagSuggestions row (no duplication)"
    t = t_rows[0]
    assert t.status == "pending", "swinv2 row for T must remain pending (untouched)"
    assert t.model_version == SWINV2_FULL, "swinv2 row for T must not be overwritten"

    # Tag U got a new caformer row
    u_rows = [r for r in rows if r.tag_id == 602]
    assert len(u_rows) == 1
    assert u_rows[0].status == "pending"
    assert u_rows[0].model_version == CAFORMER


async def test_remap_images_for_tag_scopes_to_mapped_images(db_session, monkeypatch):
    """remap_images_for_tag remaps only images that have raw predictions for
    external tags that map to the given internal_tag_id; images with unrelated
    raw predictions are left untouched."""
    monkeypatch.setattr(settings, "ML_MIN_CONFIDENCE", 0.35)

    user = await _make_user(db_session, "ftag1")
    image_a = await _make_image(db_session, user, "ftag1a")
    image_b = await _make_image(db_session, user, "ftag1b")
    # internal tag 801 is the target; tag 802 is unrelated
    await _make_tags(db_session, 801, 802)
    await db_session.commit()

    # Seed model + external tags
    model_row = MlModels(name=CAFORMER)
    db_session.add(model_row)
    await db_session.flush()

    ext_tag_a = MlExternalTags(name="blue_eyes", category=0)
    ext_tag_b = MlExternalTags(name="red_hair", category=0)
    db_session.add_all([ext_tag_a, ext_tag_b])
    await db_session.flush()

    # tag_mappings: blue_eyes → internal tag 801; red_hair is unmapped (no row)
    db_session.add(TagMappings(external_tag="blue_eyes", internal_tag_id=801))
    await db_session.flush()

    # Image A has a raw prediction for blue_eyes (the mapped tag)
    db_session.add(
        MlRawPredictions(
            image_id=image_a.image_id,
            model_id=model_row.id,
            external_tag_id=ext_tag_a.id,
            confidence=0.92,
        )
    )
    # Image B has a raw prediction for red_hair only (unrelated — no mapping to 801)
    db_session.add(
        MlRawPredictions(
            image_id=image_b.image_id,
            model_id=model_row.id,
            external_tag_id=ext_tag_b.id,
            confidence=0.88,
        )
    )
    await db_session.commit()

    # Patch resolvers so blue_eyes → tag_id=801 passes through
    resolved_a = [{"tag_id": 801, "confidence": 0.92, "model_version": CAFORMER}]

    def _resolver_for_tag_801(rows):
        """Return tag_id=801 only when the prediction list is non-empty (image A),
        otherwise return empty (image B will have no blue_eyes prediction)."""
        async def _inner(db, suggestions):
            if not suggestions:
                return []
            # Passthrough for the known external_tag; inject tag_id
            return [dict(r) for r in rows]
        return _inner

    with (
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_for_tag_801(resolved_a)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _resolver_passthrough),
    ):
        count = await remap_images_for_tag(db_session, 801, CAFORMER)

    # Only image A should have been remapped (1 image with the mapped external tag)
    assert count == 1

    # Image A must have a pending suggestion for internal tag 801
    rows_a = (
        await db_session.execute(
            select(MlTagSuggestions).where(MlTagSuggestions.image_id == image_a.image_id)
        )
    ).scalars().all()
    assert len(rows_a) == 1
    assert rows_a[0].tag_id == 801
    assert rows_a[0].status == "pending"

    # Image B must have NO suggestion rows
    rows_b = (
        await db_session.execute(
            select(MlTagSuggestions).where(MlTagSuggestions.image_id == image_b.image_id)
        )
    ).scalars().all()
    assert rows_b == [], "image B must not receive any suggestion (unrelated raw pred)"


async def test_remap_image_from_store_ignores_other_model(db_session, monkeypatch):
    """remap_image_from_store filters by model name: raw predictions for a
    different model are NOT picked up when remapping with CAFORMER."""
    monkeypatch.setattr(settings, "ML_MIN_CONFIDENCE", 0.35)

    user = await _make_user(db_session, "store2")
    image = await _make_image(db_session, user, "store2")
    await _make_tags(db_session, 701)
    await db_session.commit()

    # Seed TWO models
    caformer_row = MlModels(name=CAFORMER)
    swinv2_row = MlModels(name=SWINV2)
    db_session.add_all([caformer_row, swinv2_row])
    await db_session.flush()

    ext_tag = MlExternalTags(name="blush", category=0)
    db_session.add(ext_tag)
    await db_session.flush()

    # Only swinv2 has a prediction for this image+tag — caformer has nothing
    db_session.add(
        MlRawPredictions(
            image_id=image.image_id,
            model_id=swinv2_row.id,
            external_tag_id=ext_tag.id,
            confidence=0.88,
        )
    )
    await db_session.commit()

    # Caformer has no raw predictions for this image, so remap_image_from_store
    # will call remap_image with predictions=[] and resolve_external_tags will
    # receive an empty list → return empty → implied set is empty → added==0.
    # Use the passthrough resolver: it returns exactly what it receives (empty).
    with (
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_passthrough),
        patch(f"{PIPELINE}.resolve_tag_relationships", _resolver_passthrough),
    ):
        added = await remap_image_from_store(db_session, image.image_id, CAFORMER)

    # Caformer has no raw predictions for this image → nothing to imply → 0 added
    assert added == 0

    rows = (
        await db_session.execute(
            select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
        )
    ).scalars().all()
    assert rows == [], "swinv2 raw predictions must not be picked up by caformer remap"
