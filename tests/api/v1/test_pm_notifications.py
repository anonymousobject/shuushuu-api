"""
Tests for private message email notifications.

These tests cover:
- Email service: send_pm_notification_email function
- API integration: User schemas with email_pm_pref updates
"""

from unittest.mock import patch

import pytest

from app.services.email import send_pm_notification_email
from app.models.user import Users


@pytest.mark.asyncio
class TestSendPmNotificationEmail:
    """Tests for send_pm_notification_email function."""

    async def test_send_pm_notification_email_escapes_html(self):
        """Test that user-provided content is properly escaped."""
        recipient = Users(
            user_id=1,
            username="recipient<script>",
            password="hash",
            password_type="bcrypt",
            salt="",
            email="recipient@example.com",
            email_verified=1,
            email_pm_pref=1,
        )

        sender_username = 'attacker<img src=x onerror="alert(1)">'
        pm_subject = "<script>alert('xss')</script>"

        # Mock send_email to capture the email content
        with patch("app.services.email.send_email") as mock_send_email:
            mock_send_email.return_value = True

            result = await send_pm_notification_email(
                recipient=recipient,
                sender_username=sender_username,
                pm_subject=pm_subject,
            )

            assert result is True
            mock_send_email.assert_called_once()

            # Extract the email call arguments
            call_args = mock_send_email.call_args
            html_body = call_args.kwargs.get("html") or call_args[0][3]

            # Verify HTML entities are escaped, not raw HTML
            assert "&lt;script&gt;" in html_body
            assert "&lt;img" in html_body
            assert 'onerror="alert(1)"' not in html_body
            assert "<script>" not in html_body

    async def test_send_pm_notification_email_includes_urls(self):
        """Test that email includes correct frontend URLs."""
        recipient = Users(
            user_id=1,
            username="recipient",
            password="hash",
            password_type="bcrypt",
            salt="",
            email="recipient@example.com",
            email_verified=1,
            email_pm_pref=1,
        )

        with patch("app.services.email.send_email") as mock_send_email:
            mock_send_email.return_value = True

            await send_pm_notification_email(
                recipient=recipient,
                sender_username="sender",
                pm_subject="Test",
            )

            call_args = mock_send_email.call_args
            body = call_args.kwargs.get("body") or call_args[0][2]
            html_body = call_args.kwargs.get("html") or call_args[0][3]

            # Verify URLs are present (using localhost:3000 from test config)
            assert "/messages" in body
            assert "/settings" in body
            assert "/messages" in html_body
            assert "/settings" in html_body

    async def test_send_pm_notification_email_returns_false_on_failure(self):
        """Test that function returns False if email sending fails."""
        recipient = Users(
            user_id=1,
            username="recipient",
            password="hash",
            password_type="bcrypt",
            salt="",
            email="recipient@example.com",
            email_verified=1,
            email_pm_pref=1,
        )

        with patch("app.services.email.send_email") as mock_send_email:
            mock_send_email.return_value = False

            result = await send_pm_notification_email(
                recipient=recipient,
                sender_username="sender",
                pm_subject="Test",
            )

            assert result is False

    async def test_send_pm_notification_email_subject_line(self):
        """Test that email subject includes sender and PM subject."""
        recipient = Users(
            user_id=1,
            username="recipient",
            password="hash",
            password_type="bcrypt",
            salt="",
            email="recipient@example.com",
            email_verified=1,
            email_pm_pref=1,
        )

        with patch("app.services.email.send_email") as mock_send_email:
            mock_send_email.return_value = True

            await send_pm_notification_email(
                recipient=recipient,
                sender_username="alice",
                pm_subject="Important Topic",
            )

            call_args = mock_send_email.call_args
            subject = call_args.kwargs.get("subject") or call_args[0][1]

            # Subject should include both sender and PM subject
            assert "alice" in subject
            assert "Important Topic" in subject
            assert "New PM from" in subject
