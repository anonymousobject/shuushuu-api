"""Tests for the nightly user-taste-profile refresh arq job wrapper.

The refresh math itself is covered end-to-end in
tests/services/test_user_tag_affinity.py; this only covers the job
wrapper's own new branching logic (the TASTE_REFRESH_ENABLED guard).
"""

from app.config import settings
from app.tasks.taste_profile import refresh_user_tag_affinity_job


async def test_disabled_returns_without_opening_a_session(monkeypatch):
    """TASTE_REFRESH_ENABLED=False short-circuits before get_async_session().

    No DB fixture is used here on purpose: settings.DATABASE_URL points at
    the docker-compose-internal `mariadb` host, unreachable from the test
    runner, so if the guard didn't fire before that call this test would
    fail with a real connection error rather than passing accidentally.
    """
    monkeypatch.setattr(settings, "TASTE_REFRESH_ENABLED", False)
    result = await refresh_user_tag_affinity_job({})
    assert result is None
