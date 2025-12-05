import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.tag_resolver import resolve_tag_relationships
from app.models.tag import Tags
from app.models.user import Users


@pytest.mark.asyncio
async def test_resolve_alias_to_canonical_tag(db_session: AsyncSession):
    """Test that alias tags are resolved to canonical tags"""
    user = Users(username="test", email="test@example.com", password="hashed", salt="testsalt12345678")
    db_session.add(user)
    await db_session.flush()

    # Create canonical tag
    canonical = Tags(tag_id=46, title="long hair", type=1, user_id=user.user_id)
    db_session.add(canonical)
    await db_session.flush()

    # Create alias tag pointing to canonical
    alias = Tags(tag_id=100, title="longhair", type=1, alias=46, user_id=user.user_id)
    db_session.add(alias)
    await db_session.commit()

    # Suggestion with alias tag
    suggestions = [
        {"tag_id": 100, "confidence": 0.9, "model_source": "custom_theme"}
    ]

    resolved = await resolve_tag_relationships(db_session, suggestions)

    # Should resolve to canonical tag
    assert len(resolved) == 1
    assert resolved[0]["tag_id"] == 46  # Canonical tag
    assert resolved[0]["resolved_from_alias"] is True


@pytest.mark.asyncio
async def test_add_parent_tag_from_hierarchy(db_session: AsyncSession):
    """Test that parent tags are added when child tag has high confidence"""
    user = Users(username="test2", email="test2@example.com", password="hashed", salt="testsalt12345678")
    db_session.add(user)
    await db_session.flush()

    # Create parent tag
    parent = Tags(tag_id=50, title="hair", type=1, user_id=user.user_id)
    db_session.add(parent)
    await db_session.flush()

    # Create child tag with parent reference
    child = Tags(tag_id=46, title="long hair", type=1, inheritedfrom_id=50, user_id=user.user_id)
    db_session.add(child)
    await db_session.commit()

    suggestions = [
        {"tag_id": 46, "confidence": 0.95, "model_source": "custom_theme"}
    ]

    resolved = await resolve_tag_relationships(db_session, suggestions)

    # Should have both child and parent
    assert len(resolved) == 2
    tag_ids = [s["tag_id"] for s in resolved]
    assert 46 in tag_ids  # Child
    assert 50 in tag_ids  # Parent

    # Parent should have slightly lower confidence
    parent_sugg = next(s for s in resolved if s["tag_id"] == 50)
    assert parent_sugg["confidence"] < 0.95
    assert parent_sugg["from_hierarchy"] is True


@pytest.mark.asyncio
async def test_low_confidence_does_not_add_parent(db_session: AsyncSession):
    """Test that parent tags are NOT added when confidence is below threshold (0.7)"""
    user = Users(username="test3", email="test3@example.com", password="hashed", salt="testsalt12345678")
    db_session.add(user)
    await db_session.flush()

    # Create parent tag
    parent = Tags(tag_id=60, title="hair", type=1, user_id=user.user_id)
    db_session.add(parent)
    await db_session.flush()

    # Create child tag with parent reference
    child = Tags(tag_id=61, title="short hair", type=1, inheritedfrom_id=60, user_id=user.user_id)
    db_session.add(child)
    await db_session.commit()

    # Low confidence (0.69) - should NOT add parent
    suggestions = [
        {"tag_id": 61, "confidence": 0.69, "model_source": "custom_theme"}
    ]

    resolved = await resolve_tag_relationships(db_session, suggestions)

    # Should only have child, no parent
    assert len(resolved) == 1
    assert resolved[0]["tag_id"] == 61  # Child only
    assert "from_hierarchy" not in resolved[0]


@pytest.mark.asyncio
async def test_duplicate_prevention_keeps_highest_confidence(db_session: AsyncSession):
    """Test that duplicate tag_ids are prevented, keeping highest confidence"""
    user = Users(username="test4", email="test4@example.com", password="hashed", salt="testsalt12345678")
    db_session.add(user)
    await db_session.flush()

    # Create canonical tag
    canonical = Tags(tag_id=70, title="blue eyes", type=1, user_id=user.user_id)
    db_session.add(canonical)
    await db_session.flush()

    # Create two alias tags pointing to same canonical
    alias1 = Tags(tag_id=71, title="blueeyes", type=1, alias=70, user_id=user.user_id)
    alias2 = Tags(tag_id=72, title="blue_eyes", type=1, alias=70, user_id=user.user_id)
    db_session.add(alias1)
    db_session.add(alias2)
    await db_session.commit()

    # Two suggestions with different aliases resolving to same tag
    suggestions = [
        {"tag_id": 71, "confidence": 0.8, "model_source": "custom_theme"},
        {"tag_id": 72, "confidence": 0.95, "model_source": "custom_source"},  # Higher confidence
    ]

    resolved = await resolve_tag_relationships(db_session, suggestions)

    # Should only have one result (canonical tag)
    assert len(resolved) == 1
    assert resolved[0]["tag_id"] == 70  # Canonical tag
    assert resolved[0]["confidence"] == 0.95  # Higher confidence kept
    assert resolved[0]["resolved_from_alias"] is True
