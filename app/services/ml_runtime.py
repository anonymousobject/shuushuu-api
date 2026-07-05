"""Shared ML runtime for the single API process.

Owns the one model singleton and a global concurrency cap for inference. Both
the per-image generate endpoint and the upload /analyze endpoint use these, so
there is exactly one loaded model and one process-wide inference ceiling.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from app.config import settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.services.ml_service import MLTagSuggestionService

logger = get_logger(__name__)

_ml_service: MLTagSuggestionService | None = None
_ml_service_lock = asyncio.Lock()
_inference_semaphore = asyncio.Semaphore(settings.ML_ANALYZE_CONCURRENCY)


class InferenceBusy(HTTPException):
    """429 raised when no inference slot frees up within the wait timeout."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Server is busy running tag inference; please retry shortly.",
            headers={"Retry-After": "5"},
        )


async def get_ml_service() -> MLTagSuggestionService:
    """Get or lazily load the process-wide ML service singleton.

    Function-local import keeps onnxruntime out of the API import chain until
    the model is actually needed. The global is only set after a successful
    load, so a failed load is retried next call (not cached half-initialised).

    The load is guarded by a module-level lock with a double-checked pattern:
    the fast path returns the already-loaded singleton without locking, and
    only cold-start callers serialise on the lock so concurrent first requests
    load the ONNX model exactly once instead of one model per caller.
    """
    global _ml_service
    if _ml_service is not None:
        return _ml_service

    async with _ml_service_lock:
        # Re-check under the lock: another caller may have loaded it while we
        # waited to acquire the lock.
        if _ml_service is None:
            from app.services.ml_service import MLTagSuggestionService

            service = MLTagSuggestionService()
            await service.load_models()
            _ml_service = service
            logger.info("ml_service_loaded")
    return _ml_service


@asynccontextmanager
async def inference_slot() -> AsyncIterator[None]:
    """Acquire a global inference slot, waiting up to the configured timeout.

    Bounded wait (not fast-reject): callers queue briefly behind in-flight
    inferences and only get a 429 under sustained overload. Waiting coroutines
    suspend (no thread/core held), so the queue itself is free.
    """
    try:
        await asyncio.wait_for(
            _inference_semaphore.acquire(),
            timeout=settings.ML_ANALYZE_SEMAPHORE_TIMEOUT,
        )
    except TimeoutError:
        raise InferenceBusy() from None
    try:
        yield
    finally:
        _inference_semaphore.release()


async def warm_load_if_enabled() -> None:
    """Pre-load the model at API startup when the feature is on, so the first
    /analyze doesn't eat the ~1.5 s cold load. Never raises: a load failure is
    logged and inference falls back to lazy loading on first request (the worker
    is the strict one that must fail to start)."""
    if not settings.ML_TAG_SUGGESTIONS_ENABLED:
        return
    try:
        await get_ml_service()
    except Exception:
        logger.warning("ml_warm_load_failed", exc_info=True)
