"""Tests for password hashing in app/core/security.py."""

from app.config import Settings, settings
from app.core.security import get_password_hash, verify_password


class TestBcryptRounds:
    """Tests for the configurable bcrypt cost factor."""

    def test_bcrypt_rounds_defaults_to_12(self) -> None:
        """Production default stays at 12 rounds."""
        # Check the field default (not a Settings() instance) so the test
        # still passes when the test environment overrides BCRYPT_ROUNDS.
        assert Settings.model_fields["BCRYPT_ROUNDS"].default == 12

    def test_get_password_hash_uses_configured_rounds(self, monkeypatch) -> None:
        """get_password_hash honors settings.BCRYPT_ROUNDS."""
        monkeypatch.setattr(settings, "BCRYPT_ROUNDS", 4)
        hashed = get_password_hash("TestPassword123!")
        # bcrypt embeds the cost factor in the hash: $2b$<rounds>$...
        assert hashed.startswith("$2b$04$")
        assert verify_password("TestPassword123!", hashed)
