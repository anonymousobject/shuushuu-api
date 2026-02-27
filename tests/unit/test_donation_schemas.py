"""Tests for donation schemas."""

import pytest
from pydantic import ValidationError

from app.schemas.donations import DonationCreate


class TestDonationCreate:
    """Validation tests for DonationCreate schema."""

    def test_valid_minimal(self):
        """Amount-only donation is valid."""
        d = DonationCreate(amount=10)
        assert d.amount == 10
        assert d.nick is None
        assert d.user_id is None
        assert d.date is None

    def test_valid_full(self):
        """All fields populated is valid."""
        d = DonationCreate(amount=50, nick="Donor", user_id=123)
        assert d.amount == 50
        assert d.nick == "Donor"
        assert d.user_id == 123

    def test_amount_required(self):
        """Missing amount raises validation error."""
        with pytest.raises(ValidationError):
            DonationCreate()

    def test_nick_max_length(self):
        """Nick over 30 chars raises validation error."""
        with pytest.raises(ValidationError):
            DonationCreate(amount=10, nick="a" * 31)

    def test_nick_strips_whitespace(self):
        """Nick is stripped of leading/trailing whitespace."""
        d = DonationCreate(amount=10, nick="  Donor  ")
        assert d.nick == "Donor"

    def test_amount_must_be_positive(self):
        """Amount must be greater than 0."""
        with pytest.raises(ValidationError):
            DonationCreate(amount=0)

        with pytest.raises(ValidationError):
            DonationCreate(amount=-5)
