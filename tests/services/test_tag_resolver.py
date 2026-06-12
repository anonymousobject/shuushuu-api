"""
Tests for Tag Relationship Resolver.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import Tags
from app.models.user import Users
from app.services.tag_resolver import resolve_tag_relationships


async def test_resolve_alias_to_canonical_tag(db_session: AsyncSession):
    """Test that alias tags are resolved to canonical tags."""
    user = Users(
        username="resolver_test1",
        email="resolver1@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="testsalt12345678",
    )
    db_session.add(user)
    await db_session.flush()

    # Create canonical tag
    canonical = Tags(tag_id=46, title="long hair", type=1, user_id=user.user_id)
    db_session.add(canonical)
    await db_session.flush()

    # Create alias tag pointing to canonical
    alias = Tags(tag_id=100, title="longhair", type=1, alias_of=46, user_id=user.user_id)
    db_session.add(alias)
    await db_session.commit()

    suggestions = [
        {"tag_id": 100, "confidence": 0.9, "model_version": "wd-v1-4"},
    ]

    resolved = await resolve_tag_relationships(db_session, suggestions)

    assert len(resolved) == 1
    assert resolved[0]["tag_id"] == 46
    assert resolved[0]["resolved_from_alias"] is True


async def test_add_parent_tag_from_hierarchy(db_session: AsyncSession):
    """Test that parent tags are added when child tag has high confidence."""
    user = Users(
        username="resolver_test2",
        email="resolver2@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="testsalt12345678",
    )
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
        {"tag_id": 46, "confidence": 0.95, "model_version": "wd-v1-4"},
    ]

    resolved = await resolve_tag_relationships(db_session, suggestions)

    assert len(resolved) == 2
    tag_ids = [s["tag_id"] for s in resolved]
    assert 46 in tag_ids
    assert 50 in tag_ids

    parent_sugg = next(s for s in resolved if s["tag_id"] == 50)
    assert parent_sugg["confidence"] < 0.95
    assert parent_sugg["from_hierarchy"] is True


async def test_low_confidence_does_not_add_parent(db_session: AsyncSession):
    """Test that parent tags are NOT added when confidence is below threshold (0.7)."""
    user = Users(
        username="resolver_test3",
        email="resolver3@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="testsalt12345678",
    )
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
        {"tag_id": 61, "confidence": 0.69, "model_version": "wd-v1-4"},
    ]

    resolved = await resolve_tag_relationships(db_session, suggestions)

    assert len(resolved) == 1
    assert resolved[0]["tag_id"] == 61
    assert "from_hierarchy" not in resolved[0]


async def test_duplicate_prevention_keeps_highest_confidence(db_session: AsyncSession):
    """Test that duplicate tag_ids are prevented, keeping highest confidence."""
    user = Users(
        username="resolver_test4",
        email="resolver4@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="testsalt12345678",
    )
    db_session.add(user)
    await db_session.flush()

    # Create canonical tag
    canonical = Tags(tag_id=70, title="blue eyes", type=1, user_id=user.user_id)
    db_session.add(canonical)
    await db_session.flush()

    # Create two alias tags pointing to same canonical
    alias1 = Tags(tag_id=71, title="blueeyes", type=1, alias_of=70, user_id=user.user_id)
    alias2 = Tags(tag_id=72, title="blue_eyes", type=1, alias_of=70, user_id=user.user_id)
    db_session.add(alias1)
    db_session.add(alias2)
    await db_session.commit()

    suggestions = [
        {"tag_id": 71, "confidence": 0.8, "model_version": "wd-v1-4"},
        {"tag_id": 72, "confidence": 0.95, "model_version": "wd-v1-4"},
    ]

    resolved = await resolve_tag_relationships(db_session, suggestions)

    assert len(resolved) == 1
    assert resolved[0]["tag_id"] == 70
    assert resolved[0]["confidence"] == 0.95
    assert resolved[0]["resolved_from_alias"] is True
