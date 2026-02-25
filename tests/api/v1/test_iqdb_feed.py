"""
Tests for GET /image/index.xml iqdb.org feed endpoint.

This endpoint provides an XML feed of images for iqdb.org crawling,
matching the format of the old PHP API.
"""

import xml.etree.ElementTree as ET

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, TagType
from app.models.image import Images
from app.models.tag import Tags
from app.models.tag_link import TagLinks


@pytest.mark.api
class TestIqdbFeed:
    """Tests for GET /image/index.xml endpoint."""

    async def test_empty_feed_returns_valid_xml(self, client: AsyncClient):
        """An empty database should return valid XML with no <image> elements."""
        response = await client.get("/image/index.xml")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/xml; charset=utf-8"

        root = ET.fromstring(response.text)
        assert root.tag == "images"
        assert len(root) == 0

    async def test_returns_active_images(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Should return active images with correct XML attributes."""
        image = Images(
            filename="2026-01-15-12345",
            ext="jpg",
            md5_hash="abcdef1234567890abcdef1234567890",
            filesize=524288,
            width=1920,
            height=1080,
            user_id=1,
            status=ImageStatus.ACTIVE,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        response = await client.get("/image/index.xml")

        assert response.status_code == 200
        root = ET.fromstring(response.text)
        assert len(root) == 1

        elem = root[0]
        assert elem.tag == "image"
        assert elem.get("id") == str(image.image_id)
        assert elem.get("md5") == "abcdef1234567890abcdef1234567890"
        assert elem.get("status") == "active"
        assert elem.get("width") == "1920"
        assert elem.get("height") == "1080"
        assert elem.get("filesize") == "524288"
        assert elem.get("submitted_by") == "1"
        assert elem.get("submitted_on") is not None
        assert "2026-01-15-12345.webp" in elem.get("preview_url")

    async def test_excludes_non_active_images(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Should only return images with status == ACTIVE."""
        # Create one active and one inactive image
        active = Images(
            filename="active-image",
            ext="jpg",
            md5_hash="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1",
            filesize=1000,
            width=100,
            height=100,
            user_id=1,
            status=ImageStatus.ACTIVE,
        )
        inactive = Images(
            filename="inactive-image",
            ext="jpg",
            md5_hash="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa2",
            filesize=1000,
            width=100,
            height=100,
            user_id=1,
            status=ImageStatus.INAPPROPRIATE,
        )
        db_session.add_all([active, inactive])
        await db_session.commit()
        await db_session.refresh(active)

        response = await client.get("/image/index.xml")

        root = ET.fromstring(response.text)
        assert len(root) == 1
        assert root[0].get("id") == str(active.image_id)

    async def test_limit_parameter(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Should respect the limit parameter."""
        for i in range(5):
            image = Images(
                filename=f"limit-test-{i}",
                ext="jpg",
                md5_hash=f"limit{i:027d}",
                filesize=1000,
                width=100,
                height=100,
                user_id=1,
                status=ImageStatus.ACTIVE,
            )
            db_session.add(image)
        await db_session.commit()

        response = await client.get("/image/index.xml?limit=3")

        root = ET.fromstring(response.text)
        assert len(root) == 3

    async def test_default_limit_is_16(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Default limit should be 16."""
        for i in range(20):
            image = Images(
                filename=f"default-limit-{i}",
                ext="jpg",
                md5_hash=f"deflim{i:025d}",
                filesize=1000,
                width=100,
                height=100,
                user_id=1,
                status=ImageStatus.ACTIVE,
            )
            db_session.add(image)
        await db_session.commit()

        response = await client.get("/image/index.xml")

        root = ET.fromstring(response.text)
        assert len(root) == 16

    async def test_limit_capped_at_100(self, client: AsyncClient):
        """Limit should be capped at 100."""
        response = await client.get("/image/index.xml?limit=200")

        assert response.status_code == 422

    async def test_after_id_ascending_order(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """With after_id, should return images with id > after_id in ASC order."""
        images = []
        for i in range(5):
            image = Images(
                filename=f"after-id-test-{i}",
                ext="jpg",
                md5_hash=f"afterid{i:024d}",
                filesize=1000,
                width=100,
                height=100,
                user_id=1,
                status=ImageStatus.ACTIVE,
            )
            db_session.add(image)
            images.append(image)
        await db_session.commit()
        for img in images:
            await db_session.refresh(img)

        # Get images after the second one
        after_id = images[1].image_id
        response = await client.get(f"/image/index.xml?after_id={after_id}&limit=10")

        root = ET.fromstring(response.text)
        ids = [int(elem.get("id")) for elem in root]
        assert all(id > after_id for id in ids)
        # Should be ascending
        assert ids == sorted(ids)
        assert len(ids) == 3

    async def test_without_after_id_descending_order(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Without after_id, should return images in DESC order (newest first)."""
        for i in range(5):
            image = Images(
                filename=f"desc-test-{i}",
                ext="jpg",
                md5_hash=f"desctest{i:023d}",
                filesize=1000,
                width=100,
                height=100,
                user_id=1,
                status=ImageStatus.ACTIVE,
            )
            db_session.add(image)
        await db_session.commit()

        response = await client.get("/image/index.xml?limit=5")

        root = ET.fromstring(response.text)
        ids = [int(elem.get("id")) for elem in root]
        assert ids == sorted(ids, reverse=True)

    async def test_tags_grouped_by_type(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Tags should be grouped by type in the XML attributes."""
        image = Images(
            filename="tagged-image",
            ext="jpg",
            md5_hash="taggedimage00000000000000000001",
            filesize=1000,
            width=100,
            height=100,
            user_id=1,
            status=ImageStatus.ACTIVE,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create tags of different types
        theme_tag = Tags(title="blonde hair", type=TagType.THEME)
        artist_tag = Tags(title="Artist Name", type=TagType.ARTIST)
        source_tag = Tags(title="Source Name", type=TagType.SOURCE)
        char_tag = Tags(title="Character Name", type=TagType.CHARACTER)
        db_session.add_all([theme_tag, artist_tag, source_tag, char_tag])
        await db_session.commit()
        await db_session.refresh(theme_tag)
        await db_session.refresh(artist_tag)
        await db_session.refresh(source_tag)
        await db_session.refresh(char_tag)

        # Link tags to image
        for tag in [theme_tag, artist_tag, source_tag, char_tag]:
            link = TagLinks(tag_id=tag.tag_id, image_id=image.image_id, user_id=1)
            db_session.add(link)
        await db_session.commit()

        response = await client.get("/image/index.xml")

        root = ET.fromstring(response.text)
        elem = root[0]
        assert elem.get("theme_tags") == "blonde hair"
        assert elem.get("artist") == "Artist Name"
        assert elem.get("source") == "Source Name"
        assert elem.get("characters") == "Character Name"

    async def test_multiple_tags_comma_separated(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Multiple tags of the same type should be comma-separated."""
        image = Images(
            filename="multi-tag-image",
            ext="jpg",
            md5_hash="multitagimage000000000000000001",
            filesize=1000,
            width=100,
            height=100,
            user_id=1,
            status=ImageStatus.ACTIVE,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        tag1 = Tags(title="blonde hair", type=TagType.THEME)
        tag2 = Tags(title="blue eyes", type=TagType.THEME)
        db_session.add_all([tag1, tag2])
        await db_session.commit()
        await db_session.refresh(tag1)
        await db_session.refresh(tag2)

        for tag in [tag1, tag2]:
            link = TagLinks(tag_id=tag.tag_id, image_id=image.image_id, user_id=1)
            db_session.add(link)
        await db_session.commit()

        response = await client.get("/image/index.xml")

        root = ET.fromstring(response.text)
        elem = root[0]
        theme_tags = elem.get("theme_tags")
        assert "blonde hair" in theme_tags
        assert "blue eyes" in theme_tags
        assert ", " in theme_tags
