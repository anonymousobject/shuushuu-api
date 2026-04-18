"""Test r2_location field on Images model."""

import pytest

from app.core.r2_constants import R2Location
from app.models.image import Images


@pytest.mark.unit
class TestImageR2Location:
    def test_default_is_none(self):
        """New image instances default r2_location to NONE=0."""
        img = Images(ext="jpg", user_id=1)
        assert img.r2_location == R2Location.NONE == 0

    def test_can_set_public(self):
        img = Images(ext="jpg", user_id=1, r2_location=R2Location.PUBLIC)
        assert img.r2_location == 1

    def test_can_set_private(self):
        img = Images(ext="jpg", user_id=1, r2_location=R2Location.PRIVATE)
        assert img.r2_location == 2
