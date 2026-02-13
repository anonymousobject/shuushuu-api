"""
FastAPI Application - Shuushuu API
Modern backend for Shuushuu anime image board
"""

import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.config import settings
from app.core.database import AsyncSessionLocal
from app.core.logging import (
    clear_request_context,
    configure_logging,
    get_logger,
    set_request_context,
)
from app.core.permission_sync import sync_permissions
from app.core.security import verify_access_token
from app.tasks.queue import close_queue

# Configure logging on module import
configure_logging()
logger = get_logger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to add request ID and logging context to each request."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        """Add request tracking context to each request."""
        # Generate unique request ID
        request_id = str(uuid.uuid4())

        # Extract user_id from JWT if present (lightweight decode, no DB hit)
        user_id = None
        auth_header = request.headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            user_id = verify_access_token(auth_header[7:])
        if user_id is None:
            token = request.cookies.get("access_token")
            if token:
                user_id = verify_access_token(token)

        # Set context for this request (will be included in all logs)
        set_request_context(request_id, user_id=user_id)

        # Add request ID to request state for access in endpoints
        request.state.request_id = request_id

        try:
            start = time.monotonic()
            response = await call_next(request)
            elapsed_ms = round((time.monotonic() - start) * 1000, 1)
            # Add request ID to response headers for debugging
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "request_complete",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                elapsed_ms=elapsed_ms,
            )
            return response
        finally:
            # Clear context after request completes
            clear_request_context()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Lifespan context manager for startup and shutdown events"""
    # Startup
    logger.info(
        "application_starting",
        environment=settings.ENVIRONMENT,
        version="2.0.0",
    )

    # Sync permissions: ensure database matches Permission enum
    async with AsyncSessionLocal() as db:
        await sync_permissions(db)

    yield
    # Shutdown
    logger.info("application_shutting_down")
    await close_queue()  # Close arq pool


# Create FastAPI application
app = FastAPI(
    title="Shuushuu API",
    description="Modern FastAPI backend for Shuushuu anime image board",
    version="2.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# Add proxy headers middleware (must be first to properly handle X-Forwarded-* headers)
# Trust only the Docker bridge network to prevent header spoofing
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["172.16.0.0/12"])

# Add request logging middleware (before CORS)
app.add_middleware(RequestLoggingMiddleware)

# Configure CORS
# SvelteKit's universal fetch requires CORS headers even on server-side requests
# In development, use wildcard to allow SSR. In production, use specific origins.
if settings.ENVIRONMENT == "development":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,  # Can't use credentials with wildcard
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint - API information"""
    return {
        "name": "Shuushuu API",
        "version": "2.0.0",
        "status": "running",
        "docs": "/api/docs",
    }


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint"""
    return {"status": "healthy"}


# Import and include routers
from app.api.v1 import router as api_v1_router  # noqa: E402

app.include_router(api_v1_router, prefix="/api/v1")

from app.api.v1.media import router as media_router  # noqa: E402

# Mount media routes at root level (not under /api/v1)
# These serve image files with permission checks via X-Accel-Redirect
app.include_router(media_router)

# Note: Static images are served directly by nginx for better performance
# See docker/nginx/frontend.conf.template for configuration
