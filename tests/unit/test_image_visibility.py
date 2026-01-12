"""Tests for image visibility service."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus
from app.models.image import Images
from app.models.user import Users
from app.services.image_visibility import PUBLIC_IMAGE_STATUSES, can_view_image_file


class TestPublicImageStatuses:
    """Test the PUBLIC_IMAGE_STATUSES constant."""

    def test_public_statuses_include_repost(self):
        """REPOST (-1) should be publicly visible."""
        assert ImageStatus.REPOST in PUBLIC_IMAGE_STATUSES

    def test_public_statuses_include_active(self):
        """ACTIVE (1) should be publicly visible."""
        assert ImageStatus.ACTIVE in PUBLIC_IMAGE_STATUSES

    def test_public_statuses_include_spoiler(self):
        """SPOILER (2) should be publicly visible."""
        assert ImageStatus.SPOILER in PUBLIC_IMAGE_STATUSES

    def test_public_statuses_exclude_review(self):
        """REVIEW (-4) should NOT be publicly visible."""
        assert ImageStatus.REVIEW not in PUBLIC_IMAGE_STATUSES

    def test_public_statuses_exclude_inappropriate(self):
        """INAPPROPRIATE (-2) should NOT be publicly visible."""
        assert ImageStatus.INAPPROPRIATE not in PUBLIC_IMAGE_STATUSES

    def test_public_statuses_exclude_other(self):
        """OTHER (0) should NOT be publicly visible."""
        assert ImageStatus.OTHER not in PUBLIC_IMAGE_STATUSES


class TestCanViewImageFile:
    """Tests for can_view_image_file function."""

    @pytest.fixture
    def mock_image(self):
        """Create a mock image with configurable status and user_id."""
        image = Images(
            image_id=1,
            filename="test",
            ext="png",
            md5_hash="abc123",
            filesize=1000,
            width=100,
            height=100,
            user_id=10,
            status=ImageStatus.ACTIVE,
        )
        return image

    @pytest.fixture
    def mock_user(self):
        """Create a mock user."""
        user = Users(
            user_id=20,
            username="testuser",
            password="hash",
            password_type="bcrypt",
            salt="1234567890123456",
            email="test@example.com",
        )
        return user

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock(spec=AsyncSession)

    # === Public status tests ===

    async def test_active_image_visible_to_anonymous(self, mock_image, mock_db):
        """ACTIVE images are visible to anonymous users."""
        mock_image.status = ImageStatus.ACTIVE
        result = await can_view_image_file(mock_image, None, mock_db)
        assert result is True

    async def test_spoiler_image_visible_to_anonymous(self, mock_image, mock_db):
        """SPOILER images are visible to anonymous users."""
        mock_image.status = ImageStatus.SPOILER
        result = await can_view_image_file(mock_image, None, mock_db)
        assert result is True

    async def test_repost_image_visible_to_anonymous(self, mock_image, mock_db):
        """REPOST images are visible to anonymous users."""
        mock_image.status = ImageStatus.REPOST
        result = await can_view_image_file(mock_image, None, mock_db)
        assert result is True

    # === Protected status tests - anonymous ===

    async def test_review_image_hidden_from_anonymous(self, mock_image, mock_db):
        """REVIEW images are hidden from anonymous users."""
        mock_image.status = ImageStatus.REVIEW
        result = await can_view_image_file(mock_image, None, mock_db)
        assert result is False

    async def test_inappropriate_image_hidden_from_anonymous(self, mock_image, mock_db):
        """INAPPROPRIATE images are hidden from anonymous users."""
        mock_image.status = ImageStatus.INAPPROPRIATE
        result = await can_view_image_file(mock_image, None, mock_db)
        assert result is False

    async def test_other_image_hidden_from_anonymous(self, mock_image, mock_db):
        """OTHER images are hidden from anonymous users."""
        mock_image.status = ImageStatus.OTHER
        result = await can_view_image_file(mock_image, None, mock_db)
        assert result is False

    # === Protected status tests - non-owner regular user ===

    async def test_review_image_hidden_from_non_owner(self, mock_image, mock_user, mock_db):
        """REVIEW images are hidden from non-owner regular users."""
        mock_image.status = ImageStatus.REVIEW
        mock_image.user_id = 999  # Different from mock_user.user_id
        with patch(
            "app.services.image_visibility.has_any_permission", new_callable=AsyncMock
        ) as mock_perm:
            mock_perm.return_value = False
            result = await can_view_image_file(mock_image, mock_user, mock_db)
        assert result is False

    # === Owner visibility tests ===

    async def test_owner_can_view_review_image(self, mock_image, mock_user, mock_db):
        """Owners can view their own REVIEW images."""
        mock_image.status = ImageStatus.REVIEW
        mock_image.user_id = mock_user.user_id
        result = await can_view_image_file(mock_image, mock_user, mock_db)
        assert result is True

    async def test_owner_can_view_inappropriate_image(self, mock_image, mock_user, mock_db):
        """Owners can view their own INAPPROPRIATE images."""
        mock_image.status = ImageStatus.INAPPROPRIATE
        mock_image.user_id = mock_user.user_id
        result = await can_view_image_file(mock_image, mock_user, mock_db)
        assert result is True

    # === Moderator visibility tests ===

    async def test_moderator_with_image_edit_can_view_review(self, mock_image, mock_user, mock_db):
        """Users with IMAGE_EDIT permission can view REVIEW images."""
        mock_image.status = ImageStatus.REVIEW
        mock_image.user_id = 999  # Not the owner
        with patch(
            "app.services.image_visibility.has_any_permission", new_callable=AsyncMock
        ) as mock_perm:
            mock_perm.return_value = True
            result = await can_view_image_file(mock_image, mock_user, mock_db)
        assert result is True

    async def test_permission_check_uses_correct_permissions(self, mock_image, mock_user, mock_db):
        """Verify that IMAGE_EDIT and REVIEW_VIEW permissions are checked."""
        mock_image.status = ImageStatus.REVIEW
        mock_image.user_id = 999  # Not the owner
        with patch(
            "app.services.image_visibility.has_any_permission", new_callable=AsyncMock
        ) as mock_perm:
            mock_perm.return_value = False
            await can_view_image_file(mock_image, mock_user, mock_db)
            # Verify the correct permissions were checked
            from app.core.permissions import Permission

            mock_perm.assert_called_once()
            call_args = mock_perm.call_args
            assert call_args[0][1] == mock_user.user_id
            permissions_arg = call_args[0][2]
            assert Permission.IMAGE_EDIT in permissions_arg
            assert Permission.REVIEW_VIEW in permissions_arg
