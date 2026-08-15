"""Tests for the stateless upload-form tag analysis endpoint."""

import asyncio
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType, settings
from app.core.security import create_access_token
from app.models.tag import Tags
from app.models.tag_mapping import TagMappings
from app.models.user import Users
from app.services import ml_runtime


@pytest.fixture
async def verified_user(db_session: AsyncSession) -> Users:
    """Create a verified user for analyze testing."""
    user = Users(
        username="analyzer",
        password="hashed_password_here",
        password_type="bcrypt",
        salt="saltsalt12345678",
        email="analyzer@example.com",
        active=1,
        email_verified=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def analyze_client(client: AsyncClient, verified_user: Users) -> AsyncClient:
    """Authenticated client with a verified user."""
    access_token = create_access_token(verified_user.id)
    client.headers.update({"Authorization": f"Bearer {access_token}"})
    return client


def _fake_image_bytes(width: int = 100, height: int = 100) -> bytes:
    """Create a minimal valid JPEG for analyze tests."""
    from PIL import Image

    img = Image.new("RGB", (width, height), color="red")
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _fake_ml_service(raw_preds: list[dict], captured: dict | None = None):
    """Build a fake ML service whose generate_raw_predictions returns canned raw dicts.

    If ``captured`` is provided, the call's kwargs are recorded into it so tests can
    assert which floor/categories the endpoint inferred at.
    """

    class _FakeService:
        async def generate_raw_predictions(self, image_path, *, include_categories, min_confidence):
            if captured is not None:
                captured["min_confidence"] = min_confidence
                captured["include_categories"] = include_categories
            return raw_preds

    return _FakeService()


def _files():
    return {"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")}


async def test_analyze_503_when_disabled(analyze_client: AsyncClient, monkeypatch):
    """When the feature flag is off the endpoint returns 503."""
    monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", False)

    response = await analyze_client.post("/api/v1/ml-tag-suggestions/analyze", files=_files())

    assert response.status_code == 503, response.text


async def test_analyze_requires_auth(client: AsyncClient, monkeypatch):
    """Without an auth header the endpoint returns 401."""
    monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)

    response = await client.post("/api/v1/ml-tag-suggestions/analyze", files=_files())

    assert response.status_code == 401, response.text


async def test_analyze_returns_theme_and_character_tags(
    analyze_client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """Happy path: a theme and a character tag are resolved and returned with correct types."""
    monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "ML_CHARACTER_SUGGESTIONS_ENABLED", True)

    theme_tag = Tags(title="smile", type=TagType.THEME)  # type=1
    character_tag = Tags(title="hatsune miku", type=TagType.CHARACTER)  # type=4
    db_session.add(theme_tag)
    db_session.add(character_tag)
    await db_session.commit()
    await db_session.refresh(theme_tag)
    await db_session.refresh(character_tag)

    raw_preds = [
        {"external_tag": "smile", "confidence": 0.9, "category": 0, "model_version": "v1"},
        {"external_tag": "hatsune_miku", "confidence": 0.95, "category": 4, "model_version": "v1"},
    ]
    resolved = [
        {"tag_id": theme_tag.tag_id, "confidence": 0.9, "model_version": "v1"},
        {"tag_id": character_tag.tag_id, "confidence": 0.95, "model_version": "v1"},
    ]

    with (
        patch(
            "app.api.v1.ml_analyze.get_ml_service",
            new_callable=AsyncMock,
            return_value=_fake_ml_service(raw_preds),
        ),
        patch(
            "app.api.v1.ml_analyze.resolve_external_tags",
            new_callable=AsyncMock,
            return_value=resolved,
        ),
    ):
        response = await analyze_client.post("/api/v1/ml-tag-suggestions/analyze", files=_files())

    assert response.status_code == 200, response.text
    data = response.json()
    by_title = {s["title"]: s for s in data["suggestions"]}
    assert by_title["smile"]["type"] == TagType.THEME
    assert by_title["hatsune miku"]["type"] == TagType.CHARACTER
    # confidence is surfaced for evaluation (mapping-scaled; mapping.confidence=1.0 in this fake)
    assert by_title["smile"]["confidence"] == 0.9
    assert by_title["hatsune miku"]["confidence"] == 0.95


async def test_analyze_surfaces_canonical_tag_for_alias_mapping(
    analyze_client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """A mapping pointing at an ALIAS surfaces the canonical tag, not the alias.

    Mirrors the stored-suggestion pipeline, which resolves Tags.alias_of before
    persisting. Uses a real tag_mappings row so the whole map->resolve chain runs.
    """
    monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)

    canonical = Tags(title="shrine maiden", type=TagType.THEME)
    db_session.add(canonical)
    await db_session.commit()
    await db_session.refresh(canonical)

    alias = Tags(title="miko", type=TagType.THEME, alias_of=canonical.tag_id)
    db_session.add(alias)
    await db_session.commit()
    await db_session.refresh(alias)

    db_session.add(TagMappings(external_tag="miko", internal_tag_id=alias.tag_id, confidence=1.0))
    await db_session.commit()

    raw_preds = [{"external_tag": "miko", "confidence": 0.9, "category": 0, "model_version": "v1"}]

    with patch(
        "app.api.v1.ml_analyze.get_ml_service",
        new_callable=AsyncMock,
        return_value=_fake_ml_service(raw_preds),
    ):
        response = await analyze_client.post("/api/v1/ml-tag-suggestions/analyze", files=_files())

    assert response.status_code == 200, response.text
    suggestions = response.json()["suggestions"]
    assert [s["title"] for s in suggestions] == ["shrine maiden"]
    assert suggestions[0]["tag_id"] == canonical.tag_id


async def test_analyze_omits_character_tags_when_flag_off(
    analyze_client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """Flag off (the default): character-type tags never reach the upload form."""
    monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "ML_CHARACTER_SUGGESTIONS_ENABLED", False)

    theme_tag = Tags(title="smile", type=TagType.THEME)
    character_tag = Tags(title="hatsune miku", type=TagType.CHARACTER)
    db_session.add_all([theme_tag, character_tag])
    await db_session.commit()
    await db_session.refresh(theme_tag)
    await db_session.refresh(character_tag)

    raw_preds = [
        {"external_tag": "smile", "confidence": 0.9, "category": 0, "model_version": "v1"},
        {"external_tag": "hatsune_miku", "confidence": 0.95, "category": 4, "model_version": "v1"},
    ]
    resolved = [
        {"tag_id": theme_tag.tag_id, "confidence": 0.9, "model_version": "v1"},
        {"tag_id": character_tag.tag_id, "confidence": 0.95, "model_version": "v1"},
    ]

    with (
        patch(
            "app.api.v1.ml_analyze.get_ml_service",
            new_callable=AsyncMock,
            return_value=_fake_ml_service(raw_preds),
        ),
        patch(
            "app.api.v1.ml_analyze.resolve_external_tags",
            new_callable=AsyncMock,
            return_value=resolved,
        ),
    ):
        response = await analyze_client.post("/api/v1/ml-tag-suggestions/analyze", files=_files())

    assert response.status_code == 200, response.text
    data = response.json()
    titles = {s["title"] for s in data["suggestions"]}
    types = {s["type"] for s in data["suggestions"]}
    assert titles == {"smile"}
    assert TagType.CHARACTER not in types


async def test_analyze_character_child_does_not_supersede_theme_parent_when_flag_off(
    analyze_client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """Ordering regression (analyze path): with the flag off, a confident
    character child must be gated BEFORE parent-supersede — otherwise it first
    supersedes its theme parent and is then dropped, losing both chips."""
    monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "ML_CHARACTER_SUGGESTIONS_ENABLED", False)

    theme_parent = Tags(title="vocaloid outfit", type=TagType.THEME)
    db_session.add(theme_parent)
    await db_session.commit()
    await db_session.refresh(theme_parent)
    character_child = Tags(
        title="hatsune miku", type=TagType.CHARACTER, inheritedfrom_id=theme_parent.tag_id
    )
    db_session.add(character_child)
    await db_session.commit()
    await db_session.refresh(character_child)

    raw_preds = [
        {
            "external_tag": "vocaloid_outfit",
            "confidence": 0.7,
            "category": 0,
            "model_version": "v1",
        },
        {"external_tag": "hatsune_miku", "confidence": 0.95, "category": 4, "model_version": "v1"},
    ]
    resolved = [
        {"tag_id": theme_parent.tag_id, "confidence": 0.7, "model_version": "v1"},
        {"tag_id": character_child.tag_id, "confidence": 0.95, "model_version": "v1"},
    ]

    with (
        patch(
            "app.api.v1.ml_analyze.get_ml_service",
            new_callable=AsyncMock,
            return_value=_fake_ml_service(raw_preds),
        ),
        patch(
            "app.api.v1.ml_analyze.resolve_external_tags",
            new_callable=AsyncMock,
            return_value=resolved,
        ),
    ):
        response = await analyze_client.post("/api/v1/ml-tag-suggestions/analyze", files=_files())

    assert response.status_code == 200, response.text
    titles = {s["title"] for s in response.json()["suggestions"]}
    assert titles == {"vocaloid outfit"}


async def test_analyze_downscales_large_image(
    analyze_client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """A large image is downscaled before inference (200, not 400) and the model
    receives an image bounded to ML_ANALYZE_DOWNSCALE_EDGE."""
    monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "ML_ANALYZE_DOWNSCALE_EDGE", 256)

    seen: dict = {}

    class _CapturingService:
        async def generate_raw_predictions(self, image_path, *, include_categories, min_confidence):
            from PIL import Image

            with Image.open(image_path) as im:
                seen["size"] = im.size
            return []

    big_file = {"file": ("big.jpg", _fake_image_bytes(2000, 1500), "image/jpeg")}
    with (
        patch(
            "app.api.v1.ml_analyze.get_ml_service",
            new_callable=AsyncMock,
            return_value=_CapturingService(),
        ),
        patch(
            "app.api.v1.ml_analyze.resolve_external_tags",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        response = await analyze_client.post("/api/v1/ml-tag-suggestions/analyze", files=big_file)

    assert response.status_code == 200, response.text
    assert max(seen["size"]) <= 256  # downscaled, not rejected


async def test_analyze_small_image_not_recompressed(
    analyze_client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """An already-small image passes through unchanged (no needless recompression)."""
    monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "ML_ANALYZE_DOWNSCALE_EDGE", 2048)
    original = _fake_image_bytes(100, 100)
    seen: dict = {}

    class _CapturingService:
        async def generate_raw_predictions(self, image_path, *, include_categories, min_confidence):
            with open(image_path, "rb") as fh:
                seen["bytes"] = fh.read()
            return []

    with (
        patch(
            "app.api.v1.ml_analyze.get_ml_service",
            new_callable=AsyncMock,
            return_value=_CapturingService(),
        ),
        patch(
            "app.api.v1.ml_analyze.resolve_external_tags", new_callable=AsyncMock, return_value=[]
        ),
    ):
        response = await analyze_client.post(
            "/api/v1/ml-tag-suggestions/analyze",
            files={"file": ("small.jpg", original, "image/jpeg")},
        )
    assert response.status_code == 200, response.text
    assert seen["bytes"] == original  # unchanged — temp file holds the original bytes


async def test_analyze_busy_returns_429(analyze_client: AsyncClient, monkeypatch):
    """When no inference slot frees up within the timeout the endpoint returns 429."""
    monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)

    raw_preds = [
        {"external_tag": "smile", "confidence": 0.9, "category": 0, "model_version": "v1"},
    ]

    # Fill a 1-slot semaphore so the request must wait, with a tiny timeout.
    full_semaphore = asyncio.Semaphore(1)
    await full_semaphore.acquire()
    monkeypatch.setattr(ml_runtime, "_inference_semaphore", full_semaphore)
    monkeypatch.setattr(ml_runtime.settings, "ML_ANALYZE_SEMAPHORE_TIMEOUT", 0.05)

    with patch(
        "app.api.v1.ml_analyze.get_ml_service",
        new_callable=AsyncMock,
        return_value=_fake_ml_service(raw_preds),
    ):
        response = await analyze_client.post("/api/v1/ml-tag-suggestions/analyze", files=_files())

    assert response.status_code == 429, response.text


async def test_analyze_caches_predictions(
    analyze_client: AsyncClient, db_session: AsyncSession, mock_redis, monkeypatch
):
    """Successful analyze writes the raw predictions to redis under an ml:analyze: key."""
    monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)

    theme_tag = Tags(title="smile", type=TagType.THEME)
    db_session.add(theme_tag)
    await db_session.commit()
    await db_session.refresh(theme_tag)

    raw_preds = [
        {"external_tag": "smile", "confidence": 0.9, "category": 0, "model_version": "v1"},
    ]
    resolved = [{"tag_id": theme_tag.tag_id, "confidence": 0.9, "model_version": "v1"}]

    with (
        patch(
            "app.api.v1.ml_analyze.get_ml_service",
            new_callable=AsyncMock,
            return_value=_fake_ml_service(raw_preds),
        ),
        patch(
            "app.api.v1.ml_analyze.resolve_external_tags",
            new_callable=AsyncMock,
            return_value=resolved,
        ),
    ):
        response = await analyze_client.post("/api/v1/ml-tag-suggestions/analyze", files=_files())

    assert response.status_code == 200, response.text
    assert mock_redis.set.await_count >= 1
    cache_keys = [call.args[0] for call in mock_redis.set.await_args_list]
    assert any(key.startswith("ml:analyze:") for key in cache_keys)


async def test_analyze_child_demotes_parent_instead_of_dropping_it(
    analyze_client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """The upload form DEMOTES a superseded parent rather than dropping it.

    sundress 0.70 + dress 0.85 -> both returned, with dress marked as superseded
    by sundress so the form can tuck it behind a disclosure. Dropping it here
    would be wrong: unlike the review queue, the uploader has no other way back
    to the parent when the child turns out to be the wrong specialisation.
    """
    monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "ML_ANALYZE_MIN_CONFIDENCE", 0.5)  # display floor
    monkeypatch.setattr(settings, "ML_PARENT_SUPERSEDE_MIN_CONFIDENCE", 0.6)

    parent = Tags(title="dress", type=TagType.THEME)
    db_session.add(parent)
    await db_session.commit()
    await db_session.refresh(parent)
    child = Tags(title="sundress", type=TagType.THEME, inheritedfrom_id=parent.tag_id)
    db_session.add(child)
    await db_session.commit()
    await db_session.refresh(child)

    raw_preds = [
        {"external_tag": "dress", "confidence": 0.85, "category": 0, "model_version": "v1"},
        {"external_tag": "sundress", "confidence": 0.70, "category": 0, "model_version": "v1"},
    ]
    resolved = [
        {"tag_id": parent.tag_id, "confidence": 0.85, "model_version": "v1"},
        {"tag_id": child.tag_id, "confidence": 0.70, "model_version": "v1"},
    ]

    with (
        patch(
            "app.api.v1.ml_analyze.get_ml_service",
            new_callable=AsyncMock,
            return_value=_fake_ml_service(raw_preds),
        ),
        patch(
            "app.api.v1.ml_analyze.resolve_external_tags",
            new_callable=AsyncMock,
            return_value=resolved,
        ),
    ):
        response = await analyze_client.post("/api/v1/ml-tag-suggestions/analyze", files=_files())

    assert response.status_code == 200, response.text
    by_title = {s["title"]: s for s in response.json()["suggestions"]}
    assert by_title["sundress"]["superseded_by_tag_ids"] == []  # child stays primary
    assert by_title["dress"]["superseded_by_tag_ids"] == [child.tag_id]  # parent demoted


async def test_analyze_weak_child_still_demotes_parent(
    analyze_client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """Any child the uploader can SEE demotes its parent, however weak.

    The write path's confidence floor exists because dropping is destructive;
    demotion is not, so the upload form gates on visibility (the display floor)
    alone and ignores ML_PARENT_SUPERSEDE_MIN_CONFIDENCE entirely. Child 0.55 is
    above the 0.5 display floor but far below the 0.9 supersede floor.
    """
    monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "ML_ANALYZE_MIN_CONFIDENCE", 0.5)  # display floor
    monkeypatch.setattr(settings, "ML_PARENT_SUPERSEDE_MIN_CONFIDENCE", 0.9)

    parent = Tags(title="dress", type=TagType.THEME)
    db_session.add(parent)
    await db_session.commit()
    await db_session.refresh(parent)
    child = Tags(title="sundress", type=TagType.THEME, inheritedfrom_id=parent.tag_id)
    db_session.add(child)
    await db_session.commit()
    await db_session.refresh(child)

    raw_preds = [
        {"external_tag": "dress", "confidence": 0.80, "category": 0, "model_version": "v1"},
        {"external_tag": "sundress", "confidence": 0.55, "category": 0, "model_version": "v1"},
    ]
    resolved = [
        {"tag_id": parent.tag_id, "confidence": 0.80, "model_version": "v1"},
        {"tag_id": child.tag_id, "confidence": 0.55, "model_version": "v1"},
    ]

    with (
        patch(
            "app.api.v1.ml_analyze.get_ml_service",
            new_callable=AsyncMock,
            return_value=_fake_ml_service(raw_preds),
        ),
        patch(
            "app.api.v1.ml_analyze.resolve_external_tags",
            new_callable=AsyncMock,
            return_value=resolved,
        ),
    ):
        response = await analyze_client.post("/api/v1/ml-tag-suggestions/analyze", files=_files())

    assert response.status_code == 200, response.text
    by_title = {s["title"]: s for s in response.json()["suggestions"]}
    assert by_title["sundress"]["superseded_by_tag_ids"] == []
    assert by_title["dress"]["superseded_by_tag_ids"] == [child.tag_id]


async def test_analyze_child_below_display_floor_does_not_demote_parent(
    analyze_client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """A child that never renders cannot demote a parent that does.

    Inference runs at the storage floor (0.35), so a 0.40 child reaches the
    resolver but is cut by the 0.5 display floor. If supersede ran before that
    cut, the uploader would see NEITHER chip: the parent tucked away as
    "covered by" a child that isn't on screen.
    """
    monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "ML_MIN_CONFIDENCE", 0.35)
    monkeypatch.setattr(settings, "ML_ANALYZE_MIN_CONFIDENCE", 0.5)  # display floor

    parent = Tags(title="dress", type=TagType.THEME)
    db_session.add(parent)
    await db_session.commit()
    await db_session.refresh(parent)
    child = Tags(title="sundress", type=TagType.THEME, inheritedfrom_id=parent.tag_id)
    db_session.add(child)
    await db_session.commit()
    await db_session.refresh(child)

    raw_preds = [
        {"external_tag": "dress", "confidence": 0.80, "category": 0, "model_version": "v1"},
        {"external_tag": "sundress", "confidence": 0.40, "category": 0, "model_version": "v1"},
    ]
    resolved = [
        {"tag_id": parent.tag_id, "confidence": 0.80, "model_version": "v1"},
        {"tag_id": child.tag_id, "confidence": 0.40, "model_version": "v1"},
    ]

    with (
        patch(
            "app.api.v1.ml_analyze.get_ml_service",
            new_callable=AsyncMock,
            return_value=_fake_ml_service(raw_preds),
        ),
        patch(
            "app.api.v1.ml_analyze.resolve_external_tags",
            new_callable=AsyncMock,
            return_value=resolved,
        ),
    ):
        response = await analyze_client.post("/api/v1/ml-tag-suggestions/analyze", files=_files())

    assert response.status_code == 200, response.text
    suggestions = response.json()["suggestions"]
    assert {s["title"] for s in suggestions} == {"dress"}  # sub-floor child not rendered
    assert suggestions[0]["superseded_by_tag_ids"] == []  # ...so it demotes nothing


async def test_analyze_infers_at_storage_floor_but_displays_at_higher_floor(
    analyze_client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """Inference/cache use the STORAGE floor (ML_MIN_CONFIDENCE) so the cached raw is the
    complete set the worker will persist; the response applies the higher DISPLAY floor
    (ML_ANALYZE_MIN_CONFIDENCE). Guards against the cache-hit/cache-miss storage mismatch.
    """
    monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "ML_MIN_CONFIDENCE", 0.35)
    monkeypatch.setattr(settings, "ML_ANALYZE_MIN_CONFIDENCE", 0.5)

    high_tag = Tags(title="smile", type=TagType.THEME)
    low_tag = Tags(title="outdoors", type=TagType.THEME)
    db_session.add(high_tag)
    db_session.add(low_tag)
    await db_session.commit()
    await db_session.refresh(high_tag)
    await db_session.refresh(low_tag)

    raw_preds = [
        {"external_tag": "smile", "confidence": 0.9, "category": 0, "model_version": "v1"},
        {"external_tag": "outdoors", "confidence": 0.4, "category": 0, "model_version": "v1"},
    ]
    resolved = [
        {"tag_id": high_tag.tag_id, "confidence": 0.9, "model_version": "v1"},
        {"tag_id": low_tag.tag_id, "confidence": 0.4, "model_version": "v1"},  # below display floor
    ]
    captured: dict = {}

    with (
        patch(
            "app.api.v1.ml_analyze.get_ml_service",
            new_callable=AsyncMock,
            return_value=_fake_ml_service(raw_preds, captured),
        ),
        patch(
            "app.api.v1.ml_analyze.resolve_external_tags",
            new_callable=AsyncMock,
            return_value=resolved,
        ),
    ):
        response = await analyze_client.post("/api/v1/ml-tag-suggestions/analyze", files=_files())

    assert response.status_code == 200, response.text
    # Inference (and therefore the cache) uses the LOWER storage floor → cache is complete.
    assert captured["min_confidence"] == 0.35
    # The response applies the HIGHER display floor: the 0.4 tag is dropped, the 0.9 tag kept.
    titles = {s["title"] for s in response.json()["suggestions"]}
    assert "smile" in titles
    assert "outdoors" not in titles
