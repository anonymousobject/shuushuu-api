# Dead Link Marking Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow mods to mark tag external links as dead and optionally provide archive URLs.

**Architecture:** Add `dead_at` and `archive_url` columns to `tag_external_links`, expose via a new `PATCH` endpoint with `TAG_UPDATE` permission. TDD throughout.

**Tech Stack:** FastAPI, SQLModel, Alembic, pytest

---

## File Structure

- **Modify:** `app/models/tag_external_link.py` — add `dead_at` and `archive_url` fields
- **Modify:** `app/schemas/tag.py` — add `TagExternalLinkUpdate` schema, update `TagExternalLinkResponse`
- **Modify:** `app/api/v1/tags.py` — add `PATCH /{tag_id}/links/{link_id}` endpoint
- **Create:** `alembic/versions/xxxx_add_dead_link_fields.py` — migration for new columns
- **Modify:** `tests/api/v1/test_tags.py` — add tests for the new endpoint

---

## Task 1: Alembic Migration

**Files:**
- Create: `alembic/versions/xxxx_add_dead_link_fields.py`

- [ ] **Step 1: Create migration**

```bash
uv run alembic revision -m "add dead_at and archive_url to tag_external_links"
```

Edit the generated file:

```python
def upgrade() -> None:
    """Add dead_at and archive_url columns to tag_external_links."""
    op.add_column(
        "tag_external_links",
        sa.Column("dead_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "tag_external_links",
        sa.Column("archive_url", sa.String(length=2000), nullable=True),
    )


def downgrade() -> None:
    """Remove dead_at and archive_url columns from tag_external_links."""
    op.drop_column("tag_external_links", "archive_url")
    op.drop_column("tag_external_links", "dead_at")
```

- [ ] **Step 2: Run migration**

```bash
uv run alembic upgrade head
```

Expected: Migration applies cleanly.

- [ ] **Step 3: Commit**

```bash
git add alembic/versions/*_add_dead_at_and_archive_url_to_tag_external_links.py
git commit -m "feat: add dead_at and archive_url columns to tag_external_links"
```

---

## Task 2: Model and Schema Changes

**Files:**
- Modify: `app/models/tag_external_link.py` — add `dead_at` and `archive_url` fields
- Modify: `app/schemas/tag.py` — add `TagExternalLinkUpdate`, update `TagExternalLinkResponse`

- [ ] **Step 1: Update the model**

In `app/models/tag_external_link.py`, add to `TagExternalLinks` (the table class, NOT `TagExternalLinkBase` — these are database-managed fields that should not appear on create schemas):

```python
dead_at: datetime | None = Field(default=None)
archive_url: str | None = Field(default=None, max_length=2000)
```

Add these after the existing `date_added` field. The `datetime` import is already available in this module.

- [ ] **Step 2: Update TagExternalLinkResponse**

In `app/schemas/tag.py`, add to `TagExternalLinkResponse`:

```python
dead_at: UTCDatetimeOptional = None
archive_url: str | None = None
```

Add `UTCDatetimeOptional` to the import from `app.schemas.base`.

- [ ] **Step 3: Add TagExternalLinkUpdate schema**

In `app/schemas/tag.py`, add after `TagExternalLinkResponse`:

```python
class TagExternalLinkUpdate(BaseModel):
    """Schema for updating a tag external link (marking dead, adding archive URL)."""

    is_dead: bool | None = None
    archive_url: str | None = None

    @field_validator("archive_url")
    @classmethod
    def validate_archive_url(cls, v: str | None) -> str | None:
        """Validate archive URL has http/https protocol and trim whitespace."""
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("Archive URL cannot be empty")
        if not v.startswith(("http://", "https://")):
            raise ValueError("Archive URL must start with http:// or https://")
        if len(v) > 2000:
            raise ValueError("Archive URL exceeds maximum length of 2000 characters")
        return v
```

- [ ] **Step 4: Verify types pass**

```bash
uv run mypy app/models/tag_external_link.py app/schemas/tag.py
```

Expected: No errors.

- [ ] **Step 5: Commit**

```bash
git add app/models/tag_external_link.py app/schemas/tag.py
git commit -m "feat: add dead_at and archive_url to TagExternalLink model and schemas"
```

---

## Task 3: Tests for PATCH Endpoint

**Files:**
- Modify: `tests/api/v1/test_tags.py`

All tests go in the existing test file, following the established pattern (each test creates its own user, permissions, tag, and link). Add these tests after the existing `TestGetTagWithLinks` tests (around line 4220).

- [ ] **Step 1: Write test — mark link as dead**

```python
@pytest.mark.api
class TestUpdateTagExternalLink:
    """Tests for PATCH /tags/{tag_id}/links/{link_id} endpoint."""

    async def test_mark_link_as_dead(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test marking an external link as dead sets dead_at timestamp."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="admindeadlink",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="admindeadlink@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Create tag with link
        tag = Tags(title="dead link artist", desc="Test", type=TagType.ARTIST)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        link = TagExternalLinks(tag_id=tag.tag_id, url="https://example.com/dead")
        db_session.add(link)
        await db_session.commit()
        await db_session.refresh(link)

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "admindeadlink", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Mark link as dead
        response = await client.patch(
            f"/api/v1/tags/{tag.tag_id}/links/{link.link_id}",
            json={"is_dead": True},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["dead_at"] is not None
        assert data["archive_url"] is None
        assert data["url"] == "https://example.com/dead"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/api/v1/test_tags.py::TestUpdateTagExternalLink::test_mark_link_as_dead -v
```

Expected: FAIL (endpoint doesn't exist yet — 405 Method Not Allowed).

- [ ] **Step 3: Write test — mark link as dead with archive URL**

```python
    async def test_mark_link_dead_with_archive_url(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test marking a link dead and providing archive URL in one request."""
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username="admindeadarchive",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="admindeadarchive@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        tag = Tags(title="archive link artist", desc="Test", type=TagType.ARTIST)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        link = TagExternalLinks(tag_id=tag.tag_id, url="https://example.com/archived")
        db_session.add(link)
        await db_session.commit()
        await db_session.refresh(link)

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "admindeadarchive", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        response = await client.patch(
            f"/api/v1/tags/{tag.tag_id}/links/{link.link_id}",
            json={
                "is_dead": True,
                "archive_url": "https://web.archive.org/web/20090302035041/https://example.com/archived",
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["dead_at"] is not None
        assert data["archive_url"] == "https://web.archive.org/web/20090302035041/https://example.com/archived"
```

- [ ] **Step 4: Write test — unmark link as dead**

```python
    async def test_unmark_link_as_dead(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test setting is_dead=False clears dead_at."""
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username="adminunmark",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="adminunmark@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        tag = Tags(title="unmark artist", desc="Test", type=TagType.ARTIST)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Create link already marked dead
        from datetime import UTC, datetime
        link = TagExternalLinks(
            tag_id=tag.tag_id,
            url="https://example.com/revived",
            dead_at=datetime.now(UTC),
        )
        db_session.add(link)
        await db_session.commit()
        await db_session.refresh(link)

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "adminunmark", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        response = await client.patch(
            f"/api/v1/tags/{tag.tag_id}/links/{link.link_id}",
            json={"is_dead": False},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["dead_at"] is None
```

- [ ] **Step 5: Write test — update archive URL only**

```python
    async def test_set_archive_url_only(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test setting archive_url without changing dead status."""
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username="adminarchiveonly",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="adminarchiveonly@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        tag = Tags(title="archive only artist", desc="Test", type=TagType.ARTIST)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        link = TagExternalLinks(tag_id=tag.tag_id, url="https://example.com/archive-only")
        db_session.add(link)
        await db_session.commit()
        await db_session.refresh(link)

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "adminarchiveonly", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        response = await client.patch(
            f"/api/v1/tags/{tag.tag_id}/links/{link.link_id}",
            json={"archive_url": "https://web.archive.org/web/2024/https://example.com/archive-only"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["dead_at"] is None  # Not changed
        assert data["archive_url"] == "https://web.archive.org/web/2024/https://example.com/archive-only"
```

- [ ] **Step 6: Write test — 404 for nonexistent link**

```python
    async def test_update_nonexistent_link(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test updating a nonexistent link returns 404."""
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username="admin404link",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="admin404link@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        tag = Tags(title="404 link tag", desc="Test", type=TagType.ARTIST)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin404link", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        response = await client.patch(
            f"/api/v1/tags/{tag.tag_id}/links/99999",
            json={"is_dead": True},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 404
```

- [ ] **Step 7: Write test — 403 without permission**

```python
    async def test_update_link_without_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test updating a link without TAG_UPDATE permission returns 403."""
        # Create user WITHOUT permission
        user = Users(
            username="nopermupdate",
            password=get_password_hash("UserPassword123!"),
            password_type="bcrypt",
            salt="",
            email="nopermupdate@example.com",
            active=1,
            admin=0,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        tag = Tags(title="no perm tag", desc="Test", type=TagType.ARTIST)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        link = TagExternalLinks(tag_id=tag.tag_id, url="https://example.com/noperm")
        db_session.add(link)
        await db_session.commit()
        await db_session.refresh(link)

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "nopermupdate", "password": "UserPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        response = await client.patch(
            f"/api/v1/tags/{tag.tag_id}/links/{link.link_id}",
            json={"is_dead": True},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 403
```

- [ ] **Step 8: Write test — invalid archive URL rejected**

```python
    async def test_invalid_archive_url_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that archive URLs without http/https are rejected."""
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username="admininvalidarchive",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="admininvalidarchive@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        tag = Tags(title="invalid archive tag", desc="Test", type=TagType.ARTIST)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        link = TagExternalLinks(tag_id=tag.tag_id, url="https://example.com/invalid")
        db_session.add(link)
        await db_session.commit()
        await db_session.refresh(link)

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "admininvalidarchive", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        response = await client.patch(
            f"/api/v1/tags/{tag.tag_id}/links/{link.link_id}",
            json={"archive_url": "ftp://not-http.com"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 422
```

- [ ] **Step 9: Write test — dead_at preserved when already dead**

```python
    async def test_mark_dead_is_idempotent(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that marking an already-dead link doesn't change dead_at timestamp."""
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username="adminidempotent",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="adminidempotent@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        tag = Tags(title="idempotent artist", desc="Test", type=TagType.ARTIST)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Create link already marked dead at a known time
        from datetime import UTC, datetime
        original_dead_at = datetime(2025, 1, 1, tzinfo=UTC)
        link = TagExternalLinks(
            tag_id=tag.tag_id,
            url="https://example.com/idempotent",
            dead_at=original_dead_at,
        )
        db_session.add(link)
        await db_session.commit()
        await db_session.refresh(link)

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "adminidempotent", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        response = await client.patch(
            f"/api/v1/tags/{tag.tag_id}/links/{link.link_id}",
            json={"is_dead": True},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        # dead_at should remain the original value, not be updated
        assert data["dead_at"] == "2025-01-01T00:00:00Z"
```

- [ ] **Step 10: Write test — clear archive URL with explicit null**

```python
    async def test_clear_archive_url(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that sending archive_url: null clears an existing archive URL."""
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username="admincleararchive",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="admincleararchive@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        tag = Tags(title="clear archive artist", desc="Test", type=TagType.ARTIST)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Create link with archive URL already set
        link = TagExternalLinks(
            tag_id=tag.tag_id,
            url="https://example.com/clear-archive",
            archive_url="https://web.archive.org/web/2025/https://example.com/clear-archive",
        )
        db_session.add(link)
        await db_session.commit()
        await db_session.refresh(link)

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "admincleararchive", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        response = await client.patch(
            f"/api/v1/tags/{tag.tag_id}/links/{link.link_id}",
            json={"archive_url": None},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["archive_url"] is None
```

- [ ] **Step 11: Write test — 404 when link belongs to different tag**

```python
    async def test_update_link_on_wrong_tag(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that patching a link via a different tag's URL returns 404."""
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username="adminwrongtag",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="adminwrongtag@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Create two tags
        tag_a = Tags(title="tag a", desc="Test", type=TagType.ARTIST)
        tag_b = Tags(title="tag b", desc="Test", type=TagType.ARTIST)
        db_session.add_all([tag_a, tag_b])
        await db_session.commit()
        await db_session.refresh(tag_a)
        await db_session.refresh(tag_b)

        # Link belongs to tag_a
        link = TagExternalLinks(tag_id=tag_a.tag_id, url="https://example.com/wrong-tag")
        db_session.add(link)
        await db_session.commit()
        await db_session.refresh(link)

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "adminwrongtag", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Try to patch link via tag_b's URL
        response = await client.patch(
            f"/api/v1/tags/{tag_b.tag_id}/links/{link.link_id}",
            json={"is_dead": True},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 404
```

- [ ] **Step 12: Write test — tag detail includes dead_at and archive_url**

```python
    async def test_tag_detail_includes_dead_link_fields(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that tag detail response includes dead_at and archive_url on links."""
        tag = Tags(title="detail dead link", desc="Test", type=TagType.ARTIST)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        from datetime import UTC, datetime
        link = TagExternalLinks(
            tag_id=tag.tag_id,
            url="https://example.com/detail",
            dead_at=datetime(2025, 6, 15, tzinfo=UTC),
            archive_url="https://web.archive.org/web/2025/https://example.com/detail",
        )
        db_session.add(link)
        await db_session.commit()

        response = await client.get(f"/api/v1/tags/{tag.tag_id}")
        assert response.status_code == 200
        data = response.json()
        assert len(data["links"]) == 1
        link_data = data["links"][0]
        assert link_data["dead_at"] == "2025-06-15T00:00:00Z"
        assert link_data["archive_url"] == "https://web.archive.org/web/2025/https://example.com/detail"
```

- [ ] **Step 13: Run all tests to verify they fail**

```bash
uv run pytest tests/api/v1/test_tags.py::TestUpdateTagExternalLink -v
```

Expected: All 10 tests FAIL (endpoint doesn't exist yet — 405 Method Not Allowed).

- [ ] **Step 14: Commit tests**

```bash
git add tests/api/v1/test_tags.py
git commit -m "test: add tests for PATCH tag external link (dead link marking)"
```

---

## Task 4: PATCH Endpoint Implementation

**Files:**
- Modify: `app/api/v1/tags.py` — add PATCH endpoint
- Modify: `app/schemas/tag.py` — import `TagExternalLinkUpdate` (if not already imported in router)

- [ ] **Step 1: Add imports to `tags.py`**

Add `from datetime import UTC, datetime` to the top of `app/api/v1/tags.py` (not currently imported).

Add `TagExternalLinkUpdate` to the existing import block from `app.schemas.tag` (around line 40).

- [ ] **Step 2: Add the PATCH endpoint**

In `app/api/v1/tags.py`, add after the existing `delete_tag_link` function (around line 1517), before the `batch_tag_operation` function.

```python
@router.patch("/{tag_id}/links/{link_id}", response_model=TagExternalLinkResponse)
async def update_tag_link(
    tag_id: Annotated[int, Path(description="Tag ID")],
    link_id: Annotated[int, Path(description="Link ID")],
    link_update: TagExternalLinkUpdate,
    _: Annotated[None, Depends(require_permission(Permission.TAG_UPDATE))],
    db: AsyncSession = Depends(get_db),
) -> TagExternalLinkResponse:
    """
    Update an external link on a tag (mark dead, add archive URL).

    Requires TAG_UPDATE permission.
    Returns 404 if link doesn't exist or doesn't belong to the specified tag.
    """
    link_result = await db.execute(
        select(TagExternalLinks)
        .where(TagExternalLinks.link_id == link_id)  # type: ignore[arg-type]
        .where(TagExternalLinks.tag_id == tag_id)  # type: ignore[arg-type]
    )
    link = link_result.scalar_one_or_none()

    if not link:
        raise HTTPException(
            status_code=404,
            detail="Link not found or does not belong to this tag",
        )

    if link_update.is_dead is True and link.dead_at is None:
        link.dead_at = datetime.now(UTC)
    elif link_update.is_dead is False:
        link.dead_at = None

    if "archive_url" in link_update.model_fields_set:
        link.archive_url = link_update.archive_url

    await db.commit()
    await db.refresh(link)
    return TagExternalLinkResponse.model_validate(link)
```

The key pattern: use Pydantic's `model_fields_set` to distinguish "field omitted" from "field explicitly set to null". When `archive_url` is omitted from the request body, it won't appear in `model_fields_set`, so the existing value is preserved. When explicitly sent as `null`, it will be in `model_fields_set` and the value will be cleared.

- [ ] **Step 3: Run all new tests**

```bash
uv run pytest tests/api/v1/test_tags.py::TestUpdateTagExternalLink -v
```

Expected: All 10 tests PASS.

- [ ] **Step 4: Run full test suite**

```bash
uv run pytest tests/api/v1/test_tags.py -v
```

Expected: All existing tests still pass (especially `test_get_tag_with_links` which now returns extra fields).

- [ ] **Step 5: Run mypy**

```bash
uv run mypy app/api/v1/tags.py app/schemas/tag.py app/models/tag_external_link.py
```

Expected: No type errors.

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/tags.py app/schemas/tag.py
git commit -m "feat: add PATCH endpoint to mark tag external links as dead"
```
