"""Tests for tag history tracking on image tagging."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.image import Images
from app.models.tag import Tags
from app.models.tag_history import TagHistory
from app.models.tag_link import TagLinks
from app.models.user import Users


class TestTagHistoryOnImageTagging:
    """Tests that TagHistory is written when tags are added/removed from images."""

    @pytest.mark.asyncio
    async def test_add_tag_creates_history_entry(
        self,
        authenticated_client: AsyncClient,
        db_session: AsyncSession,
        sample_user: Users,
        sample_image_data: dict,
    ) -> None:
        """Adding a tag to an image should create a TagHistory entry with action 'a'."""
        # Create an image owned by the authenticated user
        image_data = sample_image_data.copy()
        image_data["user_id"] = sample_user.user_id
        image = Images(**image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create a tag
        tag = Tags(title="history_test_tag", type=1)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Add tag to image via API
        response = await authenticated_client.post(
            f"/api/v1/images/{image.image_id}/tags/{tag.tag_id}"
        )
        assert response.status_code == 201

        # Verify TagHistory entry was created with action 'a' (add)
        result = await db_session.execute(
            select(TagHistory).where(
                TagHistory.image_id == image.image_id,
                TagHistory.tag_id == tag.tag_id,
                TagHistory.action == "a",
            )
        )
        history = result.scalar_one_or_none()
        assert history is not None, "TagHistory entry should be created on tag add"
        assert history.user_id == sample_user.user_id
        assert history.date is not None

    @pytest.mark.asyncio
    async def test_remove_tag_creates_history_entry(
        self,
        authenticated_client: AsyncClient,
        db_session: AsyncSession,
        sample_user: Users,
        sample_image_data: dict,
    ) -> None:
        """Removing a tag from an image should create a TagHistory entry with action 'r'."""
        # Create an image owned by the authenticated user
        image_data = sample_image_data.copy()
        image_data["user_id"] = sample_user.user_id
        image = Images(**image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create a tag and link it to the image
        tag = Tags(title="history_remove_test_tag", type=1)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Add the tag link directly (bypassing history for setup)
        tag_link = TagLinks(
            image_id=image.image_id,
            tag_id=tag.tag_id,
            user_id=sample_user.user_id,
        )
        db_session.add(tag_link)
        await db_session.commit()

        # Remove tag from image via API
        response = await authenticated_client.delete(
            f"/api/v1/images/{image.image_id}/tags/{tag.tag_id}"
        )
        assert response.status_code == 204

        # Verify TagHistory entry was created with action 'r' (remove)
        result = await db_session.execute(
            select(TagHistory).where(
                TagHistory.image_id == image.image_id,
                TagHistory.tag_id == tag.tag_id,
                TagHistory.action == "r",
            )
        )
        history = result.scalar_one_or_none()
        assert history is not None, "TagHistory entry should be created on tag remove"
        assert history.user_id == sample_user.user_id
        assert history.date is not None

    @pytest.mark.asyncio
    async def test_add_tag_history_records_correct_user(
        self,
        authenticated_client: AsyncClient,
        db_session: AsyncSession,
        sample_user: Users,
        sample_image_data: dict,
    ) -> None:
        """TagHistory should record the user who performed the action, not the image owner."""
        # Create an image owned by a different user (user_id=1 from conftest)
        image_data = sample_image_data.copy()
        image_data["user_id"] = 1  # Different user (test user from db_session setup)
        image = Images(**image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create a tag
        tag = Tags(title="user_tracking_tag", type=1)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Make the sample_user an admin so they can tag others' images
        sample_user.admin = 1
        await db_session.commit()

        # Add tag to image via API (as sample_user who is now admin)
        response = await authenticated_client.post(
            f"/api/v1/images/{image.image_id}/tags/{tag.tag_id}"
        )
        assert response.status_code == 201

        # Verify TagHistory records sample_user (not image owner)
        result = await db_session.execute(
            select(TagHistory).where(
                TagHistory.image_id == image.image_id,
                TagHistory.tag_id == tag.tag_id,
            )
        )
        history = result.scalar_one_or_none()
        assert history is not None
        assert history.user_id == sample_user.user_id, "TagHistory should record the user who added the tag"
