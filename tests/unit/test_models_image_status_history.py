"""Tests for ImageStatusHistory model."""

from app.models.image_status_history import ImageStatusHistory


class TestImageStatusHistoryModel:
    """Tests for ImageStatusHistory model structure."""

    def test_model_has_required_fields(self) -> None:
        """Verify model has all required fields."""
        history = ImageStatusHistory(
            image_id=1,
            old_status=1,
            new_status=-1,
            user_id=123,
        )
        assert history.image_id == 1
        assert history.old_status == 1
        assert history.new_status == -1
        assert history.user_id == 123

    def test_user_id_nullable(self) -> None:
        """Verify user_id can be None (for system actions)."""
        history = ImageStatusHistory(
            image_id=1,
            old_status=1,
            new_status=-2,
        )
        assert history.user_id is None
