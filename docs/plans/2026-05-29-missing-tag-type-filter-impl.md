# Missing Tag Type Filter Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `missing_tag_types` + `missing_tag_types_mode` query params to `GET /api/v1/images` so callers can find images that lack a tag of a given type (Theme/Source/Artist/Character).

**Architecture:** Two new optional query params on the existing `list_images` handler. The filter adds anti-join (`NOT IN`) where-clauses against `tag_links → tags` filtered by `tags.type`, composed with `AND` into the shared `query` (so count + pagination both honor it). `any` mode = missing at least one listed type (OR of per-type `NOT IN`); `all` mode = missing every listed type (single `NOT IN` over `type IN (...)`). Aliases are intentionally NOT resolved — the applied tag's own `type` is authoritative. Inline block mirrors the existing `exclude_tags` block.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy async, MariaDB, pytest (async), `uv` for all Python execution.

**Spec:** `docs/plans/2026-05-29-missing-tag-type-filter-design.md`

---

## Reference: existing code to mirror

- Signature params group `# Tag filtering` in `app/api/v1/images.py` — the `tags_mode` param shows the exact `Query(pattern="^(any|all)$")` style to copy (`images.py:261-263`).
- The `exclude_tags` where-block (`app/api/v1/images.py:424-448`) — the `Images.image_id.notin_(select(...))` pattern and `# type: ignore` codes to mirror.
- Imports already present: `or_` (`images.py:28`), `Tags` (`images.py:67`), `TagLinks` (`images.py:66`), `HTTPException`/`status`/`Query`/`Annotated`. No new imports needed.
- Test patterns: `tests/api/v1/test_images.py` class `TestExcludeTags` (`test_images.py:455`). Use fixtures `client`, `db_session`, `sample_image_data`. Create images with `Images(**{**sample_image_data, "filename": "<unique>", "md5_hash": "<32 unique chars>"})`, `await db_session.flush()` to get IDs, create `Tags(title=, desc=, type=)`, flush, then `TagLinks(image_id=, tag_id=, user_id=1)`, then `await db_session.commit()`. The test DB is empty per test (transactional rollback), so an image with no tags will match a missing-type filter for every type — assert on `image_id` membership, not exact `total`, wherever the no-tag image participates.

## Insertion anchors

- **Signature:** add the two new params immediately after the `exclude_tags` Query param and before the `# Date filtering` comment in the `list_images` signature.
- **Where-block:** add the filter block immediately after the `exclude_tags` where-block (after its closing `)` ~`images.py:448`) and before the `# Date filtering` / `if date_from:` block.

---

## Chunk 1: Missing-tag-type filter

### Task 1: Add params + ANY-mode filter (single type)

**Files:**
- Modify: `app/api/v1/images.py` (signature + where-block)
- Test: `tests/api/v1/test_images.py` (new `TestMissingTagTypes` class, appended after `TestExcludeTags`)

- [ ] **Step 1: Write the failing test**

Append a new test class after `TestExcludeTags` in `tests/api/v1/test_images.py`. `TagType` is already imported in this file; confirm `from app.config import TagType` is at the top (it is used by existing tests).

```python
@pytest.mark.api
class TestMissingTagTypes:
    """Tests for missing_tag_types / missing_tag_types_mode params on image search."""

    async def test_missing_single_type_excludes_images_that_have_it(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """missing_tag_types=3 returns images lacking an artist tag, excludes those that have one."""
        has_artist = Images(**{**sample_image_data, "filename": "mtt_a1", "md5_hash": "a" * 32})
        no_artist = Images(**{**sample_image_data, "filename": "mtt_a2", "md5_hash": "b" * 32})
        db_session.add_all([has_artist, no_artist])
        await db_session.flush()

        artist_tag = Tags(title="some artist", desc="Artist", type=TagType.ARTIST)
        theme_tag = Tags(title="some theme", desc="Theme", type=TagType.THEME)
        db_session.add_all([artist_tag, theme_tag])
        await db_session.flush()

        db_session.add(TagLinks(image_id=has_artist.image_id, tag_id=artist_tag.tag_id, user_id=1))
        db_session.add(TagLinks(image_id=no_artist.image_id, tag_id=theme_tag.tag_id, user_id=1))
        await db_session.commit()

        response = await client.get(f"/api/v1/images?missing_tag_types={TagType.ARTIST}")

        assert response.status_code == 200
        image_ids = [img["image_id"] for img in response.json()["images"]]
        assert no_artist.image_id in image_ids
        assert has_artist.image_id not in image_ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/v1/test_images.py::TestMissingTagTypes::test_missing_single_type_excludes_images_that_have_it -v`
Expected: FAIL — without the param, FastAPI ignores the unknown query string and `has_artist` is returned (assertion `has_artist.image_id not in image_ids` fails).

- [ ] **Step 3: Add the two params to the `list_images` signature**

Insert after the `exclude_tags` Query param (before `# Date filtering`):

```python
    missing_tag_types: Annotated[
        str | None,
        Query(
            description="Comma-separated tag type IDs the image must be MISSING "
            "(1=Theme, 2=Source, 3=Artist, 4=Character)."
        ),
    ] = None,
    missing_tag_types_mode: Annotated[
        str, Query(pattern="^(any|all)$", description="Match ANY or ALL missing types")
    ] = "any",
```

- [ ] **Step 4: Add the where-block (ANY branch only for now)**

Insert immediately after the `exclude_tags` where-block (before `# Date filtering`):

```python
    # Missing tag-type filtering (images lacking a tag of the given type[s]).
    # Aliases are intentionally NOT resolved here: the applied tag's own `type` is authoritative.
    if missing_tag_types:
        missing_type_ids = sorted(
            {int(t.strip()) for t in missing_tag_types.split(",") if t.strip().isdigit()}
        )
        if missing_type_ids:
            # Missing ANY listed type
            def lacks_type(type_id: int):
                return Images.image_id.notin_(  # type: ignore[union-attr]
                    select(TagLinks.image_id)
                    .join(Tags, TagLinks.tag_id == Tags.tag_id)  # type: ignore[arg-type]
                    .where(Tags.type == type_id)  # type: ignore[arg-type]
                )

            query = query.where(or_(*(lacks_type(t) for t in missing_type_ids)))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/api/v1/test_images.py::TestMissingTagTypes::test_missing_single_type_excludes_images_that_have_it -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/images.py tests/api/v1/test_images.py
git commit -m "feat(images): add missing_tag_types filter (any mode)"
```

---

### Task 2: ALL-mode

**Files:**
- Modify: `app/api/v1/images.py` (where-block)
- Test: `tests/api/v1/test_images.py` (`TestMissingTagTypes`)

- [ ] **Step 1: Write the failing test**

```python
    async def test_missing_all_mode_requires_all_types_absent(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """mode=all returns only images missing EVERY listed type."""
        missing_both = Images(**{**sample_image_data, "filename": "mtt_all1", "md5_hash": "c" * 32})
        missing_source_only = Images(
            **{**sample_image_data, "filename": "mtt_all2", "md5_hash": "d" * 32}
        )
        db_session.add_all([missing_both, missing_source_only])
        await db_session.flush()

        artist_tag = Tags(title="artist x", desc="Artist", type=TagType.ARTIST)
        theme_tag = Tags(title="theme x", desc="Theme", type=TagType.THEME)
        db_session.add_all([artist_tag, theme_tag])
        await db_session.flush()

        # missing_both: only a theme tag -> lacks both source and artist
        db_session.add(TagLinks(image_id=missing_both.image_id, tag_id=theme_tag.tag_id, user_id=1))
        # missing_source_only: has an artist tag -> lacks source but NOT artist
        db_session.add(
            TagLinks(image_id=missing_source_only.image_id, tag_id=artist_tag.tag_id, user_id=1)
        )
        await db_session.commit()

        response = await client.get(
            f"/api/v1/images?missing_tag_types={TagType.SOURCE},{TagType.ARTIST}"
            "&missing_tag_types_mode=all"
        )

        assert response.status_code == 200
        image_ids = [img["image_id"] for img in response.json()["images"]]
        assert missing_both.image_id in image_ids
        assert missing_source_only.image_id not in image_ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/v1/test_images.py::TestMissingTagTypes::test_missing_all_mode_requires_all_types_absent -v`
Expected: FAIL — with only the ANY branch, `mode=all` is ignored and `missing_source_only` (missing source) is wrongly returned.

- [ ] **Step 3: Add the ALL branch**

Wrap the existing ANY logic in an if/else on the mode. Replace the `# Missing ANY listed type` block from Task 1 so the full block reads:

```python
        if missing_type_ids:
            if missing_tag_types_mode == "all":
                # Missing ALL listed types: no tag of any listed type
                query = query.where(
                    Images.image_id.notin_(  # type: ignore[union-attr]
                        select(TagLinks.image_id)
                        .join(Tags, TagLinks.tag_id == Tags.tag_id)  # type: ignore[arg-type]
                        .where(Tags.type.in_(missing_type_ids))  # type: ignore[attr-defined]
                    )
                )
            else:
                # Missing ANY listed type
                def lacks_type(type_id: int):
                    return Images.image_id.notin_(  # type: ignore[union-attr]
                        select(TagLinks.image_id)
                        .join(Tags, TagLinks.tag_id == Tags.tag_id)  # type: ignore[arg-type]
                        .where(Tags.type == type_id)  # type: ignore[arg-type]
                    )

                query = query.where(or_(*(lacks_type(t) for t in missing_type_ids)))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/v1/test_images.py::TestMissingTagTypes::test_missing_all_mode_requires_all_types_absent -v`
Expected: PASS

- [ ] **Step 5: Re-run Task 1 test to confirm no regression**

Run: `uv run pytest "tests/api/v1/test_images.py::TestMissingTagTypes" -v`
Expected: both tests PASS

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/images.py tests/api/v1/test_images.py
git commit -m "feat(images): support missing_tag_types_mode=all"
```

---

### Task 3: Validation of type IDs (invalid → 400)

**Files:**
- Modify: `app/api/v1/images.py` (where-block)
- Test: `tests/api/v1/test_images.py` (`TestMissingTagTypes`)

- [ ] **Step 1: Write the failing test**

```python
    async def test_missing_invalid_type_id_returns_400(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Type IDs outside 1-4 (incl. 0=All) are rejected with 400."""
        for bad in ("0", "99", "3,0"):
            response = await client.get(f"/api/v1/images?missing_tag_types={bad}")
            assert response.status_code == 400, f"expected 400 for {bad!r}"
            assert "Valid types are" in response.json()["detail"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/v1/test_images.py::TestMissingTagTypes::test_missing_invalid_type_id_returns_400 -v`
Expected: FAIL — currently invalid IDs yield 200 (no validation).

- [ ] **Step 3: Add the validation check**

Insert immediately after `if missing_type_ids:` and before the mode branch:

```python
            invalid = [t for t in missing_type_ids if t not in {1, 2, 3, 4}]
            if invalid:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Invalid tag type(s): {', '.join(map(str, invalid))}. "
                        "Valid types are 1=Theme, 2=Source, 3=Artist, 4=Character."
                    ),
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/v1/test_images.py::TestMissingTagTypes::test_missing_invalid_type_id_returns_400 -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/images.py tests/api/v1/test_images.py
git commit -m "feat(images): validate missing_tag_types IDs (400 on invalid)"
```

---

### Task 4: Lock in remaining spec cases (regression tests)

These confirm behavior already guaranteed by Tasks 1-3 (alias non-resolution, AND with include filter, no-tag image, 422 on bad mode, omitted param). They should pass immediately — they guard against regressions. If any fails, STOP and treat it as a real defect to root-cause, not a test to weaken.

**Files:**
- Test: `tests/api/v1/test_images.py` (`TestMissingTagTypes`)

Note: confirm `import pytest` and `from app.config import TagType` are present at the top of `test_images.py` (both are — used by existing tests/classes). The class decorator `@pytest.mark.api` (added in Task 1) keeps these tests in the `api` marker set, matching every other class in the file.

- [ ] **Step 1: Write the tests**

```python
    async def test_missing_any_mode_returns_image_missing_only_one_type(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """mode=any returns an image that has one listed type but lacks another (OR, not AND).

        Discriminates OR semantics: an image WITH an artist tag but WITHOUT a source must be
        returned by missing_tag_types=2,3&mode=any, yet excluded by mode=all.
        """
        has_artist_no_source = Images(
            **{**sample_image_data, "filename": "mtt_any1", "md5_hash": "3" * 32}
        )
        db_session.add(has_artist_no_source)
        await db_session.flush()

        artist_tag = Tags(title="any artist", desc="Artist", type=TagType.ARTIST)
        db_session.add(artist_tag)
        await db_session.flush()
        db_session.add(
            TagLinks(image_id=has_artist_no_source.image_id, tag_id=artist_tag.tag_id, user_id=1)
        )
        await db_session.commit()

        types = f"{TagType.SOURCE},{TagType.ARTIST}"

        any_resp = await client.get(
            f"/api/v1/images?missing_tag_types={types}&missing_tag_types_mode=any"
        )
        assert any_resp.status_code == 200
        any_ids = [img["image_id"] for img in any_resp.json()["images"]]
        assert has_artist_no_source.image_id in any_ids  # missing source -> matched by ANY

        all_resp = await client.get(
            f"/api/v1/images?missing_tag_types={types}&missing_tag_types_mode=all"
        )
        assert all_resp.status_code == 200
        all_ids = [img["image_id"] for img in all_resp.json()["images"]]
        assert has_artist_no_source.image_id not in all_ids  # has artist -> excluded by ALL

    async def test_missing_artist_does_not_resolve_alias(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """An image whose only artist tag is an ALIAS still counts as having an artist tag."""
        aliased = Images(**{**sample_image_data, "filename": "mtt_alias", "md5_hash": "e" * 32})
        db_session.add(aliased)
        await db_session.flush()

        canonical = Tags(title="canon artist", desc="Artist", type=TagType.ARTIST)
        db_session.add(canonical)
        await db_session.flush()
        alias = Tags(
            title="alias artist", desc="Alias", type=TagType.ARTIST, alias_of=canonical.tag_id
        )
        db_session.add(alias)
        await db_session.flush()

        # Image is linked to the ALIAS tag, not the canonical one
        db_session.add(TagLinks(image_id=aliased.image_id, tag_id=alias.tag_id, user_id=1))
        await db_session.commit()

        response = await client.get(f"/api/v1/images?missing_tag_types={TagType.ARTIST}")

        assert response.status_code == 200
        image_ids = [img["image_id"] for img in response.json()["images"]]
        assert aliased.image_id not in image_ids

    async def test_missing_combined_with_include_tags(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """tags= include filter AND missing_tag_types both apply."""
        keep = Images(**{**sample_image_data, "filename": "mtt_cmb1", "md5_hash": "f" * 32})
        drop = Images(**{**sample_image_data, "filename": "mtt_cmb2", "md5_hash": "0" * 32})
        db_session.add_all([keep, drop])
        await db_session.flush()

        theme_tag = Tags(title="cmb theme", desc="Theme", type=TagType.THEME)
        artist_tag = Tags(title="cmb artist", desc="Artist", type=TagType.ARTIST)
        db_session.add_all([theme_tag, artist_tag])
        await db_session.flush()

        # keep: has the theme tag, no artist -> matches include + missing artist
        db_session.add(TagLinks(image_id=keep.image_id, tag_id=theme_tag.tag_id, user_id=1))
        # drop: has theme tag AND an artist tag -> matches include but NOT missing artist
        db_session.add(TagLinks(image_id=drop.image_id, tag_id=theme_tag.tag_id, user_id=1))
        db_session.add(TagLinks(image_id=drop.image_id, tag_id=artist_tag.tag_id, user_id=1))
        await db_session.commit()

        response = await client.get(
            f"/api/v1/images?tags={theme_tag.tag_id}&missing_tag_types={TagType.ARTIST}"
        )

        assert response.status_code == 200
        image_ids = [img["image_id"] for img in response.json()["images"]]
        assert keep.image_id in image_ids
        assert drop.image_id not in image_ids

    async def test_image_with_no_tags_matches_every_type_both_modes(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """An image with no tags is missing every type, in both any and all modes."""
        untagged = Images(**{**sample_image_data, "filename": "mtt_none", "md5_hash": "1" * 32})
        db_session.add(untagged)
        await db_session.commit()

        for query in (
            f"missing_tag_types={TagType.ARTIST}",
            f"missing_tag_types={TagType.SOURCE},{TagType.ARTIST}&missing_tag_types_mode=any",
            f"missing_tag_types={TagType.SOURCE},{TagType.ARTIST}&missing_tag_types_mode=all",
        ):
            response = await client.get(f"/api/v1/images?{query}")
            assert response.status_code == 200, query
            image_ids = [img["image_id"] for img in response.json()["images"]]
            assert untagged.image_id in image_ids, query

    async def test_missing_invalid_mode_returns_422(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Invalid mode is rejected by the Query pattern with 422."""
        response = await client.get(
            f"/api/v1/images?missing_tag_types={TagType.ARTIST}&missing_tag_types_mode=foo"
        )
        assert response.status_code == 422

    async def test_omitted_param_does_not_filter(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Without the param, behavior is unchanged (image is returned)."""
        img = Images(**{**sample_image_data, "filename": "mtt_omit", "md5_hash": "2" * 32})
        db_session.add(img)
        await db_session.commit()

        response = await client.get("/api/v1/images")

        assert response.status_code == 200
        image_ids = [i["image_id"] for i in response.json()["images"]]
        assert img.image_id in image_ids
```

- [ ] **Step 2: Run the full class**

Run: `uv run pytest "tests/api/v1/test_images.py::TestMissingTagTypes" -v`
Expected: all tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/api/v1/test_images.py
git commit -m "test(images): cover missing_tag_types alias/combine/no-tag/422 cases"
```

---

### Task 5: Type-check, lint, and full-module verification

**Files:** none (verification only)

- [ ] **Step 1: Reconcile mypy ignore codes**

Run: `uv run mypy app/api/v1/images.py`
Expected: no new errors. If mypy reports an unused-ignore or a different error code on the new lines, adjust the `# type: ignore[...]` codes to match exactly what mypy reports (the codes in this plan mirror the `exclude_tags` block but the join/`Tags.type` lines may differ). Re-run until clean.

- [ ] **Step 2: Run the full images test module**

Run: `uv run pytest tests/api/v1/test_images.py -v`
Expected: all PASS (no regression in `TestExcludeTags`, `TestTagSearchValidation`, etc.).

- [ ] **Step 3: Lint (matches pre-commit)**

Run: `uv run ruff check app/api/v1/images.py tests/api/v1/test_images.py && uv run ruff format --check app/api/v1/images.py tests/api/v1/test_images.py`
Expected: pass. If `ruff format` rewrites anything, stage the result.

- [ ] **Step 4: Commit any fixups**

```bash
git add -A
git commit -m "chore(images): mypy/lint fixups for missing_tag_types" || echo "nothing to commit"
```

---

## Optional follow-up (not part of this PR)

- **Query-plan spot check (spec performance note):** with a populated DB, `EXPLAIN` one `any`-mode query (e.g. `missing_tag_types=2,3,4`) to confirm the planner uses the `type_alias` index and the `tag_links` PK rather than a full anti-join scan. Record findings in the PR description if run.
- **Handler extraction:** `list_images` is ~360 lines. Extracting tag-filter building into a helper is a worthwhile separate refactor, intentionally out of scope here.
