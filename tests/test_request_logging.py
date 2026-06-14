"""Tests for the RequestLoggingMiddleware access log in app/main.py."""

import logging

import pytest
from httpx import AsyncClient


@pytest.mark.api
async def test_request_complete_logs_client_host_and_user_agent(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
):
    """The request_complete access log records the client host and user agent.

    Without these, anonymous 401s can't be clustered by source — you can't tell
    one user fat-fingering a password from credential stuffing across many IPs.
    """
    with caplog.at_level(logging.INFO):
        response = await client.get("/health", headers={"user-agent": "test-agent/9.9"})
    assert response.status_code == 200

    # The dev ConsoleRenderer interleaves ANSI color codes between a field's key,
    # `=`, and value, so match on the contiguous value tokens rather than `key=value`.
    request_logs = [
        record.getMessage()
        for record in caplog.records
        if "request_complete" in record.getMessage()
    ]
    assert request_logs, (
        "no request_complete log emitted; "
        f"got {[record.getMessage() for record in caplog.records]}"
    )
    assert any("test-agent/9.9" in message for message in request_logs), (
        f"user agent missing from access log; got {request_logs!r}"
    )
    assert any("127.0.0.1" in message for message in request_logs), (
        f"client host missing from access log; got {request_logs!r}"
    )
