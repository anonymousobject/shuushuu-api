"""Unit tests for image status / deactivation-reason constants and model plumbing."""

import pytest
from pydantic import ValidationError

from app.config import DeactivationReason, ImageStatus
from app.models.image import Images
from app.models.image_status_history import ImageStatusHistory


def test_deactivated_status_exists_and_labels():
    assert ImageStatus.DEACTIVATED == 0  # reuses the historical "disable" bucket
    assert ImageStatus.OTHER == 0  # deprecated alias kept for backward-compat
    assert ImageStatus.get_label(ImageStatus.DEACTIVATED) == "deactivated"
    assert ImageStatus.get_label(0) == "deactivated"  # 0 now renders "deactivated"
    # Legacy values must still resolve for historical rows
    assert ImageStatus.get_label(ImageStatus.INAPPROPRIATE) == "inappropriate"
    assert ImageStatus.get_label(ImageStatus.LOW_QUALITY) == "low_quality"


def test_deactivation_reason_labels():
    assert DeactivationReason.INAPPROPRIATE == 1
    assert DeactivationReason.LOW_QUALITY == 2
    assert DeactivationReason.SPAM == 3
    assert DeactivationReason.OTHER == 4
    assert DeactivationReason.get_label(DeactivationReason.SPAM) == "Spam"
    assert DeactivationReason.get_label(999) == "unknown"


def test_images_model_accepts_deactivated():
    img = Images(user_id=1, filename="x", ext="jpg", md5_hash="a" * 32, status=ImageStatus.DEACTIVATED)
    assert img.status == ImageStatus.DEACTIVATED
    assert img.reason_category is None
    assert img.status_reason is None


def test_images_model_still_loads_legacy_statuses():
    # Old rows may still construct with legacy values during/after migration.
    # (0/DEACTIVATED is current, not legacy — covered by test_images_model_accepts_deactivated.)
    for legacy in (ImageStatus.INAPPROPRIATE, ImageStatus.LOW_QUALITY):
        assert Images(user_id=1, filename="x", ext="jpg", md5_hash="a" * 32, status=legacy).status == legacy


def test_images_model_rejects_unknown_status():
    with pytest.raises(ValidationError):
        Images(user_id=1, filename="x", ext="jpg", md5_hash="a" * 32, status=99)


def test_status_history_has_reason_fields():
    h = ImageStatusHistory(
        image_id=1, old_status=ImageStatus.ACTIVE, new_status=ImageStatus.DEACTIVATED,
        reason_category=DeactivationReason.SPAM, reason="ad spam",
    )
    assert h.reason_category == DeactivationReason.SPAM
    assert h.reason == "ad spam"
    h2 = ImageStatusHistory(image_id=1, old_status=1, new_status=2)
    assert h2.reason_category is None
    assert h2.reason is None
