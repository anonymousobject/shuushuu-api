# Password Reset Feature Design

## Summary

Add forgot/reset password functionality using the same pattern as email verification: random token stored as SHA256 hash on the Users table, sent via email, validated on use.

## Decisions

- **Token expiry:** 1 hour
- **Session handling:** Revoke all refresh tokens on successful reset
- **Reset form requires:** email + token + new password
- **Storage approach:** Fields on Users table (not a separate table)
- **Token type:** `secrets.token_urlsafe(32)`, stored as SHA256 hash

## Database

Three new nullable fields on `Users`:

| Field | Type | Notes |
|-------|------|-------|
| `password_reset_token` | `str \| None` (max 64) | SHA256 hash, indexed |
| `password_reset_sent_at` | `datetime \| None` | For rate limiting (5 min cooldown) |
| `password_reset_expires_at` | `datetime \| None` | 1 hour from request |

Alembic migration adds these fields + index `idx_password_reset_token`.

## API Endpoints

### POST `/api/v1/auth/forgot-password` (unauthenticated)

- **Input:** `{ "email": "user@example.com" }`
- **Output:** Always 200 with generic message (no email enumeration)
- **Rate limit:** One reset email per 5 minutes per email
- Generates token, stores SHA256 hash + timestamps on user
- Queues background job to send email
- Silently does nothing if email not found or account inactive

### POST `/api/v1/auth/reset-password` (unauthenticated)

- **Input:** `{ "email": "user@example.com", "token": "...", "new_password": "..." }`
- Looks up user by email, compares SHA256(token) against stored hash
- Validates token not expired (1 hour)
- Validates password strength via `validate_password_strength()`
- Hashes new password with bcrypt, sets `password_type = "bcrypt"`
- Clears the three reset fields
- Revokes all refresh tokens
- Returns success message

## Email

- New `send_password_reset_email()` in `app/services/email.py` (follows `send_verification_email()` pattern)
- Link format: `{FRONTEND_URL}/reset-password?token={raw_token}&email={email}`
- New `send_password_reset_email_job()` in `app/tasks/email_jobs.py`, registered in ARQ worker

## Schemas

In `app/schemas/auth.py`:
- `ForgotPasswordRequest` — `email: EmailStr`
- `ResetPasswordRequest` — `email: EmailStr`, `token: str`, `new_password: str`

## Security

- Generic response on forgot-password (no email enumeration)
- Token stored hashed (SHA256), never plaintext
- 1 hour expiry
- One active reset per user (new request overwrites old token)
- Revoke all sessions on successful reset
- Rate limit on requesting resets (5 min cooldown)
- Password strength validation on new password
