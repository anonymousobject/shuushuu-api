"""Tests for the RequestLoggingMiddleware access log in app/main.py."""

import logging

import pytest
from httpx import AsyncClient

from app.config import settings
from app.main import _log_level_for


class TestLogLevelForRequest:
    """The access-log level is chosen by response status and latency.

    Routine fast 2xx traffic (the bulk of the volume) drops to DEBUG, which is
    suppressed in production, while server errors, client faults, and slow
    requests stay visible. Nginx keeps the full per-request access log with real
    client IPs, so the API log is free to keep only the high-signal lines.
    """

    def test_fast_success_is_debug(self):
        assert _log_level_for(200, 5.0) == "debug"
        assert _log_level_for(204, 0.4) == "debug"

    def test_redirect_is_debug(self):
        assert _log_level_for(304, 2.0) == "debug"

    def test_slow_success_is_info(self):
        slow = settings.SLOW_REQUEST_LOG_MS
        assert _log_level_for(200, slow) == "info"
        assert _log_level_for(200, slow + 1) == "info"

    def test_client_error_is_warning_regardless_of_latency(self):
        # 4xx stays visible even when fast so anonymous 401s can be clustered by
        # source (one user fat-fingering a password vs. credential stuffing).
        assert _log_level_for(401, 1.0) == "warning"
        assert _log_level_for(404, 1.0) == "warning"

    def test_server_error_is_error(self):
        assert _log_level_for(500, 1.0) == "error"
        assert _log_level_for(503, 1.0) == "error"


@pytest.mark.api
async def test_client_error_logs_client_host_and_user_agent(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
):
    """A 4xx access log records the client host and user agent at WARNING.

    Without these, anonymous 401s can't be clustered by source — you can't tell
    one user fat-fingering a password from credential stuffing across many IPs.
    """
    with caplog.at_level(logging.WARNING):
        response = await client.get(
            "/api/v1/privmsgs/threads", headers={"user-agent": "test-agent/9.9"}
        )
    assert response.status_code in (401, 403)

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
    # The httpx ASGITransport reports the loopback address as request.client, so a
    # wired-up client_host shows up as 127.0.0.1. If the test transport ever changes
    # (e.g. a Unix-socket transport with no client), update this expected host.
    assert any("127.0.0.1" in message for message in request_logs), (
        f"client host missing from access log; got {request_logs!r}"
    )


@pytest.mark.api
async def test_fast_success_request_logged_at_debug(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
):
    """Routine fast 2xx traffic is logged at DEBUG so production (INFO+) drops it.

    The line is still emitted — just below INFO — so it's available in dev/debug
    but doesn't flood the aggregated logs in production.
    """
    with caplog.at_level(logging.DEBUG):
        response = await client.get("/health")
    assert response.status_code == 200

    request_logs = [
        record for record in caplog.records if "request_complete" in record.getMessage()
    ]
    assert request_logs, (
        "no request_complete log emitted; "
        f"got {[record.getMessage() for record in caplog.records]}"
    )
    assert all(record.levelno == logging.DEBUG for record in request_logs), (
        "fast 2xx request_complete should log at DEBUG; got "
        f"{[record.levelname for record in request_logs]}"
    )
