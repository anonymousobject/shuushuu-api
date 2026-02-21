"""Tests for password reset fields on the Users model."""

from datetime import UTC, datetime

from app.models.user import Users


class TestUserPasswordResetFields:
    """Tests for password reset fields on Users model."""

    def test_password_reset_token_defaults_to_none(self) -> None:
        user = Users(
            username="testuser",
            password="hashed",
            salt="salt",
            email="test@example.com",
        )
        assert user.password_reset_token is None

    def test_password_reset_sent_at_defaults_to_none(self) -> None:
        user = Users(
            username="testuser",
            password="hashed",
            salt="salt",
            email="test@example.com",
        )
        assert user.password_reset_sent_at is None

    def test_password_reset_expires_at_defaults_to_none(self) -> None:
        user = Users(
            username="testuser",
            password="hashed",
            salt="salt",
            email="test@example.com",
        )
        assert user.password_reset_expires_at is None

    def test_password_reset_fields_can_be_set(self) -> None:
        now = datetime.now(UTC)
        user = Users(
            username="testuser",
            password="hashed",
            salt="salt",
            email="test@example.com",
            password_reset_token="abc123",
            password_reset_sent_at=now,
            password_reset_expires_at=now,
        )
        assert user.password_reset_token == "abc123"
        assert user.password_reset_sent_at == now
        assert user.password_reset_expires_at == now
