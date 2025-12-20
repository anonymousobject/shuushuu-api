"""Tests for email service retry logic."""

from unittest.mock import AsyncMock, patch

import pytest
from aiosmtplib.errors import (
    SMTPAuthenticationError,
    SMTPConnectError,
    SMTPConnectTimeoutError,
    SMTPDataError,
    SMTPReadTimeoutError,
)

from app.services.email import send_email


@pytest.mark.asyncio
class TestEmailRetryLogic:
    """Test email retry logic for different SMTP errors."""

    async def test_send_email_success(self):
        """Test successful email send on first attempt."""
        with patch("app.services.email.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = None

            result = await send_email(
                to="test@example.com",
                subject="Test",
                body="Test body",
            )

            assert result is True
            assert mock_send.call_count == 1

    async def test_send_email_read_timeout_no_retry(self):
        """Test that SMTPReadTimeoutError does NOT retry (ambiguous state)."""
        with patch("app.services.email.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            # First attempt fails with read timeout
            mock_send.side_effect = SMTPReadTimeoutError("Timeout reading response")

            result = await send_email(
                to="test@example.com",
                subject="Test",
                body="Test body",
            )

            assert result is False
            # Should NOT retry - only 1 attempt
            assert mock_send.call_count == 1

    async def test_send_email_auth_error_no_retry(self):
        """Test that SMTPAuthenticationError does NOT retry (permanent failure)."""
        with patch("app.services.email.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            mock_send.side_effect = SMTPAuthenticationError(535, "Authentication failed")

            result = await send_email(
                to="test@example.com",
                subject="Test",
                body="Test body",
            )

            assert result is False
            # Should NOT retry - only 1 attempt
            assert mock_send.call_count == 1

    async def test_send_email_connection_error_retries(self):
        """Test that SMTPConnectError DOES retry (safe to retry)."""
        with patch("app.services.email.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            with patch("app.services.email.asyncio.sleep", new_callable=AsyncMock):
                # Fail 3 times with connection error
                mock_send.side_effect = SMTPConnectError("Cannot connect")

                result = await send_email(
                    to="test@example.com",
                    subject="Test",
                    body="Test body",
                )

                assert result is False
                # Should retry 3 times (max_retries=3)
                assert mock_send.call_count == 3

    async def test_send_email_connection_timeout_retries(self):
        """Test that SMTPConnectTimeoutError DOES retry (safe to retry)."""
        with patch("app.services.email.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            with patch("app.services.email.asyncio.sleep", new_callable=AsyncMock):
                # Fail 3 times with connection timeout
                mock_send.side_effect = SMTPConnectTimeoutError("Connection timeout")

                result = await send_email(
                    to="test@example.com",
                    subject="Test",
                    body="Test body",
                )

                assert result is False
                # Should retry 3 times
                assert mock_send.call_count == 3

    async def test_send_email_connection_error_succeeds_on_retry(self):
        """Test that connection error succeeds on second attempt."""
        with patch("app.services.email.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            with patch("app.services.email.asyncio.sleep", new_callable=AsyncMock):
                # Fail first, succeed second
                mock_send.side_effect = [
                    SMTPConnectError("Cannot connect"),
                    None,  # Success
                ]

                result = await send_email(
                    to="test@example.com",
                    subject="Test",
                    body="Test body",
                )

                assert result is True
                # Should have tried twice
                assert mock_send.call_count == 2

    async def test_send_email_other_smtp_error_no_retry(self):
        """Test that other SMTP errors do NOT retry (ambiguous or permanent)."""
        with patch("app.services.email.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            # Use SMTPDataError as example of "other" SMTP error
            mock_send.side_effect = SMTPDataError(550, "Recipient not found")

            result = await send_email(
                to="test@example.com",
                subject="Test",
                body="Test body",
            )

            assert result is False
            # Should NOT retry - only 1 attempt
            assert mock_send.call_count == 1

    async def test_send_email_unexpected_error_no_retry(self):
        """Test that unexpected non-SMTP errors do NOT retry."""
        with patch("app.services.email.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            mock_send.side_effect = RuntimeError("Unexpected error")

            result = await send_email(
                to="test@example.com",
                subject="Test",
                body="Test body",
            )

            assert result is False
            # Should NOT retry - only 1 attempt
            assert mock_send.call_count == 1

    async def test_send_email_exponential_backoff(self):
        """Test that retries use exponential backoff."""
        with patch("app.services.email.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            with patch("app.services.email.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                # Fail 3 times
                mock_send.side_effect = SMTPConnectError("Cannot connect")

                await send_email(
                    to="test@example.com",
                    subject="Test",
                    body="Test body",
                )

                # Should have called sleep twice (not on last attempt)
                # With exponential backoff: 2^0=1s, 2^1=2s
                assert mock_sleep.call_count == 2
                assert mock_sleep.call_args_list[0][0][0] == 1  # 2^0
                assert mock_sleep.call_args_list[1][0][0] == 2  # 2^1
