# R2 storage for avatars and banners — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend Cloudflare R2 storage to user avatars and site banners using a lightweight dual-write pattern gated by per-row tracking bits. Local FS remains a working fallback; existing dev (`R2_ENABLED=false`) behavior is preserved.

**Architecture:** Two new boolean columns (`users.avatar_in_r2`, `banners.in_r2`) gate URL generation and orphan-cleanup. Avatar uploads dual-write inline (best-effort R2, falls back to local-only on failure). One-shot `r2_sync.py` subcommands (`avatars-backfill`, `banners-backfill`) cover existing files. No async retry queue, no admin upload endpoint, no private-bucket support.

**Tech Stack:** Python 3.14, FastAPI, SQLModel, Alembic, MariaDB 12, aioboto3, moto (test), pytest. Spec: `docs/plans/2026-05-08-r2-avatars-banners-design.md`.

---

## Pre-flight

The design doc currently lives on branch `docs/r2-avatars-banners-design`. Implementation should branch off `main` once the spec is merged (or rebase as appropriate). Convention from prior work suggests `feat/r2-avatars-banners`.

```bash
git fetch origin
git checkout main
git pull --ff-only
git checkout -b feat/r2-avatars-banners
```

Confirm the test suite is green before starting:

```bash
uv run pytest tests/unit -x -q
```

Confirm alembic head is `6f286ed3c418` (the iqdb-hash migration) — both new migrations descend from it:

```bash
uv run alembic heads
```

If the head is different, the migration `down_revision` values in this plan must be updated to match the actual current head before generating new revisions.

## File structure

**New files**

- `alembic/versions/<rev1>_add_avatar_in_r2_to_users.py` — schema migration
- `alembic/versions/<rev2>_add_in_r2_to_banners.py` — schema migration
- `tests/unit/test_avatar_url_helper.py` — pure-function tests for the new URL helper
- `tests/unit/test_avatar_r2_dual_write.py` — moto-backed dual-write integration tests for `_upload_avatar`
- `tests/unit/test_r2_sync_avatars_backfill.py` — backfill CLI tests
- `tests/unit/test_r2_sync_banners_backfill.py` — backfill CLI tests

**Modified files**

- `app/config.py` — add `BANNER_STORAGE_PATH` setting + `set_default_banner_storage_path` validator
- `app/models/user.py` — add `avatar_in_r2` to `UserBase`
- `app/models/misc.py` — add `in_r2` to `BannerBase`
- `app/services/r2_storage.py` — add `upload_bytes` to `R2Storage` and `DummyR2Storage`
- `app/services/avatar.py` — add `_avatar_content_type`, `avatar_url`; extend `delete_avatar_if_orphaned` signature
- `app/schemas/banner.py` — `BannerResponse.in_r2` field, update `_image_url`
- `app/schemas/user.py` — update `UserResponse.avatar_url` to use helper
- `app/schemas/common.py` — update `UserSummary.avatar_url` to use helper, add `avatar_in_r2` field
- `app/api/v1/users.py` — `_upload_avatar` and `_delete_avatar` capture old_in_r2, dual-write, pass to orphan helper
- `app/api/v1/privmsgs.py` — replace 7 inline avatar URL builds with helper; queries fetch `avatar_in_r2`
- `scripts/r2_sync.py` — add `avatars-backfill` and `banners-backfill` subcommands
- `tests/unit/test_r2_storage.py` — extend with `upload_bytes` test
- `tests/unit/test_r2_client.py` — extend with `DummyR2Storage.upload_bytes` test
- `tests/unit/test_avatar.py` — existing avatar-service tests; extend with new orphan-helper signature
- `tests/unit/test_banner_schema.py` — extend with `in_r2` switch tests

---

## Chunk 1: Schema, models, config

Goal of this chunk: alembic migrations land, model fields exist, config knob exists. After this chunk, the application still behaves identically (bits default false everywhere, no code consumes them yet).

### Task 1.1: Alembic migration — `users.avatar_in_r2`

**Files:**
- Create: `alembic/versions/<auto>_add_avatar_in_r2_to_users.py`

- [ ] **Step 1: Generate the migration scaffolding**

```bash
uv run alembic revision -m "add avatar_in_r2 to users"
```

Note the generated filename (it'll be `alembic/versions/<hash>_add_avatar_in_r2_to_users.py`).

- [ ] **Step 2: Replace the generated `upgrade`/`downgrade` bodies**

Edit the new file. Set `down_revision = "6f286ed3c418"` (the current head). Replace upgrade/downgrade with:

```python
def upgrade() -> None:
    """Add avatar_in_r2 to users.

    Tracks whether the avatar file referenced by users.avatar exists in R2.
    Default 0 — backfill is a separate one-shot run via
    `scripts/r2_sync.py avatars-backfill`.

    Uses ALGORITHM=INSTANT, LOCK=NONE so the migration is metadata-only on
    InnoDB (MariaDB 12) — no table rewrite, no row locks.
    """
    op.execute(
        "ALTER TABLE users ADD COLUMN avatar_in_r2 BOOLEAN NOT NULL DEFAULT 0, "
        "ALGORITHM=INSTANT, LOCK=NONE"
    )


def downgrade() -> None:
    """Remove avatar_in_r2 column."""
    op.drop_column("users", "avatar_in_r2")
```

- [ ] **Step 3: Apply, verify, downgrade-roundtrip**

```bash
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
```

Expected: each command exits 0. After the final upgrade, `uv run alembic heads` reports the new revision as head.

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/*_add_avatar_in_r2_to_users.py
git commit -m "feat(alembic): add avatar_in_r2 column to users"
```

### Task 1.2: Alembic migration — `banners.in_r2`

**Files:**
- Create: `alembic/versions/<auto>_add_in_r2_to_banners.py`

- [ ] **Step 1: Generate the migration**

```bash
uv run alembic revision -m "add in_r2 to banners"
```

- [ ] **Step 2: Replace bodies**

`down_revision` must be the migration hash from Task 1.1. Bodies:

```python
def upgrade() -> None:
    """Add in_r2 to banners.

    Tracks whether all of full_image / left_image / middle_image / right_image
    referenced by this row exist in R2. Default 0 — backfill is a separate
    one-shot run via `scripts/r2_sync.py banners-backfill`.
    """
    op.execute(
        "ALTER TABLE banners ADD COLUMN in_r2 BOOLEAN NOT NULL DEFAULT 0, "
        "ALGORITHM=INSTANT, LOCK=NONE"
    )


def downgrade() -> None:
    """Remove in_r2 column."""
    op.drop_column("banners", "in_r2")
```

- [ ] **Step 3: Apply, verify, downgrade-roundtrip**

Same commands as Task 1.1. Confirm both columns are present after `upgrade head`:

```bash
uv run python -c "
import asyncio
from sqlalchemy import inspect
from app.core.database import engine
async def main():
    async with engine.connect() as c:
        cols = await c.run_sync(lambda s: {col['name'] for col in inspect(s).get_columns('users')} | {col['name'] for col in inspect(s).get_columns('banners')})
        assert 'avatar_in_r2' in cols and 'in_r2' in cols, cols
        print('OK')
asyncio.run(main())
"
```

Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/*_add_in_r2_to_banners.py
git commit -m "feat(alembic): add in_r2 column to banners"
```

### Task 1.3: Add `avatar_in_r2` to `UserBase`

**Files:**
- Modify: `app/models/user.py:46`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_user_loader.py` (or create `tests/unit/test_user_avatar_in_r2_field.py` if simpler — a single tiny test):

```python
def test_userbase_has_avatar_in_r2_field():
    from app.models.user import UserBase
    field = UserBase.model_fields["avatar_in_r2"]
    assert field.annotation is bool
    assert field.default is False
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/unit/test_user_avatar_in_r2_field.py -v
```

Expected: FAIL with `KeyError: 'avatar_in_r2'`.

- [ ] **Step 3: Add the field**

In `app/models/user.py`, immediately after `avatar: str = Field(...)` (line 46):

```python
    avatar_in_r2: bool = Field(default=False)
```

- [ ] **Step 4: Run to verify it passes**

```bash
uv run pytest tests/unit/test_user_avatar_in_r2_field.py -v
```

Expected: PASS.

- [ ] **Step 5: Run the full unit suite to catch fallout**

```bash
uv run pytest tests/unit -x -q
```

Expected: all green. If any test fails because a `Users(...)` constructor has a positional-arg expectation, those need fixing in the same commit — but `bool` defaults shouldn't break call sites.

- [ ] **Step 6: Commit**

```bash
git add app/models/user.py tests/unit/test_user_avatar_in_r2_field.py
git commit -m "feat(models): add avatar_in_r2 field to UserBase"
```

### Task 1.4: Add `in_r2` to `BannerBase`

**Files:**
- Modify: `app/models/misc.py` (in `BannerBase` class around line 31–54)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_banner_model.py`:

```python
def test_bannerbase_has_in_r2_field():
    from app.models.misc import BannerBase
    field = BannerBase.model_fields["in_r2"]
    assert field.annotation is bool
    assert field.default is False
```

- [ ] **Step 2: Verify it fails**

```bash
uv run pytest tests/unit/test_banner_model.py::test_bannerbase_has_in_r2_field -v
```

Expected: FAIL.

- [ ] **Step 3: Add the field**

In `BannerBase`, after `active: bool = Field(default=True)`:

```python
    in_r2: bool = Field(default=False)
```

- [ ] **Step 4: Verify pass + suite**

```bash
uv run pytest tests/unit/test_banner_model.py -v
uv run pytest tests/unit -x -q
```

- [ ] **Step 5: Commit**

```bash
git add app/models/misc.py tests/unit/test_banner_model.py
git commit -m "feat(models): add in_r2 field to BannerBase"
```

### Task 1.5: Add `BANNER_STORAGE_PATH` setting

**Files:**
- Modify: `app/config.py` (add setting field near line 181, add validator near line 244–248)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_config.py` (or wherever banner config tests live — confirm with `grep`; otherwise create `tests/unit/test_config_banner_storage.py`):

```python
def test_banner_storage_path_default_derives_from_storage_path(monkeypatch):
    from app.config import Settings
    s = Settings(STORAGE_PATH="/tmp/test", BANNER_STORAGE_PATH="")
    assert s.BANNER_STORAGE_PATH == "/tmp/test/banners"


def test_banner_storage_path_explicit_value_preserved():
    from app.config import Settings
    s = Settings(STORAGE_PATH="/tmp/test", BANNER_STORAGE_PATH="/elsewhere")
    assert s.BANNER_STORAGE_PATH == "/elsewhere"
```

- [ ] **Step 2: Verify failure**

```bash
uv run pytest tests/unit/test_config_banner_storage.py -v
```

Expected: FAIL — attribute does not exist.

- [ ] **Step 3: Add setting and validator**

In `app/config.py`, alongside `AVATAR_STORAGE_PATH` declaration around line 113:

```python
    BANNER_STORAGE_PATH: str = ""  # Derived from STORAGE_PATH if not set
```

Add a sibling `@model_validator(mode="after")` after `set_default_avatar_storage_path` (around line 244):

```python
    @model_validator(mode="after")
    def set_default_banner_storage_path(self) -> Settings:
        if not self.BANNER_STORAGE_PATH:
            self.BANNER_STORAGE_PATH = f"{self.STORAGE_PATH}/banners"
        return self
```

- [ ] **Step 4: Verify pass + suite**

```bash
uv run pytest tests/unit/test_config_banner_storage.py -v
uv run pytest tests/unit -x -q
```

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/unit/test_config_banner_storage.py
git commit -m "feat(config): add BANNER_STORAGE_PATH setting"
```

---

## Chunk 2: R2 adapter + URL helpers

Goal: `R2Storage.upload_bytes` exists and is tested; `avatar_url` and the banner URL switch read the new bits but **don't** dual-write yet. Behavior change: rows with `avatar_in_r2=true` (none yet, since defaults are false) would now serve from CDN. With all bits false post-Chunk-1, runtime behavior is unchanged.

### Task 2.1: Add `R2Storage.upload_bytes`

**Files:**
- Modify: `app/services/r2_storage.py` (add method around line 79, alongside `upload_file`)
- Modify: `tests/unit/test_r2_storage.py` (extend `TestR2Storage`)
- Modify: `tests/unit/test_r2_client.py` (extend `DummyR2Storage` test)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_r2_storage.py` inside `class TestR2Storage`:

```python
    async def test_upload_bytes_round_trip(self, setup_buckets, moto_session, moto_server):
        storage = setup_buckets
        await storage.upload_bytes(
            bucket="public",
            key="avatars/abc.png",
            body=b"PNG-bytes",
            content_type="image/png",
        )
        assert await storage.object_exists(bucket="public", key="avatars/abc.png")
        # Verify Content-Type was set
        async with moto_session.client("s3", endpoint_url=moto_server) as s3:
            head = await s3.head_object(Bucket="public", Key="avatars/abc.png")
            assert head["ContentType"] == "image/png"
```

Append to `tests/unit/test_r2_client.py`:

```python
async def test_dummy_upload_bytes_raises():
    from app.services.r2_storage import DummyR2Storage
    storage = DummyR2Storage()
    import pytest
    with pytest.raises(RuntimeError):
        await storage.upload_bytes(
            bucket="x", key="y", body=b"z", content_type="image/png"
        )
```

- [ ] **Step 2: Verify failures**

```bash
uv run pytest tests/unit/test_r2_storage.py::TestR2Storage::test_upload_bytes_round_trip tests/unit/test_r2_client.py -v
```

Expected: both tests fail (`AttributeError: 'R2Storage' object has no attribute 'upload_bytes'` etc.).

- [ ] **Step 3: Implement on `R2Storage`**

In `app/services/r2_storage.py`, add a method to the `R2Storage` class (insert after `upload_file`):

```python
    async def upload_bytes(
        self, bucket: str, key: str, body: bytes, content_type: str
    ) -> None:
        """Upload an in-memory bytes payload with an explicit Content-Type.

        Content-Type is mandatory because R2 stores `application/octet-stream`
        when none is set, which can break inline image rendering under strict
        CSP / X-Content-Type-Options: nosniff.
        """
        async with self._acquire_client() as s3:
            await s3.put_object(
                Bucket=bucket, Key=key, Body=body, ContentType=content_type
            )
```

- [ ] **Step 4: Implement on `DummyR2Storage`**

In the same file, add to `DummyR2Storage`:

```python
    async def upload_bytes(
        self, bucket: str, key: str, body: bytes, content_type: str
    ) -> None:
        raise RuntimeError(self._ERR)
```

- [ ] **Step 5: Verify pass**

```bash
uv run pytest tests/unit/test_r2_storage.py tests/unit/test_r2_client.py -v
```

Expected: all green including the new tests.

- [ ] **Step 6: Commit**

```bash
git add app/services/r2_storage.py tests/unit/test_r2_storage.py tests/unit/test_r2_client.py
git commit -m "feat(r2): add upload_bytes to R2Storage"
```

### Task 2.2: Add avatar URL helper and `_avatar_content_type`

**Files:**
- Modify: `app/services/avatar.py`
- Create: `tests/unit/test_avatar_url_helper.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_avatar_url_helper.py`:

```python
"""Tests for avatar URL helper and content-type derivation."""

import pytest

from app.config import settings
from app.services.avatar import _avatar_content_type, avatar_url


def test_avatar_url_returns_none_for_empty():
    assert avatar_url("", in_r2=True) is None
    assert avatar_url(None, in_r2=True) is None


def test_avatar_url_local_when_r2_disabled(monkeypatch):
    monkeypatch.setattr(settings, "R2_ENABLED", False)
    monkeypatch.setattr(settings, "IMAGE_BASE_URL", "http://local.test")
    monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.test")
    assert avatar_url("abc.png", in_r2=True) == "http://local.test/images/avatars/abc.png"


def test_avatar_url_local_when_bit_false(monkeypatch):
    monkeypatch.setattr(settings, "R2_ENABLED", True)
    monkeypatch.setattr(settings, "IMAGE_BASE_URL", "http://local.test")
    monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.test")
    assert avatar_url("abc.png", in_r2=False) == "http://local.test/images/avatars/abc.png"


def test_avatar_url_cdn_when_both_true(monkeypatch):
    monkeypatch.setattr(settings, "R2_ENABLED", True)
    monkeypatch.setattr(settings, "IMAGE_BASE_URL", "http://local.test")
    monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.test")
    assert avatar_url("abc.png", in_r2=True) == "https://cdn.test/avatars/abc.png"


@pytest.mark.parametrize("ext,expected", [
    ("png", "image/png"),
    ("jpg", "image/jpeg"),
    ("jpeg", "image/jpeg"),
    ("gif", "image/gif"),
])
def test_avatar_content_type(ext, expected):
    assert _avatar_content_type(ext) == expected
```

- [ ] **Step 2: Verify failure**

```bash
uv run pytest tests/unit/test_avatar_url_helper.py -v
```

Expected: ImportError on `avatar_url` and `_avatar_content_type`.

- [ ] **Step 3: Add helpers to `app/services/avatar.py`**

After the `ALLOWED_AVATAR_EXTENSIONS` constant:

```python
_AVATAR_CONTENT_TYPES: dict[str, str] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
}


def _avatar_content_type(ext: str) -> str:
    """Map an avatar extension (no dot) to its image/* MIME type."""
    return _AVATAR_CONTENT_TYPES[ext.lower()]


def avatar_url(filename: str | None, in_r2: bool) -> str | None:
    """Return the public URL for an avatar, choosing CDN vs local FS.

    When R2_ENABLED is false, the per-row bit is ignored and the local URL
    is returned — preserving today's dev-only behavior even if a dev pulls
    a prod DB dump where rows have the bit set.
    """
    if not filename:
        return None
    if settings.R2_ENABLED and in_r2:
        return f"{settings.R2_PUBLIC_CDN_URL}/avatars/{filename}"
    return f"{settings.IMAGE_BASE_URL}/images/avatars/{filename}"
```

- [ ] **Step 4: Verify pass**

```bash
uv run pytest tests/unit/test_avatar_url_helper.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add app/services/avatar.py tests/unit/test_avatar_url_helper.py
git commit -m "feat(avatar): add avatar_url helper and content-type mapping"
```

### Task 2.3: Switch `UserResponse.avatar_url` and `UserSummary.avatar_url` to the helper

**Files:**
- Modify: `app/schemas/user.py:157` (UserResponse.avatar_url computed_field)
- Modify: `app/schemas/common.py:29` (UserSummary.avatar_url computed_field)
- Modify: `app/schemas/common.py:20` (UserSummary class — add `avatar_in_r2` field)

- [ ] **Step 1: Write the failing test**

Create or extend `tests/unit/test_avatar_url_helper.py` with a schema-level test:

```python
def test_user_response_avatar_url_uses_helper(monkeypatch):
    from app.schemas.user import UserResponse
    monkeypatch.setattr(settings, "R2_ENABLED", True)
    monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.test")
    monkeypatch.setattr(settings, "IMAGE_BASE_URL", "http://local.test")
    # Build via from_attributes pathway with a minimal stand-in
    data = {
        "user_id": 1, "username": "alice", "avatar": "abc.png",
        "avatar_in_r2": True,
        # plus any other required UserResponse fields — fill from model_fields
    }
    # Use model_construct to bypass deep validation for fields we don't care about
    resp = UserResponse.model_construct(**data)
    assert resp.avatar_url == "https://cdn.test/avatars/abc.png"


def test_user_summary_avatar_url_uses_helper(monkeypatch):
    from app.schemas.common import UserSummary
    monkeypatch.setattr(settings, "R2_ENABLED", True)
    monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.test")
    monkeypatch.setattr(settings, "IMAGE_BASE_URL", "http://local.test")
    summary = UserSummary(user_id=1, username="alice", avatar="abc.png", avatar_in_r2=True)
    assert summary.avatar_url == "https://cdn.test/avatars/abc.png"
```

The exact `UserResponse` field set may need adjusting — confirm by reading `app/schemas/user.py` lines 140–177.

- [ ] **Step 2: Verify failure**

```bash
uv run pytest tests/unit/test_avatar_url_helper.py -v
```

Expected: failure — `UserSummary` doesn't have `avatar_in_r2` and the URL is built from the inline f-string, not the helper.

- [ ] **Step 3: Add `avatar_in_r2` to `UserSummary`**

In `app/schemas/common.py` after the `avatar` field (line 20):

```python
    avatar_in_r2: bool = False
```

- [ ] **Step 4: Replace both computed_fields with helper calls**

In `app/schemas/common.py:29`:

```python
    @computed_field  # type: ignore[prop-decorator]
    @property
    def avatar_url(self) -> str | None:
        """Generate avatar URL from avatar field"""
        from app.services.avatar import avatar_url as _build_avatar_url
        return _build_avatar_url(self.avatar, self.avatar_in_r2)
```

In `app/schemas/user.py:157`:

```python
    @computed_field  # type: ignore[prop-decorator]
    @property
    def avatar_url(self) -> str | None:
        """Generate avatar URL from avatar field"""
        from app.services.avatar import avatar_url as _build_avatar_url
        return _build_avatar_url(self.avatar, self.avatar_in_r2)
```

(The local-import shape avoids any lingering circular-import concerns between schemas and services. If the existing module already imports from `app.services.avatar` at top level, hoist it to module-level.)

- [ ] **Step 5: Verify pass + full unit suite**

```bash
uv run pytest tests/unit/test_avatar_url_helper.py -v
uv run pytest tests/unit -x -q
```

Expected: all green. If `UserResponse`-driven tests break because callers don't pass `avatar_in_r2`, the field reads from `UserBase` (Task 1.3) so model_validate from a row works; manual constructor calls in tests need the new field passed.

- [ ] **Step 6: Commit**

```bash
git add app/schemas/user.py app/schemas/common.py tests/unit/test_avatar_url_helper.py
git commit -m "feat(schemas): route avatar URL generation through helper"
```

### Task 2.4: Add `in_r2` to `BannerResponse` and switch `_image_url`

**Files:**
- Modify: `app/schemas/banner.py:12-80` (BannerResponse class)
- Modify: `tests/unit/test_banner_schema.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_banner_schema.py`:

```python
def test_banner_response_uses_cdn_when_in_r2_and_enabled(monkeypatch):
    from app.config import settings
    from app.models.misc import BannerSize
    from app.schemas.banner import BannerResponse
    monkeypatch.setattr(settings, "R2_ENABLED", True)
    monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.test")
    monkeypatch.setattr(settings, "BANNER_BASE_URL", "http://local.test/banners")

    resp = BannerResponse(
        banner_id=1, name="t", author=None, size=BannerSize.small,
        supports_dark=True, supports_light=True,
        full_image="eva/full.jpg", left_image=None, middle_image=None, right_image=None,
        in_r2=True,
    )
    assert resp.full_image_url == "https://cdn.test/banners/eva/full.jpg"


def test_banner_response_falls_back_when_bit_false(monkeypatch):
    from app.config import settings
    from app.models.misc import BannerSize
    from app.schemas.banner import BannerResponse
    monkeypatch.setattr(settings, "R2_ENABLED", True)
    monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.test")
    monkeypatch.setattr(settings, "BANNER_BASE_URL", "http://local.test/banners")

    resp = BannerResponse(
        banner_id=1, name="t", author=None, size=BannerSize.small,
        supports_dark=True, supports_light=True,
        full_image="eva/full.jpg", left_image=None, middle_image=None, right_image=None,
        in_r2=False,
    )
    assert resp.full_image_url == "http://local.test/banners/eva/full.jpg"


def test_banner_response_three_part_uses_cdn(monkeypatch):
    from app.config import settings
    from app.models.misc import BannerSize
    from app.schemas.banner import BannerResponse
    monkeypatch.setattr(settings, "R2_ENABLED", True)
    monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.test")
    monkeypatch.setattr(settings, "BANNER_BASE_URL", "http://local.test/banners")

    resp = BannerResponse(
        banner_id=1, name="t", author=None, size=BannerSize.large,
        supports_dark=True, supports_light=True,
        full_image=None,
        left_image="hw/l.png", middle_image="hw/m.png", right_image="hw/r.png",
        in_r2=True,
    )
    assert resp.left_image_url == "https://cdn.test/banners/hw/l.png"
    assert resp.middle_image_url == "https://cdn.test/banners/hw/m.png"
    assert resp.right_image_url == "https://cdn.test/banners/hw/r.png"
```

- [ ] **Step 2: Verify failure**

```bash
uv run pytest tests/unit/test_banner_schema.py -v
```

Expected: failure — `in_r2` field missing on `BannerResponse`.

- [ ] **Step 3: Add field and switch helper**

In `app/schemas/banner.py`, add to `BannerResponse` (after `right_image: str | None`, before `model_config`):

```python
    in_r2: bool = False
```

Replace `_image_url`:

```python
    def _image_url(self, path: str | None) -> str | None:
        if not path:
            return None
        if settings.R2_ENABLED and self.in_r2:
            return f"{settings.R2_PUBLIC_CDN_URL}/banners/{path.lstrip('/')}"
        return f"{settings.BANNER_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
```

- [ ] **Step 4: Verify pass + full unit suite**

```bash
uv run pytest tests/unit/test_banner_schema.py -v
uv run pytest tests/unit -x -q
```

If tests under `tests/unit/test_banner_*` fail because existing fixtures don't pass `in_r2`, the default of `False` should keep them green (Pydantic uses the default). Confirm with the run.

- [ ] **Step 5: Commit**

```bash
git add app/schemas/banner.py tests/unit/test_banner_schema.py
git commit -m "feat(banners): route image URLs through CDN when in_r2 set"
```

---

## Chunk 3: Avatar write path (dual-write + orphan delete)

Goal: avatar uploads dual-write to R2 when `R2_ENABLED`; failures log and continue with `avatar_in_r2=false`. Orphan-delete cleans up R2 when the old bit was true.

### Task 3.1: Extend `delete_avatar_if_orphaned` signature

**Files:**
- Modify: `app/services/avatar.py:179`
- Modify: `tests/unit/test_avatar.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_avatar.py`:

```python
class TestDeleteAvatarIfOrphanedR2:
    """Orphan deletion now considers the old_in_r2 bit."""

    @pytest.mark.asyncio
    async def test_signature_accepts_old_in_r2(self, tmp_path, monkeypatch):
        from app.services.avatar import delete_avatar_if_orphaned
        # Just confirm the new signature exists
        import inspect
        sig = inspect.signature(delete_avatar_if_orphaned)
        assert "old_in_r2" in sig.parameters
```

- [ ] **Step 2: Verify failure**

```bash
uv run pytest tests/unit/test_avatar.py::TestDeleteAvatarIfOrphanedR2 -v
```

Expected: FAIL — signature lacks `old_in_r2`.

- [ ] **Step 3: Update the function**

In `app/services/avatar.py`, change `delete_avatar_if_orphaned`:

```python
async def delete_avatar_if_orphaned(
    filename: str, old_in_r2: bool, db: AsyncSession
) -> bool:
    """Delete avatar file from disk (and R2 if old_in_r2) if no users reference it.

    Args:
        filename: Avatar filename to check
        old_in_r2: Whether this filename's R2 object existed at the start of the
            request — used to decide whether to issue an R2 delete.
        db: Database session

    Returns:
        True if the local file was deleted, False otherwise. R2 delete is
        best-effort and not reflected in the return value.
    """
    if not filename:
        return False

    result = await db.execute(
        select(func.count()).select_from(Users).where(Users.avatar == filename)  # type: ignore[arg-type]
    )
    count = result.scalar() or 0

    if count != 0:
        return False

    deleted_local = False
    file_path = Path(settings.AVATAR_STORAGE_PATH) / filename
    if file_path.exists():
        file_path.unlink()
        deleted_local = True
        logger.info("orphaned_avatar_deleted", filename=filename)

    if old_in_r2 and settings.R2_ENABLED:
        from app.core.r2_client import get_r2_storage
        r2 = get_r2_storage()
        await r2.delete_object(
            bucket=settings.R2_PUBLIC_BUCKET, key=f"avatars/{filename}"
        )
        logger.info("avatar_r2_orphan_deleted", key=f"avatars/{filename}")

    return deleted_local
```

- [ ] **Step 4: Update existing call sites**

`app/api/v1/users.py` has two callers:
- Line ~327: `await delete_avatar_if_orphaned(old_avatar, db)` → must capture `old_in_r2 = user.avatar_in_r2` before mutation, pass it.
- Line ~476 in `_delete_avatar`: same.

These edits are part of Task 3.2 below. For this task, only update the function signature and the existing tests so they still compile.

Update existing tests in `tests/unit/test_avatar.py` that call `delete_avatar_if_orphaned`: pass `old_in_r2=False` (preserves today's behavior — no R2 call).

- [ ] **Step 5: Verify pass + suite**

```bash
uv run pytest tests/unit/test_avatar.py -v
```

If `app/api/v1/users.py` still calls the old signature, expect the call sites to fail at runtime — since this is a dependency-injection wired path, it shows up under integration tests, not unit. Move quickly into Task 3.2; or if any unit test imports the route, fix the call site here.

```bash
uv run pytest tests/unit -x -q
```

Expected: green; if the integration suite is in scope, it'll be green after Task 3.2.

- [ ] **Step 6: Commit**

```bash
git add app/services/avatar.py tests/unit/test_avatar.py
git commit -m "feat(avatar): orphan-delete also clears R2 object when old_in_r2"
```

### Task 3.2: Dual-write in `_upload_avatar`, capture `old_in_r2` in `_delete_avatar`

**Files:**
- Modify: `app/api/v1/users.py:272–333` (`_upload_avatar`)
- Modify: `app/api/v1/users.py:439–478` (`_delete_avatar`)
- Create: `tests/unit/test_avatar_r2_dual_write.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_avatar_r2_dual_write.py`. This is moto-backed; reuse the `moto_server` / `moto_session` fixtures by importing them from `tests/unit/test_r2_storage.py` (or reproduce them) — for cleanliness, **lift those fixtures into `tests/unit/conftest.py`** as part of Task 2.1, and reuse here. (If skipped at Task 2.1, do it now.)

Sketch:

```python
"""Dual-write integration tests for avatar upload."""
import io
from pathlib import Path
from unittest.mock import patch
import pytest
from PIL import Image

from app.config import settings
from app.core.r2_client import get_r2_storage, reset_r2_storage
from app.services.avatar import _avatar_content_type


@pytest.fixture
def png_bytes():
    img = Image.new("RGB", (50, 50), color="red")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ... fixtures wiring R2_ENABLED=true, R2_PUBLIC_BUCKET, etc. to moto


@pytest.mark.asyncio
async def test_dual_write_success_sets_bit(...):
    """Upload writes local file AND R2 object, sets avatar_in_r2=true."""
    # Hit POST /users/me/avatar via the test client; assert
    # 1. local file at AVATAR_STORAGE_PATH/<md5>.png exists
    # 2. R2 head_object("avatars/<md5>.png") returns 200 with image/png
    # 3. user.avatar_in_r2 is True after refresh


@pytest.mark.asyncio
async def test_dual_write_r2_failure_falls_back(...):
    """If R2 upload raises, request still succeeds with avatar_in_r2=false."""
    # Patch R2Storage.upload_bytes to raise ClientError or similar
    # Assert local file exists, user.avatar_in_r2 is False
    # Assert avatar_r2_upload_failed log was emitted


@pytest.mark.asyncio
async def test_orphan_delete_clears_r2_when_old_in_r2(...):
    """Replacing an avatar with old_in_r2=true and no other refs deletes R2 object."""


@pytest.mark.asyncio
async def test_orphan_delete_skips_r2_when_old_in_r2_false(...):
    """Replacing an avatar with old_in_r2=false leaves R2 alone (and there is no R2 object anyway)."""


@pytest.mark.asyncio
async def test_same_md5_reupload_preserves_file(...):
    """User uploads identical bytes — orphan check sees the user still references the file post-commit, no delete fires."""
```

The exact test client / DB fixture pattern is determined by `tests/conftest.py`. Read it before fleshing these out.

- [ ] **Step 2: Verify the new tests fail**

```bash
uv run pytest tests/unit/test_avatar_r2_dual_write.py -v
```

Expected: failures (no dual-write yet).

- [ ] **Step 3: Modify `_upload_avatar` for dual-write**

Reading `app/api/v1/users.py:272–333`. Restructure to capture `old_in_r2` before mutation, dual-write to R2 after `save_avatar`, set the bit.

```python
async def _upload_avatar(
    user_id: int,
    avatar: UploadFile,
    db: AsyncSession,
) -> UserResponse:
    # ... unchanged: load user, raise 404, etc.

    old_avatar = user.avatar
    old_in_r2 = user.avatar_in_r2

    with tempfile.NamedTemporaryFile(delete=False) as temp_file:
        temp_path = FilePath(temp_file.name)
        content = await avatar.read()
        temp_file.write(content)

    try:
        validate_avatar_upload(avatar, temp_path)
        processed_content, ext = resize_avatar(temp_path)
        new_filename = save_avatar(processed_content, ext)

        new_in_r2 = False
        if settings.R2_ENABLED:
            try:
                r2 = get_r2_storage()
                await r2.upload_bytes(
                    bucket=settings.R2_PUBLIC_BUCKET,
                    key=f"avatars/{new_filename}",
                    body=processed_content,
                    content_type=_avatar_content_type(ext),
                )
                new_in_r2 = True
                logger.info(
                    "avatar_r2_uploaded",
                    user_id=user_id,
                    key=f"avatars/{new_filename}",
                )
            except Exception as e:
                logger.warning(
                    "avatar_r2_upload_failed",
                    user_id=user_id,
                    key=f"avatars/{new_filename}",
                    error=type(e).__name__,
                    error_msg=str(e),
                )

        user.avatar = new_filename
        user.avatar_in_r2 = new_in_r2
        await db.commit()
        await db.refresh(user)

        if old_avatar and old_avatar != new_filename:
            await delete_avatar_if_orphaned(old_avatar, old_in_r2, db)
    finally:
        temp_path.unlink(missing_ok=True)

    return UserResponse.model_validate(user)
```

Add the two new imports near the top of `app/api/v1/users.py`:

```python
from app.core.r2_client import get_r2_storage
from app.services.avatar import _avatar_content_type
```

(Confirm the existing imports — `validate_avatar_upload`, `resize_avatar`, `save_avatar`, `delete_avatar_if_orphaned` — are already imported around line 58–60.)

- [ ] **Step 4: Modify `_delete_avatar` to capture and pass `old_in_r2`**

In `_delete_avatar`:

```python
    old_avatar = user.avatar
    old_in_r2 = user.avatar_in_r2

    user.avatar = ""
    user.avatar_in_r2 = False
    await db.commit()
    await db.refresh(user)

    if old_avatar:
        await delete_avatar_if_orphaned(old_avatar, old_in_r2, db)
```

- [ ] **Step 5: Verify pass**

```bash
uv run pytest tests/unit/test_avatar_r2_dual_write.py tests/unit/test_avatar.py -v
```

If integration tests live under `tests/integration` for the routes, run those too:

```bash
uv run pytest tests/integration -x -q
```

Expected: green.

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/users.py tests/unit/test_avatar_r2_dual_write.py
git commit -m "feat(avatar): dual-write avatar uploads to R2 with fallback"
```

---

## Chunk 4: privmsgs.py — replace 7 inline URL builds

Goal: every avatar URL in `app/api/v1/privmsgs.py` flows through the helper. Each query that fetches `users.avatar` also fetches `users.avatar_in_r2`.

### Task 4.1: Replace each call site

**Files:**
- Modify: `app/api/v1/privmsgs.py` at lines 273, 415, 417, 577, 667, 741, 743 (and the surrounding queries that fetch `avatar`)

- [ ] **Step 1: Add helper import at top of `app/api/v1/privmsgs.py`**

```python
from app.services.avatar import avatar_url
```

- [ ] **Step 2: Walk each site and replace**

For each line with `f"{settings.IMAGE_BASE_URL}/images/avatars/{...}"`:

1. Read the surrounding query (a few lines up) — it selects `Users.avatar` (or aliases). Extend the SELECT to include `Users.avatar_in_r2`.
2. Where the result row is unpacked, capture both columns.
3. Replace the f-string with `avatar_url(<filename_var>, <in_r2_var>)`. Note the helper returns `str | None`; existing code already conditions on truthy `avatar`, so adapt.

Concrete example for line 273 area:

Before:
```python
sender_avatar = ...  # from query
avatar_url_str: str | None = None
if sender_avatar:
    avatar_url_str = f"{settings.IMAGE_BASE_URL}/images/avatars/{sender_avatar}"
```

After:
```python
sender_avatar, sender_avatar_in_r2 = ...  # extended query
avatar_url_str = avatar_url(sender_avatar, sender_avatar_in_r2)
```

Apply to all 7 sites (273, 415, 417, 577, 667, 741, 743) plus the corresponding queries.

- [ ] **Step 3: Run privmsg-related tests**

```bash
uv run pytest tests/unit/test_privmsg_schemas.py -v
uv run pytest tests/integration -k privmsg -v 2>&1 | tail -40   # if integration suite covers privmsg endpoints
```

Expected: green. Failures here usually mean a missed call site or a dropped column from the query.

- [ ] **Step 4: Final grep sweep**

```bash
grep -n "IMAGE_BASE_URL.*avatars" app/api/v1/privmsgs.py app/schemas/ app/api/
```

Expected: zero matches outside of test files.

- [ ] **Step 5: Run full unit suite**

```bash
uv run pytest tests/unit -x -q
```

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/privmsgs.py
git commit -m "refactor(privmsgs): route avatar URLs through helper"
```

---

## Chunk 5: backfill subcommands

Goal: `r2_sync.py avatars-backfill` and `r2_sync.py banners-backfill` exist, are gated by `R2_ALLOW_BULK_BACKFILL`, idempotent, dry-runnable.

### Task 5.1: `avatars-backfill`

**Files:**
- Modify: `scripts/r2_sync.py` (extend `_build_parser`, add `cmd_avatars_backfill`)
- Create: `tests/unit/test_r2_sync_avatars_backfill.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_r2_sync_avatars_backfill.py`. Use the existing moto fixtures (lifted to `conftest.py` in Chunk 2). Tests:

```python
@pytest.mark.asyncio
async def test_backfill_uploads_and_flips_bit(...):
    # Seed 2 users with avatars on disk, both avatar_in_r2=False
    # Run cmd_avatars_backfill
    # Assert both users have avatar_in_r2=True
    # Assert both R2 keys exist with image/png content type


@pytest.mark.asyncio
async def test_backfill_idempotent_skips_existing(...):
    # Seed 1 user, pre-upload the R2 object
    # Run cmd_avatars_backfill
    # Assert head_object short-circuits (no upload), bit flips to True


@pytest.mark.asyncio
async def test_backfill_skips_when_local_missing(...):
    # Seed 1 user with an avatar filename, NO local file on disk
    # Run cmd_avatars_backfill
    # Assert avatar_local_missing log captured, bit stays False, no R2 object


@pytest.mark.asyncio
async def test_backfill_dry_run_writes_nothing(...):
    # Seed 1 user, run with --dry-run
    # Assert no R2 object, bit stays False


def test_backfill_refuses_without_bulk_flag(monkeypatch):
    monkeypatch.setattr(settings, "R2_ENABLED", True)
    monkeypatch.setattr(settings, "R2_ALLOW_BULK_BACKFILL", False)
    with pytest.raises(BulkBackfillDisallowedError):
        require_bulk_backfill()
```

- [ ] **Step 2: Verify failure**

```bash
uv run pytest tests/unit/test_r2_sync_avatars_backfill.py -v
```

Expected: ImportError or AttributeError on `cmd_avatars_backfill`.

- [ ] **Step 3: Implement `cmd_avatars_backfill` in `scripts/r2_sync.py`**

Sketch (adapt to the existing CLI patterns in `scripts/r2_sync.py` — read `_build_parser`, the existing `cmd_*` functions, and `require_bulk_backfill` first):

```python
import asyncio
import mimetypes
from pathlib import Path as FilePath

from app.models.user import Users


async def cmd_avatars_backfill(args: argparse.Namespace) -> int:
    require_bulk_backfill()
    r2 = get_r2_storage()
    avatar_dir = FilePath(settings.AVATAR_STORAGE_PATH)
    sem = asyncio.Semaphore(args.concurrency)

    async with r2.bulk_session(), get_async_session() as db:
        result = await db.execute(
            select(Users.user_id, Users.avatar).where(
                Users.avatar != "", Users.avatar_in_r2 == False  # noqa: E712
            )
        )
        rows = result.all()

        async def process(user_id: int, filename: str) -> None:
            async with sem:
                local = avatar_dir / filename
                if not local.exists():
                    logger.warning(
                        "avatar_local_missing",
                        user_id=user_id,
                        filename=filename,
                    )
                    return
                key = f"avatars/{filename}"
                if not await r2.object_exists(
                    bucket=settings.R2_PUBLIC_BUCKET, key=key
                ):
                    if args.dry_run:
                        logger.info("avatar_r2_backfill_dry_run", key=key)
                        return
                    body = local.read_bytes()
                    ct = mimetypes.guess_type(filename)[0] or "application/octet-stream"
                    await r2.upload_bytes(
                        bucket=settings.R2_PUBLIC_BUCKET, key=key,
                        body=body, content_type=ct,
                    )
                    logger.info(
                        "avatar_r2_backfilled",
                        user_id=user_id, key=key, skipped_existing=False,
                    )
                else:
                    logger.info(
                        "avatar_r2_backfilled",
                        user_id=user_id, key=key, skipped_existing=True,
                    )
                if not args.dry_run:
                    await db.execute(
                        update(Users)
                        .where(Users.user_id == user_id)
                        .values(avatar_in_r2=True)
                    )
                    await db.commit()

        await asyncio.gather(*[process(uid, fn) for uid, fn in rows])

    return 0
```

Add to `_build_parser`:

```python
ab = sub.add_parser("avatars-backfill")
ab.add_argument("--dry-run", action="store_true")
ab.add_argument(
    "--concurrency", type=_positive_int, default=8,
    help="Max users processed in parallel (default: 8).",
)
```

Wire up in the main dispatch (find the existing dispatch table or `if/elif` chain in `main()`).

- [ ] **Step 4: Verify pass**

```bash
uv run pytest tests/unit/test_r2_sync_avatars_backfill.py -v
```

- [ ] **Step 5: Commit**

```bash
git add scripts/r2_sync.py tests/unit/test_r2_sync_avatars_backfill.py
git commit -m "feat(r2_sync): add avatars-backfill subcommand"
```

### Task 5.2: `banners-backfill`

**Files:**
- Modify: `scripts/r2_sync.py`
- Create: `tests/unit/test_r2_sync_banners_backfill.py`

- [ ] **Step 1: Write the failing tests**

Mirror `test_r2_sync_avatars_backfill.py`:

```python
@pytest.mark.asyncio
async def test_backfill_full_image_banner(...):
    # Seed 1 banner with full_image="eva/full.jpg" and the file on disk
    # Run cmd_banners_backfill
    # Assert bit flips to True, R2 object exists


@pytest.mark.asyncio
async def test_backfill_three_part_banner(...):
    # Seed 1 banner with left/middle/right_image and all 3 files on disk
    # Run cmd_banners_backfill
    # Assert bit flips, all 3 R2 objects exist


@pytest.mark.asyncio
async def test_backfill_three_part_partial_missing_skips_row(...):
    # Seed 1 banner with three parts, but only left/middle on disk
    # Run cmd_banners_backfill
    # Assert bit stays False, banner_local_missing logged for right_image
    # Critically: do NOT upload left/middle either — it's all-or-nothing per row


@pytest.mark.asyncio
async def test_backfill_idempotent_skips_existing(...):
    # Pre-upload the banner R2 object, run again
    # Assert no double-upload, bit flips to True
```

- [ ] **Step 2: Verify failure**

```bash
uv run pytest tests/unit/test_r2_sync_banners_backfill.py -v
```

- [ ] **Step 3: Implement `cmd_banners_backfill`**

```python
async def cmd_banners_backfill(args: argparse.Namespace) -> int:
    require_bulk_backfill()
    r2 = get_r2_storage()
    banner_dir = FilePath(settings.BANNER_STORAGE_PATH)

    async with r2.bulk_session(), get_async_session() as db:
        result = await db.execute(
            select(Banners).where(Banners.in_r2 == False)  # noqa: E712
        )
        banners = result.scalars().all()

        for banner in banners:
            paths = [
                p for p in (
                    banner.full_image, banner.left_image,
                    banner.middle_image, banner.right_image,
                ) if p
            ]
            # Pre-flight: confirm all files exist
            missing = [p for p in paths if not (banner_dir / p).exists()]
            if missing:
                for p in missing:
                    logger.warning(
                        "banner_local_missing",
                        banner_id=banner.banner_id, path=p,
                    )
                continue

            uploaded: list[str] = []
            skipped: list[str] = []
            for p in paths:
                key = f"banners/{p}"
                if await r2.object_exists(
                    bucket=settings.R2_PUBLIC_BUCKET, key=key
                ):
                    skipped.append(p)
                    continue
                if args.dry_run:
                    continue
                body = (banner_dir / p).read_bytes()
                ct = mimetypes.guess_type(p)[0] or "application/octet-stream"
                await r2.upload_bytes(
                    bucket=settings.R2_PUBLIC_BUCKET, key=key,
                    body=body, content_type=ct,
                )
                uploaded.append(p)

            if args.dry_run:
                continue

            await db.execute(
                update(Banners)
                .where(Banners.banner_id == banner.banner_id)
                .values(in_r2=True)
            )
            await db.commit()
            logger.info(
                "banner_r2_backfilled",
                banner_id=banner.banner_id,
                parts_uploaded=uploaded,
                parts_skipped=skipped,
            )

    return 0
```

Wire `banners-backfill` into `_build_parser` (no `--concurrency`; rows are tens):

```python
bb = sub.add_parser("banners-backfill")
bb.add_argument("--dry-run", action="store_true")
```

Wire up in the main dispatch.

- [ ] **Step 4: Verify pass**

```bash
uv run pytest tests/unit/test_r2_sync_banners_backfill.py -v
uv run pytest tests/unit -x -q
```

- [ ] **Step 5: Commit**

```bash
git add scripts/r2_sync.py tests/unit/test_r2_sync_banners_backfill.py
git commit -m "feat(r2_sync): add banners-backfill subcommand"
```

---

## Chunk 6: final wiring + verification

Goal: confirm no avatar URL is built outside the helper; full test suite green; PR-ready.

### Task 6.1: Sweep for forgotten URL builds

- [ ] **Step 1: Avatar grep**

```bash
grep -rn "IMAGE_BASE_URL.*avatars" app/ scripts/ | grep -v test_ | grep -v __pycache__
```

Expected: zero matches outside `app/services/avatar.py` (the helper itself).

If matches appear, route them through `avatar_url(...)` and run the relevant tests.

- [ ] **Step 2: Banner grep**

```bash
grep -rn "BANNER_BASE_URL" app/ scripts/ | grep -v test_ | grep -v __pycache__
```

Expected: only the helper site in `app/schemas/banner.py` and the config in `app/config.py`.

- [ ] **Step 3: Full test suite**

```bash
uv run pytest tests/ -x -q
```

Expected: all green.

- [ ] **Step 4: Type-check**

```bash
uv run mypy app/
```

Expected: clean (or no new errors introduced — compare to a pre-branch baseline if mypy already has known noise).

- [ ] **Step 5: Lint**

```bash
uv run ruff check app/ scripts/ tests/
uv run ruff format --check app/ scripts/ tests/
```

Expected: all green.

- [ ] **Step 6: Commit any final fixups**

```bash
git status
# resolve any straggling diffs, then:
git add -A
git commit -m "chore: tidy up after R2 avatars/banners landing" || true
```

### Task 6.2: PR

- [ ] **Step 1: Push branch and open PR**

```bash
git push -u origin feat/r2-avatars-banners
```

PR body should:
- Link to the design doc.
- Spell out the rollout sequence from the design doc § Rollout (deploy migration; run `avatars-backfill`; run `banners-backfill`; verify with `curl -I`).
- Note the dev-side no-op (`R2_ENABLED=false` keeps current behavior).
- Note no admin upload endpoint added.

- [ ] **Step 2: Verify CI**

Watch CI; fix any environment-specific test failures. Common gotchas:
- moto fixture port collision — fixed by `port=0` (random port, already used in existing tests).
- alembic migration ordering on a non-fresh CI DB — confirm CI runs migrations from scratch.

---

## Out-of-band notes (not implementation steps)

- **Production rollout** (post-merge, performed by an operator):
  ```bash
  uv run python scripts/r2_sync.py avatars-backfill --concurrency 8
  uv run python scripts/r2_sync.py banners-backfill
  ```
  Both require `R2_ENABLED=true` and `R2_ALLOW_BULK_BACKFILL=true` in the environment. Re-runs are safe.
- **After known R2 outage:** re-run `avatars-backfill` to mop up rows where the live write path fell back to `avatar_in_r2=false`.
- **Image Content-Type follow-up** (out of scope; design doc § Storage adapter changes notes this): existing image variants uploaded via `r2_sync.py split-existing` and `r2_finalize_upload_job` go through `R2Storage.upload_file`, which doesn't set Content-Type. A follow-up PR could fix this for image variants alongside.
