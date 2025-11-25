"""
Authentication API endpoints.

This module provides endpoints for:
- User login (with JWT + refresh token)
- Token refresh (with rotation)
- Logout (revoke refresh token)
- Token validation
"""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import (
    CurrentUser,
    get_client_ip,
    get_refresh_token_from_cookie,
    get_user_agent,
)
from app.core.database import get_db
from app.core.security import (
    create_access_token,
    create_refresh_token,
    get_password_hash,
    verify_password,
)
from app.models.refresh_token import RefreshTokens
from app.models.user import Users
from app.schemas.auth import (
    LoginRequest,
    MessageResponse,
    PasswordChangeRequest,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    """
    Set authentication cookies in response.

    Sets refresh token cookie (always) and access token cookie (development only).

    Args:
        response: FastAPI response object
        access_token: JWT access token
        refresh_token: Refresh token
    """
    # Set refresh token as HTTPOnly cookie
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,  # Prevent JavaScript access (XSS protection)
        secure=settings.ENVIRONMENT == "production",  # HTTPS only in production
        samesite="strict",  # CSRF protection
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,  # seconds
    )

    # Set access token as cookie for browser/Swagger UI convenience (development only)
    if settings.ENVIRONMENT != "production":
        response.set_cookie(
            key="access_token",
            value=access_token,
            httponly=True,  # Prevent JavaScript access (XSS protection)
            secure=False,  # Allow HTTP in development
            samesite="strict",  # CSRF protection
            max_age=30 * 24 * 60 * 60,  # 30 days in seconds
        )


def _clear_auth_cookies(response: Response) -> None:
    """
    Clear authentication cookies from response.

    Args:
        response: FastAPI response object
    """
    response.delete_cookie(key="refresh_token")
    response.delete_cookie(key="access_token")


def _verify_legacy_password(plain_password: str, hashed_password: str, salt: str) -> bool:
    """
    Verify password using legacy SHA1+salt method from PHP codebase.
    """
    hashed = hashlib.sha1((salt + plain_password).encode()).hexdigest()
    return hashed == hashed_password


async def _create_tokens_for_user(
    user: Users,
    db: AsyncSession,
    request: Request,
) -> tuple[str, str]:
    """
    Create access and refresh tokens for a user.

    Args:
        user: User object
        db: Database session
        request: FastAPI request for IP/user agent tracking

    Returns:
        Tuple of (access_token, refresh_token)
    """
    # Create access token (short-lived JWT)
    if user.user_id is None:
        raise ValueError("User ID cannot be None")
    access_token = create_access_token(user.user_id)

    # Create refresh token (long-lived, stored in DB)
    refresh_token = create_refresh_token()
    token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
    family_id = create_refresh_token()  # New family for this login

    # Store refresh token in database
    db_token = RefreshTokens(
        user_id=user.user_id,
        token_hash=token_hash,
        family_id=family_id,
        expires_at=datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    db.add(db_token)
    await db.commit()

    return access_token, refresh_token


@router.post("/login", response_model=TokenResponse)
async def login(
    credentials: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    """
    Authenticate user and return JWT access token + refresh token.

    The refresh token is set as an HTTPOnly cookie for security.
    The access token is returned in the response body.

    Flow:
    1. Check if account is locked
    2. Verify username/password (supports both bcrypt and legacy SHA1)
    3. Migrate SHA1 passwords to bcrypt on successful login
    4. Generate access token (JWT, 15 min)
    5. Generate refresh token (random, 30 days)
    6. Store refresh token in database (hashed)
    7. Set refresh token as HTTPOnly cookie
    8. Return access token in response

    Security:
    - Locks account after 5 failed attempts for 15 minutes
    - Resets lockout after successful login
    """
    # Find user by username
    result = await db.execute(select(Users).where(Users.username == credentials.username))  # type: ignore[arg-type]
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    # Check if account is locked
    if user.lockout_until and user.lockout_until > datetime.now(UTC):
        remaining_minutes = int((user.lockout_until - datetime.now(UTC)).total_seconds() / 60)
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"Account locked due to too many failed login attempts. Try again in {remaining_minutes} minutes.",
        )

    # Verify password (supports both bcrypt and legacy SHA1)
    password_valid = False
    migrate_to_bcrypt = False

    if user.password_type == "bcrypt":
        # Modern bcrypt verification
        password_valid = verify_password(credentials.password, user.password)
    else:
        # Legacy SHA1+salt verification
        password_valid = _verify_legacy_password(credentials.password, user.password, user.salt)
        if password_valid:
            # Password is correct, migrate to bcrypt
            migrate_to_bcrypt = True

    if not password_valid:
        # Increment failed login attempts
        user.failed_login_attempts += 1

        # Lock account after 5 failed attempts for 15 minutes
        if user.failed_login_attempts >= 5:
            user.lockout_until = datetime.now(UTC) + timedelta(minutes=15)
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail="Account locked due to too many failed login attempts. Try again in 15 minutes.",
            )

        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    # Check if user is active
    if not user.active:
        # Query user_suspensions to check if this is a suspension
        from app.models.user_suspension import UserSuspensions

        suspension_result = await db.execute(
            select(UserSuspensions)
            .where(UserSuspensions.user_id == user.user_id)  # type: ignore[arg-type]
            .where(UserSuspensions.action == "suspended")  # type: ignore[arg-type]
            .order_by(desc(UserSuspensions.actioned_at))  # type: ignore[arg-type]
            .limit(1)
        )
        suspension = suspension_result.scalar_one_or_none()

        if suspension:
            # Check if suspension has expired
            if suspension.suspended_until and suspension.suspended_until < datetime.now(
                UTC
            ).replace(tzinfo=None):
                # Auto-reactivate expired suspension
                user.active = 1

                # Log reactivation
                from app.models.user_suspension import UserSuspensions

                reactivation = UserSuspensions(
                    user_id=user.user_id,
                    action="reactivated",
                    actioned_by=None,  # Auto-reactivated
                )
                db.add(reactivation)
                # Note: Will be committed with last_login update below
            else:
                # Still suspended - show reason
                reason = suspension.reason or "Your account has been suspended."
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=reason,
                )
        else:
            # Inactive but not suspended
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User account is inactive",
            )

    # Reset failed login attempts and lockout on successful login
    user.failed_login_attempts = 0
    user.lockout_until = None

    # Migrate password to bcrypt if needed
    if migrate_to_bcrypt:
        user.password = get_password_hash(credentials.password)
        user.password_type = "bcrypt"
        # Note: We'll commit this along with last_login update below

    # Create tokens
    access_token, refresh_token = await _create_tokens_for_user(user, db, request)

    # Set authentication cookies
    _set_auth_cookies(response, access_token, refresh_token)

    # Update last login
    user.last_login = datetime.now(UTC)
    await db.commit()

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: Request,
    response: Response,
    refresh_token: Annotated[str, Depends(get_refresh_token_from_cookie)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    """
    Refresh access token using refresh token.

    This endpoint implements refresh token rotation for security:
    1. Verify refresh token exists and is valid
    2. Generate new access token
    3. Generate new refresh token (rotation)
    4. Revoke old refresh token
    5. Store new refresh token
    6. Return new access token

    If an already-used (revoked) refresh token is presented, this indicates
    potential token theft, and all tokens in the family are revoked.
    """
    # Hash the provided token to look up in database
    token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()

    # Look up token in database
    result = await db.execute(select(RefreshTokens).where(RefreshTokens.token_hash == token_hash))  # type: ignore[arg-type]
    db_token = result.scalar_one_or_none()

    if not db_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    # Check if token is expired
    # Note: MySQL DATETIME is timezone-naive, so we compare with naive datetime
    if db_token.expires_at < datetime.now(UTC).replace(tzinfo=None):
        # Clean up expired token
        await db.delete(db_token)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token expired",
        )

    # SECURITY: Check if token was already used (potential theft!)
    if db_token.revoked:
        # Token reuse detected! Revoke all tokens in this family
        await db.execute(delete(RefreshTokens).where(RefreshTokens.family_id == db_token.family_id))  # type: ignore[arg-type]
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token reuse detected. All sessions revoked for security.",
        )

    # Load user
    result_user = await db.execute(select(Users).where(Users.user_id == db_token.user_id))  # type: ignore[arg-type]
    user = result_user.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    # Check if user is active (including suspension check)
    if not user.active:
        # Query user_suspensions to check if this is a suspension
        from app.models.user_suspension import UserSuspensions

        suspension_result = await db.execute(
            select(UserSuspensions)
            .where(UserSuspensions.user_id == user.user_id)  # type: ignore[arg-type]
            .where(UserSuspensions.action == "suspended")  # type: ignore[arg-type]
            .order_by(desc(UserSuspensions.actioned_at))  # type: ignore[arg-type]
            .limit(1)
        )
        suspension = suspension_result.scalar_one_or_none()

        if suspension:
            # Check if suspension has expired
            if suspension.suspended_until and suspension.suspended_until < datetime.now(
                UTC
            ).replace(tzinfo=None):
                # Auto-reactivate expired suspension
                user.active = 1

                # Log reactivation
                reactivation = UserSuspensions(
                    user_id=user.user_id,
                    action="reactivated",
                    actioned_by=None,  # Auto-reactivated
                )
                db.add(reactivation)
                await db.commit()
            else:
                # Still suspended - reject refresh
                reason = suspension.reason or "Your account has been suspended."
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=reason,
                )
        else:
            # Inactive but not suspended
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User account is inactive",
            )

    # Create new access token
    if user.user_id is None:
        raise ValueError("User ID cannot be None")
    access_token = create_access_token(user.user_id)

    # Create new refresh token (rotation)
    new_refresh_token = create_refresh_token()
    new_token_hash = hashlib.sha256(new_refresh_token.encode()).hexdigest()

    # Store new refresh token (same family_id for tracking)
    new_db_token = RefreshTokens(
        user_id=user.user_id,
        token_hash=new_token_hash,
        family_id=db_token.family_id,  # Keep same family
        expires_at=datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
        parent_token_id=db_token.id,  # Track rotation chain
    )
    db.add(new_db_token)

    # Revoke old refresh token
    db_token.revoked = True
    db_token.revoked_at = datetime.now(UTC)

    await db.commit()

    # Set authentication cookies
    _set_auth_cookies(response, access_token, new_refresh_token)

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/logout", response_model=MessageResponse)
async def logout(
    response: Response,
    refresh_token: Annotated[str, Depends(get_refresh_token_from_cookie)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """
    Logout user by revoking their refresh token.

    This invalidates the current refresh token but doesn't affect the
    access token (which will expire naturally in 15 minutes).
    """
    # Hash the provided token
    token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()

    # Find and revoke token
    result = await db.execute(select(RefreshTokens).where(RefreshTokens.token_hash == token_hash))  # type: ignore[arg-type]
    db_token = result.scalar_one_or_none()

    if db_token and not db_token.revoked:
        db_token.revoked = True
        db_token.revoked_at = datetime.now(UTC)
        await db.commit()

    # Clear authentication cookies
    _clear_auth_cookies(response)

    return MessageResponse(message="Successfully logged out")


@router.post("/logout-all", response_model=MessageResponse)
async def logout_all_devices(
    current_user: CurrentUser,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """
    Logout user from all devices by revoking all their refresh tokens.

    Useful for:
    - "Logout everywhere" feature
    - Security incidents (compromised account)
    - Password change
    """
    if current_user.user_id is None:
        raise ValueError("User ID cannot be None")

    # Revoke all user's refresh tokens
    await db.execute(delete(RefreshTokens).where(RefreshTokens.user_id == current_user.user_id))  # type: ignore[arg-type]
    await db.commit()

    # Clear authentication cookies
    _clear_auth_cookies(response)

    return MessageResponse(message="Successfully logged out from all devices")


@router.get("/me")
async def get_current_user_info(current_user: CurrentUser) -> dict[str, object]:
    """
    Get current authenticated user information.

    This is a simple endpoint to test authentication and get user details.
    """
    return {
        "user_id": current_user.user_id,
        "username": current_user.username,
        "email": current_user.email,
        "active": current_user.active,
        "admin": current_user.admin,
        "date_joined": current_user.date_joined,
        "last_login": current_user.last_login,
    }


@router.post("/change-password", response_model=MessageResponse)
async def change_password(
    request_data: PasswordChangeRequest,
    current_user: CurrentUser,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """
    Change user password and revoke all sessions (force re-login).

    Security flow:
    1. Verify current password
    2. Hash and store new password
    3. Revoke all refresh tokens (logout from all devices)
    4. User must login again with new password
    """
    # Verify current password (support both bcrypt and legacy SHA1)
    password_valid = False
    if current_user.password_type == "bcrypt":
        password_valid = verify_password(request_data.current_password, current_user.password)
    else:
        password_valid = _verify_legacy_password(
            request_data.current_password, current_user.password, current_user.salt
        )

    if not password_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    # Update password (always use bcrypt for new password)
    current_user.password = get_password_hash(request_data.new_password)
    current_user.password_type = "bcrypt"

    if current_user.user_id is None:
        raise ValueError("User ID cannot be None")

    # Revoke all refresh tokens (force re-login everywhere)
    await db.execute(delete(RefreshTokens).where(RefreshTokens.user_id == current_user.user_id))  # type: ignore[arg-type]

    await db.commit()

    # Clear authentication cookies
    _clear_auth_cookies(response)

    return MessageResponse(
        message="Password changed successfully. Please login again with your new password."
    )
