"""Tests for media file serving endpoints."""

from app.api.v1.media import get_extension_from_filename, parse_image_id_from_filename


class TestFilenameParsing:
    """Tests for filename parsing utilities."""

    def test_parse_valid_filename(self):
        """Parse image_id from valid filename like '2026-01-02-1112196.png'."""
        result = parse_image_id_from_filename("2026-01-02-1112196.png")
        assert result == 1112196

    def test_parse_filename_with_jpeg(self):
        """Parse image_id from jpeg filename."""
        result = parse_image_id_from_filename("2025-12-31-999.jpeg")
        assert result == 999

    def test_parse_invalid_filename_no_id(self):
        """Return None for filename without image_id."""
        result = parse_image_id_from_filename("invalid.png")
        assert result is None

    def test_parse_invalid_filename_no_extension(self):
        """Return None for filename without extension."""
        result = parse_image_id_from_filename("2026-01-02-1112196")
        assert result is None

    def test_parse_invalid_filename_non_numeric_id(self):
        """Return None for filename with non-numeric id."""
        result = parse_image_id_from_filename("2026-01-02-abc.png")
        assert result is None

    def test_parse_empty_filename(self):
        """Return None for empty filename."""
        result = parse_image_id_from_filename("")
        assert result is None

    def test_get_extension_png(self):
        """Get extension from png filename."""
        result = get_extension_from_filename("2026-01-02-123.png")
        assert result == "png"

    def test_get_extension_jpeg(self):
        """Get extension from jpeg filename."""
        result = get_extension_from_filename("test.jpeg")
        assert result == "jpeg"

    def test_get_extension_none(self):
        """Return empty string for filename without extension."""
        result = get_extension_from_filename("noextension")
        assert result == ""
