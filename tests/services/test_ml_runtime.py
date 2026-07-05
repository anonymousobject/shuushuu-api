import asyncio

import pytest

from app.services import ml_runtime
from app.services.ml_runtime import InferenceBusy, inference_slot


async def test_inference_slot_allows_within_capacity(monkeypatch):
    monkeypatch.setattr(ml_runtime, "_inference_semaphore", asyncio.Semaphore(1))
    async with inference_slot():
        pass


async def test_inference_slot_times_out_to_429(monkeypatch):
    sem = asyncio.Semaphore(1)
    monkeypatch.setattr(ml_runtime, "_inference_semaphore", sem)
    monkeypatch.setattr(ml_runtime.settings, "ML_ANALYZE_SEMAPHORE_TIMEOUT", 0.05)
    await sem.acquire()  # fill the only slot
    with pytest.raises(InferenceBusy) as exc:
        async with inference_slot():
            pass
    assert exc.value.status_code == 429


async def test_inference_slot_released_on_success(monkeypatch):
    sem = asyncio.Semaphore(1)
    monkeypatch.setattr(ml_runtime, "_inference_semaphore", sem)
    async with inference_slot():
        pass
    assert sem._value == 1  # released back


async def test_inference_slot_released_on_exception(monkeypatch):
    # A failure inside the slot must still release it, or slots leak and
    # inference eventually 429s forever.
    sem = asyncio.Semaphore(1)
    monkeypatch.setattr(ml_runtime, "_inference_semaphore", sem)
    with pytest.raises(ValueError):
        async with inference_slot():
            raise ValueError("boom")
    assert sem._value == 1  # released despite exception


async def test_get_ml_service_loads_once_under_concurrency(monkeypatch):
    """Concurrent cold-start callers must load the ONNX model exactly once and all
    receive the same singleton. The double-checked lock closes the check-then-set
    race where each caller would otherwise instantiate and load its own model.

    The heavy ONNX load is faked at its boundary (MLTagSuggestionService) with a
    slow load_models() to widen the race window; the assertions are on the real
    runtime behaviour (one instantiation, one load, one shared object).
    """
    monkeypatch.setattr(ml_runtime, "_ml_service", None)

    instances = 0
    loads = 0

    class _SlowService:
        def __init__(self) -> None:
            nonlocal instances
            instances += 1

        async def load_models(self) -> None:
            nonlocal loads
            loads += 1
            await asyncio.sleep(0.05)  # widen the cold-start race window

    monkeypatch.setattr("app.services.ml_service.MLTagSuggestionService", _SlowService)

    first, second = await asyncio.gather(
        ml_runtime.get_ml_service(), ml_runtime.get_ml_service()
    )

    assert instances == 1, "model must be instantiated exactly once"
    assert loads == 1, "model must be loaded exactly once"
    assert first is second, "both callers must get the same singleton"


async def test_get_ml_service_reloads_after_failed_load(monkeypatch):
    """A failed load must not cache a half-initialised service: the next call
    retries. Guards that the double-checked lock still only assigns on success."""
    monkeypatch.setattr(ml_runtime, "_ml_service", None)

    attempts = 0

    class _FlakyService:
        async def load_models(self) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("first load fails")

    monkeypatch.setattr("app.services.ml_service.MLTagSuggestionService", _FlakyService)

    with pytest.raises(RuntimeError, match="first load fails"):
        await ml_runtime.get_ml_service()

    # Second call retries (global was not set on the failed load) and succeeds.
    service = await ml_runtime.get_ml_service()
    assert attempts == 2
    assert service is not None


async def test_warm_load_calls_get_ml_service_when_enabled(monkeypatch):
    called = False
    async def fake_get():
        nonlocal called
        called = True
        return object()
    monkeypatch.setattr(ml_runtime.settings, "ML_TAG_SUGGESTIONS_ENABLED", True)
    monkeypatch.setattr(ml_runtime, "get_ml_service", fake_get)
    await ml_runtime.warm_load_if_enabled()
    assert called is True


async def test_warm_load_noop_when_disabled(monkeypatch):
    called = False
    async def fake_get():
        nonlocal called
        called = True
        return object()
    monkeypatch.setattr(ml_runtime.settings, "ML_TAG_SUGGESTIONS_ENABLED", False)
    monkeypatch.setattr(ml_runtime, "get_ml_service", fake_get)
    await ml_runtime.warm_load_if_enabled()
    assert called is False
