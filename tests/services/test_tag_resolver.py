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
