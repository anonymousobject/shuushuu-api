"""Tests for R2 shared constants."""

import pytest

from app.config import ImageStatus
from app.core.r2_constants import (
    PUBLIC_IMAGE_STATUSES_FOR_R2,
    R2_VARIANTS,
    R2Location,
)


@pytest.mark.unit
class TestR2Location:
    """R2Location enum values."""

    def test_values(self):
        assert R2Location.NONE == 0
        assert R2Location.PUBLIC == 1
        assert R2Location.PRIVATE == 2

    def test_int_comparison(self):
        """Enum is IntEnum so it compares to ints directly."""
        assert R2Location.NONE == 0
        assert int(R2Location.PUBLIC) == 1


@pytest.mark.unit
class TestPublicStatuses:
    """The set of statuses that map to the public bucket."""

    def test_members(self):
        assert PUBLIC_IMAGE_STATUSES_FOR_R2 == frozenset(
            {ImageStatus.ACTIVE, ImageStatus.SPOILER, ImageStatus.REPOST}
        )

    def test_is_frozen(self):
        with pytest.raises(AttributeError):
            PUBLIC_IMAGE_STATUSES_FOR_R2.add(999)  # type: ignore[attr-defined]


@pytest.mark.unit
class TestR2Variants:
    """The canonical list of variant prefixes used as R2 key prefixes."""

    def test_list(self):
        assert R2_VARIANTS == ("fullsize", "thumbs", "medium", "large")
