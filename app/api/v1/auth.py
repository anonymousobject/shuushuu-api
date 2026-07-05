"""
Authentication API endpoints.

This module provides endpoints for:
- User login (with JWT + refresh token)
- Token refresh (with rotation)
- Logout (revoke refresh token)
- Token validation
"""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import SuspensionAction, settings
from app.core.auth import (
    CurrentUser,
    get_client_ip,
    get_refresh_token_from_cookie,
    get_user_agent,
)
from app.core.database import get_db
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.core.security import (
    RedactedStr,
    create_access_token,
    create_refresh_token,
    get_password_hash,
    verify_password,
)
from app.models.permissions import UserGroups
from app.models.refresh_token import RefreshTokens
from app.models.user import Users
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    PasswordChangeRequest,
    ResetPasswordRequest,
    TokenResponse,
)
from app.services.user import build_user_private_response
from app.tasks.queue import enqueue_job

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


async def _check_and_handle_suspension(
    user: Users,
    db: AsyncSession,
    *,
    log_event: str | None = None,
) -> None:
    """
    Check if a user is suspended and handle auto-reactivation if expired.

    Raises HTTPException if user is currently suspended.

    Args:
        user: User object to check
        db: Database session
        log_event: When set, emit a structured log under this event name (e.g.
            "login_attempt") for the blocked-access outcomes (account_suspended /
            account_inactive). Left None by callers that have their own logging.

    Raises:
        HTTPException: 403 if user is suspended, 401 if inactive for other reasons
    """
    if not user.active:
        # Query user_suspensions to check if this is a suspension
        from app.models.user_suspension import UserSuspensions

        suspension_result = await db.execute(
            select(UserSuspensions)
            .where(UserSuspensions.user_id == user.user_id)  # type: ignore[arg-type]
            .order_by(desc(UserSuspensions.actioned_at))  # type: ignore[arg-type]
            .limit(2)  # Get latest 2 to check if there's a reactivation after suspension
        )
        suspensions = suspension_result.scalars().all()

        # Check if latest action is a reactivation
        if suspensions and suspensions[0].action == SuspensionAction.REACTIVATED:
            # User was reactivated, trust the database state
            return

        # Find latest suspension
        suspension = next((s for s in suspensions if s.action == SuspensionAction.SUSPENDED), None)
        if suspension:
            # Check if suspension has expired
            if suspension.suspended_until and suspension.suspended_until < datetime.now(UTC):
                # Auto-reactivate expired suspension
                user.active = 1

                # Log reactivation
                reactivation = UserSuspensions(
                    user_id=user.user_id,
                    action=SuspensionAction.REACTIVATED,
                    actioned_by=None,  # Auto-reactivated
                )
                db.add(reactivation)
                # Note: Caller must commit
            else:
                # Still suspended - build detailed message
                if suspension.suspended_until:
                    # Temporary suspension - show duration
                    remaining = suspension.suspended_until - datetime.now(UTC)
                    days = remaining.days
                    hours = remaining.seconds // 3600
                    minutes = (remaining.seconds % 3600) // 60

                    if days > 0:
                        duration = f"{days} day{'s' if days != 1 else ''}"
                    elif hours > 0:
                        duration = f"{hours} hour{'s' if hours != 1 else ''}"
                    else:
                        duration = f"{minutes} minute{'s' if minutes != 1 else ''}"

                    message = f"Suspended for {duration}."
                else:
                    # Permanent suspension
                    message = "Permanently suspended."

                if suspension.reason:
                    message += f" Reason: {suspension.reason}"

                if log_event:
                    logger.info(
                        log_event,
                        outcome="account_suspended",
                        username=user.username,
                        user_id=user.user_id,
                        suspended_until=(
                            suspension.suspended_until.isoformat()
                            if suspension.suspended_until
                            else None
                        ),
                    )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=message,
                )
        else:
            # Inactive but not suspended
            if log_event:
                logger.info(
                    log_event,
                    outcome="account_inactive",
                    username=user.username,
                    user_id=user.user_id,
                )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User account is inactive",
            )


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    """
    Set authentication cookies in response.

    Sets both access token and refresh token as HTTPOnly cookies for SSR compatibility.
    This supports SvelteKit's server-side rendering which needs access to tokens on the server.

    Args:
        response: FastAPI response object
        access_token: JWT access token
        refresh_token: Refresh token
    """
    # SameSite=Lax (NOT Strict). This is a server-rendered app, so the SSR needs
    # the auth cookie on the *top-level document request*. SameSite=Strict
    # withholds cookies from top-level navigations the browser doesn't treat as
    # same-site-initiated — reloads, links from other sites, or a tab first opened
    # cross-site — so the server renders logged-out even though the session is
    # valid (this caused the recurring "logged out after refresh" reports). Lax
    # sends cookies on top-level GET navigations while still withholding them from
    # cross-site POST/PUT/DELETE, so CSRF protection for state-changing requests is
    # preserved (and the API has no state-changing GET endpoints). Do NOT change
    # this back to Strict without first fixing SSR auth.

    # Set refresh token as HTTPOnly cookie
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,  # Prevent JavaScript access (XSS protection)
        secure=settings.ENVIRONMENT != "development",  # HTTPS everywhere except development
        samesite="lax",
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,  # seconds
    )

    # Set access token as HTTPOnly cookie (for SSR and Swagger UI).
    # Cookie Max-Age matches the refresh token, not the JWT expiry: the JWT's own
    # `exp` claim is what's enforced server-side, and pinning the cookie to a
    # 30-minute lifetime just creates a "cookie disappeared" branch in SSR auth
    # that costs an extra round trip every page render after expiry.
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,  # Prevent JavaScript access (XSS protection)
        secure=settings.ENVIRONMENT != "development",  # HTTPS everywhere except development
        samesite="lax",
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
    )


def _clear_auth_cookies(response: Response) -> None:
    """
    Clear authentication cookies from response.

    Args:
        response: FastAPI response object
    """
    # Clear refresh token (match set_cookie params, incl. SameSite=Lax)
    response.delete_cookie(
        key="refresh_token",
        path="/",
        httponly=True,
        secure=settings.ENVIRONMENT != "development",
        samesite="lax",
    )

    # Clear access token (match set_cookie params, incl. SameSite=Lax)
    response.delete_cookie(
        key="access_token",
        path="/",
        httponly=True,
        secure=settings.ENVIRONMENT != "development",
        samesite="lax",
    )


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
    redis_client: Annotated[redis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> TokenResponse:
    """
    Authenticate user and return JWT access token + refresh token.

    The refresh token is set as an HTTPOnly cookie for security.
    The access token is returned in the response body.

    Flow:
    1. Check if account is locked
    2. Verify username/password (supports both bcrypt and legacy SHA1)
    3. Migrate SHA1 passwords to bcrypt on successful login
    4. Generate access token (JWT, 30 min)
    5. Generate refresh token (random, 30 days)
    6. Store refresh token in database (hashed)
    7. Set refresh token as HTTPOnly cookie
    8. Return access token in response

    Security:
    - Locks account after 5 failed attempts for 15 minutes
    - Resets lockout after successful login
    """
    # Find user by username. Eager-load groups so build_user_private_response
    # below can reuse this row instead of issuing a second SELECT.
    result = await db.execute(
        select(Users)
        .options(
            selectinload(Users.user_groups).selectinload(UserGroups.group)  # type: ignore[arg-type]
        )
        .where(Users.username == credentials.username)  # type: ignore[arg-type]
    )
    user = result.scalar_one_or_none()

    if not user:
        logger.info(
            "login_attempt",
            outcome="user_not_found",
            username=credentials.username,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    # Check if account is locked
    if user.lockout_until:
        now = datetime.now(UTC)
        if user.lockout_until > now:
            remaining_minutes = int((user.lockout_until - now).total_seconds() / 60)
            logger.info(
                "login_attempt",
                outcome="account_locked",
                username=user.username,
                user_id=user.user_id,
                lockout_until=user.lockout_until.isoformat(),
            )
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail=f"Account locked due to too many failed login attempts. Try again in {remaining_minutes} minutes.",
            )
        else:
            # Lockout expired — reset counter so user gets fresh attempts
            user.failed_login_attempts = 0
            user.lockout_until = None

    # Verify password
    password_valid = False
    migrate_to_bcrypt = False
    # Capture pre-migration type so the success log records what the user
    # actually authenticated with (user.password_type gets overwritten below).
    original_password_type = user.password_type

    if user.password_type == "bcrypt":
        password_valid = verify_password(credentials.password, user.password)
    elif user.password_type == "sha1":
        # Legacy SHA1+salt verification — migrate to bcrypt on success
        password_valid = _verify_legacy_password(credentials.password, user.password, user.salt)
        if password_valid:
            migrate_to_bcrypt = True
    # MD5: verification is technically possible but intentionally unsupported due to insecure
    # legacy format — fall through to the failure path so attempts are counted/rate-limited

    if not password_valid:
        # Increment failed login attempts
        user.failed_login_attempts += 1

        # Lock account after 5 failed attempts for 15 minutes
        if user.failed_login_attempts >= 5:
            user.lockout_until = datetime.now(UTC) + timedelta(minutes=15)
            await db.commit()
            logger.warning(
                "login_attempt",
                outcome="lockout_triggered",
                username=user.username,
                user_id=user.user_id,
                failed_attempts=user.failed_login_attempts,
                password_type=user.password_type,
            )
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail="Account locked due to too many failed login attempts. Try again in 15 minutes.",
            )

        await db.commit()
        if user.password_type == "md5":
            logger.info(
                "login_attempt",
                outcome="md5_unsupported",
                username=user.username,
                user_id=user.user_id,
                failed_attempts=user.failed_login_attempts,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Your account uses a legacy password format that is no longer supported. Please reset your password.",
            )
        logger.info(
            "login_attempt",
            outcome="wrong_password",
            username=user.username,
            user_id=user.user_id,
            failed_attempts=user.failed_login_attempts,
            password_type=user.password_type,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    # Check if user is active and handle suspension
    await _check_and_handle_suspension(user, db, log_event="login_attempt")

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

    # Update last login and last active
    now = datetime.now(UTC)
    user.last_login = now
    user.last_active = now
    await db.commit()

    logger.info(
        "login_attempt",
        outcome="success",
        username=user.username,
        user_id=user.user_id,
        password_type=original_password_type,
        migrated_to_bcrypt=migrate_to_bcrypt,
    )

    # The just-authenticated `user` object already has user_groups eager-loaded
    # (see the SELECT at the top of this function), so the helper skips its
    # internal DB round trip and the None branch can't fire here.
    user_response = await build_user_private_response(db, redis_client, user=user)

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=user_response,
    )


def _refresh_failure(detail: str, status_code: int = status.HTTP_401_UNAUTHORIZED) -> JSONResponse:
    """Failure response for a refresh that ALSO clears the auth cookies.

    A refresh that can't succeed for a session-terminal reason — a dead token
    (row gone / expired / theft-revoked family) or a suspended/inactive account —
    is otherwise retried by the browser on every SSR page load, since the cookie
    is left in place. Clearing the cookies resets the session to a clean
    logged-out state so that retry loop stops.
    """
    failure = JSONResponse(
        status_code=status_code,
        content={"detail": detail},
    )
    _clear_auth_cookies(failure)
    return failure


async def _issue_rotated_session(
    user: Users,
    db: AsyncSession,
    request: Request,
    response: Response,
    redis_client: redis.Redis,  # type: ignore[type-arg]
    *,
    family_id: str,
    parent_token_id: int | None,
) -> TokenResponse:
    """Mint a new access token + refresh token in `family_id`, set cookies, and
    return the token response.

    Shared by the normal rotation path and the benign-race recovery path. The
    caller is responsible for revoking the presented token (normal rotation) or
    leaving it alone (race recovery, where it is already revoked).
    """
    if user.user_id is None:
        raise ValueError("User ID cannot be None")

    new_access_token = create_access_token(user.user_id)
    new_refresh_token = create_refresh_token()
    new_token_hash = hashlib.sha256(new_refresh_token.encode()).hexdigest()

    db.add(
        RefreshTokens(
            user_id=user.user_id,
            token_hash=new_token_hash,
            family_id=family_id,
            expires_at=datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
            ip_address=get_client_ip(request),
            user_agent=get_user_agent(request),
            parent_token_id=parent_token_id,
        )
    )
    user.last_active = datetime.now(UTC)
    await db.commit()

    _set_auth_cookies(response, new_access_token, new_refresh_token)

    # `user` already has user_groups eager-loaded by the caller, so the helper
    # skips its internal DB round trip and the None branch can't fire here.
    user_response = await build_user_private_response(db, redis_client, user=user)
    return TokenResponse(
        access_token=new_access_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=user_response,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: Request,
    response: Response,
    refresh_token: Annotated[str, Depends(get_refresh_token_from_cookie)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> TokenResponse | JSONResponse:
    """
    Refresh access token using refresh token (with rotation).

    1. Look up the presented refresh token; mint a new access token + rotated
       refresh token, revoking the old one.
    2. A *dead* token (unknown / expired / theft-revoked family) returns 401 and
       clears the auth cookies, so the browser stops re-presenting it on every
       page load (the SSR would otherwise retry it indefinitely).
    3. A *benign race* — the presented token was rotated <10s ago and a child
       already exists (concurrent refreshes during a page load) — recovers with a
       fresh session instead of 401, so the loser of the race stays logged in.
    4. Reuse of a token revoked outside the grace window (or with no child) is
       treated as theft: the whole token family is revoked.
    """
    # Hash the provided token to look up in database
    token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()

    result = await db.execute(select(RefreshTokens).where(RefreshTokens.token_hash == token_hash))  # type: ignore[arg-type]
    db_token = result.scalar_one_or_none()

    # Dead token — its row is gone (prior family-nuke, logout, cleanup). Clear
    # cookies so the browser stops re-presenting it on every SSR page load.
    if not db_token:
        return _refresh_failure("Invalid refresh token")

    # Expired — clean up the row and clear cookies.
    if db_token.expires_at < datetime.now(UTC):
        await db.delete(db_token)
        await db.commit()
        return _refresh_failure("Refresh token expired")

    # Whether to revoke the presented token below. A benign-race token is already
    # revoked, so we recover without touching it.
    revoke_presented_token = True

    # SECURITY: token already revoked — distinguish a benign concurrent-refresh
    # race from genuine reuse/theft.
    if db_token.revoked:
        is_race_condition = False
        if db_token.revoked_at:
            time_since_revoked = datetime.now(UTC) - db_token.revoked_at
            if time_since_revoked.total_seconds() < 10:
                # A child token means a legitimate refresh already happened.
                # limit(1) avoids MultipleResultsFound under concurrent refreshes.
                child_result = await db.execute(
                    select(RefreshTokens)
                    .where(RefreshTokens.parent_token_id == db_token.id)  # type: ignore[arg-type]
                    .limit(1)
                )
                if child_result.scalars().first() is not None:
                    is_race_condition = True
                    logger.info(
                        "refresh_race_condition_detected",
                        token_id=db_token.id,
                        user_id=db_token.user_id,
                        seconds_since_revoked=time_since_revoked.total_seconds(),
                    )

        if not is_race_condition:
            # Suspicious — likely theft. Nuke the entire family and clear cookies.
            logger.warning(
                "refresh_token_reuse_detected",
                token_id=db_token.id,
                user_id=db_token.user_id,
                family_id=db_token.family_id,
            )
            await db.execute(
                delete(RefreshTokens).where(RefreshTokens.family_id == db_token.family_id)  # type: ignore[arg-type]
            )
            await db.commit()
            return _refresh_failure(
                "Refresh token reuse detected. All sessions revoked for security."
            )

        # Benign race: the presented token is already revoked with a live child.
        # Recover with a fresh session (don't re-revoke, don't nuke).
        revoke_presented_token = False

    # Load user. Eager-load groups so build_user_private_response can reuse this
    # row instead of issuing a second SELECT.
    result_user = await db.execute(
        select(Users)
        .options(
            selectinload(Users.user_groups).selectinload(UserGroups.group)  # type: ignore[arg-type]
        )
        .where(Users.user_id == db_token.user_id)  # type: ignore[arg-type]
    )
    user = result_user.scalar_one_or_none()

    if not user:
        return _refresh_failure("User not found")

    # Check if user is active and handle suspension. An active suspension /
    # inactive account raises; convert it to a cookie-clearing response so a
    # suspended user doesn't re-present the cookie and hammer /auth/refresh (403)
    # on every SSR page load.
    try:
        await _check_and_handle_suspension(user, db)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else "Account is not active"
        return _refresh_failure(detail, status_code=exc.status_code)
    # If auto-reactivated, commit immediately (refresh needs this)
    if user.active:
        await db.commit()

    # Revoke the presented token (normal rotation); skip for benign-race recovery
    # since that token is already revoked.
    if revoke_presented_token:
        db_token.revoked = True
        db_token.revoked_at = datetime.now(UTC)

    return await _issue_rotated_session(
        user,
        db,
        request,
        response,
        redis_client,
        family_id=db_token.family_id,
        parent_token_id=db_token.id,
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
    access token (which will expire naturally in 30 minutes).
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


@router.post("/verify-email", response_model=MessageResponse)
async def verify_email(
    token: Annotated[str, Query()],
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Verify user email with token from verification link."""
    # Hash token to compare with DB
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    # Find user by token
    result = await db.execute(
        select(Users).where(Users.email_verification_token == token_hash)  # type: ignore[arg-type]
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid verification token"
        )

    # Check expiration (IMPORTANT: null check first!)
    if not user.email_verification_expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid verification token"
        )

    if user.email_verification_expires_at < datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification token expired. Please request a new one.",
        )

    # Verify email
    user.email_verified = True
    user.email_verification_token = None  # Clear token
    user.email_verification_expires_at = None  # Clear expiration
    await db.commit()

    logger.info("email_verified", user_id=user.user_id, username=user.username)

    return MessageResponse(message="Email verified successfully!")


@router.post("/resend-verification", response_model=MessageResponse)
async def resend_verification(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Resend verification email to current user."""
    if current_user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already verified"
        )

    # Rate limit: only 1 resend per 5 minutes
    if current_user.email_verification_sent_at:
        time_since_last = datetime.now(UTC) - current_user.email_verification_sent_at
        if time_since_last < timedelta(minutes=5):
            remaining_seconds = int((timedelta(minutes=5) - time_since_last).total_seconds())
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Please wait {remaining_seconds} seconds before requesting another verification email",
            )

    # Generate new token
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    # Update user
    current_user.email_verification_token = token_hash
    current_user.email_verification_sent_at = datetime.now(UTC)
    current_user.email_verification_expires_at = datetime.now(UTC) + timedelta(hours=24)
    await db.commit()

    # Queue verification email via ARQ (non-blocking, reliable). RedactedStr
    # keeps the token usable but hides it from arq's repr-based job-arg log.
    await enqueue_job(
        "send_verification_email_job",
        user_id=current_user.user_id,
        token=RedactedStr(raw_token),
    )

    return MessageResponse(message="Verification email sent! Check your inbox.")


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(
    request_data: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """
    Request a password reset email.

    Always returns 200 with a generic message to prevent email enumeration.
    """
    generic_message = "If an account with that email exists, a password reset link has been sent."

    # Look up user by email
    result = await db.execute(
        select(Users).where(Users.email == request_data.email)  # type: ignore[arg-type]
    )
    user = result.scalar_one_or_none()

    # Silently do nothing if user not found or inactive
    if not user:
        logger.info(
            "forgot_password_attempt",
            outcome="user_not_found",
            email=request_data.email,
        )
        return MessageResponse(message=generic_message)
    if not user.active:
        logger.info(
            "forgot_password_attempt",
            outcome="user_inactive",
            user_id=user.user_id,
        )
        return MessageResponse(message=generic_message)

    # Rate limit: 1 reset per 5 minutes
    # Return generic 200 even when rate limited to prevent email enumeration
    # (a 429 only for existing users would leak whether the email exists)
    if user.password_reset_sent_at:
        time_since_last = datetime.now(UTC) - user.password_reset_sent_at
        if time_since_last < timedelta(minutes=5):
            logger.info(
                "forgot_password_attempt",
                outcome="rate_limited",
                user_id=user.user_id,
                seconds_since_last=int(time_since_last.total_seconds()),
            )
            return MessageResponse(message=generic_message)

    # Generate token
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    # Store hashed token + timestamps
    user.password_reset_token = token_hash
    user.password_reset_sent_at = datetime.now(UTC)
    user.password_reset_expires_at = datetime.now(UTC) + timedelta(hours=1)
    await db.commit()

    # Queue email. RedactedStr keeps the token usable but hides it from arq's
    # repr-based job-arg log (which is INFO-level and ingested by Loki).
    await enqueue_job(
        "send_password_reset_email_job",
        user_id=user.user_id,
        token=RedactedStr(raw_token),
    )

    logger.info(
        "forgot_password_attempt",
        outcome="enqueued",
        user_id=user.user_id,
    )

    return MessageResponse(message=generic_message)


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(
    request_data: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """
    Reset password using email token from forgot-password flow.

    Validates token, updates password, and revokes all sessions.
    """
    # Look up user by email
    result = await db.execute(
        select(Users).where(Users.email == request_data.email)  # type: ignore[arg-type]
    )
    user = result.scalar_one_or_none()

    # The client always sees the same generic 400 to avoid leaking which step failed;
    # server-side we log the specific outcome so reset failures aren't invisible.
    if not user:
        logger.info(
            "reset_password_attempt",
            outcome="user_not_found",
            email=request_data.email,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )
    if not user.password_reset_token:
        logger.info(
            "reset_password_attempt",
            outcome="no_reset_token",
            user_id=user.user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    # Verify token matches (constant-time comparison to prevent timing side-channels)
    token_hash = hashlib.sha256(request_data.token.encode()).hexdigest()
    if not hmac.compare_digest(user.password_reset_token, token_hash):
        logger.info(
            "reset_password_attempt",
            outcome="token_mismatch",
            user_id=user.user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    # Check expiration
    if not user.password_reset_expires_at:
        logger.info(
            "reset_password_attempt",
            outcome="missing_expiry",
            user_id=user.user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    if user.password_reset_expires_at < datetime.now(UTC):
        logger.info(
            "reset_password_attempt",
            outcome="token_expired",
            user_id=user.user_id,
            expired_at=user.password_reset_expires_at.isoformat(),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset token has expired. Please request a new one.",
        )

    # Update password
    user.password = get_password_hash(request_data.new_password)
    user.password_type = "bcrypt"

    # Clear reset fields
    user.password_reset_token = None
    user.password_reset_sent_at = None
    user.password_reset_expires_at = None

    # Revoke all refresh tokens
    await db.execute(
        delete(RefreshTokens).where(RefreshTokens.user_id == user.user_id)  # type: ignore[arg-type]
    )

    await db.commit()

    logger.info(
        "reset_password_attempt",
        outcome="success",
        user_id=user.user_id,
        username=user.username,
    )

    return MessageResponse(
        message="Password reset successfully. Please login with your new password."
    )
