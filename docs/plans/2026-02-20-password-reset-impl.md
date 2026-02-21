# Password Reset Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add forgot-password and reset-password endpoints so users can reset their password via email token.

**Architecture:** Follows the existing email verification pattern exactly — `secrets.token_urlsafe(32)` stored as SHA256 hash on the Users table, emailed via ARQ background job, validated and consumed on use.

**Tech Stack:** FastAPI, SQLModel, Alembic, ARQ (async Redis queue), aiosmtplib

**Design doc:** `docs/plans/2026-02-20-password-reset-design.md`

---

### Task 1: Add password reset fields to Users model + Alembic migration

**Files:**
- Modify: `app/models/user.py:141` (after email verification fields)
- Create: `alembic/versions/xxxx_add_password_reset_fields.py`

**Step 1: Add fields to Users model**

In `app/models/user.py`, add these three fields after line 141 (after `email_verification_expires_at`):

```python
    # Password reset
    password_reset_token: str | None = Field(default=None, max_length=64)
    password_reset_sent_at: datetime | None = Field(default=None)
    password_reset_expires_at: datetime | None = Field(default=None)
```

**Step 2: Create Alembic migration**

Run: `uv run alembic revision -m "add password reset fields"`

Edit the generated migration file to contain:

```python
def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('password_reset_token', sa.String(64), nullable=True),
    )
    op.add_column(
        'users',
        sa.Column('password_reset_sent_at', sa.DateTime(), nullable=True),
    )
    op.add_column(
        'users',
        sa.Column('password_reset_expires_at', sa.DateTime(), nullable=True),
    )
    op.create_index('idx_password_reset_token', 'users', ['password_reset_token'])


def downgrade() -> None:
    op.drop_index('idx_password_reset_token', table_name='users')
    op.drop_column('users', 'password_reset_expires_at')
    op.drop_column('users', 'password_reset_sent_at')
    op.drop_column('users', 'password_reset_token')
```

**Step 3: Run migration**

Run: `uv run alembic upgrade head`
Expected: Migration applies successfully.

**Step 4: Commit**

```bash
git add app/models/user.py alembic/versions/*add_password_reset_fields*
git commit -m "feat: add password reset fields to users table"
```

---

### Task 2: Add request schemas

**Files:**
- Modify: `app/schemas/auth.py` (after `PasswordChangeRequest`)

**Step 1: Write the failing test**

In `tests/unit/test_schemas.py` (or create if needed), add:

```python
import pytest
from pydantic import ValidationError

from app.schemas.auth import ForgotPasswordRequest, ResetPasswordRequest


class TestForgotPasswordRequest:
    def test_valid_email(self):
        req = ForgotPasswordRequest(email="user@example.com")
        assert req.email == "user@example.com"

    def test_invalid_email_rejected(self):
        with pytest.raises(ValidationError):
            ForgotPasswordRequest(email="not-an-email")


class TestResetPasswordRequest:
    def test_valid_request(self):
        req = ResetPasswordRequest(
            email="user@example.com",
            token="abc123",
            new_password="NewPassword123!",
        )
        assert req.email == "user@example.com"
        assert req.token == "abc123"

    def test_weak_password_rejected(self):
        with pytest.raises(ValidationError, match="special character"):
            ResetPasswordRequest(
                email="user@example.com",
                token="abc123",
                new_password="weakpassword",
            )
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_schemas.py -v -k "ForgotPassword or ResetPassword"`
Expected: FAIL — `ImportError: cannot import name 'ForgotPasswordRequest'`

**Step 3: Write minimal implementation**

In `app/schemas/auth.py`, after the `PasswordChangeRequest` class (line 73), add:

```python
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
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_schemas.py -v -k "ForgotPassword or ResetPassword"`
Expected: PASS

**Step 5: Commit**

```bash
git add app/schemas/auth.py tests/unit/test_schemas.py
git commit -m "feat: add forgot/reset password request schemas"
```

---

### Task 3: Add password reset email template + background job

**Files:**
- Modify: `app/services/email.py` (after `send_verification_email`)
- Modify: `app/tasks/email_jobs.py` (add new job)
- Modify: `app/tasks/worker.py` (register new job)

**Step 1: Write the failing test for the email template**

In `tests/unit/test_email.py` (create if needed):

```python
from unittest.mock import AsyncMock, patch

import pytest

from app.models.user import Users
from app.services.email import send_password_reset_email


@pytest.mark.unit
class TestSendPasswordResetEmail:
    @patch("app.services.email.send_email", new_callable=AsyncMock)
    async def test_sends_email_with_reset_link(self, mock_send):
        mock_send.return_value = True
        user = Users(
            user_id=1,
            username="testuser",
            email="test@example.com",
            password="hash",
            salt="",
        )
        result = await send_password_reset_email(user, "raw_token_abc")

        assert result is True
        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args
        assert call_kwargs[1]["to"] == "test@example.com"
        assert "raw_token_abc" in call_kwargs[1]["body"]
        assert "test%40example.com" in call_kwargs[1]["body"] or "test@example.com" in call_kwargs[1]["body"]
        assert "1 hour" in call_kwargs[1]["body"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_email.py::TestSendPasswordResetEmail -v`
Expected: FAIL — `ImportError: cannot import name 'send_password_reset_email'`

**Step 3: Implement `send_password_reset_email`**

In `app/services/email.py`, after `send_verification_email` (after line 207), add:

```python
async def send_password_reset_email(user: Users, token: str) -> bool:
    """
    Send password reset link to user.

    Args:
        user: User object
        token: Raw reset token (not hashed)

    Returns:
        True if email sent successfully, False otherwise
    """
    from urllib.parse import quote

    reset_url = f"{settings.FRONTEND_URL}/reset-password?token={token}&email={quote(user.email)}"

    subject = "Reset your password"
    body = f"""Hi {user.username},

We received a request to reset your password. Click the link below:

{reset_url}

This link will expire in 1 hour.

If you didn't request this, you can safely ignore this email. Your password will not change.
"""

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
        <h2>Reset Your Password</h2>
        <p>Hi {user.username},</p>
        <p>We received a request to reset your password.</p>
        <p><a href="{reset_url}" class="button">Reset Password</a></p>
        <p>Or copy this link into your browser:</p>
        <p><code>{reset_url}</code></p>
        <p><small>This link will expire in 1 hour.</small></p>
        <p><small>If you didn't request this, you can safely ignore this email.</small></p>
    </div>
</body>
</html>
"""

    return await send_email(to=user.email, subject=subject, body=body, html=html)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_email.py::TestSendPasswordResetEmail -v`
Expected: PASS

**Step 5: Add background job**

In `app/tasks/email_jobs.py`, add import at top:

```python
from app.services.email import send_password_reset_email, send_verification_email
```

(Replace the existing single import.)

Then add after `send_verification_email_job`:

```python
async def send_password_reset_email_job(ctx: dict[str, Any], user_id: int, token: str) -> None:
    """
    Background task to send password reset email.

    Args:
        ctx: ARQ context dict
        user_id: ID of the user
        token: Raw reset token (not hashed)

    Raises:
        Retry: If database query or email fails (will retry up to max_tries)
    """
    bind_context(task="send_password_reset_email", user_id=user_id)

    try:
        async with get_async_session() as db:
            user_query = select(Users).where(Users.user_id == user_id)  # type: ignore[arg-type]
            user_result = await db.execute(user_query)
            user = user_result.scalar_one_or_none()

            if not user:
                logger.warning("password_reset_email_user_not_found", user_id=user_id)
                return

            success = await send_password_reset_email(user=user, token=token)

            if success:
                logger.info(
                    "password_reset_email_sent",
                    user_id=user_id,
                    email=user.email,
                )
            else:
                logger.error(
                    "password_reset_email_failed",
                    user_id=user_id,
                    email=user.email,
                )
                raise Retry(defer=ctx["job_try"] * 5)

    except Exception as e:
        if isinstance(e, Retry):
            raise
        logger.error(
            "password_reset_email_task_error",
            user_id=user_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise Retry(defer=ctx["job_try"] * 5) from e
```

**Step 6: Register job in worker**

In `app/tasks/worker.py`:

1. Update import (line 25): add `send_password_reset_email_job`
   ```python
   from app.tasks.email_jobs import send_password_reset_email_job, send_verification_email_job
   ```

2. Add to `functions` list (after line 92):
   ```python
   func(send_password_reset_email_job, max_tries=3),
   ```

**Step 7: Commit**

```bash
git add app/services/email.py app/tasks/email_jobs.py app/tasks/worker.py tests/unit/test_email.py
git commit -m "feat: add password reset email template and background job"
```

---

### Task 4: Implement forgot-password endpoint

**Files:**
- Modify: `app/api/v1/auth.py` (add endpoint after `resend_verification`)
- Test: `tests/api/v1/test_auth.py`

**Step 1: Write the failing tests**

In `tests/api/v1/test_auth.py`, add a new test class:

```python
@pytest.mark.api
class TestForgotPassword:
    """Tests for POST /api/v1/auth/forgot-password endpoint."""

    async def test_forgot_password_valid_email(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test forgot password with valid email returns 200 and generic message."""
        user = Users(
            username="forgotuser",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="forgot@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        response = await client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "forgot@example.com"},
        )
        assert response.status_code == 200
        assert "reset" in response.json()["message"].lower()

        # Verify token was stored (hashed) in DB
        await db_session.refresh(user)
        assert user.password_reset_token is not None
        assert user.password_reset_expires_at is not None

    async def test_forgot_password_nonexistent_email(self, client: AsyncClient):
        """Test forgot password with unknown email still returns 200 (no enumeration)."""
        response = await client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "nobody@example.com"},
        )
        assert response.status_code == 200
        # Same generic message — no information leakage
        assert "reset" in response.json()["message"].lower()

    async def test_forgot_password_rate_limited(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test forgot password rate limits to 1 request per 5 minutes."""
        user = Users(
            username="ratelimituser",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="ratelimit@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()

        # First request: should succeed
        response1 = await client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "ratelimit@example.com"},
        )
        assert response1.status_code == 200

        # Second request immediately: should be rate limited
        response2 = await client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "ratelimit@example.com"},
        )
        assert response2.status_code == 429

    async def test_forgot_password_inactive_user(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test forgot password for inactive user returns 200 but does nothing."""
        user = Users(
            username="inactiveuser",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="inactive@example.com",
            active=0,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        response = await client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "inactive@example.com"},
        )
        assert response.status_code == 200

        # Token should NOT be set for inactive user
        await db_session.refresh(user)
        assert user.password_reset_token is None
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_auth.py::TestForgotPassword -v`
Expected: FAIL — 404 (endpoint doesn't exist)

**Step 3: Implement the endpoint**

In `app/api/v1/auth.py`, update imports at top — add to the `app.schemas.auth` import block:

```python
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    PasswordChangeRequest,
    RefreshRequest,
    ResetPasswordRequest,
    TokenResponse,
)
```

Also add `enqueue_job` import:

```python
from app.tasks.queue import enqueue_job
```

Then add the endpoint after `resend_verification` (after line 719):

```python
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
    if not user or not user.active:
        return MessageResponse(message=generic_message)

    # Rate limit: 1 reset per 5 minutes
    if user.password_reset_sent_at:
        time_since_last = (
            datetime.now(UTC).replace(tzinfo=None) - user.password_reset_sent_at
        )
        if time_since_last < timedelta(minutes=5):
            remaining_seconds = int((timedelta(minutes=5) - time_since_last).total_seconds())
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Please wait {remaining_seconds} seconds before requesting another reset email",
            )

    # Generate token
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    # Store hashed token + timestamps
    user.password_reset_token = token_hash
    user.password_reset_sent_at = datetime.now(UTC)
    user.password_reset_expires_at = datetime.now(UTC) + timedelta(hours=1)
    await db.commit()

    # Queue email
    await enqueue_job(
        "send_password_reset_email_job",
        user_id=user.user_id,
        token=raw_token,
    )

    return MessageResponse(message=generic_message)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_auth.py::TestForgotPassword -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/api/v1/auth.py tests/api/v1/test_auth.py
git commit -m "feat: add forgot-password endpoint"
```

---

### Task 5: Implement reset-password endpoint

**Files:**
- Modify: `app/api/v1/auth.py` (add endpoint after `forgot_password`)
- Test: `tests/api/v1/test_auth.py`

**Step 1: Write the failing tests**

In `tests/api/v1/test_auth.py`, add:

```python
@pytest.mark.api
class TestResetPassword:
    """Tests for POST /api/v1/auth/reset-password endpoint."""

    async def _create_user_with_reset_token(self, db_session: AsyncSession) -> tuple[Users, str]:
        """Helper: create a user and set a valid reset token. Returns (user, raw_token)."""
        import hashlib
        import secrets
        from datetime import UTC, datetime, timedelta

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        user = Users(
            username="resetuser",
            password=get_password_hash("OldPassword123!"),
            password_type="bcrypt",
            salt="",
            email="reset@example.com",
            active=1,
            password_reset_token=token_hash,
            password_reset_sent_at=datetime.now(UTC),
            password_reset_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user, raw_token

    async def test_reset_password_success(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test successful password reset."""
        user, raw_token = await self._create_user_with_reset_token(db_session)

        response = await client.post(
            "/api/v1/auth/reset-password",
            json={
                "email": "reset@example.com",
                "token": raw_token,
                "new_password": "NewPassword456!",
            },
        )
        assert response.status_code == 200

        # Verify token fields are cleared
        await db_session.refresh(user)
        assert user.password_reset_token is None
        assert user.password_reset_expires_at is None

        # Verify can login with new password
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "resetuser", "password": "NewPassword456!"},
        )
        assert login_response.status_code == 200

        # Verify old password no longer works
        old_login = await client.post(
            "/api/v1/auth/login",
            json={"username": "resetuser", "password": "OldPassword123!"},
        )
        assert old_login.status_code == 401

    async def test_reset_password_invalid_token(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test reset with wrong token returns 400."""
        await self._create_user_with_reset_token(db_session)

        response = await client.post(
            "/api/v1/auth/reset-password",
            json={
                "email": "reset@example.com",
                "token": "wrong_token",
                "new_password": "NewPassword456!",
            },
        )
        assert response.status_code == 400

    async def test_reset_password_expired_token(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test reset with expired token returns 400."""
        import hashlib
        import secrets
        from datetime import UTC, datetime, timedelta

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        user = Users(
            username="expireduser",
            password=get_password_hash("OldPassword123!"),
            password_type="bcrypt",
            salt="",
            email="expired@example.com",
            active=1,
            password_reset_token=token_hash,
            password_reset_sent_at=datetime.now(UTC) - timedelta(hours=2),
            password_reset_expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        db_session.add(user)
        await db_session.commit()

        response = await client.post(
            "/api/v1/auth/reset-password",
            json={
                "email": "expired@example.com",
                "token": raw_token,
                "new_password": "NewPassword456!",
            },
        )
        assert response.status_code == 400
        assert "expired" in response.json()["detail"].lower()

    async def test_reset_password_wrong_email(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test reset with wrong email returns 400."""
        _user, raw_token = await self._create_user_with_reset_token(db_session)

        response = await client.post(
            "/api/v1/auth/reset-password",
            json={
                "email": "wrong@example.com",
                "token": raw_token,
                "new_password": "NewPassword456!",
            },
        )
        assert response.status_code == 400

    async def test_reset_password_weak_password(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test reset with weak password returns 422 (schema validation)."""
        _user, raw_token = await self._create_user_with_reset_token(db_session)

        response = await client.post(
            "/api/v1/auth/reset-password",
            json={
                "email": "reset@example.com",
                "token": raw_token,
                "new_password": "weak",
            },
        )
        assert response.status_code == 422

    async def test_reset_password_revokes_sessions(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that password reset revokes all refresh tokens."""
        user, raw_token = await self._create_user_with_reset_token(db_session)

        # Login to create a refresh token
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "resetuser", "password": "OldPassword123!"},
        )
        assert login_response.status_code == 200

        # Verify refresh token exists
        from sqlalchemy import select as sa_select
        from app.models.refresh_token import RefreshTokens
        token_count = await db_session.execute(
            sa_select(RefreshTokens).where(RefreshTokens.user_id == user.user_id)
        )
        assert token_count.scalar_one_or_none() is not None

        # Reset password
        response = await client.post(
            "/api/v1/auth/reset-password",
            json={
                "email": "reset@example.com",
                "token": raw_token,
                "new_password": "NewPassword456!",
            },
        )
        assert response.status_code == 200

        # Verify refresh tokens are revoked
        token_count = await db_session.execute(
            sa_select(RefreshTokens).where(RefreshTokens.user_id == user.user_id)
        )
        assert token_count.scalar_one_or_none() is None
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_auth.py::TestResetPassword -v`
Expected: FAIL — 404 (endpoint doesn't exist)

**Step 3: Implement the endpoint**

In `app/api/v1/auth.py`, after the `forgot_password` endpoint, add:

```python
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

    if not user or not user.password_reset_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    # Verify token matches
    token_hash = hashlib.sha256(request_data.token.encode()).hexdigest()
    if user.password_reset_token != token_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    # Check expiration
    if not user.password_reset_expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    if user.password_reset_expires_at < datetime.now(UTC).replace(tzinfo=None):
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

    logger.info("password_reset_complete", user_id=user.user_id, username=user.username)

    return MessageResponse(
        message="Password reset successfully. Please login with your new password."
    )
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_auth.py::TestResetPassword -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/api/v1/auth.py tests/api/v1/test_auth.py
git commit -m "feat: add reset-password endpoint"
```

---

### Task 6: Run full test suite and verify

**Step 1: Run all tests**

Run: `uv run pytest -x -v`
Expected: All tests pass, no regressions.

**Step 2: Run linting**

Run: `uv run ruff check app/ tests/`
Expected: No errors.

**Step 3: Final commit if any fixups needed**

Only if tests or linting revealed issues that needed fixing.
