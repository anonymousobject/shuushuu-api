"""
Authentication schemas for request/response validation.

This module defines Pydantic models for authentication-related API operations:
- Login credentials
- Token responses
- User registration
"""

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.security import validate_password_strength


class LoginRequest(BaseModel):
    """Request schema for user login."""

    username: str = Field(..., min_length=3, max_length=30)
    password: str = Field(..., min_length=1, max_length=255)  # Allow any length for existing users


class TokenResponse(BaseModel):
    """Response schema for successful authentication."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Access token expiration time in seconds from now")


class RefreshRequest(BaseModel):
    """
    Request schema for token refresh (optional body for non-cookie flow).

    When using HTTPOnly cookies, the refresh token is sent automatically.
    This schema allows for alternative flows where refresh token is in body.
    """

    refresh_token: str | None = Field(
        default=None, description="Refresh token (optional if using cookies)"
    )


class UserRegisterRequest(BaseModel):
    """Request schema for user registration."""

    username: str = Field(..., min_length=3, max_length=30)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=255)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """Validate password strength."""
        is_valid, error_message = validate_password_strength(v)
        if not is_valid:
            raise ValueError(error_message)
        return v


class PasswordChangeRequest(BaseModel):
    """Request schema for password change."""

    current_password: str = Field(..., min_length=1, max_length=255)  # Allow any length for current
    new_password: str = Field(..., min_length=8, max_length=255)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        """Validate new password strength."""
        is_valid, error_message = validate_password_strength(v)
        if not is_valid:
            raise ValueError(error_message)
        return v


class ForgotPasswordRequest(BaseModel):
    """Request schema for forgot password."""

    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Request schema for password reset with token."""

    email: EmailStr
    token: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=255)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        """Validate new password strength."""
        is_valid, error_message = validate_password_strength(v)
        if not is_valid:
            raise ValueError(error_message)
        return v


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str
