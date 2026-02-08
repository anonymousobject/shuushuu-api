"""Tests for miscmeta field exposure in models and schemas."""

from app.models.image import ImageBase
from app.schemas.image import ImageUpdate


class TestMiscmetaInImageBase:
    """miscmeta should be a public field on ImageBase (and thus on all response schemas)."""

    def test_miscmeta_is_a_field_on_image_base(self):
        """ImageBase should include miscmeta as a field."""
        assert "miscmeta" in ImageBase.model_fields

    def test_miscmeta_defaults_to_none(self):
        """miscmeta should default to None when not provided."""
        image = ImageBase(ext="jpg")
        assert image.miscmeta is None

    def test_miscmeta_accepts_string_value(self):
        """miscmeta should accept a string value."""
        image = ImageBase(ext="jpg", miscmeta="pixiv: 12345")
        assert image.miscmeta == "pixiv: 12345"


class TestMiscmetaInImageUpdate:
    """ImageUpdate schema should accept miscmeta for editing."""

    def test_image_update_accepts_miscmeta(self):
        """ImageUpdate should have miscmeta as an optional field."""
        assert "miscmeta" in ImageUpdate.model_fields

    def test_image_update_miscmeta_defaults_to_none(self):
        """miscmeta should default to None (not included in update) when not provided."""
        update = ImageUpdate()
        assert update.miscmeta is None

    def test_image_update_with_miscmeta_value(self):
        """ImageUpdate should accept a miscmeta string."""
        update = ImageUpdate(miscmeta="some metadata")
        assert update.miscmeta == "some metadata"
