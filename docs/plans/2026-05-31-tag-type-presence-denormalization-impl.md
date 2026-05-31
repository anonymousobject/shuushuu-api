# Tag-Type Presence Denormalization Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the timing-out `missing_tag_types` anti-join with maintained per-image boolean flags (`has_theme/has_source/has_artist/has_character`) so the filter is a fast, sargable, indexed column predicate.

**Architecture:** 4 internal boolean columns on `images` (migration + model), kept correct by a single flush-first, set-based recompute helper hooked into every tag-link mutation site and every tag-level operation that changes type composition. The filter rewrites to column predicates. A separate batched script backfills the 1.1M existing rows.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy async, MariaDB 12, Alembic, pytest (async), `uv`.

**Spec:** `docs/plans/2026-05-31-tag-type-presence-denormalization-design.md`

## Key facts (verified against the codebase)

- Test DB runs `alembic upgrade head` (`tests/conftest.py:229`), so the migration MUST be valid for the suite to have the columns.
- Session is `autoflush=False` (`app/core/database.py`), so the recompute helper MUST `await db.flush()` before reading `tag_links`.
- Services do NOT commit (routes/callers commit). The helper must NOT commit — it joins the caller's transaction. Call it BEFORE the `await db.commit()` in each path.
- Migration DDL precedent (`alembic/versions/...add_in_r2_to_banners.py`): `op.execute("ALTER TABLE ... ADD COLUMN ... BOOLEAN NOT NULL DEFAULT 0, ALGORITHM=INSTANT, LOCK=NONE")`.
- House idiom for boolean-column comparison: `Col == False  # noqa: E712` (e.g. `app/models/comment.py:95`). `and_`/`or_` already imported in `app/api/v1/images.py:28`.
- Raw `text()` SQL is idiomatic here (cf. `get_tag_hierarchy` CTE in `tags.py`, repost `INSERT ... SELECT`).
- Use `uv run` for all Python. Pre-commit runs ruff and blocks commits to `main` — work stays on branch `missing-tag-type-filter`.

---

## Chunk 1: Schema, model, recompute helper, filter rewrite

### Task 1: Add the 4 columns + indexes (model + migration)

**Files:**
- Modify: `app/models/image.py` (Images table fields + `__table_args__`)
- Create: `alembic/versions/<generated>_add_tag_type_flags_to_images.py`
- Test: `tests/api/v1/test_images.py` (one assertion in a new test)

- [ ] **Step 1: Add the model fields.** In `app/models/image.py`, in the `Images` table class with the other internal scalar fields (near `medium`/`large`/`total_pixels`, after `image_id`/scalar fields — match surrounding style), add:

```python
    # Denormalized tag-type presence (maintained by app.services.tag_type_flags).
    # Internal cache of "image has >=1 tag of this type"; source of truth is tag_links + tags.type.
    has_theme: bool = Field(default=False)
    has_source: bool = Field(default=False)
    has_artist: bool = Field(default=False)
    has_character: bool = Field(default=False)
```

- [ ] **Step 2: Add the indexes** to `Images.__table_args__` (alongside the existing `Index(...)` entries):

```python
        Index("idx_images_has_theme", "has_theme", "image_id"),
        Index("idx_images_has_source", "has_source", "image_id"),
        Index("idx_images_has_artist", "has_artist", "image_id"),
        Index("idx_images_has_character", "has_character", "image_id"),
```

- [ ] **Step 3: Generate the migration.**

Run: `uv run alembic revision -m "add tag-type flags to images"`
Edit the generated file so `upgrade()`/`downgrade()` are (keep the auto-generated `revision`/`down_revision`; do NOT hand-edit those):

```python
def upgrade() -> None:
    # Metadata-only column add (INSTANT) — instant on the 1.1M-row table.
    op.execute(
        "ALTER TABLE images "
        "ADD COLUMN has_theme BOOLEAN NOT NULL DEFAULT 0, "
        "ADD COLUMN has_source BOOLEAN NOT NULL DEFAULT 0, "
        "ADD COLUMN has_artist BOOLEAN NOT NULL DEFAULT 0, "
        "ADD COLUMN has_character BOOLEAN NOT NULL DEFAULT 0, "
        "ALGORITHM=INSTANT, LOCK=NONE"
    )
    # Online index builds (INPLACE, non-blocking).
    for col in ("has_theme", "has_source", "has_artist", "has_character"):
        op.execute(
            f"CREATE INDEX idx_images_{col} ON images ({col}, image_id) "
            "ALGORITHM=INPLACE, LOCK=NONE"
        )


def downgrade() -> None:
    for col in ("has_theme", "has_source", "has_artist", "has_character"):
        op.execute(f"DROP INDEX idx_images_{col} ON images")
    op.execute(
        "ALTER TABLE images "
        "DROP COLUMN has_theme, DROP COLUMN has_source, "
        "DROP COLUMN has_artist, DROP COLUMN has_character, "
        "ALGORITHM=INSTANT, LOCK=NONE"
    )
```

- [ ] **Step 4: Apply the migration locally.**

Run: `uv run alembic upgrade head`
Expected: succeeds. Then `uv run alembic downgrade -1 && uv run alembic upgrade head` to prove the down/up round-trips cleanly. (If your local dev DB is the large one and INPLACE index builds are slow, that's expected — the test DB is small.)

- [ ] **Step 5: Write a test that the columns exist and default to False.**

Append to `tests/api/v1/test_images.py` inside `TestMissingTagTypes` (it gets the migration via conftest):

```python
    async def test_new_image_defaults_tag_type_flags_false(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """The denormalized presence flags exist and default to False on a fresh image."""
        img = Images(**{**sample_image_data, "filename": "flags_default", "md5_hash": "9c" * 16})
        db_session.add(img)
        await db_session.commit()
        await db_session.refresh(img)
        assert img.has_theme is False
        assert img.has_source is False
        assert img.has_artist is False
        assert img.has_character is False
```

- [ ] **Step 6: Run it.** `uv run pytest tests/api/v1/test_images.py::TestMissingTagTypes::test_new_image_defaults_tag_type_flags_false -v` → PASS (proves the migration ran in the test DB and the model maps the columns).

- [ ] **Step 7: mypy + commit.**

Run: `uv run mypy app/models/image.py` → clean.
```bash
git add app/models/image.py alembic/versions/
git commit -m "feat(images): add denormalized tag-type presence columns + indexes"
```

---

### Task 2: Recompute-from-source helper

**Files:**
- Create: `app/services/tag_type_flags.py`
- Test: `tests/unit/test_tag_type_flags.py` (new) — or `tests/api/v1/` if a DB session fixture is needed; use whichever fixture pattern gives an async `db_session` + ability to insert Images/Tags/TagLinks (mirror `tests/api/v1/test_images.py`).

- [ ] **Step 1: Write the failing test.** Create `tests/api/v1/test_tag_type_flags.py`:

```python
import pytest
from httpx import AsyncClient  # not used directly but keeps fixture parity if needed
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.models import Images, TagLinks, Tags
from app.services.tag_type_flags import (
    refresh_image_tag_type_flags,
    refresh_images_tag_type_flags,
)


@pytest.mark.api
class TestTagTypeFlagsHelper:
    async def test_refresh_sets_flags_for_present_types(
        self, db_session: AsyncSession, sample_image_data: dict
    ):
        img = Images(**{**sample_image_data, "filename": "ttf1", "md5_hash": "11" * 16})
        db_session.add(img)
        await db_session.flush()
        artist = Tags(title="ttf artist", desc="a", type=TagType.ARTIST)
        theme = Tags(title="ttf theme", desc="t", type=TagType.THEME)
        db_session.add_all([artist, theme])
        await db_session.flush()
        # NOTE: links added but NOT flushed — exercises the helper's internal flush.
        db_session.add(TagLinks(image_id=img.image_id, tag_id=artist.tag_id, user_id=1))
        db_session.add(TagLinks(image_id=img.image_id, tag_id=theme.tag_id, user_id=1))

        await refresh_image_tag_type_flags(db_session, img.image_id)

        await db_session.refresh(img)
        assert img.has_artist is True
        assert img.has_theme is True
        assert img.has_source is False
        assert img.has_character is False

    async def test_refresh_clears_flag_when_last_tag_of_type_gone(
        self, db_session: AsyncSession, sample_image_data: dict
    ):
        img = Images(**{**sample_image_data, "filename": "ttf2", "md5_hash": "22" * 16})
        db_session.add(img)
        await db_session.flush()
        artist = Tags(title="ttf artist2", desc="a", type=TagType.ARTIST)
        db_session.add(artist)
        await db_session.flush()
        db_session.add(TagLinks(image_id=img.image_id, tag_id=artist.tag_id, user_id=1))
        await refresh_image_tag_type_flags(db_session, img.image_id)
        await db_session.refresh(img)
        assert img.has_artist is True

        from sqlalchemy import delete
        await db_session.execute(
            delete(TagLinks).where(
                TagLinks.image_id == img.image_id, TagLinks.tag_id == artist.tag_id
            )
        )
        await refresh_image_tag_type_flags(db_session, img.image_id)
        await db_session.refresh(img)
        assert img.has_artist is False

    async def test_refresh_empty_set_is_noop(self, db_session: AsyncSession):
        await refresh_images_tag_type_flags(db_session, [])  # must not raise
```

- [ ] **Step 2: Run → FAIL** (module doesn't exist). `uv run pytest tests/api/v1/test_tag_type_flags.py -v`

- [ ] **Step 3: Implement** `app/services/tag_type_flags.py`:

```python
"""Maintain denormalized per-image tag-type presence flags on the images table.

Source of truth is tag_links + tags.type; these helpers recompute the cached
has_theme/has_source/has_artist/has_character columns from it (idempotent).
"""

from collections.abc import Collection

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

# Single set-based recompute over a set of image_ids. MariaDB multi-table UPDATE;
# MAX(t.type = N) is a boolean aggregate (1 if any tag of that type, else 0).
_RECOMPUTE_SQL = text(
    """
    UPDATE images i
    LEFT JOIN (
        SELECT tl.image_id,
               MAX(t.type = 1) AS ht,
               MAX(t.type = 2) AS hs,
               MAX(t.type = 3) AS ha,
               MAX(t.type = 4) AS hc
        FROM tag_links tl
        JOIN tags t ON tl.tag_id = t.tag_id
        WHERE tl.image_id IN :ids
        GROUP BY tl.image_id
    ) agg ON agg.image_id = i.image_id
    SET i.has_theme = COALESCE(agg.ht, 0),
        i.has_source = COALESCE(agg.hs, 0),
        i.has_artist = COALESCE(agg.ha, 0),
        i.has_character = COALESCE(agg.hc, 0)
    WHERE i.image_id IN :ids
    """
).bindparams(bindparam("ids", expanding=True))


async def refresh_images_tag_type_flags(
    db: AsyncSession, image_ids: Collection[int]
) -> None:
    """Recompute the 4 tag-type presence flags for the given images from tag_links.

    Idempotent. Does NOT commit — joins the caller's transaction. Flushes first
    because the session is autoflush=False and add-tag paths leave pending,
    unflushed TagLinks the recompute SELECT must see.
    """
    ids = list({int(i) for i in image_ids})
    if not ids:
        return
    await db.flush()
    await db.execute(_RECOMPUTE_SQL, {"ids": ids})


async def refresh_image_tag_type_flags(db: AsyncSession, image_id: int) -> None:
    """Convenience wrapper for a single image."""
    await refresh_images_tag_type_flags(db, [image_id])
```

- [ ] **Step 4: Run → PASS.** `uv run pytest tests/api/v1/test_tag_type_flags.py -v`

- [ ] **Step 5: mypy + commit.** `uv run mypy app/services/tag_type_flags.py` → clean.
```bash
git add app/services/tag_type_flags.py tests/api/v1/test_tag_type_flags.py
git commit -m "feat(tags): add tag-type presence recompute helper"
```

---

### Task 3: Rewrite the `missing_tag_types` filter to use the flags

**Files:**
- Modify: `app/api/v1/images.py` (the `missing_tag_types` where-block, ~lines 462–500)
- Modify: `tests/api/v1/test_images.py` (`TestMissingTagTypes` fixtures must set flags)

- [ ] **Step 1: Update the existing behavior tests' fixtures FIRST (they will fail against the new mechanism otherwise, but for the right reason).** In `tests/api/v1/test_images.py`, in each `TestMissingTagTypes` test that inserts `TagLinks` and then queries (`test_missing_single_type_*`, `test_missing_all_mode_*`, `test_missing_any_mode_*`, `test_missing_artist_does_not_resolve_alias`, `test_missing_combined_with_include_tags`), after the `await db_session.commit()` that persists the links, call the helper for every seeded image, then commit again. Add at the top of the file:

```python
from app.services.tag_type_flags import refresh_image_tag_type_flags
```

And after each fixture's link `commit()`, e.g.:

```python
        await db_session.commit()
        # Maintain denormalized flags the filter now reads (prod does this via hooks).
        for img in (has_artist, no_artist):
            await refresh_image_tag_type_flags(db_session, img.image_id)
        await db_session.commit()
```

(Apply to each test using the actual image variables it created. The no-tag and 422/omitted/validation/unicode tests need no change — no links to reflect.)

- [ ] **Step 2: Run the class against the OLD implementation → still PASS** (anti-join still works; the helper-maintained flags are just unused so far). `uv run pytest "tests/api/v1/test_images.py::TestMissingTagTypes" -v`. This confirms the fixture edits didn't break anything before the rewrite.

- [ ] **Step 3: Rewrite the where-block.** Replace the entire `if missing_type_ids:` body (the `all`/`else` branches with the `notin_`/`lacks_type` anti-joins) so the parse + validation stay and only the query construction changes:

```python
        missing_type_ids = sorted({int(t) for t in raw_tokens})
        if missing_type_ids:
            type_column = {
                1: Images.has_theme,
                2: Images.has_source,
                3: Images.has_artist,
                4: Images.has_character,
            }
            # "missing type T" == that image's has_<type> flag is False.
            clauses = [
                type_column[t] == False  # type: ignore[arg-type]  # noqa: E712
                for t in missing_type_ids
            ]
            if missing_tag_types_mode == "all":
                query = query.where(and_(*clauses))  # missing every listed type
            else:
                query = query.where(or_(*clauses))  # missing at least one listed type
```

Leave the param definitions, `raw_tokens` parsing, `isdecimal()` validation, and the 400 raise unchanged.

- [ ] **Step 4: Run the class → PASS.** `uv run pytest "tests/api/v1/test_images.py::TestMissingTagTypes" -v` (all behavior tests green against the new flag-based mechanism).

- [ ] **Step 5: mypy/ruff reconcile.** `uv run mypy app/api/v1/images.py` → clean. The house idiom (verified at `app/api/v1/comments.py:95`) is `col == False  # type: ignore[arg-type]  # noqa: E712`; apply that to each clause (the `noqa` suppresses ruff E712, the `type: ignore[arg-type]` satisfies mypy). `uv run ruff check app/api/v1/images.py` → clean.

- [ ] **Step 6: Commit.**
```bash
git add app/api/v1/images.py tests/api/v1/test_images.py
git commit -m "feat(images): filter missing_tag_types via denormalized flags"
```

---

## Chunk 2: Maintenance hooks

Pattern for every hook task: call the recompute helper for the affected image(s) **before** the path's `await db.commit()`, then add an integration test that drives the real endpoint/service and asserts the flag flips. Import inside the function or at module top, matching the file's import style.

**Test assertion caveat (applies to every Chunk 2 task):** the recompute is a raw `UPDATE` that bypasses the ORM, and conftest uses `expire_on_commit=False`, so a pre-fetched `Images` object keeps **stale** `has_*` attributes. Assert by **re-querying** the image (`select(Images).where(...)`) or `await db.refresh(img)` first — never on an `Images` instance loaded before the helper ran. (The filter tests in Task 3 are unaffected: they hit the SQL WHERE clause, not a Python object.)

### Task 4: Single add/remove tag (images.py)

**Files:**
- Modify: `app/api/v1/images.py` (add handler ~line 1828 `await db.commit()`; remove handler ~line 1899 `await db.commit()`)
- Test: `tests/api/v1/test_images.py`

- [ ] **Step 1: Failing tests** — add to a suitable class (e.g. a new `TestTagTypeFlagMaintenance` in `test_images.py`). Use the real add/remove endpoints with an authenticated client (mirror existing add/remove-tag tests in the file for auth fixture usage; find them via `grep -n "tags\b" tests/api/v1/test_images.py` and the `/tags` POST/DELETE routes). Assert: after POST add of an artist tag, the image row `has_artist` is True; after DELETE of that tag, `has_artist` is False.

- [ ] **Step 2: Run → FAIL** (flags not maintained yet).

- [ ] **Step 3: Implement.** In the add handler, immediately before `await db.commit()` (the one at ~line 1828, after `db.add(history_entry)`):
```python
    await refresh_image_tag_type_flags(db, image_id)
```
In the remove handler, immediately before its `await db.commit()` (~line 1899, after the `delete(TagLinks)` execute):
```python
    await refresh_image_tag_type_flags(db, image_id)
```
Add `from app.services.tag_type_flags import refresh_image_tag_type_flags` to the imports.

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: mypy + commit.** `git commit -m "feat(images): maintain tag-type flags on single tag add/remove"`

---

### Task 5: Upload (initial tagging)

**Files:**
- Modify: `app/services/upload.py` (`link_tags_to_image`, loop ends ~line 157; function has no commit — caller commits)
- Test: `tests/api/v1/` or `tests/unit/` — an upload-path test that asserts a freshly uploaded image with an artist tag has `has_artist=True`. Reuse existing upload test patterns (`grep -rn "link_tags_to_image\|upload" tests/`).

- [ ] **Step 1: Failing test.** Drive `link_tags_to_image` (or the upload endpoint) with an artist tag; assert `has_artist` True after the caller commits.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** At the END of `link_tags_to_image`, after the `for` loop (all `db.add(TagLinks(...))` done), add:
```python
    await refresh_image_tag_type_flags(db, image_id)
```
Import the helper. (One call for the image; the helper flushes the pending links.)
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: mypy + commit.** `git commit -m "feat(upload): maintain tag-type flags on initial tagging"`

---

### Task 6: Batch add / remove (batch_tag.py)

**Files:**
- Modify: `app/services/batch_tag.py` (`batch_add_tags` before commit ~line 136; `batch_remove_tags` before commit ~line 276)
- Test: `tests/` batch-tag tests (`grep -rn "batch_add_tags\|batch_remove_tags\|batch" tests/`)

- [ ] **Step 1: Failing tests.** Batch-add an artist tag to two images → both `has_artist` True; batch-remove → both False.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** Reuse the bookkeeping each function already keeps (no new tracking needed), and call before the `await db.commit()`:
```python
    await refresh_images_tag_type_flags(db, affected_image_ids)
```
- `batch_add_tags`: `affected_image_ids = {i.image_id for i in added}` (the existing `added` result list; each item carries `image_id`).
- `batch_remove_tags`: `affected_image_ids = {p[0] for p in pairs_to_remove}` (the existing `pairs_to_remove` set of `(image_id, tag_id)`).
Import `refresh_images_tag_type_flags`.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: mypy + commit.** `git commit -m "feat(batch-tag): maintain tag-type flags on batch add/remove"`

---

### Task 7: Repost merge (repost.py)

**Files:**
- Modify: `app/services/repost.py` (`migrate_repost_data`, before `return` ~line 155; no commit — both callers commit)
- Test: `tests/` repost tests (`grep -rn "migrate_repost_data\|repost" tests/`)

- [ ] **Step 1: Failing test.** Set up `original_id` with NO artist tag and `repost_id` WITH an artist tag; run `migrate_repost_data`; commit; assert `original_id.has_artist` is now True (it gained the repost's tags) and `repost_id` flags all False (its links were deleted).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** Immediately before the `return {...}` in `migrate_repost_data`, after the `delete(TagLinks)` for `repost_id`:
```python
    await refresh_images_tag_type_flags(db, [original_id, repost_id])
```
Import the helper. This covers both callers (`images.py:1023`, `admin.py:770`) atomically since neither commits before calling.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: mypy + commit.** `git commit -m "feat(repost): maintain tag-type flags on repost merge"`

---

### Task 8: Admin report-resolution (admin.py)

**Files:**
- Modify: `app/api/v1/admin.py` (the suggestion loop ~lines 1559–1595; find the handler's `await db.commit()` after the loop)
- Test: `tests/api/v1/test_admin_images.py` (report/suggestion setup) — there is no `admin_client` fixture; admin tests create an admin via the per-file `create_admin_user(db_session, "name")` helper (see `tests/api/v1/test_admin_actions.py:29`). Reuse that pattern and the report-setup fixtures already in `test_admin_images.py`.

- [ ] **Step 1: Failing test.** Approve an "add artist tag" suggestion for a report's image → image `has_artist` True; approve a "remove" of the last artist tag → False. Assert by re-querying the image (see Chunk 2 caveat).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** After the suggestion loop and before the handler's `await db.commit()`, add a single refresh for the report's image:
```python
    await refresh_image_tag_type_flags(db, report.image_id)
```
Import the helper.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: mypy + commit.** `git commit -m "feat(admin): maintain tag-type flags on report tag-suggestion resolution"`

---

### Task 9: Tag delete (tags.py)

**Files:**
- Modify: `app/api/v1/tags.py` (`delete_tag`, ~lines 1586–1593)
- Test: `tests/api/v1/test_tags*.py` (or `test_images.py`)

- [ ] **Step 1: Failing test.** Image has exactly one artist tag (`has_artist` True via the helper). DELETE that tag. Assert the image `has_artist` is now False (the cascade removed its only artist link).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** Link cleanup is DB FK CASCADE (no ORM relationship), so capture affected images BEFORE deleting, flush so the cascade applies, then recompute:
```python
    # Capture images linked to this tag before the CASCADE removes the links.
    affected = await db.execute(
        select(TagLinks.image_id).where(TagLinks.tag_id == tag_id)  # type: ignore[call-overload]
    )
    affected_image_ids = [row[0] for row in affected]

    await db.delete(tag)
    await db.flush()  # apply the FK CASCADE within this transaction before recompute
    await refresh_images_tag_type_flags(db, affected_image_ids)
    await db.commit()
```
Import `refresh_images_tag_type_flags`.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: mypy + commit.** `git commit -m "feat(tags): maintain tag-type flags on tag delete"`

---

> **Note (Tasks 10 & 11):** `update_tag` has a SINGLE `await db.commit()` (~line 1558) covering both the type-change branch (~1360) and the alias branch (~1455). Don't look for two commits. Both recompute calls land before that one commit; a combined type+alias PATCH runs both (idempotent, harmless).

### Task 10: Tag type change (tags.py update_tag)

**Files:**
- Modify: `app/api/v1/tags.py` (`update_tag`, type-change branch ~line 1360; single commit ~line 1558)
- Test: `tests/api/v1/test_tags*.py`

- [ ] **Step 1: Failing test.** Image linked to a tag of type ARTIST (`has_artist` True, `has_source` False). PATCH the tag's `type` to SOURCE. Assert the image is now `has_artist` False, `has_source` True.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** In the `if tag.type != original_type:` branch, gather the affected images and recompute (set-based; bounded by the tag's usage). Add before the handler's commit:
```python
    if tag.type != original_type:
        # ... existing audit log ...
        affected = await db.execute(
            select(TagLinks.image_id).where(TagLinks.tag_id == tag_id)  # type: ignore[call-overload]
        )
        await refresh_images_tag_type_flags(db, [row[0] for row in affected])
```
(The helper flushes, so the in-session `tag.type` change is visible to the recompute JOIN.) Import the helper.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: mypy + commit.** `git commit -m "feat(tags): maintain tag-type flags on tag type change"`

---

### Task 11: Tag merge / alias reassignment (tags.py)

**Files:**
- Modify: `app/api/v1/tags.py` (alias-set branch ~lines 1455–1488)
- Test: `tests/api/v1/test_tags*.py`

- [ ] **Step 1: Failing/characterization test.** This is nearly always a flag no-op (API enforces alias & canonical share type), so the test mainly guards idempotency: image linked to an alias-artist tag (`has_artist` True). Set the alias's `alias_of` to a canonical artist tag (reassigns links). Assert the image still has `has_artist` True. (If it already passes once the recompute is added, that's the expected lock-in.)
- [ ] **Step 2: Run → confirm behavior** (may pass even pre-change because flags don't move; the recompute makes it explicitly correct/drift-proof).
- [ ] **Step 3: Implement.** Before the usage_count recompute block (after the `update(TagLinks).values(tag_id=canonical_id)`), capture and refresh the affected images:
```python
        # Refresh flags for images whose links moved (idempotent; types match so usually a no-op).
        affected = await db.execute(
            select(TagLinks.image_id).where(TagLinks.tag_id == canonical_id)  # type: ignore[call-overload]
        )
        await refresh_images_tag_type_flags(db, [row[0] for row in affected])
```
Import the helper.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: mypy + commit.** `git commit -m "feat(tags): refresh tag-type flags on alias reassignment"`

---

## Chunk 3: Backfill + verification

### Task 12: Backfill script

**Files:**
- Create: `scripts/backfill_tag_type_flags.py`
- Test: `tests/` — a test that seeds images+links, runs the batched recompute over a range, and asserts flags match.

- [ ] **Step 1: Failing test.** Seed 2 images (one with an artist tag, one with none), call the script's batch function over their id range, assert flags correct.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** `scripts/backfill_tag_type_flags.py` — a resumable, batched, idempotent backfill. Reuse the same recompute SQL but keyed by an `image_id` range instead of an explicit id list (so it doesn't need the full id list in memory):

```python
"""Backfill images.has_theme/source/artist/character from tag_links.

Idempotent and resumable. Run AFTER the migration that adds the columns.
Usage: uv run python scripts/backfill_tag_type_flags.py [--batch 10000] [--start 0]
"""
import argparse
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings  # confirm settings.DATABASE_URL is exported here

_BATCH_SQL = text(
    """
    UPDATE images i
    LEFT JOIN (
        SELECT tl.image_id,
               MAX(t.type = 1) AS ht, MAX(t.type = 2) AS hs,
               MAX(t.type = 3) AS ha, MAX(t.type = 4) AS hc
        FROM tag_links tl JOIN tags t ON tl.tag_id = t.tag_id
        WHERE tl.image_id >= :lo AND tl.image_id < :hi
        GROUP BY tl.image_id
    ) agg ON agg.image_id = i.image_id
    SET i.has_theme = COALESCE(agg.ht, 0), i.has_source = COALESCE(agg.hs, 0),
        i.has_artist = COALESCE(agg.ha, 0), i.has_character = COALESCE(agg.hc, 0)
    WHERE i.image_id >= :lo AND i.image_id < :hi
    """
)

async def backfill(batch: int, start: int) -> None:
    # Match the engine/session pattern in scripts/audit_alias_parent_violations.py.
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as db:
        max_id = (await db.execute(text("SELECT MAX(image_id) FROM images"))).scalar() or 0
        lo = start
        while lo <= max_id:
            hi = lo + batch
            await db.execute(_BATCH_SQL, {"lo": lo, "hi": hi})
            await db.commit()
            print(f"backfilled image_id [{lo}, {hi})  (max={max_id})", flush=True)
            lo = hi
    await engine.dispose()

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=10000)
    ap.add_argument("--start", type=int, default=0)
    args = ap.parse_args()
    asyncio.run(backfill(args.batch, args.start))

if __name__ == "__main__":
    main()
```

(Confirm the actual async session factory name in `app/core/database.py` — adjust the import. Match existing `scripts/*.py` conventions for session setup.)

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit.** `git commit -m "feat(scripts): batched backfill for tag-type presence flags"`

---

### Task 13: Whole-feature verification

**Files:** none (verification + EXPLAIN evidence only)

- [ ] **Step 1: Full suite of touched areas.** Run:
```
uv run pytest tests/api/v1/test_images.py tests/api/v1/test_tag_type_flags.py -q
uv run pytest tests/api/v1/test_tags.py tests/api/v1/test_admin_images.py -q   # adjust to actual test files touched
```
Expected: all green.

- [ ] **Step 2: mypy on all touched modules.**
```
uv run mypy app/api/v1/images.py app/api/v1/tags.py app/api/v1/admin.py app/services/tag_type_flags.py app/services/upload.py app/services/batch_tag.py app/services/repost.py app/models/image.py
```
Expected: clean.

- [ ] **Step 3: EXPLAIN verification against the dev DB** (the original motivation). After backfilling the dev DB (`uv run python scripts/backfill_tag_type_flags.py`), confirm the page query is fast:
```bash
curl -s -m 10 -o /dev/null -w "missing_artist http=%{http_code} time=%{time_total}s\n" \
  "http://localhost:8000/api/v1/images?missing_tag_types=3&per_page=20"
```
Expected: http=200, time well under 1s (vs the 60s+ timeout before). Run `EXPLAIN` on the single-type page query and confirm it uses `idx_images_has_artist` (no large filesort). Record the `any`-mode EXPLAIN too; if `any` mode is slow, note it (per the design's caveat) — it does not block single-type.

- [ ] **Step 4: ruff.** `uv run ruff check <touched files>` and `uv run ruff format --check <touched files>` (only the lines this work added; pre-existing drift elsewhere is out of scope).

- [ ] **Step 5: Final commit (if any fixups).** `git commit -m "chore: verification fixups for tag-type presence flags" || echo "nothing to commit"`

---

## Out of scope (not this PR)

- Scheduled reconciliation job (the backfill script is the manual reconciliation tool).
- Indexing fewer flags / write-cost tuning — revisit only if uploads measurably regress.
- `any`-mode page-query optimization beyond EXPLAIN verification.
