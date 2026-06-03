"""Unit tests for image status / deactivation-reason constants and model plumbing."""

from app.config import DeactivationReason, ImageStatus


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
