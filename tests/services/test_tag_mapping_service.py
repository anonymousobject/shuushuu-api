"""
Tests for Tag Mapping Service.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import Tags
from app.models.tag_mapping import TagMappings
from app.models.user import Users
from app.services.tag_mapping_service import (
    find_orphan_mappings,
    get_mapped_external_tag_names,
    resolve_external_tags,
)


async def test_resolve_danbooru_tags_to_internal(
    db_session: AsyncSession,
):
    """Test mapping external tags to internal tag IDs."""
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

    tag = Tags(title="long hair", type=1, user_id=user.user_id)
    db_session.add(tag)
    await db_session.flush()

    mapping = TagMappings(
        external_tag="long_hair",
        internal_tag_id=tag.tag_id,
        confidence=1.0,
    )
    db_session.add(mapping)
    await db_session.commit()

    suggestions = [
        {"external_tag": "long_hair", "confidence": 0.9, "model_version": "wd-v1-4"},
    ]
    resolved = await resolve_external_tags(db_session, suggestions)

    assert len(resolved) == 1
    assert resolved[0]["tag_id"] == tag.tag_id
    assert resolved[0]["confidence"] == 0.9
    assert resolved[0]["model_version"] == "wd-v1-4"


async def test_ignore_tags_with_null_internal_id(
    db_session: AsyncSession,
):
    """Test that tags with NULL internal_tag_id are ignored."""
    mapping = TagMappings(
        external_tag="1girl",
        internal_tag_id=None,
        confidence=1.0,
    )
    db_session.add(mapping)
    await db_session.commit()

    suggestions = [
        {"external_tag": "1girl", "confidence": 0.95, "model_version": "wd-v1-4"},
    ]
    resolved = await resolve_external_tags(db_session, suggestions)

    assert len(resolved) == 0


async def test_unmapped_tags_excluded(
    db_session: AsyncSession,
):
    """Test that tags without mappings are excluded."""
    suggestions = [
        {"external_tag": "unknown_tag", "confidence": 0.9, "model_version": "wd-v1-4"},
    ]
    resolved = await resolve_external_tags(db_session, suggestions)

    assert len(resolved) == 0


async def test_mapping_confidence_multiplied(
    db_session: AsyncSession,
):
    """Test that mapping confidence is multiplied with prediction confidence."""
    user = Users(
        username="tag_mapping_conf_user",
        email="tagmappingconf@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="testsalt12345678",
        active=1,
    )
    db_session.add(user)
    await db_session.flush()

    tag = Tags(title="smile", type=1, user_id=user.user_id)
    db_session.add(tag)
    await db_session.flush()

    mapping = TagMappings(
        external_tag="smile",
        internal_tag_id=tag.tag_id,
        confidence=0.8,
    )
    db_session.add(mapping)
    await db_session.commit()

    suggestions = [
        {"external_tag": "smile", "confidence": 0.9, "model_version": "wd-v1-4"},
    ]
    resolved = await resolve_external_tags(db_session, suggestions)

    assert len(resolved) == 1
    # Confidence should be 0.9 * 0.8 = 0.72
    assert abs(resolved[0]["confidence"] - 0.72) < 0.001


async def test_batch_resolution_multiple_tags(
    db_session: AsyncSession,
):
    """Test resolving multiple tags in one batch."""
    user = Users(
        username="tag_mapping_batch_user",
        email="tagmappingbatch@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="testsalt12345678",
        active=1,
    )
    db_session.add(user)
    await db_session.flush()

    tag_long_hair = Tags(title="long hair", type=1, user_id=user.user_id)
    tag_smile = Tags(title="smile", type=1, user_id=user.user_id)
    db_session.add(tag_long_hair)
    db_session.add(tag_smile)
    await db_session.flush()

    mappings = [
        TagMappings(
            external_tag="long_hair_batch",
            internal_tag_id=tag_long_hair.tag_id,
            confidence=1.0,
        ),
        TagMappings(
            external_tag="smile_batch",
            internal_tag_id=tag_smile.tag_id,
            confidence=1.0,
        ),
        TagMappings(
            external_tag="1girl_batch",
            internal_tag_id=None,
            confidence=1.0,
        ),
    ]
    for m in mappings:
        db_session.add(m)
    await db_session.commit()

    suggestions = [
        {"external_tag": "long_hair_batch", "confidence": 0.92, "model_version": "wd-v1-4"},
        {"external_tag": "smile_batch", "confidence": 0.85, "model_version": "wd-v1-4"},
        {"external_tag": "1girl_batch", "confidence": 0.99, "model_version": "wd-v1-4"},
        {"external_tag": "unmapped_batch", "confidence": 0.80, "model_version": "wd-v1-4"},
    ]
    resolved = await resolve_external_tags(db_session, suggestions)

    # Should only resolve long_hair and smile (1girl ignored, unmapped excluded)
    assert len(resolved) == 2

    tag_ids = {r["tag_id"] for r in resolved}
    assert tag_long_hair.tag_id in tag_ids
    assert tag_smile.tag_id in tag_ids


async def test_empty_suggestions_returns_empty(
    db_session: AsyncSession,
):
    """Test that empty suggestions returns empty list."""
    resolved = await resolve_external_tags(db_session, [])
    assert resolved == []


async def test_preserves_model_version(
    db_session: AsyncSession,
):
    """Test that model_version is preserved in resolved suggestions."""
    user = Users(
        username="tag_mapping_ver_user",
        email="tagmappingver@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="testsalt12345678",
        active=1,
    )
    db_session.add(user)
    await db_session.flush()

    tag = Tags(title="dress", type=1, user_id=user.user_id)
    db_session.add(tag)
    await db_session.flush()

    mapping = TagMappings(
        external_tag="dress_ver",
        internal_tag_id=tag.tag_id,
        confidence=1.0,
    )
    db_session.add(mapping)
    await db_session.commit()

    suggestions = [
        {
            "external_tag": "dress_ver",
            "confidence": 0.88,
            "model_version": "wd-swinv2-tagger-v3",
        },
    ]
    resolved = await resolve_external_tags(db_session, suggestions)

    assert len(resolved) == 1
    assert resolved[0]["model_version"] == "wd-swinv2-tagger-v3"


# ---------------------------------------------------------------------------
# Unit tests for find_orphan_mappings (pure function, no DB)
# ---------------------------------------------------------------------------


def test_find_orphan_mappings_all_present() -> None:
    """All mapped tags exist in the vocab → no orphans."""
    result = find_orphan_mappings(
        {"long_hair", "smile", "1girl"},
        {"long_hair", "smile", "1girl", "extra_tag"},
    )
    assert result == []


def test_find_orphan_mappings_some_missing() -> None:
    """Tags in mappings but not in the vocab are returned, sorted."""
    result = find_orphan_mappings(
        {"long_hair", "missing_tag", "another_missing"},
        {"long_hair"},
    )
    assert result == ["another_missing", "missing_tag"]


def test_find_orphan_mappings_empty_mapped() -> None:
    """No mapped tags → no orphans regardless of vocab."""
    result = find_orphan_mappings(set(), {"long_hair", "smile"})
    assert result == []


def test_find_orphan_mappings_empty_vocab() -> None:
    """All mapped tags are orphans when the vocab is empty."""
    result = find_orphan_mappings({"long_hair", "smile"}, set())
    assert result == ["long_hair", "smile"]


# ---------------------------------------------------------------------------
# Integration tests for get_mapped_external_tag_names (real DB)
# ---------------------------------------------------------------------------


async def test_get_mapped_external_tag_names_returns_names(
    db_session: AsyncSession,
) -> None:
    """Returns the external_tag names from all real mappings (both mapped and ignored)."""
    user = Users(
        username="orphan_check_user",
        email="orphancheck@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="testsalt12345678",
        active=1,
    )
    db_session.add(user)
    await db_session.flush()

    tag = Tags(title="orphan test tag", type=1, user_id=user.user_id)
    db_session.add(tag)
    await db_session.flush()

    # One real mapping and one ignored (NULL internal_tag_id) mapping
    db_session.add(TagMappings(external_tag="orphan_mapped_ext", internal_tag_id=tag.tag_id))
    db_session.add(TagMappings(external_tag="orphan_ignored_ext", internal_tag_id=None))
    await db_session.commit()

    names = await get_mapped_external_tag_names(db_session)

    assert "orphan_mapped_ext" in names
    assert "orphan_ignored_ext" in names


async def test_get_mapped_external_tag_names_excludes_unmapped(
    db_session: AsyncSession,
) -> None:
    """Rows that were never inserted are not in the result (basic sanity check)."""
    names = await get_mapped_external_tag_names(db_session)
    assert "this_tag_was_never_inserted_xyz" not in names


async def test_find_orphan_mappings_integration(
    db_session: AsyncSession,
) -> None:
    """End-to-end: seed two mappings, drop one from the vocab, confirm the orphan is flagged."""
    user = Users(
        username="orphan_integration_user",
        email="orphanintegration@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="testsalt12345678",
        active=1,
    )
    db_session.add(user)
    await db_session.flush()

    tag = Tags(title="orphan integration tag", type=1, user_id=user.user_id)
    db_session.add(tag)
    await db_session.flush()

    db_session.add(TagMappings(external_tag="orphan_in_vocab", internal_tag_id=tag.tag_id))
    db_session.add(TagMappings(external_tag="orphan_not_in_vocab", internal_tag_id=tag.tag_id))
    await db_session.commit()

    mapped = await get_mapped_external_tag_names(db_session)

    # Vocab that only contains one of the two mapped tags
    vocab = {"orphan_in_vocab", "some_other_model_tag"}
    orphans = find_orphan_mappings(mapped, vocab)

    assert "orphan_not_in_vocab" in orphans
    assert "orphan_in_vocab" not in orphans
