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
