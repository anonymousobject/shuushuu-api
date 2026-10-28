"""Tests for the RequestLoggingMiddleware access log in app/main.py."""

import logging

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import _log_level_for


class TestLogLevelForRequest:
    """The access-log level is chosen by response status and latency.

    Every request now logs `request_complete` at INFO or above (#320) so
    duration data is available for the successful bulk of traffic, not just
    failures. Slow requests and client faults (kept so anonymous 401s can be
    clustered by source) are promoted to WARNING so they still stand out from
    routine traffic; server errors go to ERROR. Nginx keeps the full
    per-request access log with real client IPs, so the API log doesn't need
    to carry that separately.
    """

    def test_fast_success_is_info(self):
        assert _log_level_for(200, 5.0) == "info"
        assert _log_level_for(204, 0.4) == "info"

    def test_redirect_is_info(self):
        assert _log_level_for(304, 2.0) == "info"

    def test_slow_success_is_warning(self):
        slow = settings.SLOW_REQUEST_LOG_MS
        assert _log_level_for(200, slow) == "warning"
        assert _log_level_for(200, slow + 1) == "warning"

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
        f"no request_complete log emitted; got {[record.getMessage() for record in caplog.records]}"
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
async def test_fast_success_request_logged_at_info(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
):
    """Routine fast 2xx traffic now logs request_complete at INFO (#320).

    Previously this dropped to DEBUG and was suppressed in production
    (INFO+), leaving successful requests with no duration trace. Production
    runs at ~27 req/s, so INFO for everything is cheap enough not to need
    sampling.
    """
    with caplog.at_level(logging.INFO):
        response = await client.get("/health")
    assert response.status_code == 200

    request_logs = [
        record for record in caplog.records if "request_complete" in record.getMessage()
    ]
    assert request_logs, (
        f"no request_complete log emitted; got {[record.getMessage() for record in caplog.records]}"
    )
    assert all(record.levelno == logging.INFO for record in request_logs), (
        "fast 2xx request_complete should log at INFO; got "
        f"{[record.levelname for record in request_logs]}"
    )


@pytest.mark.api
async def test_unhandled_exception_logs_request_complete_at_error_and_still_returns_500(
    app: FastAPI, caplog: pytest.LogCaptureFixture
):
    """An unhandled exception must still produce a request_complete log (#380).

    Before this fix, an exception propagating out of a route skipped the
    middleware's logging entirely — the 2026-09-05 outage's thousands of
    exceptions never showed up in Loki because `request_complete` is only
    logged after `call_next` *returns*. The middleware must now catch the
    exception, log `request_complete` at ERROR with status_code=500 and a
    traceback, then re-raise so Starlette's ServerErrorMiddleware still sends
    the client a 500.
    """
    captured: dict[str, str] = {}

    async def _raise_unhandled_exception(request: Request) -> None:
        # Grab the request_id the middleware attached before we blow up, so
        # the assertions below can confirm it made it into the error log.
        captured["request_id"] = request.state.request_id
        raise RuntimeError("boom - intentional failure for the #380 test")

    original_route_count = len(app.router.routes)
    app.add_api_route("/__test_unhandled_exception", _raise_unhandled_exception, methods=["GET"])
    try:
        # raise_app_exceptions=False mirrors production: uvicorn never lets an
        # app exception reach the client directly, it just sees the 500 that
        # Starlette's ServerErrorMiddleware already sent before re-raising for
        # server-side logging. The default (True) would instead surface the
        # RuntimeError to this test as a raised exception.
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as ac:
            with caplog.at_level(logging.ERROR):
                response = await ac.get("/__test_unhandled_exception")
    finally:
        del app.router.routes[original_route_count:]

    # The client still gets a 500 — the exception must not escape the ASGI app.
    assert response.status_code == 500

    request_logs = [
        record for record in caplog.records if "request_complete" in record.getMessage()
    ]
    assert len(request_logs) == 1, (
        "expected exactly one request_complete log for the failed request; got "
        f"{[record.getMessage() for record in caplog.records]}"
    )
    log_record = request_logs[0]
    assert log_record.levelno == logging.ERROR, f"expected ERROR level; got {log_record.levelname}"

    message = log_record.getMessage()
    assert captured["request_id"] in message, f"request_id missing from access log; got {message!r}"
    # exc_info=True must actually carry a traceback, not just be requested.
    assert "Traceback" in message, f"traceback missing from access log; got {message!r}"
