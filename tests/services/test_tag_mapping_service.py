"""
Tests for Tag Mapping Service.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import Tags
from app.models.tag_mapping import TagMapping
from app.models.user import Users
from app.services.tag_mapping_service import resolve_external_tags


@pytest.fixture
async def test_user(db_session: AsyncSession) -> Users:
    """Create a test user."""
    user = Users(
        username="tag_mapping_test_user",
        email="tagmapping@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="testsalt12345678",
        active=1,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.fixture
async def test_tags(db_session: AsyncSession, test_user: Users) -> dict[str, Tags]:
    """Create test tags."""
    tags = {
        "long_hair": Tags(title="long hair", type=1, user_id=test_user.user_id),
        "smile": Tags(title="smile", type=1, user_id=test_user.user_id),
        "dress": Tags(title="dress", type=1, user_id=test_user.user_id),
    }
    for tag in tags.values():
        db_session.add(tag)
    await db_session.flush()
    return tags


@pytest.mark.asyncio
async def test_resolve_danbooru_tags_to_internal(
    db_session: AsyncSession,
    test_user: Users,
    test_tags: dict[str, Tags],
):
    """Test mapping Danbooru tags to internal tag IDs."""
    # Create mapping: danbooru "long_hair" → internal "long hair"
    mapping = TagMapping(
        external_tag="long_hair",
        external_source="danbooru",
        internal_tag_id=test_tags["long_hair"].tag_id,
        confidence=1.0,
    )
    db_session.add(mapping)
    await db_session.commit()

    # Resolve suggestions
    suggestions = [
        {"external_tag": "long_hair", "confidence": 0.9, "model_source": "danbooru"},
    ]
    resolved = await resolve_external_tags(db_session, suggestions)

    assert len(resolved) == 1
    assert resolved[0]["tag_id"] == test_tags["long_hair"].tag_id
    assert resolved[0]["confidence"] == 0.9
    assert resolved[0]["model_source"] == "danbooru"


@pytest.mark.asyncio
async def test_ignore_tags_with_null_internal_id(
    db_session: AsyncSession,
):
    """Test that tags with NULL internal_tag_id are ignored."""
    # Create mapping with NULL internal_tag_id (means ignore)
    mapping = TagMapping(
        external_tag="1girl",
        external_source="danbooru",
        internal_tag_id=None,  # Ignore this tag
        confidence=1.0,
    )
    db_session.add(mapping)
    await db_session.commit()

    suggestions = [
        {"external_tag": "1girl", "confidence": 0.95, "model_source": "danbooru"},
    ]
    resolved = await resolve_external_tags(db_session, suggestions)

    assert len(resolved) == 0  # Tag was ignored


@pytest.mark.asyncio
async def test_unmapped_tags_excluded(
    db_session: AsyncSession,
):
    """Test that tags without mappings are excluded."""
    # No mappings created
    suggestions = [
        {"external_tag": "unknown_tag", "confidence": 0.9, "model_source": "danbooru"},
    ]
    resolved = await resolve_external_tags(db_session, suggestions)

    assert len(resolved) == 0


@pytest.mark.asyncio
async def test_mapping_confidence_multiplied(
    db_session: AsyncSession,
    test_user: Users,
    test_tags: dict[str, Tags],
):
    """Test that mapping confidence is multiplied with prediction confidence."""
    # Create mapping with 0.8 confidence
    mapping = TagMapping(
        external_tag="smile",
        external_source="danbooru",
        internal_tag_id=test_tags["smile"].tag_id,
        confidence=0.8,  # 80% confidence in mapping
    )
    db_session.add(mapping)
    await db_session.commit()

    suggestions = [
        {"external_tag": "smile", "confidence": 0.9, "model_source": "danbooru"},
    ]
    resolved = await resolve_external_tags(db_session, suggestions)

    assert len(resolved) == 1
    # Confidence should be 0.9 * 0.8 = 0.72
    assert abs(resolved[0]["confidence"] - 0.72) < 0.001


@pytest.mark.asyncio
async def test_batch_resolution_multiple_tags(
    db_session: AsyncSession,
    test_user: Users,
    test_tags: dict[str, Tags],
):
    """Test resolving multiple tags in one batch."""
    # Create mappings
    mappings = [
        TagMapping(
            external_tag="long_hair",
            external_source="danbooru",
            internal_tag_id=test_tags["long_hair"].tag_id,
            confidence=1.0,
        ),
        TagMapping(
            external_tag="smile",
            external_source="danbooru",
            internal_tag_id=test_tags["smile"].tag_id,
            confidence=1.0,
        ),
        TagMapping(
            external_tag="1girl",
            external_source="danbooru",
            internal_tag_id=None,  # Ignore
            confidence=1.0,
        ),
    ]
    for m in mappings:
        db_session.add(m)
    await db_session.commit()

    suggestions = [
        {"external_tag": "long_hair", "confidence": 0.92, "model_source": "danbooru"},
        {"external_tag": "smile", "confidence": 0.85, "model_source": "danbooru"},
        {"external_tag": "1girl", "confidence": 0.99, "model_source": "danbooru"},
        {"external_tag": "unmapped", "confidence": 0.80, "model_source": "danbooru"},
    ]
    resolved = await resolve_external_tags(db_session, suggestions)

    # Should only resolve long_hair and smile (1girl ignored, unmapped excluded)
    assert len(resolved) == 2

    tag_ids = {r["tag_id"] for r in resolved}
    assert test_tags["long_hair"].tag_id in tag_ids
    assert test_tags["smile"].tag_id in tag_ids


@pytest.mark.asyncio
async def test_empty_suggestions_returns_empty(
    db_session: AsyncSession,
):
    """Test that empty suggestions returns empty list."""
    resolved = await resolve_external_tags(db_session, [])
    assert resolved == []


@pytest.mark.asyncio
async def test_preserves_model_version(
    db_session: AsyncSession,
    test_user: Users,
    test_tags: dict[str, Tags],
):
    """Test that model_version is preserved in resolved suggestions."""
    mapping = TagMapping(
        external_tag="dress",
        external_source="danbooru",
        internal_tag_id=test_tags["dress"].tag_id,
        confidence=1.0,
    )
    db_session.add(mapping)
    await db_session.commit()

    suggestions = [
        {
            "external_tag": "dress",
            "confidence": 0.88,
            "model_source": "danbooru",
            "model_version": "wd-swinv2-tagger-v3",
        },
    ]
    resolved = await resolve_external_tags(db_session, suggestions)

    assert len(resolved) == 1
    assert resolved[0]["model_version"] == "wd-swinv2-tagger-v3"
