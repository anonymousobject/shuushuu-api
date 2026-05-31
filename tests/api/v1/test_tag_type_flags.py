import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.models import Images, TagLinks, Tags
from app.services.tag_type_flags import (
    refresh_image_tag_type_flags,
    refresh_images_tag_type_flags,
)


@pytest.mark.api
class TestTagTypeFlagsHelper:
    async def test_refresh_sets_flags_for_present_types(
        self, db_session: AsyncSession, sample_image_data: dict
    ):
        img = Images(**{**sample_image_data, "filename": "ttf1", "md5_hash": "11" * 16})
        db_session.add(img)
        await db_session.flush()
        artist = Tags(title="ttf artist", desc="a", type=TagType.ARTIST)
        theme = Tags(title="ttf theme", desc="t", type=TagType.THEME)
        db_session.add_all([artist, theme])
        await db_session.flush()
        # links added but NOT flushed — exercises the helper's internal flush.
        db_session.add(TagLinks(image_id=img.image_id, tag_id=artist.tag_id, user_id=1))
        db_session.add(TagLinks(image_id=img.image_id, tag_id=theme.tag_id, user_id=1))

        await refresh_image_tag_type_flags(db_session, img.image_id)

        await db_session.refresh(img)
        assert img.has_artist is True
        assert img.has_theme is True
        assert img.has_source is False
        assert img.has_character is False

    async def test_refresh_clears_flag_when_last_tag_of_type_gone(
        self, db_session: AsyncSession, sample_image_data: dict
    ):
        img = Images(**{**sample_image_data, "filename": "ttf2", "md5_hash": "22" * 16})
        db_session.add(img)
        await db_session.flush()
        artist = Tags(title="ttf artist2", desc="a", type=TagType.ARTIST)
        db_session.add(artist)
        await db_session.flush()
        db_session.add(TagLinks(image_id=img.image_id, tag_id=artist.tag_id, user_id=1))
        await refresh_image_tag_type_flags(db_session, img.image_id)
        await db_session.refresh(img)
        assert img.has_artist is True

        from sqlalchemy import delete
        await db_session.execute(
            delete(TagLinks).where(
                TagLinks.image_id == img.image_id, TagLinks.tag_id == artist.tag_id
            )
        )
        await refresh_image_tag_type_flags(db_session, img.image_id)
        await db_session.refresh(img)
        assert img.has_artist is False

    async def test_refresh_empty_set_is_noop(self, db_session: AsyncSession):
        await refresh_images_tag_type_flags(db_session, [])  # must not raise
