"""Tests for the conftest guard that keeps arq jobs out of real queues.

Prod runs from this same checkout (env_file: .env), so settings.ARQ_REDIS_URL
points at the production queue. On 2026-08-29 a pytest run enqueued PM
notification and password-reset jobs there, and the prod worker resolved the
small pytest-DB IDs against prod data and emailed real users. The autouse
stub_arq_pool fixture in conftest.py replaces the arq pool for every test;
these tests pin that guard in place.
"""

from app.config import settings
from app.tasks.queue import enqueue_job


async def test_enqueue_job_never_reaches_a_real_queue(monkeypatch):
    # Without the autouse stub, enqueue_job builds a pool from
    # settings.ARQ_REDIS_URL; point it somewhere unreachable so that failure
    # mode returns None here instead of enqueuing into a live queue.
    monkeypatch.setattr(settings, "ARQ_REDIS_URL", "redis://127.0.0.1:1/15")

    job_id = await enqueue_job("send_pm_notification", privmsg_id=123)

    assert job_id is not None


async def test_stub_pool_captures_enqueued_jobs(stub_arq_pool):
    await enqueue_job("send_pm_notification", privmsg_id=123)

    assert stub_arq_pool.jobs == [("send_pm_notification", (), {"privmsg_id": 123})]
