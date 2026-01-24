"""Tests for email service retry logic and SMTP configuration."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiosmtplib.errors import (
    SMTPAuthenticationError,
    SMTPConnectError,
    SMTPConnectTimeoutError,
    SMTPDataError,
    SMTPReadTimeoutError,
)
from pydantic import ValidationError

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


@pytest.mark.asyncio
class TestEmailSmtpParameters:
    """Test that SMTP parameters are passed correctly to aiosmtplib."""

    async def test_send_email_passes_starttls_parameter(self):
        """Test that start_tls parameter is passed to aiosmtplib.send."""
        with patch("app.services.email.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            with patch("app.services.email.settings") as mock_settings:
                mock_settings.SMTP_HOST = "localhost"
                mock_settings.SMTP_PORT = 587
                mock_settings.SMTP_USER = "user"
                mock_settings.SMTP_PASSWORD = "pass"
                mock_settings.SMTP_TLS = False
                mock_settings.SMTP_STARTTLS = True
                mock_settings.SMTP_FROM_NAME = "Test"
                mock_settings.SMTP_FROM_EMAIL = "test@example.com"

                await send_email(to="recipient@example.com", subject="Test", body="Body")

                mock_send.assert_called_once()
                call_kwargs = mock_send.call_args.kwargs
                assert call_kwargs["start_tls"] is True
                assert call_kwargs["use_tls"] is False

    async def test_send_email_passes_use_tls_parameter(self):
        """Test that use_tls parameter is passed to aiosmtplib.send."""
        with patch("app.services.email.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            with patch("app.services.email.settings") as mock_settings:
                mock_settings.SMTP_HOST = "smtp.example.com"
                mock_settings.SMTP_PORT = 465
                mock_settings.SMTP_USER = "user"
                mock_settings.SMTP_PASSWORD = "pass"
                mock_settings.SMTP_TLS = True
                mock_settings.SMTP_STARTTLS = False
                mock_settings.SMTP_FROM_NAME = "Test"
                mock_settings.SMTP_FROM_EMAIL = "test@example.com"

                await send_email(to="recipient@example.com", subject="Test", body="Body")

                mock_send.assert_called_once()
                call_kwargs = mock_send.call_args.kwargs
                assert call_kwargs["use_tls"] is True
                assert call_kwargs["start_tls"] is False

    async def test_send_email_empty_credentials_passed_as_none(self):
        """Test that empty username/password are passed as None."""
        with patch("app.services.email.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            with patch("app.services.email.settings") as mock_settings:
                mock_settings.SMTP_HOST = "localhost"
                mock_settings.SMTP_PORT = 25
                mock_settings.SMTP_USER = ""  # Empty string
                mock_settings.SMTP_PASSWORD = ""  # Empty string
                mock_settings.SMTP_TLS = False
                mock_settings.SMTP_STARTTLS = False
                mock_settings.SMTP_FROM_NAME = "Test"
                mock_settings.SMTP_FROM_EMAIL = "test@example.com"

                await send_email(to="recipient@example.com", subject="Test", body="Body")

                mock_send.assert_called_once()
                call_kwargs = mock_send.call_args.kwargs
                assert call_kwargs["username"] is None
                assert call_kwargs["password"] is None

    async def test_send_email_credentials_passed_when_set(self):
        """Test that credentials are passed when configured."""
        with patch("app.services.email.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            with patch("app.services.email.settings") as mock_settings:
                mock_settings.SMTP_HOST = "smtp.example.com"
                mock_settings.SMTP_PORT = 587
                mock_settings.SMTP_USER = "myuser"
                mock_settings.SMTP_PASSWORD = "mypassword"
                mock_settings.SMTP_TLS = False
                mock_settings.SMTP_STARTTLS = True
                mock_settings.SMTP_FROM_NAME = "Test"
                mock_settings.SMTP_FROM_EMAIL = "test@example.com"

                await send_email(to="recipient@example.com", subject="Test", body="Body")

                mock_send.assert_called_once()
                call_kwargs = mock_send.call_args.kwargs
                assert call_kwargs["username"] == "myuser"
                assert call_kwargs["password"] == "mypassword"


class TestSmtpConfigValidation:
    """Test SMTP configuration validation."""

    def test_smtp_tls_and_starttls_mutually_exclusive(self):
        """Test that enabling both SMTP_TLS and SMTP_STARTTLS raises an error."""
        from app.config import Settings

        with pytest.raises(ValidationError) as exc_info:
            Settings(
                DATABASE_URL="mysql+aiomysql://user:pass@localhost/db",
                DATABASE_URL_SYNC="mysql+pymysql://user:pass@localhost/db",
                SECRET_KEY="test-secret-key-min-32-characters-long",
                SMTP_TLS=True,
                SMTP_STARTTLS=True,
            )

        assert "mutually exclusive" in str(exc_info.value).lower()

    def test_smtp_tls_only_valid(self):
        """Test that SMTP_TLS=True with SMTP_STARTTLS=False is valid."""
        from app.config import Settings

        settings = Settings(
            DATABASE_URL="mysql+aiomysql://user:pass@localhost/db",
            DATABASE_URL_SYNC="mysql+pymysql://user:pass@localhost/db",
            SECRET_KEY="test-secret-key-min-32-characters-long",
            SMTP_TLS=True,
            SMTP_STARTTLS=False,
        )
        assert settings.SMTP_TLS is True
        assert settings.SMTP_STARTTLS is False

    def test_smtp_starttls_only_valid(self):
        """Test that SMTP_STARTTLS=True with SMTP_TLS=False is valid."""
        from app.config import Settings

        settings = Settings(
            DATABASE_URL="mysql+aiomysql://user:pass@localhost/db",
            DATABASE_URL_SYNC="mysql+pymysql://user:pass@localhost/db",
            SECRET_KEY="test-secret-key-min-32-characters-long",
            SMTP_TLS=False,
            SMTP_STARTTLS=True,
        )
        assert settings.SMTP_TLS is False
        assert settings.SMTP_STARTTLS is True

    def test_smtp_no_tls_valid(self):
        """Test that both TLS options disabled is valid (localhost relay)."""
        from app.config import Settings

        settings = Settings(
            DATABASE_URL="mysql+aiomysql://user:pass@localhost/db",
            DATABASE_URL_SYNC="mysql+pymysql://user:pass@localhost/db",
            SECRET_KEY="test-secret-key-min-32-characters-long",
            SMTP_TLS=False,
            SMTP_STARTTLS=False,
        )
        assert settings.SMTP_TLS is False
        assert settings.SMTP_STARTTLS is False
