"""
SQLModel-based RefreshToken model for JWT authentication.

This module defines the RefreshTokens database table for managing refresh tokens
used in JWT-based authentication with token rotation.

Security features:
- Stores hashed tokens (not plaintext)
- Tracks token family for reuse detection
- Supports token rotation on refresh
- Auto-expiration with created_at + expiry_days
"""

from datetime import datetime

from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel


class RefreshTokens(SQLModel, table=True):
    """
    Database table for refresh tokens with security features.

    Refresh tokens are long-lived credentials used to obtain new access tokens.
    This implementation includes:
    - Token hashing for security
    - Token family tracking for reuse detection
    - Revocation support (revoked flag)
    - User agent and IP tracking for security auditing
    """

    __tablename__ = "refresh_tokens"

    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_refresh_tokens_user_id",
        ),
        Index("idx_refresh_tokens_user_id", "user_id"),
        Index("idx_refresh_tokens_token_hash", "token_hash", unique=True),
        Index("idx_refresh_tokens_family_id", "family_id"),
    )

    # Primary key
    id: int | None = Field(default=None, primary_key=True)

    # User reference
    user_id: int = Field(foreign_key="users.user_id")

    # Token (hashed for security - never store plaintext!)
    token_hash: str = Field(max_length=255, unique=True, index=True)

    # Token family for rotation tracking
    # All tokens generated from the same initial login share a family_id
    # Used for detecting token theft via reuse detection
    family_id: str = Field(max_length=255, index=True)

    # Expiration
    created_at: datetime = Field(sa_column_kwargs={"server_default": text("current_timestamp()")})
    expires_at: datetime

    # Revocation
    revoked: bool = Field(default=False)
    revoked_at: datetime | None = Field(default=None)

    # Security tracking
    ip_address: str | None = Field(default=None, max_length=45)  # Supports IPv6
    user_agent: str | None = Field(default=None, max_length=255)

    # Parent token tracking (for rotation chains)
    parent_token_id: int | None = Field(default=None)
