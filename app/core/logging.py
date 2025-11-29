"""
Structured logging configuration using structlog.

Provides JSON-formatted logs with contextual information that works across
main thread and background tasks.

Features:
- JSON structured logging for production
- Pretty console logging for development
- Request ID tracking
- Context binding (user_id, image_id, etc.)
- Background task logging support
"""

import logging
import sys
from contextvars import ContextVar
from typing import Any, cast

import structlog
from structlog.types import EventDict, Processor

from app.config import settings

# Context variables for request tracking
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_ctx: ContextVar[int | None] = ContextVar("user_id", default=None)


def add_context_info(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """Add contextual information to log records."""
    # Add request ID if available
    request_id = request_id_ctx.get(None)
    if request_id:
        event_dict["request_id"] = request_id

    # Add user ID if available
    user_id = user_id_ctx.get(None)
    if user_id:
        event_dict["user_id"] = user_id

    return event_dict


def configure_logging() -> None:
    """
    Configure structured logging for the application.

    In development: Pretty console output with colors
    In production: JSON-formatted logs for aggregation
    """
    # Determine processors based on environment
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        add_context_info,
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.ENVIRONMENT == "development":
        # Development: Pretty console output
        processors: list[Processor] = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True)
        ]
    else:
        # Production: JSON output
        processors = shared_processors + [
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]

    # Configure structlog
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.NOTSET),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.LOG_LEVEL.upper()),
    )

    # Reduce noise from third-party libraries
    # Keep uvicorn.access at INFO to see request logs in development
    if settings.ENVIRONMENT != "development":
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    Get a logger instance with the given name.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured logger instance

    Example:
        logger = get_logger(__name__)
        logger.info("user_logged_in", user_id=123, ip="192.168.1.1")
    """
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))


def set_request_context(request_id: str, user_id: int | None = None) -> None:
    """
    Set context variables for the current request.

    This should be called at the start of each request to track
    the request ID and user ID across all logs.

    Args:
        request_id: Unique request identifier
        user_id: Optional user ID if authenticated
    """
    request_id_ctx.set(request_id)
    if user_id:
        user_id_ctx.set(user_id)


def clear_request_context() -> None:
    """Clear context variables after request completes."""
    request_id_ctx.set(None)
    user_id_ctx.set(None)


def bind_context(**kwargs: Any) -> None:
    """
    Bind additional context to all subsequent logs in this context.

    Useful for background tasks to add task-specific context.

    Args:
        **kwargs: Key-value pairs to add to log context

    Example:
        bind_context(task_name="thumbnail_generation", image_id=1111822)
        logger.info("task_started")  # Will include task_name and image_id
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def unbind_context(*keys: str) -> None:
    """
    Remove context variables.

    Args:
        *keys: Keys to remove from context
    """
    structlog.contextvars.unbind_contextvars(*keys)
