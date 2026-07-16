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

from app.config import TagType, settings
from app.models.image import Images
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.user import Users
from app.services.ml_suggestion_pipeline import (
    compute_implied_suggestions,
    filter_superseded_parents,
    generate_and_store_suggestions,
    store_predictions,
)

PIPELINE = "app.services.ml_suggestion_pipeline"


class FakeMLService:
    """Minimal stand-in for MLTagSuggestionService.

    Returns canned raw predictions and records the include_categories /
    min_confidence it was called with. It does not implement model loading —
    the pipeline only calls ``generate_raw_predictions``.
    """

    def __init__(self, predictions: list[dict[str, Any]]) -> None:
        self._predictions = predictions
        self.called_with_min_confidence: float | None = None
        self.called_with_include_categories: set[int] | None = None

    async def generate_raw_predictions(
        self,
        image_path: str,
        *,
        include_categories: set[int],
        min_confidence: float,
    ) -> list[dict[str, Any]]:
        self.called_with_min_confidence = min_confidence
        self.called_with_include_categories = include_categories
        return list(self._predictions)


def _predictions(*tags: str) -> list[dict[str, Any]]:
    """Build canned raw predictions (confidence/category unused downstream here
    because resolvers are patched to return tag_id rows)."""
    return [
        {"external_tag": t, "confidence": 0.9, "category": 0, "model_version": "v3"} for t in tags
    ]


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


async def test_keeps_substring_inside_word(db_session, tmp_path, monkeypatch):
    """A suggestion whose title only appears mid-word in an existing tag is kept.

    Redundancy filtering is whole-word aware: existing "catgirl" must NOT silently
    drop suggested "cat" (raw substring containment would have wrongly dropped it).
    """
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))

    existing = Tags(tag_id=210, title="catgirl")
    suggested = Tags(tag_id=211, title="cat")
    db_session.add_all([existing, suggested])
    await db_session.flush()

    user = await _make_user(db_session, "wordboundary")
    image = await _make_image(db_session, user, "wordboundary", tmp_path)
    await db_session.flush()

    db_session.add(TagLinks(image_id=image.image_id, tag_id=210, user_id=user.user_id))
    await db_session.commit()

    ml = FakeMLService(_predictions("cat"))
    mapped = [{"tag_id": 211, "confidence": 0.9, "model_version": "v3"}]

    with (
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(mapped)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _passthrough_resolver),
    ):
        created = await generate_and_store_suggestions(db_session, image, ml)

    # "cat" only appears mid-word in "catgirl" → NOT redundant, suggestion kept.
    assert created == 1
    result = await db_session.execute(
        select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
    )
    suggestions = result.scalars().all()
    assert {s.tag_id for s in suggestions} == {211}


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


async def test_generate_and_store_populates_raw_store_and_suggestions(
    db_session, tmp_path, monkeypatch
):
    """The live path runs ONE inference (general+character) that feeds BOTH the
    raw-prediction store and the pending suggestions.

    Asserts the fake service is asked for SUGGESTION_CATEGORIES, that the raw
    predictions are forwarded verbatim to ingest_raw_predictions, and that
    suggestions are created from the same predictions.
    """
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))

    tags = [Tags(tag_id=46, title="long hair"), Tags(tag_id=99, title="hatsune miku")]
    db_session.add_all(tags)
    await db_session.flush()

    user = await _make_user(db_session, "rawstore")
    image = await _make_image(db_session, user, "rawstore", tmp_path)
    await db_session.commit()

    raw = [
        {"external_tag": "long_hair", "confidence": 0.92, "category": 0, "model_version": "v3"},
        {"external_tag": "hatsune_miku", "confidence": 0.88, "category": 4, "model_version": "v3"},
    ]

    class FakeService:
        model_name = "v3"

        async def generate_raw_predictions(
            self, image_path, *, include_categories, min_confidence
        ):
            assert include_categories == {0, 4}  # SUGGESTION_CATEGORIES
            return list(raw)

    captured: dict[str, Any] = {}

    async def fake_ingest(db, records):
        captured["records"] = records
        return sum(len(r["predictions"]) for r in records)

    mapped = [
        {"tag_id": 46, "confidence": 0.92, "model_version": "v3"},
        {"tag_id": 99, "confidence": 0.88, "model_version": "v3"},
    ]
    with (
        patch(f"{PIPELINE}.ingest_raw_predictions", fake_ingest),
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(mapped)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _passthrough_resolver),
    ):
        created = await generate_and_store_suggestions(db_session, image, FakeService())

    assert created == 2
    assert captured["records"] == [{"image_id": image.image_id, "predictions": raw}]

    result = await db_session.execute(
        select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
    )
    suggestions = result.scalars().all()
    assert {s.tag_id for s in suggestions} == {46, 99}


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


@pytest.mark.needs_commit
async def test_store_predictions_swallows_duplicate_race(db_session, tmp_path, monkeypatch):
    """Two concurrent regenerations of the same image (arq job vs sync generate,
    or overlapping retries) can insert the same (image_id, tag_id) between this
    call's read and its commit, tripping the UNIQUE(image_id, tag_id) constraint.
    Regeneration is idempotent and the other writer's rows ARE the desired state,
    so store_predictions must roll back, log the race, and return 0 new — never
    let the IntegrityError surface as a 500.

    Testing approach (noted honestly): the read-then-insert race window is not
    deterministically reproducible in-process, so a single IntegrityError is
    injected at the real commit boundary (db.commit). The assertions are on the
    service's real handling of it: no exception escapes, it returns 0, and it
    rolls the session back (the action that un-poisons the session in the real
    race). needs_commit is used for real transaction semantics; the follow-up
    query confirms the session is reusable afterward.
    """
    from sqlalchemy.exc import IntegrityError

    tags = [Tags(tag_id=46, title="long hair")]
    db_session.add_all(tags)
    await db_session.flush()

    user = await _make_user(db_session, "dup_race")
    image = await _make_image(db_session, user, "dup_race", tmp_path)
    await db_session.commit()
    image_id = image.image_id  # capture before the rollback below expires `image`

    predictions = [{"external_tag": "long_hair", "confidence": 0.92, "model_version": "v3"}]
    mapped = [{"tag_id": 46, "confidence": 0.92, "model_version": "v3"}]

    real_commit = db_session.commit
    real_rollback = db_session.rollback
    commit_calls = {"n": 0}
    rollback_calls = {"n": 0}

    async def _commit_raises_once() -> None:
        commit_calls["n"] += 1
        if commit_calls["n"] == 1:
            raise IntegrityError(
                "INSERT INTO ml_tag_suggestions ...",
                {},
                Exception("Duplicate entry for key 'unique_ml_suggestion_image_tag'"),
            )
        await real_commit()

    async def _rollback_spy() -> None:
        rollback_calls["n"] += 1
        await real_rollback()

    monkeypatch.setattr(db_session, "commit", _commit_raises_once)
    monkeypatch.setattr(db_session, "rollback", _rollback_spy)

    with (
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(mapped)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _passthrough_resolver),
    ):
        created = await store_predictions(db_session, image_id, predictions)

    # Degraded gracefully: no raise, 0 new, and it rolled back the failed unit.
    assert created == 0
    assert commit_calls["n"] == 1
    assert rollback_calls["n"] == 1

    # Session is reusable afterward (rollback cleared the aborted work).
    monkeypatch.setattr(db_session, "commit", real_commit)
    monkeypatch.setattr(db_session, "rollback", real_rollback)
    rows = (
        await db_session.execute(
            select(MlTagSuggestions).where(MlTagSuggestions.image_id == image_id)
        )
    ).scalars().all()
    assert rows == []  # our aborted attempt persisted nothing


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


async def test_filter_superseded_parents_confident_child_drops_parent(db_session):
    """A confident child (>= min) drops its suggested parent; the child stays."""
    parent = Tags(tag_id=600, title="dress")
    child = Tags(tag_id=601, title="sundress", inheritedfrom_id=600)
    db_session.add_all([parent, child])
    await db_session.commit()

    suggestions = [
        {"tag_id": 600, "confidence": 0.85, "model_version": "v3"},  # dress (parent)
        {"tag_id": 601, "confidence": 0.70, "model_version": "v3"},  # sundress (child)
    ]

    result = await filter_superseded_parents(db_session, suggestions, 0.6)

    assert {s["tag_id"] for s in result} == {601}


async def test_filter_superseded_parents_weak_child_keeps_both(db_session):
    """A weak child (< min) does NOT suppress its parent; both are kept."""
    parent = Tags(tag_id=610, title="dress")
    child = Tags(tag_id=611, title="sundress", inheritedfrom_id=610)
    db_session.add_all([parent, child])
    await db_session.commit()

    suggestions = [
        {"tag_id": 610, "confidence": 0.80, "model_version": "v3"},  # dress (parent)
        {"tag_id": 611, "confidence": 0.40, "model_version": "v3"},  # sundress (weak child)
    ]

    result = await filter_superseded_parents(db_session, suggestions, 0.6)

    assert {s["tag_id"] for s in result} == {610, 611}


async def test_filter_superseded_parents_three_level_chain(db_session):
    """A confident leaf in a GP -> P -> C chain drops both ancestors that are suggested."""
    grandparent = Tags(tag_id=620, title="clothing")
    parent = Tags(tag_id=621, title="dress", inheritedfrom_id=620)
    child = Tags(tag_id=622, title="sundress", inheritedfrom_id=621)
    db_session.add_all([grandparent, parent, child])
    await db_session.commit()

    suggestions = [
        {"tag_id": 620, "confidence": 0.90, "model_version": "v3"},  # clothing (grandparent)
        {"tag_id": 621, "confidence": 0.85, "model_version": "v3"},  # dress (parent)
        {"tag_id": 622, "confidence": 0.70, "model_version": "v3"},  # sundress (child)
    ]

    result = await filter_superseded_parents(db_session, suggestions, 0.6)

    assert {s["tag_id"] for s in result} == {622}


async def test_filter_superseded_parents_no_hierarchy_unchanged(db_session):
    """Unrelated suggested tags (no hierarchy link) are all kept."""
    tag_a = Tags(tag_id=630, title="smile")
    tag_b = Tags(tag_id=631, title="blush")
    db_session.add_all([tag_a, tag_b])
    await db_session.commit()

    suggestions = [
        {"tag_id": 630, "confidence": 0.90, "model_version": "v3"},
        {"tag_id": 631, "confidence": 0.85, "model_version": "v3"},
    ]

    result = await filter_superseded_parents(db_session, suggestions, 0.6)

    assert {s["tag_id"] for s in result} == {630, 631}


async def test_filter_superseded_parents_single_suggestion_unchanged(db_session):
    """Fewer than two suggestions short-circuits to the input unchanged."""
    suggestions = [{"tag_id": 640, "confidence": 0.90, "model_version": "v3"}]

    result = await filter_superseded_parents(db_session, suggestions, 0.6)

    assert result == suggestions


async def test_filter_superseded_parents_parent_not_suggested_kept(db_session):
    """A parent that is NOT itself in the suggestion set is never dropped (operates
    only within the suggested set), and the child is kept."""
    parent = Tags(tag_id=650, title="dress")
    child = Tags(tag_id=651, title="sundress", inheritedfrom_id=650)
    other = Tags(tag_id=652, title="smile")
    db_session.add_all([parent, child, other])
    await db_session.commit()

    # Parent 650 is NOT suggested; only the confident child and an unrelated tag are.
    suggestions = [
        {"tag_id": 651, "confidence": 0.70, "model_version": "v3"},  # sundress (child)
        {"tag_id": 652, "confidence": 0.80, "model_version": "v3"},  # unrelated
    ]

    result = await filter_superseded_parents(db_session, suggestions, 0.6)

    assert {s["tag_id"] for s in result} == {651, 652}


async def test_character_suggestions_dropped_when_flag_off(db_session, tmp_path, monkeypatch):
    """Flag off: suggestions resolving to character-type tags are not stored;
    theme suggestions are unaffected. Raw inference/ingest is untouched by this
    gate (it lives in compute_implied_suggestions, not the raw-store path)."""
    monkeypatch.setattr(settings, "ML_CHARACTER_SUGGESTIONS_ENABLED", False)

    theme = Tags(tag_id=301, title="smile", type=TagType.THEME)
    character = Tags(tag_id=302, title="hatsune miku", type=TagType.CHARACTER)
    db_session.add_all([theme, character])
    await db_session.flush()

    user = await _make_user(db_session, "charflag_off")
    image = await _make_image(db_session, user, "charflag_off", tmp_path)
    await db_session.commit()

    predictions = [
        {"external_tag": "smile", "confidence": 0.9, "model_version": "v3"},
        {"external_tag": "hatsune_miku", "confidence": 0.95, "model_version": "v3"},
    ]
    mapped = [
        {"tag_id": 301, "confidence": 0.9, "model_version": "v3"},
        {"tag_id": 302, "confidence": 0.95, "model_version": "v3"},
    ]

    with (
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(mapped)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _passthrough_resolver),
    ):
        created = await store_predictions(db_session, image.image_id, predictions)

    assert created == 1
    result = await db_session.execute(
        select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
    )
    assert {s.tag_id for s in result.scalars().all()} == {301}


async def test_character_suggestions_stored_when_flag_on(db_session, tmp_path, monkeypatch):
    """Flag on: current behavior preserved — character suggestions stored."""
    monkeypatch.setattr(settings, "ML_CHARACTER_SUGGESTIONS_ENABLED", True)

    theme = Tags(tag_id=311, title="smile", type=TagType.THEME)
    character = Tags(tag_id=312, title="hatsune miku", type=TagType.CHARACTER)
    db_session.add_all([theme, character])
    await db_session.flush()

    user = await _make_user(db_session, "charflag_on")
    image = await _make_image(db_session, user, "charflag_on", tmp_path)
    await db_session.commit()

    predictions = [
        {"external_tag": "smile", "confidence": 0.9, "model_version": "v3"},
        {"external_tag": "hatsune_miku", "confidence": 0.95, "model_version": "v3"},
    ]
    mapped = [
        {"tag_id": 311, "confidence": 0.9, "model_version": "v3"},
        {"tag_id": 312, "confidence": 0.95, "model_version": "v3"},
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
    assert {s.tag_id for s in result.scalars().all()} == {311, 312}


async def test_generate_skips_suggestion_ineligible_image(db_session):
    """generate_and_store_suggestions returns 0 for an ineligible image without
    touching the filesystem or the model (guard runs before file resolution).

    No local file is created for this image (unlike _make_image) — if the
    guard did not run before file resolution, this would fail with
    FileNotFoundError instead.
    """
    from app.config import ImageStatus

    user = await _make_user(db_session, "inelig")
    image = Images(
        filename="2024-01-01-inelig",
        ext="jpg",
        user_id=user.user_id,
        md5_hash="hash_inelig",
        filesize=1024,
        width=800,
        height=600,
        status=ImageStatus.REPOST,
    )
    db_session.add(image)
    await db_session.commit()

    # ml_service is never reached: the guard returns before any use.
    created = await generate_and_store_suggestions(db_session, image, None)  # type: ignore[arg-type]

    assert created == 0


async def test_character_child_does_not_supersede_theme_parent_when_gated(
    db_session, tmp_path, monkeypatch
):
    """Gate ordering regression: with the flag off, a confident character-type
    child must be dropped BEFORE parent-supersede runs — otherwise it first
    supersedes its theme parent and is then dropped itself, losing both.
    Nothing in the schema forbids cross-type inheritedfrom chains, so this is
    enforced by ordering, not by data convention."""
    monkeypatch.setattr(settings, "ML_CHARACTER_SUGGESTIONS_ENABLED", False)

    theme_parent = Tags(tag_id=401, title="vocaloid outfit", type=TagType.THEME)
    db_session.add(theme_parent)
    await db_session.flush()
    character_child = Tags(
        tag_id=402,
        title="hatsune miku",
        type=TagType.CHARACTER,
        inheritedfrom_id=401,
    )
    db_session.add(character_child)
    await db_session.flush()

    user = await _make_user(db_session, "gate_order")
    image = await _make_image(db_session, user, "gate_order", tmp_path)
    await db_session.commit()

    predictions = [
        {"external_tag": "vocaloid_outfit", "confidence": 0.7, "model_version": "v3"},
        {"external_tag": "hatsune_miku", "confidence": 0.95, "model_version": "v3"},
    ]
    mapped = [
        {"tag_id": 401, "confidence": 0.7, "model_version": "v3"},
        {"tag_id": 402, "confidence": 0.95, "model_version": "v3"},
    ]

    with (
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(mapped)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _passthrough_resolver),
    ):
        created = await store_predictions(db_session, image.image_id, predictions)

    # The character child is gated out BEFORE it can supersede its theme parent,
    # so the parent survives as the sole stored suggestion.
    assert created == 1
    result = await db_session.execute(
        select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
    )
    assert {s.tag_id for s in result.scalars().all()} == {401}
