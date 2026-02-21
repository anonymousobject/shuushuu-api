"""Email sending service with SMTP."""

import asyncio
import html as html_escape
from email.message import EmailMessage

import aiosmtplib
from aiosmtplib.errors import (
    SMTPAuthenticationError,
    SMTPConnectError,
    SMTPConnectTimeoutError,
    SMTPException,
    SMTPReadTimeoutError,
)

from app.config import settings
from app.core.logging import get_logger
from app.models.user import Users

logger = get_logger(__name__)


async def send_email(
    to: str | list[str],
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

    # Retry logic: Conservative approach to avoid duplicate emails
    # Only retry connection failures where we know email wasn't queued
    max_retries = 3
    for attempt in range(max_retries):
        try:
            await aiosmtplib.send(
                message,
                hostname=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                username=settings.SMTP_USER or None,
                password=settings.SMTP_PASSWORD or None,
                use_tls=settings.SMTP_TLS,
                start_tls=settings.SMTP_STARTTLS,
                timeout=30,
            )
            logger.info(
                "email_sent_success",
                to=to,
                subject=subject,
                attempt=attempt + 1,
            )
            return True

        except SMTPReadTimeoutError as e:
            # NEVER RETRY - Email might already be queued on server
            # Better to lose an email than send duplicates
            logger.error(
                "email_send_timeout_after_data",
                to=to,
                subject=subject,
                error=str(e),
                error_type=type(e).__name__,
                note="Not retrying - ambiguous state, email might be delivered",
            )
            return False

        except SMTPAuthenticationError as e:
            # NEVER RETRY - Credentials are wrong, will never succeed
            logger.error(
                "email_auth_failed",
                to=to,
                subject=subject,
                error=str(e),
                error_type=type(e).__name__,
            )
            return False

        except (SMTPConnectError, SMTPConnectTimeoutError) as e:
            # SAFE TO RETRY - Connection never established, no data sent
            logger.warning(
                "email_connection_failed",
                to=to,
                subject=subject,
                attempt=attempt + 1,
                error=str(e),
                error_type=type(e).__name__,
            )
            if attempt < max_retries - 1:
                # Exponential backoff: 1s, 2s, 4s
                await asyncio.sleep(2**attempt)
            else:
                logger.error(
                    "email_connection_failed_all_retries",
                    to=to,
                    subject=subject,
                    error=str(e),
                )
                return False

        except SMTPException as e:
            # Other SMTP errors (e.g., recipient not found, mailbox full)
            # Don't retry - likely permanent or ambiguous state
            logger.error(
                "email_smtp_error",
                to=to,
                subject=subject,
                error=str(e),
                error_type=type(e).__name__,
            )
            return False

        except Exception as e:
            # Unexpected error
            logger.error(
                "email_send_unexpected_error",
                to=to,
                subject=subject,
                error=str(e),
                error_type=type(e).__name__,
            )
            return False

    return False


async def send_verification_email(user: Users, token: str) -> bool:
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


async def send_pm_notification_email(
    recipient: Users,
    sender_username: str,
    pm_subject: str,
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
    # Escape all user-provided content to prevent HTML injection
    safe_sender = html_escape.escape(sender_username)
    safe_recipient = html_escape.escape(recipient.username)
    safe_subject = html_escape.escape(pm_subject)

    # Build URLs
    messages_url = f"{settings.FRONTEND_URL}/messages"
    settings_url = f"{settings.FRONTEND_URL}/settings"

    subject = f"New PM from {safe_sender}: {safe_subject}"

    # Plain text body
    body = f"""Hi {safe_recipient},

You have a new private message from {safe_sender}.

Subject: {safe_subject}

View your messages: {messages_url}

---
You can disable private message email notifications in your account settings:
{settings_url}
"""

    # HTML body
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .button {{
            display: inline-block;
            padding: 12px 24px;
            background-color: #4CAF50;
            color: white;
            text-decoration: none;
            border-radius: 4px;
            margin: 20px 0;
        }}
        .footer {{
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #ddd;
            font-size: 12px;
            color: #666;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h2>New Private Message</h2>
        <p>Hi {safe_recipient},</p>
        <p>You have a new private message from <strong>{safe_sender}</strong>.</p>
        <p><strong>Subject:</strong> {safe_subject}</p>
        <p><a href="{messages_url}" class="button">View Your Messages</a></p>
        <div class="footer">
            <p>You can disable private message email notifications in your
            <a href="{settings_url}">account settings</a>.</p>
        </div>
    </div>
</body>
</html>
"""

    return await send_email(to=recipient.email, subject=subject, body=body, html=html)
