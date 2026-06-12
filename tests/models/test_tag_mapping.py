"""Tests for the TagMappings model against a real database."""

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.tag_mapping import TagMappings


async def test_tag_mapping_creation(db_session, test_tag):
    """A mapping links an external tag name to an internal tag id."""
    mapping = TagMappings(
        external_tag="long_hair",
        internal_tag_id=test_tag.tag_id,
        confidence=1.0,
    )
    db_session.add(mapping)
    await db_session.commit()
    await db_session.refresh(mapping)

    assert mapping.mapping_id is not None
    assert mapping.external_tag == "long_hair"
    assert mapping.internal_tag_id == test_tag.tag_id
    assert mapping.created_at is not None


async def test_tag_mapping_null_internal_tag(db_session):
    """A null internal_tag_id means the external tag is known but ignored."""
    mapping = TagMappings(
        external_tag="1girl",  # Danbooru tag we deliberately drop
        internal_tag_id=None,
        confidence=1.0,
    )
    db_session.add(mapping)
    await db_session.commit()
    await db_session.refresh(mapping)

    assert mapping.internal_tag_id is None


@pytest.mark.needs_commit  # failed commit needs real-commit + truncate isolation
async def test_tag_mapping_unique_constraint(db_session):
    """The same external tag cannot be mapped twice."""
    mapping1 = TagMappings(external_tag="1girl")
    db_session.add(mapping1)
    await db_session.commit()

    mapping2 = TagMappings(external_tag="1girl")
    db_session.add(mapping2)

    with pytest.raises(IntegrityError):  # unique_external_tag
        await db_session.commit()
