# Private Message Email Notifications - Design Document

**Date:** 2025-12-15
**Status:** Approved for Implementation

## Overview

Add email notifications for new private messages using the existing ARQ background task system. Users receive an email when they get a new PM, with the ability to disable notifications via account settings.

## Requirements

- Send email notification when user receives a private message
- Respect user's `email_pm_pref` setting (existing database field)
- Only send to verified email addresses (`email_verified = 1`)
- Use background task queue (ARQ) for non-blocking email delivery
- Support markdown in PM content (already implemented)
- Provide email template with subject line and link to view message
- Expose `email_pm_pref` in user settings API

## Architecture

### Email Notification Flow

1. User sends PM via `POST /privmsgs` endpoint
2. PM is saved to database (existing behavior)
3. Background task is queued: `send_pm_notification(privmsg_id)`
4. API responds immediately (non-blocking)
5. ARQ worker picks up task, checks conditions, sends email

### Skip Conditions

Email will NOT be sent if:
- Recipient has `email_pm_pref = 0` (disabled notifications)
- Recipient's email is not verified (`email_verified != 1`)
- Email sending fails (logged as error, does not retry)

### No Rate Limiting

Initially no rate limiting on email notifications. Users can disable via `email_pm_pref` if they receive too many.

## Email Template Design

### Subject Line
```
New PM from {sender_username}: {pm_subject}
```

### Plain Text Body
```
Hi {recipient_username},

You have a new private message from {sender_username}.

Subject: {pm_subject}

View your messages: {FRONTEND_URL}/messages

---
You can disable private message email notifications in your account settings:
{FRONTEND_URL}/settings
```

### HTML Body
```html
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body { font-family: Arial, sans-serif; line-height: 1.6; color: #333; }
        .container { max-width: 600px; margin: 0 auto; padding: 20px; }
        .button {
            display: inline-block;
            padding: 12px 24px;
            background-color: #4CAF50;
            color: white;
            text-decoration: none;
            border-radius: 4px;
            margin: 20px 0;
        }
        .footer {
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #ddd;
            font-size: 12px;
            color: #666;
        }
    </style>
</head>
<body>
    <div class="container">
        <h2>New Private Message</h2>
        <p>Hi {recipient_username},</p>
        <p>You have a new private message from <strong>{sender_username}</strong>.</p>
        <p><strong>Subject:</strong> {pm_subject}</p>
        <p><a href="{messages_url}" class="button">View Your Messages</a></p>
        <div class="footer">
            <p>You can disable private message email notifications in your
            <a href="{settings_url}">account settings</a>.</p>
        </div>
    </div>
</body>
</html>
```

### Security Considerations
- Escape all user-provided content (sender_username, recipient_username, pm_subject) using `html.escape()`
- No markdown rendering in email (just showing subject)
- URLs are from settings (FRONTEND_URL), not user input

## Database Schema

### Users Table
The `email_pm_pref` field already exists:
- Type: `tinyint(1)`
- Default: `1` (enabled)
- Location: `app/models/user.py:141`
- No migration needed ✓

### Private Messages
Existing `privmsgs` table is used. No schema changes needed.

## API Changes

### User Schemas (`app/schemas/user.py`)

**New schema - UserPrivateResponse:**
```python
class UserPrivateResponse(UserResponse):
    """Schema for authenticated user's own profile - includes private settings"""

    email: EmailStr  # User's own email
    email_verified: bool  # Email verification status
    email_pm_pref: int  # PM email notification preference (0=disabled, 1=enabled)
```

**Update UserUpdate schema:**
```python
class UserUpdate(BaseModel):
    """Schema for updating a user profile - all fields optional"""

    location: str | None = None
    website: str | None = None
    interests: str | None = None
    user_title: str | None = None
    gender: str | None = None
    email: EmailStr | None = None
    password: str | None = None
    email_pm_pref: int | None = None  # NEW: Allow toggling PM email notifications

    @field_validator("email_pm_pref")
    @classmethod
    def validate_email_pm_pref(cls, v: int | None) -> int | None:
        """Validate email_pm_pref is 0 or 1"""
        if v is not None and v not in [0, 1]:
            raise ValueError("email_pm_pref must be 0 or 1")
        return v
```

### User Endpoints (`app/api/v1/users.py`)

Update response models for authenticated user endpoints:
```python
@router.get("/me", response_model=UserPrivateResponse)  # Changed from UserResponse
@router.patch("/me", response_model=UserPrivateResponse)  # Changed from UserResponse
```

All other user endpoints (`GET /users/{user_id}`, etc.) continue using `UserResponse` which doesn't include private fields like `email_pm_pref`.

### Privacy Model
- `email_pm_pref` is only visible to the authenticated user themselves (via `/users/me`)
- Not visible when viewing other users' profiles
- Can be updated via `PATCH /users/me`

## Implementation Components

### 1. Email Service (`app/services/email.py`)

**New function:**
```python
async def send_pm_notification_email(
    recipient: Users,
    sender_username: str,
    pm_subject: str
) -> bool:
    """
    Send email notification for new private message.

    Args:
        recipient: User receiving the PM
        sender_username: Username of PM sender
        pm_subject: Subject line of the PM

    Returns:
        True if email sent successfully, False otherwise
    """
```

Responsibilities:
- Generate email subject: `"New PM from {sender_username}: {pm_subject}"`
- Render HTML and plain text bodies
- Escape all user-provided content
- Build URLs: `{FRONTEND_URL}/messages` and `{FRONTEND_URL}/settings`
- Call existing `send_email()` function

### 2. Background Task (`app/tasks/worker.py`)

**New task:**
```python
async def send_pm_notification(ctx: dict, privmsg_id: int) -> None:
    """
    Background task to send PM notification email.

    Args:
        ctx: ARQ context
        privmsg_id: ID of the private message
    """
```

Responsibilities:
- Fetch PM with sender/recipient info (single query with joins)
- Check conditions: `email_pm_pref = 1` AND `email_verified = 1`
- Call `send_pm_notification_email()` if conditions met
- Log outcome (sent, skipped, failed) with structured logging

Database query should join:
- `privmsgs` table
- `users` table (sender - for username)
- `users` table (recipient - for email, email_verified, email_pm_pref)

### 3. Privmsg Endpoint (`app/api/v1/privmsgs.py`)

**Update `POST /privmsgs` endpoint:**

After creating PM (line 40, after `db.refresh()`), enqueue the task:
```python
await arq_queue.enqueue_job('send_pm_notification', privmsg_id=new_privmsg.privmsg_id)
```

Note: Will need to add ARQ queue dependency injection to this endpoint.

### 4. ARQ Worker Registration

Register new task in ARQ worker configuration to make it discoverable by the worker process.

## Testing Strategy

### Email Service Tests
- Test `send_pm_notification_email()` renders template correctly
- Test escaping of user-provided content (username, subject)
- Test both HTML and plain text bodies are generated
- Test URL construction

### Background Task Tests
- Test `send_pm_notification()` sends email when conditions met
- Test skips when `email_pm_pref = 0`
- Test skips when `email_verified = 0`
- Test handles missing privmsg_id gracefully
- Test logs correctly (sent, skipped, failed)

### API Endpoint Tests - Privmsgs
- Test PM creation enqueues background task
- Verify task is queued with correct privmsg_id
- Test API responds immediately (doesn't block on email)

### User Schema Tests
- Test `GET /users/me` includes `email_pm_pref`, `email`, `email_verified`
- Test `GET /users/{other_user_id}` excludes `email_pm_pref`
- Test `PATCH /users/me` can update `email_pm_pref`
- Test validation rejects values other than 0 or 1

## Implementation Checklist

- [ ] Add `UserPrivateResponse` schema to `app/schemas/user.py`
- [ ] Update `UserUpdate` schema with `email_pm_pref` field + validation
- [ ] Update `/users/me` endpoints to use `UserPrivateResponse`
- [ ] Add `send_pm_notification_email()` to `app/services/email.py`
- [ ] Add `send_pm_notification()` task to `app/tasks/worker.py`
- [ ] Update `POST /privmsgs` endpoint to enqueue task
- [ ] Update ARQ worker to register new task
- [ ] Write tests for all components
- [ ] Manual testing of email delivery
- [ ] Update frontend `/settings` page to include PM email toggle (frontend work, not backend)

## Future Enhancements (Out of Scope)

- Rate limiting on email notifications (e.g., max 1 email per 5 minutes)
- Batched notifications (digest emails)
- Email preview of PM content (currently just subject + link)
- Rich email templates with branding
- Preference for other user settings in `UserPrivateResponse` (timezone, spoiler_warning_pref, etc.)
