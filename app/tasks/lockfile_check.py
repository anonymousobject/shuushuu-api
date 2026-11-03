"""
Stale Docker image detection for the arq worker.

Deliberately imports only the standard library: this check has to run before
any third-party dependency is imported, because a missing dependency is the
very thing it exists to diagnose.
"""

import hashlib
from pathlib import Path
from typing import Any

# The Dockerfile bakes a sha256 of uv.lock into HASH_FILE at build time and
# docker-compose mounts the host's uv.lock at LOCK_FILE at runtime.
HASH_FILE = Path("/app/.uv-lock-hash")
LOCK_FILE = Path("/app/uv.lock")


def check_lockfile_freshness(logger: Any) -> None:
    """Verify the mounted uv.lock matches the one used to build the image.

    If the hashes differ, the image was built against an older lockfile —
    installed packages are stale and the worker would crash on the first
    import of a missing dependency.

    Raises SystemExit(1) with an actionable message so the operator knows
    to rebuild instead of debugging a raw ModuleNotFoundError traceback.
    """
    # Older image without the hash file — skip check (no false positive).
    if not HASH_FILE.exists() or not LOCK_FILE.exists():
        return

    expected = HASH_FILE.read_text().strip()
    actual = hashlib.sha256(LOCK_FILE.read_bytes()).hexdigest()

    if expected != actual:
        logger.error(
            "worker_lockfile_mismatch",
            expected_hash=expected,
            actual_hash=actual,
            hint="Rebuild the Docker image: docker compose build --no-cache arq-worker",
        )
        raise SystemExit(
            "uv.lock has changed since the Docker image was built — "
            "installed packages are stale.\n"
            "Rebuild: docker compose build --no-cache arq-worker"
        )
