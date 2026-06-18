"""
Tests for the shared ML suggestion pipeline.

These drive ``generate_and_store_suggestions(db, image, ml_service)`` against
the real test database. The ML inference boundary is a small fake service
returning canned predictions. ``resolve_external_tags`` is patched to return
canned tag-ID rows in every test here — mapping has its own real-DB coverage
in ``test_tag_mapping_service.py``. ``resolve_tag_relationships`` runs for real
in tests that exercise alias resolution or ancestor-walk logic; it is patched
to a passthrough elsewhere. No mocked behavior is asserted — the assertions
are all on real DB rows the pipeline wrote.
"""

from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.image import Images
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.user import Users
from app.services.ml_suggestion_pipeline import (
    compute_implied_suggestions,
    generate_and_store_suggestions,
    store_predictions,
)

PIPELINE = "app.services.ml_suggestion_pipeline"


class FakeMLService:
    """Minimal stand-in for MLTagSuggestionService.

    Returns canned external-tag predictions and records the min_confidence it
    was called with. It does not implement model loading — the pipeline only
    calls ``generate_suggestions``.
    """

    def __init__(self, predictions: list[dict[str, Any]]) -> None:
        self._predictions = predictions
        self.called_with_min_confidence: float | None = None

    async def generate_suggestions(
        self, image_path: str, min_confidence: float = 0.35
    ) -> list[dict[str, Any]]:
        self.called_with_min_confidence = min_confidence
        return list(self._predictions)


def _predictions(*tags: str) -> list[dict[str, Any]]:
    """Build canned external-tag predictions (confidence unused downstream
    here because resolvers are patched to return tag_id rows)."""
    return [{"external_tag": t, "confidence": 0.9, "model_version": "v3"} for t in tags]


async def _make_user(db_session, suffix: str) -> Users:
    user = Users(
        username=f"pipeline_{suffix}",
        email=f"pipeline_{suffix}@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="testsalt12345678",
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _make_image(db_session, user: Users, suffix: str, tmp_path) -> Images:
    """Create an image row and the local fullsize file the pipeline expects."""
    image = Images(
        filename=f"2024-01-01-{suffix}",
        ext="jpg",
        user_id=user.user_id,
        md5_hash=f"hash_{suffix}",
        filesize=1024,
        width=800,
        height=600,
    )
    db_session.add(image)
    await db_session.flush()

    fake_image = tmp_path / "fullsize" / f"{image.filename}.{image.ext}"
    fake_image.parent.mkdir(parents=True, exist_ok=True)
    fake_image.write_bytes(b"fake image data")
    return image


def _resolver_to_tag_ids(rows: list[dict[str, Any]]):
    """Build a fake resolve_external_tags that returns the given tag_id rows."""

    async def _resolver(db, suggestions):
        return [dict(r) for r in rows]

    return _resolver


async def _passthrough_resolver(db, suggestions):
    return suggestions


async def test_creates_suggestions(db_session, tmp_path, monkeypatch):
    """Pipeline maps/resolves predictions and stores pending suggestions."""
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))

    tags = [
        Tags(tag_id=46, title="long hair"),
        Tags(tag_id=161, title="short hair"),
        Tags(tag_id=25, title="blush"),
    ]
    db_session.add_all(tags)
    await db_session.flush()

    user = await _make_user(db_session, "create")
    image = await _make_image(db_session, user, "1", tmp_path)
    await db_session.commit()

    ml = FakeMLService(_predictions("long_hair", "short_hair", "blush"))
    mapped = [
        {"tag_id": 46, "confidence": 0.92, "model_version": "v3"},
        {"tag_id": 161, "confidence": 0.88, "model_version": "v3"},
        {"tag_id": 25, "confidence": 0.85, "model_version": "v3"},
    ]

    with (
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(mapped)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _passthrough_resolver),
    ):
        created = await generate_and_store_suggestions(db_session, image, ml)

    assert created == 3

    result = await db_session.execute(
        select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
    )
    suggestions = result.scalars().all()
    assert len(suggestions) == 3
    assert all(s.status == "pending" for s in suggestions)
    assert all(s.model_version == "v3" for s in suggestions)
    assert {s.tag_id for s in suggestions} == {46, 161, 25}


async def test_uses_min_confidence_from_settings(db_session, tmp_path, monkeypatch):
    """The pipeline asks the model for predictions at settings.ML_MIN_CONFIDENCE."""
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
    monkeypatch.setattr(settings, "ML_MIN_CONFIDENCE", 0.35)

    user = await _make_user(db_session, "minconf")
    image = await _make_image(db_session, user, "minconf", tmp_path)
    await db_session.commit()

    ml = FakeMLService([])
    with (
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids([])),
        patch(f"{PIPELINE}.resolve_tag_relationships", _passthrough_resolver),
    ):
        await generate_and_store_suggestions(db_session, image, ml)

    assert ml.called_with_min_confidence == 0.35


async def test_skips_existing_tags(db_session, tmp_path, monkeypatch):
    """Tags already linked to the image are not re-suggested."""
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))

    tags = [
        Tags(tag_id=46, title="long hair"),
        Tags(tag_id=161, title="short hair"),
        Tags(tag_id=25, title="blush"),
    ]
    db_session.add_all(tags)
    await db_session.flush()

    user = await _make_user(db_session, "existing")
    image = await _make_image(db_session, user, "2", tmp_path)
    await db_session.flush()

    db_session.add(TagLinks(image_id=image.image_id, tag_id=46, user_id=user.user_id))
    await db_session.commit()

    ml = FakeMLService(_predictions("long_hair", "short_hair", "blush"))
    mapped = [
        {"tag_id": 46, "confidence": 0.92, "model_version": "v3"},
        {"tag_id": 161, "confidence": 0.88, "model_version": "v3"},
        {"tag_id": 25, "confidence": 0.85, "model_version": "v3"},
    ]

    with (
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(mapped)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _passthrough_resolver),
    ):
        created = await generate_and_store_suggestions(db_session, image, ml)

    assert created == 2
    result = await db_session.execute(
        select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
    )
    suggestions = result.scalars().all()
    assert {s.tag_id for s in suggestions} == {161, 25}


async def test_skips_existing_suggestion(db_session, tmp_path, monkeypatch):
    """A tag that already has a suggestion row is not duplicated on regenerate."""
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))

    tags = [Tags(tag_id=46, title="long hair"), Tags(tag_id=161, title="short hair")]
    db_session.add_all(tags)
    await db_session.flush()

    user = await _make_user(db_session, "existsugg")
    image = await _make_image(db_session, user, "existsugg", tmp_path)
    await db_session.flush()

    db_session.add(
        MlTagSuggestions(
            image_id=image.image_id, tag_id=46, confidence=0.9, model_version="v3", status="pending"
        )
    )
    await db_session.commit()

    ml = FakeMLService(_predictions("long_hair", "short_hair"))
    mapped = [
        {"tag_id": 46, "confidence": 0.92, "model_version": "v3"},
        {"tag_id": 161, "confidence": 0.88, "model_version": "v3"},
    ]

    with (
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(mapped)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _passthrough_resolver),
    ):
        created = await generate_and_store_suggestions(db_session, image, ml)

    assert created == 1
    result = await db_session.execute(
        select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
    )
    suggestions = result.scalars().all()
    assert {s.tag_id for s in suggestions} == {46, 161}


async def test_resets_approved_when_tag_removed(db_session, tmp_path, monkeypatch):
    """Approved suggestions reset to pending when their tag is no longer on the image."""
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))

    tags = [Tags(tag_id=46, title="long hair"), Tags(tag_id=161, title="short hair")]
    db_session.add_all(tags)
    await db_session.flush()

    user = await _make_user(db_session, "reset")
    image = await _make_image(db_session, user, "reset", tmp_path)
    await db_session.flush()

    # Approved suggestion with NO matching TagLink (tag was removed).
    approved = MlTagSuggestions(
        image_id=image.image_id, tag_id=46, confidence=0.92, model_version="v3", status="approved"
    )
    approved.reviewed_by_user_id = user.user_id
    rejected = MlTagSuggestions(
        image_id=image.image_id, tag_id=161, confidence=0.88, model_version="v3", status="rejected"
    )
    db_session.add_all([approved, rejected])
    await db_session.commit()

    ml = FakeMLService(_predictions("long_hair", "short_hair"))
    mapped = [
        {"tag_id": 46, "confidence": 0.92, "model_version": "v3"},
        {"tag_id": 161, "confidence": 0.88, "model_version": "v3"},
    ]

    with (
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(mapped)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _passthrough_resolver),
    ):
        created = await generate_and_store_suggestions(db_session, image, ml)

    # Both tags already have suggestion rows: nothing new created.
    assert created == 0

    await db_session.refresh(approved)
    await db_session.refresh(rejected)
    assert approved.status == "pending"
    assert approved.reviewed_at is None
    assert approved.reviewed_by_user_id is None
    # Rejected stays rejected (its tag is absent too, but only approved rows reset).
    assert rejected.status == "rejected"


async def test_filters_low_confidence(db_session, tmp_path, monkeypatch):
    """Predictions below ML_MIN_CONFIDENCE are dropped before insert."""
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
    monkeypatch.setattr(settings, "ML_MIN_CONFIDENCE", 0.6)

    tags = [
        Tags(tag_id=46, title="long hair"),
        Tags(tag_id=161, title="short hair"),
        Tags(tag_id=25, title="blush"),
    ]
    db_session.add_all(tags)
    await db_session.flush()

    user = await _make_user(db_session, "lowconf")
    image = await _make_image(db_session, user, "lowconf", tmp_path)
    await db_session.commit()

    ml = FakeMLService(_predictions("long_hair", "short_hair", "blush"))
    # The mapping stage can return below-threshold confidences (e.g. mapping
    # confidence multiplier). The pipeline double-checks before insert.
    mapped = [
        {"tag_id": 46, "confidence": 0.92, "model_version": "v3"},
        {"tag_id": 161, "confidence": 0.55, "model_version": "v3"},
        {"tag_id": 25, "confidence": 0.65, "model_version": "v3"},
    ]

    with (
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(mapped)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _passthrough_resolver),
    ):
        created = await generate_and_store_suggestions(db_session, image, ml)

    assert created == 2
    result = await db_session.execute(
        select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
    )
    suggestions = result.scalars().all()
    assert {s.tag_id for s in suggestions} == {46, 25}
    assert all(s.confidence >= 0.6 for s in suggestions)


async def test_filters_redundant_ancestor(db_session, tmp_path, monkeypatch):
    """A suggestion that is an ancestor of an existing tag is dropped."""
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))

    # "hair" (parent) is an ancestor of "long hair" (child, already on image).
    parent = Tags(tag_id=50, title="hair")
    child = Tags(tag_id=46, title="long hair", inheritedfrom_id=50)
    db_session.add_all([parent, child])
    await db_session.flush()

    user = await _make_user(db_session, "ancestor")
    image = await _make_image(db_session, user, "ancestor", tmp_path)
    await db_session.flush()

    db_session.add(TagLinks(image_id=image.image_id, tag_id=46, user_id=user.user_id))
    await db_session.commit()

    ml = FakeMLService(_predictions("hair"))
    mapped = [{"tag_id": 50, "confidence": 0.9, "model_version": "v3"}]

    with (
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(mapped)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _passthrough_resolver),
    ):
        created = await generate_and_store_suggestions(db_session, image, ml)

    # Parent "hair" is an ancestor of the existing "long hair" → redundant.
    assert created == 0


async def test_filters_redundant_substring_title(db_session, tmp_path, monkeypatch):
    """A suggestion whose title is a proper substring of an existing tag is dropped."""
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))

    existing = Tags(tag_id=200, title="short kimono")
    suggested = Tags(tag_id=201, title="kimono")
    db_session.add_all([existing, suggested])
    await db_session.flush()

    user = await _make_user(db_session, "substr")
    image = await _make_image(db_session, user, "substr", tmp_path)
    await db_session.flush()

    db_session.add(TagLinks(image_id=image.image_id, tag_id=200, user_id=user.user_id))
    await db_session.commit()

    ml = FakeMLService(_predictions("kimono"))
    mapped = [{"tag_id": 201, "confidence": 0.9, "model_version": "v3"}]

    with (
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(mapped)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _passthrough_resolver),
    ):
        created = await generate_and_store_suggestions(db_session, image, ml)

    # "kimono" is a substring of existing "short kimono" → redundant.
    assert created == 0


async def test_no_mappings_creates_nothing(db_session, tmp_path, monkeypatch):
    """When nothing maps to an internal tag, the pipeline completes with 0 created."""
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))

    user = await _make_user(db_session, "nomap")
    image = await _make_image(db_session, user, "nomap", tmp_path)
    await db_session.commit()

    ml = FakeMLService(_predictions("unmapped_tag"))
    with (
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids([])),
        patch(f"{PIPELINE}.resolve_tag_relationships", _passthrough_resolver),
    ):
        created = await generate_and_store_suggestions(db_session, image, ml)

    assert created == 0


async def test_missing_file_raises(db_session, tmp_path, monkeypatch):
    """A missing local image file raises FileNotFoundError (job wrapper translates)."""
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))

    user = await _make_user(db_session, "missing")
    # Build image row WITHOUT creating the local file.
    image = Images(
        filename="2024-01-01-missing",
        ext="jpg",
        user_id=user.user_id,
        md5_hash="hash_missing",
        filesize=1024,
        width=800,
        height=600,
    )
    db_session.add(image)
    await db_session.commit()

    ml = FakeMLService(_predictions("long_hair"))
    with pytest.raises(FileNotFoundError):
        await generate_and_store_suggestions(db_session, image, ml)


async def test_skips_missing_tag_id(db_session, tmp_path, monkeypatch):
    """A mapped tag_id that doesn't exist in tags is skipped, not crashed on.

    The resolver already drops unknown tags; the redundancy filter and insert
    path must also tolerate a stray tag_id without raising.
    """
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))

    user = await _make_user(db_session, "missingtag")
    image = await _make_image(db_session, user, "missingtag", tmp_path)
    await db_session.commit()

    ml = FakeMLService(_predictions("ghost_tag"))
    # Mapping returns a tag_id that has no row in `tags`. The resolver runs for
    # real here and should drop it; the pipeline must not crash and must create 0.
    mapped = [{"tag_id": 999999, "confidence": 0.9, "model_version": "v3"}]

    with patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(mapped)):
        created = await generate_and_store_suggestions(db_session, image, ml)

    assert created == 0
    result = await db_session.execute(
        select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
    )
    assert result.scalars().all() == []


async def test_alias_resolves_to_canonical(db_session, tmp_path, monkeypatch):
    """Pipeline stores the canonical tag when the predicted tag is an alias.

    resolve_external_tags is patched to return the alias tag_id (as all other
    tests do); resolve_tag_relationships runs for real against the DB rows and
    must redirect the suggestion to the canonical tag_id.
    """
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))

    canonical = Tags(tag_id=300, title="long hair")
    alias = Tags(tag_id=301, title="longhair", alias_of=300)
    db_session.add_all([canonical, alias])
    await db_session.flush()

    user = await _make_user(db_session, "alias")
    image = await _make_image(db_session, user, "alias", tmp_path)
    await db_session.commit()

    ml = FakeMLService(_predictions("longhair"))
    # Mapping stage returns the alias tag_id; real resolver redirects to canonical.
    mapped = [{"tag_id": 301, "confidence": 0.9, "model_version": "v3"}]

    with patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(mapped)):
        created = await generate_and_store_suggestions(db_session, image, ml)

    assert created == 1
    result = await db_session.execute(
        select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
    )
    suggestions = result.scalars().all()
    assert len(suggestions) == 1
    # The stored suggestion must reference the canonical tag, not the alias.
    assert suggestions[0].tag_id == 300


async def test_store_predictions_persists_without_inference(db_session, tmp_path, monkeypatch):
    """store_predictions runs the DB half (map→resolve→filter→insert) on its own.

    This is the seam the bulk-ingest path reuses: external-tag predictions in,
    stored MlTagSuggestions rows out, no image file or ML service involved.
    """
    tags = [Tags(tag_id=46, title="long hair"), Tags(tag_id=161, title="short hair")]
    db_session.add_all(tags)
    await db_session.flush()

    user = await _make_user(db_session, "store")
    image = await _make_image(db_session, user, "store", tmp_path)
    await db_session.commit()

    predictions = [
        {"external_tag": "long_hair", "confidence": 0.92, "model_version": "v3"},
        {"external_tag": "short_hair", "confidence": 0.88, "model_version": "v3"},
    ]
    mapped = [
        {"tag_id": 46, "confidence": 0.92, "model_version": "v3"},
        {"tag_id": 161, "confidence": 0.88, "model_version": "v3"},
    ]

    with (
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(mapped)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _passthrough_resolver),
    ):
        created = await store_predictions(db_session, image.image_id, predictions)

    assert created == 2
    result = await db_session.execute(
        select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
    )
    suggestions = result.scalars().all()
    assert {s.tag_id for s in suggestions} == {46, 161}
    assert all(s.model_version == "v3" for s in suggestions)
    assert all(s.status == "pending" for s in suggestions)


async def test_filters_redundant_grandparent(db_session, tmp_path, monkeypatch):
    """A suggestion that is a grandparent of an existing tag is also filtered.

    Exercises the multi-level ancestor walk inside filter_redundant_suggestions
    (beyond depth 1: child → parent → grandparent).
    """
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))

    grandparent = Tags(tag_id=400, title="color")
    parent = Tags(tag_id=401, title="hair color", inheritedfrom_id=400)
    child = Tags(tag_id=402, title="blonde hair", inheritedfrom_id=401)
    db_session.add_all([grandparent, parent, child])
    await db_session.flush()

    user = await _make_user(db_session, "grandparent")
    image = await _make_image(db_session, user, "grandparent", tmp_path)
    await db_session.flush()

    # The child tag is already on the image.
    db_session.add(TagLinks(image_id=image.image_id, tag_id=402, user_id=user.user_id))
    await db_session.commit()

    ml = FakeMLService(_predictions("color"))
    # Predict the grandparent directly; passthrough so it arrives at the filter as-is.
    mapped = [{"tag_id": 400, "confidence": 0.9, "model_version": "v3"}]

    with (
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(mapped)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _passthrough_resolver),
    ):
        created = await generate_and_store_suggestions(db_session, image, ml)

    # Grandparent "color" is an ancestor of the existing "blonde hair" → redundant.
    assert created == 0


async def test_compute_implied_suggestions(db_session, monkeypatch):
    """compute_implied_suggestions returns (implied, applied) after map/resolve/filter.

    Exercises:
    - applied_tag_ids correctly reflects TagLinks on the image
    - a tag already applied to the image is excluded from implied
    - a tag below ML_MIN_CONFIDENCE is excluded from implied
    - a tag that passes all filters is included in implied (with tag_id, confidence,
      model_version)
    """
    monkeypatch.setattr(settings, "ML_MIN_CONFIDENCE", 0.6)

    tags = [
        Tags(tag_id=500, title="long hair"),
        Tags(tag_id=501, title="blush"),
        Tags(tag_id=502, title="smile"),
    ]
    db_session.add_all(tags)
    await db_session.flush()

    user = await _make_user(db_session, "implied")
    image = Images(
        filename="2024-01-01-implied",
        ext="jpg",
        user_id=user.user_id,
        md5_hash="hash_implied",
        filesize=1024,
        width=800,
        height=600,
    )
    db_session.add(image)
    await db_session.flush()

    # tag 500 is already applied to the image
    db_session.add(TagLinks(image_id=image.image_id, tag_id=500, user_id=user.user_id))
    await db_session.commit()

    # Three predictions:
    #   tag 500 -> already applied, must be excluded
    #   tag 501 -> confidence 0.55 < 0.6, must be excluded
    #   tag 502 -> confidence 0.85 >= 0.6 and not applied, must be included
    predictions = [
        {"tag_id": 500, "confidence": 0.9, "model_version": "v3"},
        {"tag_id": 501, "confidence": 0.55, "model_version": "v3"},
        {"tag_id": 502, "confidence": 0.85, "model_version": "v3"},
    ]

    with (
        # predictions already carry tag_id; _resolver_to_tag_ids is a passthrough here.
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(predictions)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _passthrough_resolver),
    ):
        implied, applied = await compute_implied_suggestions(
            db_session, image.image_id, predictions
        )

    assert applied == {500}
    assert len(implied) == 1
    kept = implied[0]
    assert kept["tag_id"] == 502
    assert kept["confidence"] == 0.85
    assert kept["model_version"] == "v3"
