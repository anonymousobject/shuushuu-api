"""Tests for email template functions."""

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
        assert "1 hour" in call_kwargs[1]["body"]
