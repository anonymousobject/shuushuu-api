"""
Security utilities for authentication and authorization.

This module provides:
- Password hashing and verification using bcrypt
- JWT token generation and verification
- Refresh token management with rotation
"""

import base64
import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.config import settings


def validate_password_strength(password: str) -> tuple[bool, str | None]:
    """
    Validate password meets security requirements.

    Requirements:
    - At least 8 characters
    - Contains at least one uppercase letter
    - Contains at least one lowercase letter
    - Contains at least one digit
    - Contains at least one special character

    Args:
        password: The password to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    if len(password) < 8:
        return False, "Password must be at least 8 characters long"

    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter"

    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter"

    if not re.search(r"\d", password):
        return False, "Password must contain at least one digit"

    if not re.search(r'[!@#$%^&*(),.?":{}|<>_\-+=\[\]\\\/~`]', password):
        return False, "Password must contain at least one special character"

    return True, None


def _prepare_password_for_bcrypt(password: str) -> str:
    """
    Prepare password for bcrypt by handling long passwords.

    Bcrypt has a 72 byte limit. For passwords longer than 72 bytes,
    we SHA256 hash them first and encode as base64.

    Args:
        password: The plain text password

    Returns:
        Password ready for bcrypt (guaranteed <= 72 bytes)
    """
    password_bytes = password.encode("utf-8")

    # If password is <= 72 bytes, use as-is
    if len(password_bytes) <= 72:
        return password

    # For long passwords, SHA256 hash first then base64 encode
    # SHA256 produces 32 bytes, base64 encoding produces 44 chars (well under 72)
    hashed = hashlib.sha256(password_bytes).digest()
    return base64.b64encode(hashed).decode("ascii")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain password against a hashed password.

    Args:
        plain_password: The plain text password to verify
        hashed_password: The bcrypt hashed password

    Returns:
        True if password matches, False otherwise
    """
    prepared_password = _prepare_password_for_bcrypt(plain_password)
    return bcrypt.checkpw(prepared_password.encode("utf-8"), hashed_password.encode("utf-8"))


def get_password_hash(password: str) -> str:
    """
    Hash a password using bcrypt.

    For passwords longer than 72 bytes (bcrypt's limit), we SHA256 hash them first.

    Args:
        password: The plain text password to hash

    Returns:
        The bcrypt hashed password
    """
    prepared_password = _prepare_password_for_bcrypt(password)
    # Generate salt and hash password
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(prepared_password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def create_access_token(user_id: int, expires_delta: timedelta | None = None) -> str:
    """
    Create a JWT access token.

    Args:
        user_id: The user ID to encode in the token
        expires_delta: Optional custom expiration time (defaults to settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    Returns:
        Encoded JWT token string
    """
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    expire = datetime.now(UTC) + expires_delta

    payload = {
        "sub": str(user_id),  # "sub" (subject) is standard JWT claim
        "exp": expire,  # "exp" (expiration) is standard JWT claim
        "type": "access",  # Custom claim to distinguish token types
    }

    encoded_jwt = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def create_refresh_token() -> str:
    """
    Create a cryptographically secure refresh token.

    Returns:
        URL-safe random token string (43 characters)
    """
    # Generate 32 random bytes, encode as URL-safe base64 (43 chars)
    return secrets.token_urlsafe(32)


def verify_access_token(token: str) -> int | None:
    """
    Verify and decode a JWT access token.

    Args:
        token: The JWT token to verify

    Returns:
        User ID if token is valid, None otherwise
    """
    try:
        # Explicitly verify expiration and signature
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            options={"verify_exp": True, "verify_signature": True},
        )

        # Verify token type
        if payload.get("type") != "access":
            return None

        user_id: str | None = payload.get("sub")
        if user_id is None:
            return None

        return int(user_id)

    except jwt.ExpiredSignatureError:
        # Token has expired
        return None
    except jwt.DecodeError:
        # Invalid token format or signature
        return None
    except (ValueError, TypeError):
        # Invalid user_id or other type errors
        return None


def get_token_expiration(token: str) -> datetime | None:
    """
    Get the expiration time of a JWT token without full verification.

    Args:
        token: The JWT token

    Returns:
        Expiration datetime if present, None otherwise
    """
    try:
        # Decode without verification to check expiration
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            options={"verify_exp": False},
        )
        exp = payload.get("exp")
        if exp:
            return datetime.fromtimestamp(exp, tz=UTC)
        return None
    except jwt.DecodeError:
        return None
