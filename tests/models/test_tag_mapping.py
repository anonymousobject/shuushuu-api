import pytest

from app.models.tag import Tags
from app.models.tag_mapping import TagMapping
from app.models.user import Users


@pytest.mark.asyncio
async def test_tag_mapping_creation(db_session):
    """Test creating a TagMapping instance"""
    user = Users(
        username="tag_mapping_test_user",
        email="tag_mapping@example.com",
        password="hashed",
        salt="testsalt12345678"
    )
    db_session.add(user)
    await db_session.flush()

    tag = Tags(title="long hair", type=1, user_id=user.user_id)
    db_session.add(tag)
    await db_session.flush()

    mapping = TagMapping(
        external_tag="long_hair",
        external_source="danbooru",
        internal_tag_id=tag.tag_id,
        confidence=1.0
    )
    db_session.add(mapping)
    await db_session.commit()
    await db_session.refresh(mapping)

    assert mapping.mapping_id is not None
    assert mapping.external_tag == "long_hair"
    assert mapping.internal_tag_id == tag.tag_id


@pytest.mark.asyncio
async def test_tag_mapping_null_internal_tag(db_session):
    """Test mapping with null internal_tag_id (means ignore this tag)"""
    mapping = TagMapping(
        external_tag="1girl",  # Danbooru tag we don't use
        external_source="danbooru",
        internal_tag_id=None,  # Ignore this tag
        confidence=1.0
    )
    db_session.add(mapping)
    await db_session.commit()
    await db_session.refresh(mapping)

    assert mapping.internal_tag_id is None
