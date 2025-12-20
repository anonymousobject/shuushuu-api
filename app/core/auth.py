"""
Authentication dependencies for FastAPI route protection.

This module provides dependency functions for:
- Extracting and verifying JWT tokens from requests
- Loading current user from database
- Protecting routes with authentication requirements
"""

from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import verify_access_token
from app.models.user import Users

# Define the security scheme for OpenAPI documentation
security = HTTPBearer()


async def get_current_user_id(
    access_token: Annotated[str | None, Cookie()] = None,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))
    ] = None,
) -> int:
    """
    Extract and verify JWT access token from Authorization header or cookie.

    Checks both Authorization header (Bearer token) and access_token cookie,
    preferring the header if both are present. This allows the API to work
    with both Swagger UI (header) and browser requests (cookie).

    Args:
        access_token: Access token from cookie
        credentials: HTTP Bearer credentials from Authorization header

    Returns:
        User ID from valid token

    Raises:
        HTTPException: 401 if token is missing, invalid, or expired
    """
    # Prefer Authorization header, fall back to cookie
    token = None
    if credentials:
        token = credentials.credentials
    elif access_token:
        token = access_token

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Verify token and extract user_id
    user_id = verify_access_token(token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user_id


async def get_current_user(
    user_id: Annotated[int, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Users:
    """
    Load current user from database using verified token.

    Args:
        user_id: User ID from verified JWT token
        db: Database session

    Returns:
        User object from database

    Raises:
        HTTPException: 401 if user not found or inactive
    """
    result = await db.execute(select(Users).where(Users.user_id == user_id))  # type: ignore[arg-type]
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    # Ensure user_id is not None (database-loaded users always have IDs)
    assert user.user_id is not None

    # Check if user is active
    if not user.active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is inactive",
        )

    return user


async def get_optional_current_user(
    access_token: Annotated[str | None, Cookie()] = None,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))
    ] = None,
    db: AsyncSession = Depends(get_db),
) -> Users | None:
    """
    Get current user if authenticated, otherwise return None.

    Useful for endpoints that have different behavior for authenticated vs anonymous users.
    Checks both Authorization header (Bearer token) and access_token cookie.

    Args:
        access_token: Access token from cookie
        credentials: Optional HTTP Bearer credentials
        db: Database session

    Returns:
        User object if authenticated, None otherwise
    """
    # Get token from either header or cookie (prefer header)
    token = None
    if credentials:
        token = credentials.credentials
    elif access_token:
        token = access_token

    if not token:
        return None

    try:
        user_id = verify_access_token(token)
        if user_id is None:
            return None
        result = await db.execute(select(Users).where(Users.user_id == user_id))  # type: ignore[arg-type]
        user = result.scalar_one_or_none()
        return user if user and user.active else None
    except HTTPException:
        return None


async def require_admin(
    current_user: Annotated[Users, Depends(get_current_user)],
) -> Users:
    """
    Require current user to be an admin.

    Args:
        current_user: Current authenticated user

    Returns:
        User object if admin

    Raises:
        HTTPException: 403 if user is not an admin
    """
    if not current_user.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user


def get_client_ip(request: Request) -> str:
    """
    Extract client IP address from request.

    Checks X-Forwarded-For header first (for proxies/load balancers),
    falls back to direct client IP.

    Args:
        request: FastAPI request object

    Returns:
        Client IP address string
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # X-Forwarded-For can contain multiple IPs, take the first (client)
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def get_user_agent(request: Request) -> str:
    """
    Extract User-Agent header from request.

    Args:
        request: FastAPI request object

    Returns:
        User-Agent string (or "unknown" if not present)
    """
    return request.headers.get("User-Agent", "unknown")


async def get_refresh_token_from_cookie(
    refresh_token: Annotated[str | None, Cookie()] = None,
) -> str:
    """
    Extract refresh token from HTTPOnly cookie.

    Args:
        refresh_token: Refresh token from cookie

    Returns:
        Refresh token string

    Raises:
        HTTPException: 401 if refresh token cookie is missing
    """
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token missing",
        )
    return refresh_token


async def get_verified_user(
    current_user: Annotated[Users, Depends(get_current_user)],
) -> Users:
    """
    Require authenticated user with verified email.

    Use this dependency for endpoints that require email verification:
    - Image uploads
    - Comments
    - Posts/submissions
    """
    if not current_user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email verification required. Check your inbox for verification link or request a new one at /api/v1/auth/resend-verification",
        )
    return current_user


# Type aliases for dependency injection
CurrentUser = Annotated[Users, Depends(get_current_user)]
VerifiedUser = Annotated[Users, Depends(get_verified_user)]
OptionalCurrentUser = Annotated[Users | None, Depends(get_optional_current_user)]
AdminUser = Annotated[Users, Depends(require_admin)]
