"""Tests for password hashing and access-token verification in app/core/security.py."""

import base64

import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.core.security import get_password_hash, verify_access_token, verify_password


class TestBcryptRounds:
    """Tests for the configurable bcrypt cost factor."""

    def test_bcrypt_rounds_defaults_to_12(self) -> None:
        """Production default stays at 12 rounds."""
        # Check the field default (not a Settings() instance) so the test
        # still passes when the test environment overrides BCRYPT_ROUNDS.
        assert Settings.model_fields["BCRYPT_ROUNDS"].default == 12

    def test_bcrypt_rounds_out_of_range_rejected_at_startup(self) -> None:
        """bcrypt.gensalt only accepts 4-31; invalid values must fail at
        settings load, not at first hash."""
        with pytest.raises(ValidationError):
            Settings(BCRYPT_ROUNDS=3)
        with pytest.raises(ValidationError):
            Settings(BCRYPT_ROUNDS=32)

    def test_get_password_hash_uses_configured_rounds(self, monkeypatch) -> None:
        """get_password_hash honors settings.BCRYPT_ROUNDS."""
        monkeypatch.setattr(settings, "BCRYPT_ROUNDS", 4)
        hashed = get_password_hash("TestPassword123!")
        # bcrypt embeds the cost factor in the hash: $2b$<rounds>$...
        assert hashed.startswith("$2b$04$")
        assert verify_password("TestPassword123!", hashed)


class TestVerifyAccessTokenHostileInput:
    """verify_access_token must answer None, never raise, for attacker-shaped
    tokens: the bearer header is unauthenticated input on every endpoint."""

    def test_deeply_nested_header_is_rejected_not_raised(self) -> None:
        """A header nested past CPython's native stack guard (~65k levels)
        used to escape jwt.decode as RecursionError instead of DecodeError
        (CVE-2026-102265, fixed in PyJWT 2.14.0). Only the uv.lock pin stops
        that regressing, so pin the behaviour here. Depth, not size, is the
        trigger: the advisory's PoC uses 200k nested arrays."""
        header_b64 = base64.urlsafe_b64encode(b"[" * 200_000).rstrip(b"=").decode()
        token = f"{header_b64}.e30.sig"

        assert verify_access_token(token) is None
