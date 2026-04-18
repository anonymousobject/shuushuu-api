"""Process-wide R2 storage accessor.

Returns a real R2Storage when R2_ENABLED, or a DummyR2Storage when disabled.
The singleton is rebuilt after reset_r2_storage() — useful in tests.
"""

import aioboto3

from app.config import settings
from app.services.r2_storage import DummyR2Storage, R2Storage

_instance: R2Storage | DummyR2Storage | None = None


def get_r2_storage() -> R2Storage | DummyR2Storage:
    """Return the process-wide R2 storage adapter."""
    global _instance
    if _instance is None:
        if settings.R2_ENABLED:
            session = aioboto3.Session(
                aws_access_key_id=settings.R2_ACCESS_KEY_ID,
                aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
                region_name="auto",
            )
            _instance = R2Storage(session=session, endpoint_url=settings.R2_ENDPOINT)
        else:
            _instance = DummyR2Storage()
    return _instance


def reset_r2_storage() -> None:
    """Reset the singleton. Used by tests and on config reload."""
    global _instance
    _instance = None
