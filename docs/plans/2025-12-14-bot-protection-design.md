# Bot Protection for User Registration

**Date:** 2025-12-14
**Status:** Design Revised (v2)
**Approach:** Balanced protection with minimal user friction

## Overview

Implement comprehensive bot protection for user registration focusing on preventing spam bots while maintaining good user experience. Uses Cloudflare Turnstile (invisible CAPTCHA) + email verification + rate limiting + honeypot field.

## Goals

- **Primary:** Prevent spam bots from creating accounts to post unwanted content
- **Secondary:** Prevent resource abuse (uploads), data scraping, automated account creation
- **User Experience:** Minimal friction for legitimate users - can browse/favorite immediately, must verify to post/upload

## Architecture

### Approach: Balanced Protection (Approved)

**Registration Flow:**
1. User fills registration form + completes Turnstile challenge (invisible)
2. Account created and **can login immediately** (`active=1`)
3. Verification email sent **asynchronously** (account creation succeeds even if email fails)
4. Unverified users can browse and favorite, but **cannot post/upload/comment**
5. After email verification: full access unlocked

**Protection Layers (execution order):**
1. **Honeypot field** (catches simple bots) - checked first, fail fast
2. **Rate limiting** (5 registrations per IP per hour via Redis)
3. **Cloudflare Turnstile** (invisible CAPTCHA verification)
4. **Email verification** (required for posting/uploading, sent async)
5. **Auto-cleanup** (delete inactive unverified accounts after 30 days)

---

## Component 1: Database Schema Changes

### Migration: Add Email Verification Fields

**Remove:**
- `actkey` - Legacy activation key from PHP, no longer used

**Add to `users` table:**
```python
# app/models/user.py
email_verified: bool = Field(default=False)
email_verification_token: str | None = Field(default=None, max_length=64)
email_verification_sent_at: datetime | None = Field(default=None)
email_verification_expires_at: datetime | None = Field(default=None)
```

**Index:**
```python
Index("idx_email_verification_token", "email_verification_token")
```
Speeds up verification link lookups.

**Migration Strategy:**
- Existing users: Set `email_verified=True` (grandfathered in).
  - **Implementation Note:** The Alembic migration must be manually edited to include `op.execute("UPDATE users SET email_verified = 1 WHERE active = 1")` immediately after adding the column to ensure all existing active users are verified.
- New registrations: Default `email_verified=False`

**Important:** Audit existing user emails for validity. Legacy PHP users may have placeholder/invalid emails.

**Files to modify:**
- `app/models/user.py` - Add new fields, remove `actkey`
- `alembic/versions/xxx_add_email_verification.py` - Create migration

---

## Component 2: Cloudflare Turnstile Integration

### Configuration

**Add to `app/config.py`:**
```python
class Settings(BaseSettings):
    # ... existing settings

    # Cloudflare Turnstile
    TURNSTILE_SITE_KEY: str = Field(..., description="Turnstile site key (public)")
    TURNSTILE_SECRET_KEY: str = Field(..., description="Turnstile secret key (private)")
```

**Environment variables (.env):**
```bash
TURNSTILE_SITE_KEY=<public_key>      # Frontend uses this
TURNSTILE_SECRET_KEY=<secret_key>    # Backend only, never expose
```

**Dependencies:**
- Add `httpx>=0.27.0` to `pyproject.toml` for async HTTP verification

### Frontend Integration

**Note:** Frontend is in separate repo (Svelte). Coordination required.

```html
<!-- Add to registration form in Svelte frontend -->
<script>
  import { onMount } from 'svelte';

  let turnstileToken = '';

  onMount(() => {
    // Load Turnstile script
    const script = document.createElement('script');
    script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js';
    script.async = true;
    script.defer = true;
    document.head.appendChild(script);
  });

  function onTurnstileSuccess(token) {
    turnstileToken = token;
  }
</script>

<form on:submit|preventDefault={handleRegister}>
  <!-- ... username, email, password fields ... -->

  <!-- Turnstile widget -->
  <div class="cf-turnstile"
       data-sitekey="{PUBLIC_TURNSTILE_SITE_KEY}"
       data-callback="onTurnstileSuccess"></div>

  <!-- Token bound to variable, sent as JSON field 'turnstile_token' -->
  <input type="hidden" name="cf-turnstile-response" bind:value={turnstileToken} />

  <button type="submit">Register</button>
</form>
```

### Backend Verification

**Utility function (NOT a dependency) in `app/services/turnstile.py`:**
```python
"""Cloudflare Turnstile verification service."""
import httpx
from fastapi import HTTPException, status

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def verify_turnstile_token(token: str, ip_address: str | None = None) -> None:
    """
    Verify Cloudflare Turnstile challenge response.

    Args:
        token: The turnstile_token from the request body
        ip_address: Optional user IP for additional verification

    Raises:
        HTTPException: 400 if verification fails
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            payload = {
                "secret": settings.TURNSTILE_SECRET_KEY,
                "response": token,
            }
            if ip_address:
                payload["remoteip"] = ip_address

            response = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data=payload,
            )
            response.raise_for_status()
            result = response.json()

            if not result.get("success"):
                error_codes = result.get("error-codes", [])
                logger.warning(
                    "turnstile_verification_failed",
                    error_codes=error_codes,
                    ip_address=ip_address,
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="CAPTCHA verification failed. Please try again.",
                )

            logger.info("turnstile_verification_success", ip_address=ip_address)

    except httpx.HTTPError as e:
        logger.error("turnstile_api_error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CAPTCHA service temporarily unavailable. Please try again.",
        )
```

**Apply to registration endpoint:**
```python
# app/api/v1/users.py
from app.services.turnstile import verify_turnstile_token

@router.post("/", response_model=UserCreateResponse)
async def create_user(
    user_data: UserCreate,
    request: Request,  # IMPORTANT: Need Request for IP extraction
    db: AsyncSession = Depends(get_db),
) -> UserCreateResponse:
    """Create a new user with bot protection."""

    # 1. Honeypot check (fail fast)
    if user_data.website_url:
        raise HTTPException(status_code=400, detail="Invalid registration request.")

    # 2. Rate limiting
    ip_address = get_client_ip(request)
    await check_registration_rate_limit(ip_address)

    # 3. Turnstile verification
    # Token is now part of the JSON body (UserCreate schema)
    await verify_turnstile_token(user_data.turnstile_token, ip_address)

    # 4. Continue with user creation...
```

**Free Tier:**
- 1M verifications/month (effectively unlimited for this use case)
- ~33,000 registration attempts per day before exceeding free tier

---

## Component 3: Email Verification Flow

### Email Sending Service (SMTP)

**Add to `app/config.py`:**
```python
class Settings(BaseSettings):
    # ... existing settings

    # SMTP Configuration
    SMTP_HOST: str = Field(..., description="SMTP server hostname")
    SMTP_PORT: int = Field(587, description="SMTP server port")
    SMTP_USER: str = Field(..., description="SMTP username")
    SMTP_PASSWORD: str = Field(..., description="SMTP password")
    SMTP_TLS: bool = Field(True, description="Use TLS for SMTP")
    SMTP_FROM_EMAIL: str = Field(..., description="From email address")
    SMTP_FROM_NAME: str = Field("Shuushuu", description="From name")

    # Frontend URL for verification links
    FRONTEND_URL: str = Field(..., description="Frontend base URL")
```

**Environment variables (.env):**
```bash
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=noreply@yourdomain.com
SMTP_PASSWORD=<password>
SMTP_TLS=true
SMTP_FROM_EMAIL=noreply@yourdomain.com
SMTP_FROM_NAME=Shuushuu
FRONTEND_URL=https://yourdomain.com
```

**Dependencies:**
```toml
# pyproject.toml
dependencies = [
    # ... existing
    "httpx>=0.27.0",  # For Turnstile verification
    "aiosmtplib>=3.0.0",  # Async SMTP client
]
```

**Implementation: `app/services/email.py`**
```python
"""Email sending service with SMTP."""
import asyncio
from email.message import EmailMessage
from typing import List

import aiosmtplib
from aiosmtplib.errors import SMTPException

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def send_email(
    to: str | List[str],
    subject: str,
    body: str,
    html: str | None = None,
) -> bool:
    """
    Send email via SMTP with retry logic.

    Args:
        to: Recipient email address(es)
        subject: Email subject
        body: Plain text email body
        html: Optional HTML email body

    Returns:
        True if email sent successfully, False otherwise

    Note:
        This function logs errors but does NOT raise exceptions.
        Callers should check return value if they need to know success/failure.
    """
    message = EmailMessage()
    message["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL}>"
    message["To"] = to if isinstance(to, str) else ", ".join(to)
    message["Subject"] = subject
    message.set_content(body)

    if html:
        message.add_alternative(html, subtype="html")

    # Retry logic: 3 attempts with exponential backoff
    max_retries = 3
    for attempt in range(max_retries):
        try:
            await aiosmtplib.send(
                message,
                hostname=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                username=settings.SMTP_USER,
                password=settings.SMTP_PASSWORD,
                use_tls=settings.SMTP_TLS,
                timeout=30,
            )
            logger.info(
                "email_sent_success",
                to=to,
                subject=subject,
                attempt=attempt + 1,
            )
            return True

        except SMTPException as e:
            logger.warning(
                "email_send_failed",
                to=to,
                subject=subject,
                attempt=attempt + 1,
                error=str(e),
            )
            if attempt < max_retries - 1:
                # Exponential backoff: 1s, 2s, 4s
                await asyncio.sleep(2 ** attempt)
            else:
                logger.error(
                    "email_send_failed_all_retries",
                    to=to,
                    subject=subject,
                    error=str(e),
                )
                return False

        except Exception as e:
            # Unexpected error
            logger.error(
                "email_send_unexpected_error",
                to=to,
                subject=subject,
                error=str(e),
            )
            return False

    return False


async def send_verification_email(user: "Users", token: str) -> bool:
    """
    Send email verification link to user.

    Args:
        user: User object
        token: Raw verification token (not hashed)

    Returns:
        True if email sent successfully, False otherwise
    """
    verification_url = f"{settings.FRONTEND_URL}/verify-email?token={token}"

    subject = "Verify your email address"
    body = f"""Welcome to Shuushuu, {user.username}!

Please verify your email address by clicking the link below:

{verification_url}

This link will expire in 24 hours.

If you didn't create an account, you can safely ignore this email.
"""

    # TODO: Add HTML template in future
    html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .button {{
            display: inline-block;
            padding: 12px 24px;
            background-color: #4CAF50;
            color: white;
            text-decoration: none;
            border-radius: 4px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h2>Welcome to Shuushuu, {user.username}!</h2>
        <p>Please verify your email address to start uploading images and posting comments.</p>
        <p><a href="{verification_url}" class="button">Verify Email Address</a></p>
        <p>Or copy this link into your browser:</p>
        <p><code>{verification_url}</code></p>
        <p><small>This link will expire in 24 hours.</small></p>
        <p><small>If you didn't create an account, you can safely ignore this email.</small></p>
    </div>
</body>
</html>
"""

    return await send_email(to=user.email, subject=subject, body=body, html=html)
```

### Token Generation (on registration)

```python
# In registration endpoint
import secrets
import hashlib
from datetime import timedelta, datetime, UTC

# Generate secure random token
raw_token = secrets.token_urlsafe(32)  # 43 chars, URL-safe

# Hash for database storage (only hash stored, not raw token)
token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

# Store in DB
new_user.email_verification_token = token_hash
new_user.email_verification_sent_at = datetime.now(UTC)
new_user.email_verification_expires_at = datetime.now(UTC) + timedelta(hours=24)

# Send email ASYNCHRONOUSLY (don't block registration on email sending)
# This ensures account creation succeeds even if email fails
asyncio.create_task(send_verification_email(new_user, raw_token))
```

**Important:** Email sending is async and non-blocking. If it fails, user can use resend endpoint.

### Verification Endpoint

**New endpoint in `app/api/v1/auth.py`:**
```python
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
        select(Users).where(Users.email_verification_token == token_hash)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification token"
        )

    # Check expiration (IMPORTANT: null check first!)
    if not user.email_verification_expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification token"
        )

    if user.email_verification_expires_at < datetime.now(UTC).replace(tzinfo=None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification token expired. Please request a new one."
        )

    # Verify email
    user.email_verified = True
    user.email_verification_token = None  # Clear token
    user.email_verification_expires_at = None  # Clear expiration
    await db.commit()

    logger.info("email_verified", user_id=user.user_id, username=user.username)

    return MessageResponse(message="Email verified successfully!")
```

### Resend Verification Endpoint

**New endpoint in `app/api/v1/auth.py`:**
```python
@router.post("/resend-verification", response_model=MessageResponse)
async def resend_verification(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Resend verification email to current user."""
    if current_user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already verified"
        )

    # Rate limit: only 1 resend per 5 minutes
    if current_user.email_verification_sent_at:
        time_since_last = datetime.now(UTC).replace(tzinfo=None) - current_user.email_verification_sent_at
        if time_since_last < timedelta(minutes=5):
            remaining_seconds = int((timedelta(minutes=5) - time_since_last).total_seconds())
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Please wait {remaining_seconds} seconds before requesting another verification email"
            )

    # Generate new token
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    # Update user
    current_user.email_verification_token = token_hash
    current_user.email_verification_sent_at = datetime.now(UTC)
    current_user.email_verification_expires_at = datetime.now(UTC) + timedelta(hours=24)
    await db.commit()

    # Send email asynchronously
    success = await send_verification_email(current_user, raw_token)

    if not success:
        logger.error("resend_verification_email_failed", user_id=current_user.user_id)
        # Don't fail the request - user can try again

    return MessageResponse(message="Verification email sent! Check your inbox.")
```

---

## Component 4: Permission System

### Verified User Dependency

**Add to `app/core/auth.py`:**
```python
async def get_verified_user(
    current_user: CurrentUser,
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

# Convenience type alias
VerifiedUser = Annotated[Users, Depends(get_verified_user)]
```

### Apply to Protected Endpoints

**Endpoints requiring verification:**
- `POST /api/v1/images/` - Upload images
- `POST /api/v1/comments/` - Create comments
- Any other content creation endpoints

**Change dependency from `CurrentUser` to `VerifiedUser`:**
```python
# app/api/v1/images.py
# Before
async def upload_image(
    user: CurrentUser,
    ...
) -> ImageResponse:

# After
async def upload_image(
    user: VerifiedUser,  # Now requires email verification
    ...
) -> ImageResponse:
```

**Endpoints that DON'T require verification:**
- Browse images (public)
- View user profiles (public)
- Search/filter (public)
- Add to favorites (`CurrentUser` - unverified can favorite)
- Login/logout (public)

**User Experience:**
- Unverified users attempting protected actions get clear 403 error
- Error message includes resend endpoint URL
- Frontend should check `email_verified` field in user object
- Show persistent banner: "Please verify your email to upload images and post comments. [Resend email]"

---

## Component 5: Rate Limiting

### Registration Rate Limiting (IP-based)

**Add to `app/config.py`:**
```python
class Settings(BaseSettings):
    # ... existing settings

    # Rate Limiting
    REGISTRATION_RATE_LIMIT: int = Field(5, description="Max registrations per IP per window")
    REGISTRATION_RATE_WINDOW_HOURS: int = Field(1, description="Rate limit window in hours")
```

**Implementation: `app/services/rate_limit.py`**
```python
"""Rate limiting service using Redis."""
from datetime import timedelta

from fastapi import HTTPException, status
from redis.asyncio import Redis

from app.config import settings
from app.core.database import get_redis
from app.core.logging import get_logger

logger = get_logger(__name__)


async def check_registration_rate_limit(ip_address: str) -> None:
    """
    Enforce registration rate limit per IP address.

    Limit: 5 registrations per IP per hour (configurable)

    Uses Redis for fast lookups and automatic expiration.

    Args:
        ip_address: Client IP address

    Raises:
        HTTPException: 429 if rate limit exceeded
    """
    redis = await get_redis()
    key = f"registration_rate:{ip_address}"

    # Get current count
    count_bytes = await redis.get(key)
    count = int(count_bytes) if count_bytes else 0

    if count >= settings.REGISTRATION_RATE_LIMIT:
        logger.warning(
            "registration_rate_limit_exceeded",
            ip_address=ip_address,
            count=count,
            limit=settings.REGISTRATION_RATE_LIMIT,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many registration attempts. Please try again in {settings.REGISTRATION_RATE_WINDOW_HOURS} hour(s).",
        )

    # Increment counter with expiration
    pipe = redis.pipeline()
    pipe.incr(key)
    if count == 0:
        # First registration from this IP - set expiration
        pipe.expire(key, timedelta(hours=settings.REGISTRATION_RATE_WINDOW_HOURS))
    await pipe.execute()

    logger.debug(
        "registration_rate_check",
        ip_address=ip_address,
        count=count + 1,
        limit=settings.REGISTRATION_RATE_LIMIT,
    )
```

**Apply to registration endpoint:**
```python
# app/api/v1/users.py
from app.core.auth import get_client_ip
from app.services.rate_limit import check_registration_rate_limit

@router.post("/", response_model=UserCreateResponse)
async def create_user(
    user_data: UserCreate,
    request: Request,  # IMPORTANT: Need for IP extraction
    db: AsyncSession = Depends(get_db),
) -> UserCreateResponse:
    """Create a new user with bot protection."""

    # 1. Honeypot check (fail fast)
    if user_data.website_url:
        raise HTTPException(status_code=400, detail="Invalid registration request.")

    # 2. Rate limiting (before expensive Turnstile API call)
    ip_address = get_client_ip(request)
    await check_registration_rate_limit(ip_address)

    # 3. Turnstile verification
    await verify_turnstile_token(user_data.turnstile_token, ip_address)

    # 4. Continue with existing validation and user creation...
```

**Why Redis:**
- Fast O(1) lookups
- Automatic key expiration (no cleanup needed)
- Already in stack (used for caching)
- Can extend to other rate limits (login, password reset, etc.)

---

## Component 6: Honeypot Field

### Concept

Invisible form field that humans never see/fill, but bots auto-fill. If filled → reject silently.

### Implementation

**IMPORTANT:** Make field optional to avoid breaking existing code that creates Users directly.

**Add to `app/schemas/user.py`:**
```python
class UserCreate(UserBase):
    password: str
    email: str

    # Honeypot field (should always be empty for legitimate users)
    # Optional with default to avoid breaking existing Users() instantiations
    # Field name looks legitimate to bots (not "honeypot" or "trap")
    website_url: str = ""

    # Cloudflare Turnstile token (required)
    turnstile_token: str
```

**Audit required:** Search codebase for all `Users(` instantiations and verify they handle the new field:
```bash
rg "Users\(" --type py
```

**Frontend (CSS hides from humans):**
```html
<!-- Svelte frontend component -->
<!-- Hidden via CSS positioning, not HTML 'hidden' attribute -->
<!-- Bots often ignore 'hidden' but render CSS -->
<input
    type="text"
    name="website_url"
    value=""
    autocomplete="off"
    tabindex="-1"
    aria-hidden="true"
    style="position:absolute;left:-9999px;width:1px;height:1px;opacity:0;" />
```

**Backend validation (registration endpoint only):**
```python
@router.post("/", response_model=UserCreateResponse)
async def create_user(
    user_data: UserCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> UserCreateResponse:
    """Create a new user with bot protection."""

    # 1. Honeypot check FIRST (fail fast, cheapest check)
    if user_data.website_url:
        # Bot detected! Silently reject without revealing honeypot
        logger.warning("honeypot_triggered", ip_address=get_client_ip(request))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid registration request.",
        )

    # 2. Continue with other checks...
```

**Why this works:**
- CSS-hidden fields are invisible to humans (positioned off-screen, zero opacity)
- Screen readers skip `aria-hidden="true"` and `tabindex="-1"`
- Bots auto-fill all fields regardless of CSS/ARIA attributes
- If filled → 100% bot detection (humans can't see the field)
- Silent rejection prevents bots from learning/adapting

**Field naming strategy:**
- `website_url` looks like a legitimate profile field
- Bots won't recognize it as a trap (not named `honeypot`, `hp`, etc.)

---

## Component 7: Cleanup & Maintenance

### Auto-Delete Inactive Unverified Accounts

**Purpose:** Remove accounts that never verify email and never login (likely abandoned or bot accounts).

**Implementation: `app/services/user_cleanup.py`**
```python
"""User account cleanup service."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.user import Users

logger = get_logger(__name__)


async def cleanup_unverified_accounts(db: AsyncSession) -> int:
    """
    Delete unverified accounts older than 30 days with no login activity.

    Deletion criteria (ALL must be true):
    - email_verified = False
    - Created 30+ days ago (date_joined < cutoff)
    - Never logged in OR last_login same as date_joined

    Verified users are NEVER deleted, regardless of inactivity.

    Args:
        db: Database session

    Returns:
        Count of deleted accounts
    """
    cutoff_date = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=30)

    # Build query for stale unverified accounts
    query = select(Users).where(
        Users.email_verified == False,  # Not verified
        Users.date_joined < cutoff_date,  # Created 30+ days ago
        or_(
            Users.last_login == None,  # Never logged in
            Users.last_login <= Users.date_joined  # Logged in once at creation (legacy)
        )
    )

    result = await db.execute(query)
    stale_users = result.scalars().all()

    count = 0
    for user in stale_users:
        logger.info(
            "deleting_stale_unverified_user",
            user_id=user.user_id,
            username=user.username,
            date_joined=user.date_joined,
            last_login=user.last_login,
        )
        await db.delete(user)
        count += 1

    if count > 0:
        await db.commit()
        logger.info("cleanup_unverified_accounts_complete", deleted_count=count)
    else:
        logger.debug("cleanup_unverified_accounts_no_deletions")

    return count
```

**Note on last_login logic:**
- Check actual login behavior in current system
- Some systems set `last_login = date_joined` on registration
- Others leave `last_login = None` until first actual login
- Query handles both cases

### Scheduling with arq

**Add to `app/worker.py`:**
```python
from arq.cron import cron
from app.services.user_cleanup import cleanup_unverified_accounts
from app.core.database import get_async_session

async def cleanup_stale_accounts(ctx):
    """
    Daily cleanup of unverified inactive accounts.

    Runs at 3 AM UTC daily via arq cron.
    """
    async for db in get_async_session():
        try:
            count = await cleanup_unverified_accounts(db)
            logger.info("cleanup_task_complete", deleted_accounts=count)
        finally:
            await db.close()

# Add to existing WorkerSettings
class WorkerSettings:
    # ... existing settings

    cron_jobs = [
        # Run daily at 3 AM UTC
        cron(cleanup_stale_accounts, hour=3, minute=0),
        # ... other existing cron jobs
    ]
```

**Why arq:**
- **Required:** The system uses `arq` for background tasks.
- Already configured for background tasks (IQDB matching)
- Integrated with application (same logging, config)
- Persistent scheduling (survives restarts)
- Error handling and retry built-in

**Why 30 days:**
- Generous time for users to verify (most verify within 24 hours)
- If they never login after 30 days → likely abandoned or bot
- Verified users are NEVER deleted (even if inactive for years)

---

## Component 8: Testing Strategy

### Test Fixtures

**Add to `tests/conftest.py`:**
```python
import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.security import get_password_hash
from app.models.user import Users


@pytest.fixture
def mock_turnstile_success(monkeypatch):
    """Mock successful Turnstile verification."""
    async def mock_verify(token: str, ip_address: str | None = None):
        pass  # Success = no exception

    monkeypatch.setattr(
        "app.services.turnstile.verify_turnstile_token",
        mock_verify
    )


@pytest.fixture
def mock_turnstile_fail(monkeypatch):
    """Mock failed Turnstile verification."""
    from fastapi import HTTPException, status

    async def mock_verify(token: str, ip_address: str | None = None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CAPTCHA verification failed"
        )

    monkeypatch.setattr(
        "app.services.turnstile.verify_turnstile_token",
        mock_verify
    )


@pytest.fixture
def mock_email(monkeypatch):
    """Mock email sending (don't actually send during tests)."""
    sent_emails = []

    async def mock_send(to, subject, body, html=None):
        sent_emails.append({
            "to": to,
            "subject": subject,
            "body": body,
            "html": html,
        })
        return True  # Always succeed

    monkeypatch.setattr("app.services.email.send_email", mock_send)
    return sent_emails


@pytest.fixture
async def unverified_user(db: AsyncSession):
    """Create unverified user directly in DB (for testing verification flow)."""
    user = Users(
        username="unverified_test",
        email="unverified@test.com",
        password=get_password_hash("TestPass123!"),
        password_type="bcrypt",
        salt="",
        email_verified=False,
        active=1,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest.fixture
async def verified_user(db: AsyncSession):
    """Create verified user directly in DB."""
    user = Users(
        username="verified_test",
        email="verified@test.com",
        password=get_password_hash("TestPass123!"),
        password_type="bcrypt",
        salt="",
        email_verified=True,
        active=1,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest.fixture
async def unverified_user_token(client, db, unverified_user):
    """Get auth token for unverified user (via login)."""
    response = await client.post("/api/v1/auth/login", json={
        "username": "unverified_test",
        "password": "TestPass123!"
    })
    assert response.status_code == 200
    return response.json()["access_token"]


@pytest.fixture
async def verified_user_token(client, db, verified_user):
    """Get auth token for verified user (via login)."""
    response = await client.post("/api/v1/auth/login", json={
        "username": "verified_test",
        "password": "TestPass123!"
    })
    assert response.status_code == 200
    return response.json()["access_token"]
```

### Test Coverage

**1. Database Schema (Unit)**
```python
# tests/unit/test_user_model.py

def test_new_user_defaults_to_unverified():
    """New users created with email_verified=False by default."""
    user = Users(
        username="testuser",
        email="test@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="",
    )
    assert user.email_verified == False
    assert user.email_verification_token is None
    assert user.email_verification_sent_at is None
    assert user.email_verification_expires_at is None
```

**2. Turnstile Verification (Integration)**
```python
# tests/api/v1/test_registration.py

@pytest.mark.asyncio
async def test_registration_fails_without_turnstile(client, db):
    """Registration fails with invalid Turnstile token."""
    response = await client.post("/api/v1/users/", data={
        "username": "newuser",
        "email": "new@example.com",
        "password": "SecurePass123!",
        "cf-turnstile-response": "invalid_token",  # Note hyphen
        "website_url": "",  # Honeypot
    })

    assert response.status_code == 400
    assert "CAPTCHA" in response.json()["detail"]


@pytest.mark.asyncio
async def test_registration_succeeds_with_valid_turnstile(
    client, db, mock_turnstile_success, mock_email
):
    """Registration succeeds with valid Turnstile token."""
    response = await client.post("/api/v1/users/", data={
        "username": "newuser",
        "email": "new@example.com",
        "password": "SecurePass123!",
        "cf-turnstile-response": "valid_token",
        "website_url": "",
    })

    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "newuser"
    assert data["email"] == "new@example.com"

    # Verify user created in DB as unverified
    result = await db.execute(
        select(Users).where(Users.username == "newuser")
    )
    user = result.scalar_one()
    assert user.email_verified == False

    # Verify email was sent
    assert len(mock_email) == 1
    assert mock_email[0]["to"] == "new@example.com"
    assert "verify" in mock_email[0]["subject"].lower()
```

**3. Email Verification Flow (Integration)**
```python
# tests/api/v1/test_email_verification.py

@pytest.mark.asyncio
async def test_email_verification_success(client, db, unverified_user):
    """Valid token verifies email successfully."""
    # Set up verification token
    raw_token = "test_token_abc123"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    unverified_user.email_verification_token = token_hash
    unverified_user.email_verification_expires_at = (
        datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
    )
    await db.commit()

    # Verify email
    response = await client.post(
        f"/api/v1/auth/verify-email?token={raw_token}"
    )

    assert response.status_code == 200
    assert "success" in response.json()["message"].lower()

    # Check user is now verified
    await db.refresh(unverified_user)
    assert unverified_user.email_verified == True
    assert unverified_user.email_verification_token is None


@pytest.mark.asyncio
async def test_email_verification_expired_token(client, db, unverified_user):
    """Expired token fails verification."""
    raw_token = "expired_token"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    unverified_user.email_verification_token = token_hash
    unverified_user.email_verification_expires_at = (
        datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)  # Expired
    )
    await db.commit()

    response = await client.post(
        f"/api/v1/auth/verify-email?token={raw_token}"
    )

    assert response.status_code == 400
    assert "expired" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_resend_verification_rate_limit(
    client, db, unverified_user_token, unverified_user
):
    """Can only resend verification once per 5 minutes."""
    # Set sent_at to now (just sent)
    unverified_user.email_verification_sent_at = datetime.now(UTC).replace(tzinfo=None)
    await db.commit()

    # Try to resend immediately - should fail
    response = await client.post(
        "/api/v1/auth/resend-verification",
        headers={"Authorization": f"Bearer {unverified_user_token}"}
    )

    assert response.status_code == 429
    assert "wait" in response.json()["detail"].lower()
```

**4. Permission Gates (Integration)**
```python
# tests/api/v1/test_permissions.py

@pytest.mark.asyncio
async def test_unverified_user_cannot_upload(
    client, db, unverified_user_token, test_image_file
):
    """Unverified users cannot upload images."""
    response = await client.post(
        "/api/v1/images/",
        headers={"Authorization": f"Bearer {unverified_user_token}"},
        files={"file": ("test.jpg", test_image_file, "image/jpeg")},
        data={"tags": "test"}
    )

    assert response.status_code == 403
    assert "verification required" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_verified_user_can_upload(
    client, db, verified_user_token, test_image_file
):
    """Verified users can upload images."""
    response = await client.post(
        "/api/v1/images/",
        headers={"Authorization": f"Bearer {verified_user_token}"},
        files={"file": ("test.jpg", test_image_file, "image/jpeg")},
        data={"tags": "test"}
    )

    assert response.status_code == 201


@pytest.mark.asyncio
async def test_unverified_user_can_favorite(
    client, db, unverified_user_token, test_image
):
    """Unverified users CAN favorite images (allowed)."""
    response = await client.post(
        f"/api/v1/images/{test_image.image_id}/favorite",
        headers={"Authorization": f"Bearer {unverified_user_token}"}
    )

    assert response.status_code == 200
```

**5. Rate Limiting (Integration)**
```python
# tests/api/v1/test_rate_limiting.py

@pytest.mark.asyncio
async def test_registration_rate_limit(
    client, db, redis, mock_turnstile_success, mock_email
):
    """Cannot register more than 5 times per hour from same IP."""
    # Clear any existing rate limit
    await redis.delete("registration_rate:127.0.0.1")

    # Register 5 times successfully
    for i in range(5):
        response = await client.post("/api/v1/users/", data={
            "username": f"user{i}",
            "email": f"user{i}@example.com",
            "password": "SecurePass123!",
            "cf-turnstile-response": "valid",
            "website_url": "",
        })
        assert response.status_code == 201, f"Registration {i+1} failed"

    # 6th attempt fails with 429
    response = await client.post("/api/v1/users/", data={
        "username": "user6",
        "email": "user6@example.com",
        "password": "SecurePass123!",
        "cf-turnstile-response": "valid",
        "website_url": "",
    })
    assert response.status_code == 429
    assert "too many" in response.json()["detail"].lower()
```

**6. Honeypot (Unit/Integration)**
```python
# tests/api/v1/test_honeypot.py

@pytest.mark.asyncio
async def test_honeypot_rejects_filled_field(client, db):
    """Registration fails if honeypot field is filled (bot detected)."""
    response = await client.post("/api/v1/users/", data={
        "username": "bot",
        "email": "bot@example.com",
        "password": "BotPass123!",
        "website_url": "http://spam.com",  # Honeypot filled = bot
        "cf-turnstile-response": "doesnt_matter",
    })

    assert response.status_code == 400
    assert "invalid" in response.json()["detail"].lower()

    # Verify user was NOT created
    result = await db.execute(select(Users).where(Users.username == "bot"))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_honeypot_allows_empty_field(
    client, db, mock_turnstile_success, mock_email
):
    """Registration succeeds if honeypot field is empty (legitimate)."""
    response = await client.post("/api/v1/users/", data={
        "username": "human",
        "email": "human@example.com",
        "password": "HumanPass123!",
        "website_url": "",  # Empty = legitimate human
        "cf-turnstile-response": "valid",
    })

    assert response.status_code == 201

    # Verify user was created
    result = await db.execute(select(Users).where(Users.username == "human"))
    assert result.scalar_one() is not None
```

**7. Cleanup Task (Unit)**
```python
# tests/unit/test_user_cleanup.py

@pytest.mark.asyncio
async def test_cleanup_deletes_stale_unverified_accounts(db):
    """Unverified accounts 30+ days old with no login are deleted."""
    old_date = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=31)

    # Create stale unverified user (should be deleted)
    stale_user = Users(
        username="stale",
        email="stale@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="",
        email_verified=False,
        date_joined=old_date,
        last_login=None,
        active=1,
    )
    db.add(stale_user)

    # Create recent unverified user (should NOT be deleted)
    recent_user = Users(
        username="recent",
        email="recent@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="",
        email_verified=False,
        date_joined=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=5),
        active=1,
    )
    db.add(recent_user)

    # Create old verified user (should NOT be deleted)
    verified_user = Users(
        username="verified_old",
        email="verified_old@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="",
        email_verified=True,
        date_joined=old_date,
        active=1,
    )
    db.add(verified_user)

    await db.commit()

    # Run cleanup
    from app.services.user_cleanup import cleanup_unverified_accounts
    count = await cleanup_unverified_accounts(db)

    assert count == 1  # Only stale_user deleted

    # Verify deletions
    result = await db.execute(select(Users).where(Users.username == "stale"))
    assert result.scalar_one_or_none() is None

    result = await db.execute(select(Users).where(Users.username == "recent"))
    assert result.scalar_one_or_none() is not None

    result = await db.execute(select(Users).where(Users.username == "verified_old"))
    assert result.scalar_one_or_none() is not None
```

---

## Implementation Order

Recommended implementation sequence based on dependencies:

1. **Configuration** - Add all settings to `app/config.py` (SMTP, Turnstile, rate limits)
2. **Database migration** - Add email verification fields, remove `actkey`, index
3. **Email service** - Implement `app/services/email.py` with SMTP, test with simple email
4. **Honeypot field** - Add to `UserCreate` schema, audit existing code
5. **Rate limiting** - Implement `app/services/rate_limit.py`, test with Redis
6. **Turnstile service** - Implement `app/services/turnstile.py`, test with mock
7. **Email verification flow** - Token generation, `/verify-email`, `/resend-verification` endpoints
8. **Permission gates** - `VerifiedUser` dependency, apply to upload/comment endpoints
9. **Registration endpoint** - Integrate all protections in correct order
10. **Cleanup task** - Implement cleanup service, add to arq worker
11. **Testing** - Comprehensive test coverage for all components
12. **Frontend coordination** - Turnstile widget, verification banner, resend button

---

## Configuration Summary

**New settings in `app/config.py`:**
```python
class Settings(BaseSettings):
    # ... existing settings

    # Cloudflare Turnstile
    TURNSTILE_SITE_KEY: str
    TURNSTILE_SECRET_KEY: str

    # SMTP Email
    SMTP_HOST: str
    SMTP_PORT: int = 587
    SMTP_USER: str
    SMTP_PASSWORD: str
    SMTP_TLS: bool = True
    SMTP_FROM_EMAIL: str
    SMTP_FROM_NAME: str = "Shuushuu"

    # Application URLs
    FRONTEND_URL: str

    # Rate Limiting
    REGISTRATION_RATE_LIMIT: int = 5
    REGISTRATION_RATE_WINDOW_HOURS: int = 1
```

**Environment variables (.env):**
```bash
# Cloudflare Turnstile
TURNSTILE_SITE_KEY=<public_key>
TURNSTILE_SECRET_KEY=<secret_key>

# SMTP Email
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=noreply@yourdomain.com
SMTP_PASSWORD=<password>
SMTP_TLS=true
SMTP_FROM_EMAIL=noreply@yourdomain.com
SMTP_FROM_NAME=Shuushuu

# Application URLs
FRONTEND_URL=https://yourdomain.com

# Rate Limiting (optional, defaults shown)
REGISTRATION_RATE_LIMIT=5
REGISTRATION_RATE_WINDOW_HOURS=1
```

**New dependencies (pyproject.toml):**
```toml
dependencies = [
    # ... existing
    "httpx>=0.27.0",  # For Turnstile API calls
    "aiosmtplib>=3.0.0",  # Async SMTP client
]
```

---

## Success Metrics

**Bot prevention effectiveness:**
- Registration rate from suspicious IPs decreases
- Spam content submissions decrease significantly
- Verified user ratio increases (legitimate users verify emails)
- Honeypot catches simple bots (monitor logs)

**User experience:**
- Registration completion rate remains high (>90%)
- Email verification rate within 24 hours (>70%)
- Support requests about "can't register" remain low
- Time-to-first-post for legitimate users is acceptable (<5 minutes after verification)

**System health:**
- Redis rate limiting performs well (latency <10ms)
- Email delivery success rate >95%
- SMTP errors logged and alerted
- Cleanup task runs successfully daily
- No false positives from Turnstile or honeypot

---

## Known Limitations & Future Enhancements

### Current Limitations

1. **Frontend not included** - Svelte frontend repo needs separate implementation
2. **Email templates basic** - Plain text + simple HTML (not beautiful)
3. **No disposable email blocking** - Allows mailinator, guerrillamail, etc.
4. **No IP reputation checking** - All IPs treated equally
5. **Single email per user** - No email change workflow
6. **No admin dashboard** - Can't view/manage unverified users from UI

### Future Enhancements (Not in Initial Implementation)

1. **Disposable email detection** - Block/warn on known disposable email providers
2. **IP reputation checking** - Flag known VPN/proxy/datacenter IPs for extra scrutiny
3. **Progressive restrictions** - New verified accounts have 24hr cooldown before first upload
4. **Admin dashboard** - View unverified accounts, manually verify, resend verification
5. **Beautiful email templates** - Professional HTML templates with branding
6. **Multi-language support** - Verification emails in user's preferred language
7. **Email change workflow** - Allow users to change email with reverification
8. **SMS verification** - Optional SMS verification for high-value users
9. **Turnstile difficulty adjustment** - Increase challenge difficulty for suspicious IPs
10. **Analytics dashboard** - Track verification rates, bot detection, email delivery

---

## Edge Cases & Error Handling

### Email Sending Failures

- **Problem:** SMTP server down, email bounces, network timeout
- **Solution:** Email sending is async and non-blocking. Registration succeeds even if email fails. User can use `/resend-verification` endpoint.
- **Monitoring:** Log all email failures. Alert if delivery rate drops below 95%.

### Token Expiration Edge Case

- **Problem:** User clicks verification link after 24 hours
- **Solution:** Show friendly error message with resend link. They can login and use `/resend-verification`.

### Rate Limit False Positives

- **Problem:** Shared IP (NAT, corporate network) hits rate limit
- **Solution:** Limit is generous (5/hour). If issue persists, admins can manually clear Redis key.
- **Monitoring:** Track rate limit hits. If excessive, may need IP whitelist.

### Turnstile API Downtime

- **Problem:** Cloudflare Turnstile API unavailable
- **Solution:** Return 503 error with clear message. Don't block registrations permanently.
- **Monitoring:** Alert on Turnstile failures. Consider fallback (temporarily disable if >1hr outage).

### Honeypot False Positives

- **Problem:** Browser autofill fills honeypot field
- **Solution:** Use `autocomplete="off"` and off-screen positioning. Modern browsers respect this.
- **Monitoring:** If legitimate users are blocked, review honeypot implementation.

### Legacy User Migration

- **Problem:** Existing users may have invalid/placeholder emails
- **Solution:** Migration sets `email_verified=True` for all existing users. Future: nag them to update/verify email.

---

## Security Considerations

### Token Security

- **Storage:** Only hashed tokens stored in database (SHA256)
- **Transmission:** Raw tokens only sent via email (HTTPS) and verification link
- **Expiration:** 24 hours - short enough to limit exposure
- **One-time use:** Token cleared after successful verification

### Rate Limiting Bypass

- **VPN/Proxy rotation:** Sophisticated bots can rotate IPs. Turnstile helps here.
- **Distributed attacks:** If bots use many IPs, each gets 5 registrations. Monitor total registration rate.

### Email Validation

- **No strict validation:** We accept any email format that passes basic regex
- **Verification required:** Invalid emails can't verify, so they can't post/upload

### SMTP Credentials

- **Environment variables:** Never commit SMTP password to git
- **Least privilege:** SMTP user should only have send permission
- **Monitoring:** Alert on authentication failures (possible credential leak)

### Turnstile Secret Key

- **Backend only:** Never expose secret key to frontend
- **Environment variables:** Never commit to git
- **Rotation:** Can rotate key in Cloudflare dashboard if compromised

---

## Files to Create/Modify

### New Files

- `app/services/email.py` - Email sending service with SMTP
- `app/services/turnstile.py` - Turnstile verification service
- `app/services/rate_limit.py` - Registration rate limiting
- `app/services/user_cleanup.py` - Cleanup stale accounts
- `alembic/versions/xxx_add_email_verification.py` - Migration
- `tests/api/v1/test_email_verification.py` - Email verification tests
- `tests/api/v1/test_permissions.py` - Permission gate tests
- `tests/api/v1/test_rate_limiting.py` - Rate limiting tests
- `tests/api/v1/test_honeypot.py` - Honeypot tests
- `tests/unit/test_user_cleanup.py` - Cleanup task tests

### Modified Files

- `app/models/user.py` - Add email verification fields, remove `actkey`
- `app/schemas/user.py` - Add honeypot field to `UserCreate`
- `app/api/v1/users.py` - Registration endpoint with all protections
- `app/api/v1/auth.py` - Add `/verify-email` and `/resend-verification` endpoints
- `app/core/auth.py` - Add `get_verified_user` dependency
- `app/api/v1/images.py` - Change to `VerifiedUser` dependency for uploads
- `app/api/v1/comments.py` - Change to `VerifiedUser` dependency for comments
- `app/worker.py` - Add cleanup cron job
- `app/config.py` - Add Turnstile, SMTP, and rate limit settings
- `pyproject.toml` - Add `httpx` and `aiosmtplib` dependencies
- `tests/conftest.py` - Add test fixtures (mocks, test users, tokens)

---

## Questions & Decisions

- ✅ CAPTCHA choice: Cloudflare Turnstile (invisible, free 1M/month, privacy-focused)
- ✅ Email provider: SMTP (flexible, self-hosted or third-party)
- ✅ Rate limit: 5 registrations per IP per hour (strict but fair)
- ✅ Verification requirement: Can login immediately, must verify to post/upload
- ✅ Email sending: Asynchronous, non-blocking (registration succeeds even if email fails)
- ✅ Cleanup schedule: arq cron job daily at 3 AM UTC
- ✅ Cleanup threshold: 30 days unverified + no login activity
- ✅ Token expiration: 24 hours
- ✅ Resend rate limit: 1 per 5 minutes
- ✅ Honeypot field: Optional with default (doesn't break existing code)
- ✅ Legacy users: Grandfathered as verified (email may be invalid)

---

## Open Questions for User

1. **SMTP provider:** Do you have an SMTP server set up, or should we use a service (SendGrid, AWS SES, Mailgun)?
2. **Legacy user emails:** Are existing user emails valid? Should we require them to reverify?
3. **Frontend coordination:** Who handles the Svelte frontend changes (Turnstile widget, verification banner)?
4. **Domain name:** What's the production domain for `FRONTEND_URL`?
5. **Monitoring:** Do you have error tracking (Sentry, etc.) for email failure alerts?

---

**Design Status:** ✅ Revised and complete (v2)
**Ready for:** Review → Implementation planning
