# Tag Exclusion Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `exclude_tags` query parameter to `list_images` that filters out images containing specified tags.

**Architecture:** Add a new `exclude_tags` parameter to the `list_images` endpoint. Parse, validate, and resolve aliases the same way as `tags`. Apply as a single `NOT IN` subquery on `tag_links`. Enforce shared `MAX_SEARCH_TAGS` limit across both params.

**Tech Stack:** FastAPI, SQLAlchemy async, existing `resolve_tag_alias()` function, pytest.

**Design doc:** `docs/plans/2026-02-19-exclude-tags-design.md`

---

### Task 1: Write failing test for exclude-only search

**Files:**
- Modify: `tests/api/v1/test_images.py`

**Step 1: Write the failing test**

Add a new test class after the existing `TestTagSearchValidation` class (around line 460):

```python
@pytest.mark.api
class TestExcludeTags:
    """Tests for exclude_tags parameter on image search."""

    async def test_exclude_tags_basic(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test that exclude_tags filters out images with the excluded tag."""
        # Create two images
        image1 = Images(**{**sample_image_data, "filename": "excl1", "md5_hash": "a" * 32})
        image2 = Images(**{**sample_image_data, "filename": "excl2", "md5_hash": "b" * 32})
        db_session.add_all([image1, image2])
        await db_session.flush()

        # Create tags
        tag_keep = Tags(title="mizugi", desc="Swimsuit", type=1)
        tag_exclude = Tags(title="school mizugi", desc="School swimsuit", type=1)
        db_session.add_all([tag_keep, tag_exclude])
        await db_session.flush()

        # image1 has both tags, image2 has only the keep tag
        db_session.add(TagLinks(image_id=image1.image_id, tag_id=tag_keep.tag_id, user_id=1))
        db_session.add(TagLinks(image_id=image1.image_id, tag_id=tag_exclude.tag_id, user_id=1))
        db_session.add(TagLinks(image_id=image2.image_id, tag_id=tag_keep.tag_id, user_id=1))
        await db_session.commit()

        # Exclude the school mizugi tag — should only return image2
        response = await client.get(f"/api/v1/images?exclude_tags={tag_exclude.tag_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        image_ids = [img["image_id"] for img in data["images"]]
        assert image2.image_id in image_ids
        assert image1.image_id not in image_ids
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/v1/test_images.py::TestExcludeTags::test_exclude_tags_basic -v`
Expected: FAIL — `exclude_tags` query param is ignored, both images returned, `total` is 2 not 1.

### Task 2: Implement exclude_tags parameter and filtering logic

**Files:**
- Modify: `app/api/v1/images.py:143-175` (add parameter) and `~317` (add filtering logic after tag inclusion)

**Step 1: Add the `exclude_tags` query parameter**

In the `list_images` function signature, add after the `tag_depth` parameter (line 174):

```python
    exclude_tags: Annotated[
        str | None, Query(description="Comma-separated tag IDs to exclude (e.g., '4,5,6')")
    ] = None,
```

**Step 2: Add the exclude filtering logic**

After the existing tag filtering block (after line 317, before the `# Date filtering` comment), add:

```python
    # Exclude tag filtering (always exact match, no hierarchy expansion)
    if exclude_tags:
        exclude_tag_ids = [
            int(tid.strip()) for tid in exclude_tags.split(",") if tid.strip().isdigit()
        ]
        if exclude_tag_ids:
            # Enforce shared MAX_SEARCH_TAGS limit across include + exclude
            include_count = len(tag_ids) if tags and tag_ids else 0
            total_tag_count = include_count + len(exclude_tag_ids)
            if total_tag_count > settings.MAX_SEARCH_TAGS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"You can only search for up to {settings.MAX_SEARCH_TAGS} tags at a time.",
                )

            # Resolve aliases (no hierarchy expansion)
            resolved_exclude_ids: set[int] = set()
            for etid in exclude_tag_ids:
                _, resolved_etid = await resolve_tag_alias(db, etid)
                resolved_exclude_ids.add(resolved_etid)

            # Apply NOT IN subquery
            query = query.where(
                Images.image_id.notin_(  # type: ignore[union-attr]
                    select(TagLinks.image_id).where(
                        TagLinks.tag_id.in_(resolved_exclude_ids)  # type: ignore[attr-defined]
                    )
                )
            )
```

Note: The `tag_ids` variable from the inclusion block is scoped to the `if tags:` block, so we need to handle the case where `tags` is None. The `include_count` calculation handles this.

**Step 3: Run test to verify it passes**

Run: `uv run pytest tests/api/v1/test_images.py::TestExcludeTags::test_exclude_tags_basic -v`
Expected: PASS

**Step 4: Commit**

```bash
git add app/api/v1/images.py tests/api/v1/test_images.py
git commit -m "feat: add exclude_tags parameter to image search"
```

### Task 3: Write and pass test for include + exclude combo

**Files:**
- Modify: `tests/api/v1/test_images.py`

**Step 1: Write the failing test**

Add to `TestExcludeTags`:

```python
    async def test_include_and_exclude_tags_combined(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test tags + exclude_tags together: include mizugi, exclude school mizugi."""
        # Create three images
        image1 = Images(**{**sample_image_data, "filename": "combo1", "md5_hash": "c" * 32})
        image2 = Images(**{**sample_image_data, "filename": "combo2", "md5_hash": "d" * 32})
        image3 = Images(**{**sample_image_data, "filename": "combo3", "md5_hash": "e" * 32})
        db_session.add_all([image1, image2, image3])
        await db_session.flush()

        tag_mizugi = Tags(title="mizugi", desc="Swimsuit", type=1)
        tag_school = Tags(title="school mizugi", desc="School swimsuit", type=1)
        tag_other = Tags(title="blonde hair", desc="Blonde hair", type=1)
        db_session.add_all([tag_mizugi, tag_school, tag_other])
        await db_session.flush()

        # image1: mizugi + school mizugi (should be excluded)
        # image2: mizugi only (should appear)
        # image3: blonde hair only (no mizugi, should not appear)
        db_session.add(TagLinks(image_id=image1.image_id, tag_id=tag_mizugi.tag_id, user_id=1))
        db_session.add(TagLinks(image_id=image1.image_id, tag_id=tag_school.tag_id, user_id=1))
        db_session.add(TagLinks(image_id=image2.image_id, tag_id=tag_mizugi.tag_id, user_id=1))
        db_session.add(TagLinks(image_id=image3.image_id, tag_id=tag_other.tag_id, user_id=1))
        await db_session.commit()

        response = await client.get(
            f"/api/v1/images?tags={tag_mizugi.tag_id}&exclude_tags={tag_school.tag_id}"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["images"][0]["image_id"] == image2.image_id
```

**Step 2: Run test to verify it passes**

Run: `uv run pytest tests/api/v1/test_images.py::TestExcludeTags::test_include_and_exclude_tags_combined -v`
Expected: PASS (implementation from Task 2 handles this)

**Step 3: Commit**

```bash
git add tests/api/v1/test_images.py
git commit -m "test: add include+exclude combo test for tag exclusion"
```

### Task 4: Write and pass test for alias resolution on excluded tags

**Files:**
- Modify: `tests/api/v1/test_images.py`

**Step 1: Write the test**

Add to `TestExcludeTags`:

```python
    async def test_exclude_tags_resolves_aliases(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test that excluding an alias tag resolves to the actual tag."""
        image1 = Images(**{**sample_image_data, "filename": "alias1", "md5_hash": "f" * 32})
        image2 = Images(**{**sample_image_data, "filename": "alias2", "md5_hash": "0" * 32})
        db_session.add_all([image1, image2])
        await db_session.flush()

        # Create real tag and alias
        real_tag = Tags(title="cat ears", desc="Cat ears", type=1)
        db_session.add(real_tag)
        await db_session.flush()

        alias_tag = Tags(title="neko mimi", desc="Alias", type=1, alias_of=real_tag.tag_id)
        db_session.add(alias_tag)
        await db_session.flush()

        other_tag = Tags(title="blue eyes", desc="Blue eyes", type=1)
        db_session.add(other_tag)
        await db_session.flush()

        # image1 has the real "cat ears" tag, image2 has "blue eyes"
        db_session.add(TagLinks(image_id=image1.image_id, tag_id=real_tag.tag_id, user_id=1))
        db_session.add(TagLinks(image_id=image2.image_id, tag_id=other_tag.tag_id, user_id=1))
        await db_session.commit()

        # Exclude using alias ID — should still exclude image1
        response = await client.get(f"/api/v1/images?exclude_tags={alias_tag.tag_id}")

        assert response.status_code == 200
        data = response.json()
        image_ids = [img["image_id"] for img in data["images"]]
        assert image1.image_id not in image_ids
        assert image2.image_id in image_ids
```

**Step 2: Run test to verify it passes**

Run: `uv run pytest tests/api/v1/test_images.py::TestExcludeTags::test_exclude_tags_resolves_aliases -v`
Expected: PASS (alias resolution already implemented in Task 2)

**Step 3: Commit**

```bash
git add tests/api/v1/test_images.py
git commit -m "test: add alias resolution test for exclude_tags"
```

### Task 5: Write and pass test for MAX_SEARCH_TAGS enforcement

**Files:**
- Modify: `tests/api/v1/test_images.py`

**Step 1: Write the test**

Add to `TestExcludeTags`:

```python
    async def test_exclude_tags_shared_max_limit(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test that include + exclude tags together are capped by MAX_SEARCH_TAGS."""
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.flush()

        # Create enough tags to exceed limit when combined
        include_tags = []
        exclude_tags = []
        for i in range(settings.MAX_SEARCH_TAGS):
            tag = Tags(title=f"IncTag{i}", desc=f"Include tag {i}", type=1)
            db_session.add(tag)
            await db_session.flush()
            include_tags.append(tag.tag_id)

        # One more as exclude — total exceeds limit
        extra_tag = Tags(title="ExcludeTag", desc="Exclude tag", type=1)
        db_session.add(extra_tag)
        await db_session.flush()
        exclude_tags.append(extra_tag.tag_id)
        await db_session.commit()

        include_param = ",".join(str(tid) for tid in include_tags)
        exclude_param = ",".join(str(tid) for tid in exclude_tags)
        response = await client.get(
            f"/api/v1/images?tags={include_param}&exclude_tags={exclude_param}"
        )

        assert response.status_code == 400
        data = response.json()
        assert str(settings.MAX_SEARCH_TAGS) in data["detail"]
```

**Step 2: Run test to verify it passes**

Run: `uv run pytest tests/api/v1/test_images.py::TestExcludeTags::test_exclude_tags_shared_max_limit -v`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/api/v1/test_images.py
git commit -m "test: add MAX_SEARCH_TAGS enforcement test for exclude_tags"
```

### Task 6: Write and pass tests for tags_mode interaction and overlap

**Files:**
- Modify: `tests/api/v1/test_images.py`

**Step 1: Write tests for tags_mode=all with excludes and overlap**

Add to `TestExcludeTags`:

```python
    async def test_exclude_with_tags_mode_all(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test exclude_tags works correctly with tags_mode=all."""
        image1 = Images(**{**sample_image_data, "filename": "all1", "md5_hash": "1a" * 16})
        image2 = Images(**{**sample_image_data, "filename": "all2", "md5_hash": "2b" * 16})
        db_session.add_all([image1, image2])
        await db_session.flush()

        tag_a = Tags(title="tag_a", desc="A", type=1)
        tag_b = Tags(title="tag_b", desc="B", type=1)
        tag_x = Tags(title="tag_x", desc="Exclude me", type=1)
        db_session.add_all([tag_a, tag_b, tag_x])
        await db_session.flush()

        # image1: tag_a + tag_b + tag_x
        # image2: tag_a + tag_b
        for tag in [tag_a, tag_b, tag_x]:
            db_session.add(TagLinks(image_id=image1.image_id, tag_id=tag.tag_id, user_id=1))
        for tag in [tag_a, tag_b]:
            db_session.add(TagLinks(image_id=image2.image_id, tag_id=tag.tag_id, user_id=1))
        await db_session.commit()

        response = await client.get(
            f"/api/v1/images?tags={tag_a.tag_id},{tag_b.tag_id}"
            f"&tags_mode=all&exclude_tags={tag_x.tag_id}"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["images"][0]["image_id"] == image2.image_id

    async def test_exclude_overlaps_with_include(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test that when same tag is in both tags and exclude_tags, exclusion wins."""
        image = Images(**{**sample_image_data, "filename": "overlap", "md5_hash": "3c" * 16})
        db_session.add(image)
        await db_session.flush()

        tag = Tags(title="conflicting_tag", desc="Both included and excluded", type=1)
        db_session.add(tag)
        await db_session.flush()

        db_session.add(TagLinks(image_id=image.image_id, tag_id=tag.tag_id, user_id=1))
        await db_session.commit()

        # Include and exclude the same tag — exclusion should win
        response = await client.get(
            f"/api/v1/images?tags={tag.tag_id}&exclude_tags={tag.tag_id}"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
```

**Step 2: Run tests**

Run: `uv run pytest tests/api/v1/test_images.py::TestExcludeTags -v`
Expected: ALL PASS

**Step 3: Commit**

```bash
git add tests/api/v1/test_images.py
git commit -m "test: add tags_mode and overlap tests for exclude_tags"
```

### Task 7: Run full test suite and verify

**Step 1: Run all tests**

Run: `uv run pytest tests/ -v --tb=short`
Expected: All tests pass, no regressions.

**Step 2: Run linting**

Run: `uv run ruff check app/api/v1/images.py`
Expected: No errors.

**Step 3: Final commit if any cleanup needed, then verify git log**

Run: `git log --oneline -10`
