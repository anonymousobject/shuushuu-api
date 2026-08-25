# ML Ancestor-Suggestion Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop redundant ancestor tags (e.g. "bag" on randoseru images) from surviving the ML suggestion pipeline and the per-tag review queue.

**Architecture:** Four independent behaviors, all in shuushuu-api: (1) `filter_superseded_parents` walks the real `Tags.inheritedfrom_id` chain so a suggested grandparent is dropped even when the intermediate tag isn't suggested; (2) the supersede confidence threshold default rises 0.6 → 0.9 (data-driven: bad-suppress rate 24% → 7%); (3) creating a TagLink deletes now-redundant *pending* ancestor suggestions on that image; (4) `list_pending_for_tag` hides a tag's pending suggestion while any *descendant* suggestion is pending on the same image, making per-tag review most-specific-first by construction (rejecting the child naturally resurfaces the parent).

**Tech Stack:** FastAPI + SQLAlchemy async + MariaDB. Tests run against a real isolated DB via `make pytest` (never mock DB behavior).

## Global Constraints

- Branch: `fix/ml-ancestor-suggestions` off `main` in `/home/dtaylor/shuu/shuushuu-api`.
- NEVER `git add -A` or `git add .` — the working tree has unrelated uncommitted WIP (`scripts/db_utils.py`, `scripts/prune_inactive_users.py`, `scripts/restore_prod_db.py`, untracked `docs/plans/*`, `missing-images`). Stage only files you created/modified for your task, by exact path.
- TDD strictly: write the failing test, run it, watch it fail, then implement.
- Run single test files with: `docker compose -f docker-compose.pytest.yml` DB must be up — use `make pytest-db-up` once, then `TEST_DB_HOST=127.0.0.1 TEST_DB_PORT=3307 uv run pytest tests/services/test_X.py -x -q` (check `Makefile` `pytest:` target for the exact env vars it exports and mirror them; if unsure run the full `make pytest`).
- All service tests hit the real test DB; assertions target real rows. No mocked behavior in assertions.
- Match surrounding code style, including the file's existing `# type: ignore[...]` comment patterns on SQLAlchemy calls.
- Depth cap for all hierarchy walks: 10 (matches existing `filter_redundant_suggestions`).

---

### Task 1: Full-chain walk in `filter_superseded_parents`

**Files:**
- Modify: `app/services/ml_suggestion_pipeline.py:148-192` (function `filter_superseded_parents`)
- Test: `tests/services/test_ml_suggestion_pipeline.py` (append after `test_filter_superseded_parents_parent_not_suggested_kept`, ~line 960)

**Interfaces:**
- Produces: `async def fetch_parent_map(db: AsyncSession, tag_ids: set[int]) -> dict[int, int | None]` — module-level helper in `ml_suggestion_pipeline.py`; returns tag_id → inheritedfrom_id covering the full ancestry of `tag_ids` (walks through tags not in the input set, depth-capped at 10). Task 3 imports and reuses this.
- `filter_superseded_parents` signature unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/services/test_ml_suggestion_pipeline.py` (imports for `Tags` and `filter_superseded_parents` already exist in the file):

```python
async def test_filter_superseded_parents_gap_in_chain_drops_grandparent(db_session):
    """A confident child drops a suggested GRANDPARENT even when the
    intermediate parent is not itself suggested (randoseru -> school bag ->
    bag with only randoseru + bag suggested)."""
    grandparent = Tags(tag_id=660, title="bag")
    parent = Tags(tag_id=661, title="school bag", inheritedfrom_id=660)
    child = Tags(tag_id=662, title="randoseru", inheritedfrom_id=661)
    db_session.add_all([grandparent, parent, child])
    await db_session.commit()

    # Intermediate 661 is NOT suggested — the chain must be walked through it.
    suggestions = [
        {"tag_id": 660, "confidence": 0.90, "model_version": "v3"},  # bag (grandparent)
        {"tag_id": 662, "confidence": 0.70, "model_version": "v3"},  # randoseru (child)
    ]

    result = await filter_superseded_parents(db_session, suggestions, 0.6)

    assert {s["tag_id"] for s in result} == {662}


async def test_filter_superseded_parents_gap_chain_weak_child_keeps_grandparent(db_session):
    """A below-threshold child does not suppress ancestors across a gap either."""
    grandparent = Tags(tag_id=670, title="bag")
    parent = Tags(tag_id=671, title="school bag", inheritedfrom_id=670)
    child = Tags(tag_id=672, title="randoseru", inheritedfrom_id=671)
    db_session.add_all([grandparent, parent, child])
    await db_session.commit()

    suggestions = [
        {"tag_id": 670, "confidence": 0.90, "model_version": "v3"},
        {"tag_id": 672, "confidence": 0.55, "model_version": "v3"},
    ]

    result = await filter_superseded_parents(db_session, suggestions, 0.6)

    assert {s["tag_id"] for s in result} == {670, 672}
```

- [ ] **Step 2: Run the new tests to verify the first fails**

Run: `uv run pytest tests/services/test_ml_suggestion_pipeline.py -k "gap" -x -q` (with pytest DB env per Global Constraints)
Expected: `test_filter_superseded_parents_gap_in_chain_drops_grandparent` FAILS (result contains 660); the weak-child test PASSES (guards against regression).

- [ ] **Step 3: Implement the full-chain walk**

Replace the body of `filter_superseded_parents` in `app/services/ml_suggestion_pipeline.py` and add the shared helper above it:

```python
async def fetch_parent_map(db: AsyncSession, tag_ids: set[int]) -> dict[int, int | None]:
    """tag_id -> inheritedfrom_id covering the full ancestry of ``tag_ids``.

    Iteratively fetches parents not yet seen, so chains can be walked through
    tags outside the input set. Depth-capped at 10 like
    filter_redundant_suggestions' walk.
    """
    parent_of: dict[int, int | None] = {}
    to_fetch = set(tag_ids)
    for _ in range(10):
        if not to_fetch:
            break
        rows = (
            await db.execute(
                select(Tags.tag_id, Tags.inheritedfrom_id).where(  # type: ignore[call-overload]
                    Tags.tag_id.in_(to_fetch)  # type: ignore[union-attr]
                )
            )
        ).all()
        for tag_id, parent_id in rows:
            parent_of[tag_id] = parent_id
        to_fetch = {
            parent_id
            for _, parent_id in rows
            if parent_id is not None and parent_id not in parent_of
        }
    return parent_of
```

New `filter_superseded_parents` (docstring must be updated — the old one says it "operates only WITHIN the suggestion set", which is no longer true of the *walk*, only of the *drops*):

```python
async def filter_superseded_parents(
    db: AsyncSession,
    suggestions: list[dict[str, Any]],
    min_child_confidence: float,
) -> list[dict[str, Any]]:
    """Drop a suggested tag when a more-specific suggested descendant (via
    Tags.inheritedfrom_id) is present and that descendant's confidence is
    >= min_child_confidence. The ancestor chain is walked through the Tags
    table, so a suggested grandparent is dropped even when intermediate tags
    in the chain are not themselves suggested. Only tags that are in the
    suggestion set are ever dropped. A low-confidence child does not suppress
    its ancestors (all kept). Input dicts have at least tag_id + confidence.
    """
    if len(suggestions) < 2:
        return suggestions

    conf_by_id: dict[int, float] = {}
    for s in suggestions:
        tid = s["tag_id"]
        conf_by_id[tid] = max(conf_by_id.get(tid, 0.0), s["confidence"])
    suggested_ids = set(conf_by_id)

    parent_of = await fetch_parent_map(db, suggested_ids)

    superseded: set[int] = set()
    for child_id, conf in conf_by_id.items():
        if conf < min_child_confidence:
            continue
        cur = parent_of.get(child_id)
        depth = 0
        while cur is not None and depth < 10:
            if cur in suggested_ids:
                superseded.add(cur)
            cur = parent_of.get(cur)
            depth += 1

    if not superseded:
        return suggestions
    return [s for s in suggestions if s["tag_id"] not in superseded]
```

- [ ] **Step 4: Run the full pipeline test file**

Run: `uv run pytest tests/services/test_ml_suggestion_pipeline.py -x -q`
Expected: ALL pass — including the six pre-existing `filter_superseded_parents` tests (they pass thresholds explicitly, so they are unaffected by later config changes).

- [ ] **Step 5: Commit**

```bash
cd /home/dtaylor/shuu/shuushuu-api
git add app/services/ml_suggestion_pipeline.py tests/services/test_ml_suggestion_pipeline.py
git commit -m "fix(ml): supersede walks the real tag chain, not just suggested tags"
```

---

### Task 2: Raise supersede threshold default to 0.9

**Files:**
- Modify: `app/config.py:168-177` (field `ML_PARENT_SUPERSEDE_MIN_CONFIDENCE`)
- Test: `tests/services/test_ml_suggestion_pipeline.py` (append)

**Interfaces:**
- Consumes/Produces: nothing new — config default only. `.env` files do not set this variable anywhere, so the default is authoritative.

- [ ] **Step 1: Write the failing test**

```python
def test_parent_supersede_default_threshold_is_conservative():
    """Default raised 0.6 -> 0.9 (2026-07-23 analysis: at 0.6 ~24% of marginal
    supersedes suppressed a parent the human applied; at 0.9 that is ~7%).
    Ambiguous child/parent pairs are left for the review queue to sequence."""
    from app.config import Settings

    assert Settings.model_fields["ML_PARENT_SUPERSEDE_MIN_CONFIDENCE"].default == 0.9
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/services/test_ml_suggestion_pipeline.py -k "default_threshold" -x -q`
Expected: FAIL (`0.6 != 0.9`)

- [ ] **Step 3: Change the default**

In `app/config.py`, change `default=0.6` to `default=0.9` on `ML_PARENT_SUPERSEDE_MIN_CONFIDENCE`. Keep the description; append to it: `"; default chosen from tag-hierarchy precision analysis (2026-07)"`.

- [ ] **Step 4: Run the test file**

Run: `uv run pytest tests/services/test_ml_suggestion_pipeline.py -x -q`
Expected: ALL pass (grep the tests dir for `0.6` first to confirm no other test pins the old default; existing supersede tests pass the threshold explicitly).

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/services/test_ml_suggestion_pipeline.py
git commit -m "feat(ml): raise parent-supersede threshold to 0.9 (data-driven)"
```

---

### Task 3: Delete pending ancestor suggestions when a TagLink is created

**Files:**
- Modify: `app/services/ml_suggestion_review.py` (new helper + wire into `approve_pending_suggestions_for_links` and `_apply_reviews_for_image`)
- Test: `tests/services/test_ml_suggestion_review_bulk.py` (append a new test class)

**Interfaces:**
- Consumes: `fetch_parent_map` from `app.services.ml_suggestion_pipeline` (Task 1).
- Produces: `async def delete_pending_ancestor_suggestions(db: AsyncSession, links: Iterable[tuple[int, int]]) -> None` in `ml_suggestion_review.py`. Called automatically by `approve_pending_suggestions_for_links` (so batch_tag.py, repost.py, images.py, admin.py call sites need NO changes) and by `_apply_reviews_for_image`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/services/test_ml_suggestion_review_bulk.py` (module already has `_make_user`, `_make_image`, `_make_tag`, `_make_suggestion` helpers and imports for `Tags`, `TagLinks`, `MlTagSuggestions`, `select`; add `approve_pending_suggestions_for_links` to the existing `from app.services.ml_suggestion_review import ...`):

```python
async def _make_chain(db: AsyncSession, user: Users, suffix: str) -> tuple[Tags, Tags, Tags]:
    """grandparent <- parent <- child via inheritedfrom_id."""
    grandparent = await _make_tag(db, user, f"gp_{suffix}")
    parent = Tags(
        title=f"bulk tag p_{suffix}", type=1, user_id=user.user_id,
        inheritedfrom_id=grandparent.tag_id,
    )
    db.add(parent)
    await db.flush()
    child = Tags(
        title=f"bulk tag c_{suffix}", type=1, user_id=user.user_id,
        inheritedfrom_id=parent.tag_id,
    )
    db.add(child)
    await db.flush()
    return grandparent, parent, child


class TestAncestorCleanupOnApprove:
    """Creating a TagLink deletes now-redundant pending ancestor suggestions."""

    async def test_review_approve_child_deletes_pending_ancestors(
        self, db_session: AsyncSession
    ):
        user = await _make_user(db_session, "anc1")
        image = await _make_image(db_session, user, "anc1")
        grandparent, parent, child = await _make_chain(db_session, user, "anc1")
        await _make_suggestion(db_session, image, grandparent)
        await _make_suggestion(db_session, image, parent)
        sugg_c = await _make_suggestion(db_session, image, child)
        await db_session.commit()

        result = await bulk_review_suggestions(
            db_session,
            [{"suggestion_id": sugg_c.suggestion_id, "action": "approve"}],
            user.user_id,
        )
        assert result.approved == 1

        rows = (
            (
                await db_session.execute(
                    select(MlTagSuggestions).where(
                        MlTagSuggestions.image_id == image.image_id
                    )
                )
            )
            .scalars()
            .all()
        )
        by_tag = {r.tag_id: r for r in rows}
        assert by_tag[child.tag_id].status == "approved"
        # Both pending ancestors are deleted — including the grandparent.
        assert parent.tag_id not in by_tag
        assert grandparent.tag_id not in by_tag

    async def test_reviewed_ancestor_rows_are_never_touched(
        self, db_session: AsyncSession
    ):
        user = await _make_user(db_session, "anc2")
        image = await _make_image(db_session, user, "anc2")
        grandparent, parent, child = await _make_chain(db_session, user, "anc2")
        await _make_suggestion(db_session, image, grandparent, status="rejected")
        await _make_suggestion(db_session, image, parent, status="approved")
        sugg_c = await _make_suggestion(db_session, image, child)
        await db_session.commit()

        await bulk_review_suggestions(
            db_session,
            [{"suggestion_id": sugg_c.suggestion_id, "action": "approve"}],
            user.user_id,
        )

        rows = (
            (
                await db_session.execute(
                    select(MlTagSuggestions).where(
                        MlTagSuggestions.image_id == image.image_id
                    )
                )
            )
            .scalars()
            .all()
        )
        by_tag = {r.tag_id: r.status for r in rows}
        assert by_tag[grandparent.tag_id] == "rejected"
        assert by_tag[parent.tag_id] == "approved"
        assert by_tag[child.tag_id] == "approved"

    async def test_reject_does_not_delete_ancestors(self, db_session: AsyncSession):
        user = await _make_user(db_session, "anc3")
        image = await _make_image(db_session, user, "anc3")
        _grandparent, parent, child = await _make_chain(db_session, user, "anc3")
        sugg_p = await _make_suggestion(db_session, image, parent)
        sugg_c = await _make_suggestion(db_session, image, child)
        await db_session.commit()

        await bulk_review_suggestions(
            db_session,
            [{"suggestion_id": sugg_c.suggestion_id, "action": "reject"}],
            user.user_id,
        )

        refreshed = (
            (
                await db_session.execute(
                    select(MlTagSuggestions).where(
                        MlTagSuggestions.suggestion_id == sugg_p.suggestion_id
                    )
                )
            )
            .scalars()
            .one_or_none()
        )
        assert refreshed is not None and refreshed.status == "pending"

    async def test_out_of_band_link_approval_deletes_pending_ancestors(
        self, db_session: AsyncSession
    ):
        """approve_pending_suggestions_for_links (manual add / batch tagging
        path) also deletes pending ancestor rows."""
        user = await _make_user(db_session, "anc4")
        image = await _make_image(db_session, user, "anc4")
        grandparent, _parent, child = await _make_chain(db_session, user, "anc4")
        await _make_suggestion(db_session, image, grandparent)
        await _make_suggestion(db_session, image, child)
        db_session.add(
            TagLinks(image_id=image.image_id, tag_id=child.tag_id, user_id=user.user_id)
        )
        await db_session.flush()

        await approve_pending_suggestions_for_links(
            db_session, [(image.image_id, child.tag_id)], user.user_id
        )
        await db_session.commit()

        rows = (
            (
                await db_session.execute(
                    select(MlTagSuggestions).where(
                        MlTagSuggestions.image_id == image.image_id
                    )
                )
            )
            .scalars()
            .all()
        )
        by_tag = {r.tag_id: r.status for r in rows}
        assert by_tag[child.tag_id] == "approved"
        assert grandparent.tag_id not in by_tag

    async def test_pending_descendant_of_applied_tag_survives(
        self, db_session: AsyncSession
    ):
        """Applying a PARENT tag must not delete the more-specific pending
        child suggestion (cleanup goes up the chain only)."""
        user = await _make_user(db_session, "anc5")
        image = await _make_image(db_session, user, "anc5")
        _grandparent, parent, child = await _make_chain(db_session, user, "anc5")
        sugg_c = await _make_suggestion(db_session, image, child)
        db_session.add(
            TagLinks(image_id=image.image_id, tag_id=parent.tag_id, user_id=user.user_id)
        )
        await db_session.flush()

        await approve_pending_suggestions_for_links(
            db_session, [(image.image_id, parent.tag_id)], user.user_id
        )
        await db_session.commit()

        refreshed = (
            (
                await db_session.execute(
                    select(MlTagSuggestions).where(
                        MlTagSuggestions.suggestion_id == sugg_c.suggestion_id
                    )
                )
            )
            .scalars()
            .one_or_none()
        )
        assert refreshed is not None and refreshed.status == "pending"
```

- [ ] **Step 2: Run to verify the deletion tests fail**

Run: `uv run pytest tests/services/test_ml_suggestion_review_bulk.py -k "Ancestor" -x -q`
Expected: `test_review_approve_child_deletes_pending_ancestors` and `test_out_of_band_link_approval_deletes_pending_ancestors` FAIL (ancestor rows still present). The never-touched / reject / descendant tests PASS (current behavior already satisfies them — they pin the boundaries).

- [ ] **Step 3: Implement the helper and wire it in**

In `app/services/ml_suggestion_review.py`:

Add `delete` to the existing sqlalchemy import (`from sqlalchemy import delete, select, tuple_, update`) and add `from app.services.ml_suggestion_pipeline import fetch_parent_map`.

New function after `approve_pending_suggestions_for_links`:

```python
async def delete_pending_ancestor_suggestions(
    db: AsyncSession,
    links: Iterable[tuple[int, int]],
) -> None:
    """Delete pending suggestions made redundant by a newly applied descendant.

    ``links`` is the (image_id, tag_id) pairs for TagLinks the caller just
    created. Each applied tag's Tags.inheritedfrom_id chain is walked and any
    PENDING suggestion rows for those ancestors on that image are deleted —
    once the more specific tag is on the image, suggesting the generic one is
    redundant (generation applies the same rule via
    filter_redundant_suggestions). Approved/rejected rows are never touched,
    and only ancestors are affected: a pending suggestion for a DESCENDANT of
    the applied tag keeps its own review.

    Flush-only; the caller owns the transaction and commit.
    """
    pairs = list(links)
    if not pairs:
        return

    parent_of = await fetch_parent_map(db, {tag_id for _, tag_id in pairs})

    doomed: set[tuple[int, int]] = set()
    for image_id, tag_id in pairs:
        cur = parent_of.get(tag_id)
        depth = 0
        while cur is not None and depth < 10:
            doomed.add((image_id, cur))
            cur = parent_of.get(cur)
            depth += 1

    if not doomed:
        return

    await db.execute(
        delete(MlTagSuggestions).where(
            MlTagSuggestions.status == "pending",  # type: ignore[arg-type]
            tuple_(MlTagSuggestions.image_id, MlTagSuggestions.tag_id).in_(doomed),  # type: ignore[arg-type]
        )
    )
```

Wire-up (two one-line calls):
1. At the end of `approve_pending_suggestions_for_links` (after the `update` execute), add:
   ```python
   await delete_pending_ancestor_suggestions(db, pairs)
   ```
2. In `_apply_reviews_for_image`, after the `for review_item in items:` loop and before the `refresh_image_tag_type_flags` block, add:
   ```python
   if created_link_tag_ids:
       await delete_pending_ancestor_suggestions(
           db, [(image_id, tag_id) for tag_id in created_link_tag_ids]
       )
   ```
   (Cleanup keys off links actually created; rows the same batch just set to approved/rejected are not pending, so they are naturally immune.)

- [ ] **Step 4: Run the review test file**

Run: `uv run pytest tests/services/test_ml_suggestion_review_bulk.py -x -q`
Expected: ALL pass.

- [ ] **Step 5: Run neighbors that exercise approve_pending_suggestions_for_links call sites**

Run: `uv run pytest tests/services/test_ml_suggestion_invalidation.py tests/services/test_ml_suggestion_lifecycle.py tests/services/test_ml_remap.py -q` and `uv run pytest tests/ -k "batch_tag or repost" -q`
Expected: ALL pass (no call-site signature changed).

- [ ] **Step 6: Commit**

```bash
git add app/services/ml_suggestion_review.py tests/services/test_ml_suggestion_review_bulk.py
git commit -m "feat(ml): applying a tag deletes redundant pending ancestor suggestions"
```

---

### Task 4: Descendant-aware hiding in `list_pending_for_tag`

**Files:**
- Modify: `app/services/ml_suggestion_queue.py` (new `_descendant_tag_ids` helper; extend `list_pending_for_tag`; extend `count_pending_by_tag` docstring note)
- Test: `tests/services/test_ml_suggestion_queue.py` (append a test class; module helpers `_make_user`, `_make_image`, `_make_tag`, `_make_suggestion`, `_apply_tag` already exist)

**Interfaces:**
- Consumes: nothing from other tasks (independent).
- Produces: `list_pending_for_tag` signature unchanged; behavior addition only.

- [ ] **Step 1: Write the failing tests**

Append to `tests/services/test_ml_suggestion_queue.py`:

```python
async def _make_child_tag(
    db: AsyncSession, user: Users, suffix: str, parent: Tags
) -> Tags:
    tag = Tags(
        title=f"queue tag {suffix}", type=TagType.THEME, user_id=user.user_id,
        inheritedfrom_id=parent.tag_id,
    )
    db.add(tag)
    await db.flush()
    return tag


class TestListPendingDescendantHiding:
    """An ancestor's pending suggestion is hidden from its per-tag queue while
    a more-specific descendant suggestion is pending on the same image, so
    per-tag review is most-specific-first by construction."""

    async def test_pending_child_hides_parent_row(self, db_session: AsyncSession):
        user = await _make_user(db_session, "hide1")
        image = await _make_image(db_session, user, "hide1")
        parent = await _make_tag(db_session, user, "hide1_parent")
        child = await _make_child_tag(db_session, user, "hide1_child", parent)
        await _make_suggestion(db_session, image, parent)
        await _make_suggestion(db_session, image, child)
        await db_session.commit()

        items, total = await list_pending_for_tag(
            db_session, parent.tag_id, min_confidence=0.0, page=1, per_page=10
        )
        assert total == 0 and items == []

        # The child's own queue still shows the image.
        items, total = await list_pending_for_tag(
            db_session, child.tag_id, min_confidence=0.0, page=1, per_page=10
        )
        assert total == 1

    async def test_pending_grandchild_hides_grandparent_row(
        self, db_session: AsyncSession
    ):
        """Transitive: the intermediate tag has NO suggestion row at all."""
        user = await _make_user(db_session, "hide2")
        image = await _make_image(db_session, user, "hide2")
        grandparent = await _make_tag(db_session, user, "hide2_gp")
        parent = await _make_child_tag(db_session, user, "hide2_p", grandparent)
        grandchild = await _make_child_tag(db_session, user, "hide2_c", parent)
        await _make_suggestion(db_session, image, grandparent)
        await _make_suggestion(db_session, image, grandchild)
        await db_session.commit()

        items, total = await list_pending_for_tag(
            db_session, grandparent.tag_id, min_confidence=0.0, page=1, per_page=10
        )
        assert total == 0 and items == []

    async def test_rejected_descendant_does_not_hide(self, db_session: AsyncSession):
        """Rejecting the child resurfaces the parent — the cascade."""
        user = await _make_user(db_session, "hide3")
        image = await _make_image(db_session, user, "hide3")
        parent = await _make_tag(db_session, user, "hide3_parent")
        child = await _make_child_tag(db_session, user, "hide3_child", parent)
        await _make_suggestion(db_session, image, parent)
        await _make_suggestion(db_session, image, child, status="rejected")
        await db_session.commit()

        items, total = await list_pending_for_tag(
            db_session, parent.tag_id, min_confidence=0.0, page=1, per_page=10
        )
        assert total == 1

    async def test_unrelated_pending_does_not_hide(self, db_session: AsyncSession):
        user = await _make_user(db_session, "hide4")
        image = await _make_image(db_session, user, "hide4")
        parent = await _make_tag(db_session, user, "hide4_parent")
        unrelated = await _make_tag(db_session, user, "hide4_other")
        await _make_suggestion(db_session, image, parent)
        await _make_suggestion(db_session, image, unrelated)
        await db_session.commit()

        items, total = await list_pending_for_tag(
            db_session, parent.tag_id, min_confidence=0.0, page=1, per_page=10
        )
        assert total == 1

    async def test_hiding_is_per_image(self, db_session: AsyncSession):
        """Another image whose parent suggestion has no pending descendant
        stays listed while the blocked image is hidden."""
        user = await _make_user(db_session, "hide5")
        image_blocked = await _make_image(db_session, user, "hide5a")
        image_free = await _make_image(db_session, user, "hide5b")
        parent = await _make_tag(db_session, user, "hide5_parent")
        child = await _make_child_tag(db_session, user, "hide5_child", parent)
        await _make_suggestion(db_session, image_blocked, parent)
        await _make_suggestion(db_session, image_blocked, child)
        await _make_suggestion(db_session, image_free, parent)
        await db_session.commit()

        items, total = await list_pending_for_tag(
            db_session, parent.tag_id, min_confidence=0.0, page=1, per_page=10
        )
        assert total == 1
        assert [item[1] for item in items] == [image_free.image_id]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/services/test_ml_suggestion_queue.py -k "Hiding" -x -q`
Expected: the first two and the last FAIL (hidden rows still returned); rejected/unrelated tests PASS (boundary pins).

- [ ] **Step 3: Implement**

In `app/services/ml_suggestion_queue.py`, add `from sqlalchemy.orm import aliased` and:

```python
async def _descendant_tag_ids(db: AsyncSession, tag_id: int) -> set[int]:
    """All tags whose inheritedfrom chain leads to ``tag_id`` (children,
    grandchildren, ...), excluding ``tag_id`` itself. Breadth-first with the
    same depth cap the pipeline's ancestor walks use."""
    descendants: set[int] = set()
    frontier = {tag_id}
    for _ in range(10):
        rows = (
            await db.execute(
                select(Tags.tag_id).where(  # type: ignore[call-overload]
                    Tags.inheritedfrom_id.in_(frontier)  # type: ignore[union-attr]
                )
            )
        ).all()
        new_ids = {row[0] for row in rows} - descendants
        if not new_ids:
            break
        descendants |= new_ids
        frontier = new_ids
    return descendants
```

In `list_pending_for_tag`, change `base_filter` from a tuple to a list and append the exclusion when descendants exist (docstring: add a line "Also excludes suggestions on images that still have a pending suggestion for a DESCENDANT of ``tag_id`` — per-tag review is most-specific-first; rejecting the descendant resurfaces the ancestor here."):

```python
    descendant_ids = await _descendant_tag_ids(db, tag_id)

    base_filter = [
        MlTagSuggestions.status == "pending",
        MlTagSuggestions.tag_id == tag_id,
        MlTagSuggestions.confidence >= min_confidence,
        _TAG_NOT_ALREADY_APPLIED,
    ]
    if descendant_ids:
        descendant_pending = aliased(MlTagSuggestions)
        base_filter.append(
            ~(
                select(descendant_pending.suggestion_id)  # type: ignore[call-overload]
                .where(
                    descendant_pending.image_id == MlTagSuggestions.image_id,
                    descendant_pending.status == "pending",
                    descendant_pending.tag_id.in_(descendant_ids),  # type: ignore[attr-defined]
                )
                .exists()
            )
        )
```

(The two `.where(*base_filter)` uses below need no change.) Also append to `count_pending_by_tag`'s existing "does NOT exclude" docstring note: it likewise does not exclude descendant-blocked rows, same accepted overcount, same perf reason.

- [ ] **Step 4: Run the queue test file**

Run: `uv run pytest tests/services/test_ml_suggestion_queue.py -x -q`
Expected: ALL pass, including pre-existing list/count tests (tags created by `_make_tag` have no hierarchy, so `_descendant_tag_ids` returns empty and the new filter is skipped for them).

- [ ] **Step 5: Commit**

```bash
git add app/services/ml_suggestion_queue.py tests/services/test_ml_suggestion_queue.py
git commit -m "feat(ml): per-tag queue hides ancestors while a descendant suggestion is pending"
```

---

### Task 5: Full validation

**Files:**
- None new. Runs the whole suite + static checks.

- [ ] **Step 1: Full test suite**

Run: `cd /home/dtaylor/shuu/shuushuu-api && make pytest`
Expected: exit 0. (Uses the isolated pytest MariaDB; safe on the memory-constrained dev host. If unrelated pre-existing failures appear, report them verbatim — do not fix or silence them in this branch.)

- [ ] **Step 2: Lint/typecheck**

Run whatever the repo's Makefile/CI uses (check `Makefile` and `.github/workflows` for `ruff` / `mypy` invocations) against the changed files only, e.g.: `uv run ruff check app/services/ml_suggestion_pipeline.py app/services/ml_suggestion_review.py app/services/ml_suggestion_queue.py app/config.py` and the repo's mypy command if CI runs one.
Expected: no NEW errors attributable to changed lines (pre-existing repo-wide mypy debt is out of scope).

- [ ] **Step 3: Report**

No commit. Report suite results verbatim to the orchestrator.

---

## Post-merge operational steps (NOT part of this plan's tasks — orchestrator/user handles)

1. Deploy API to prod (kyouko) and test (shuu).
2. Re-run `scripts/ml_remap.py` on prod and test for model `swinv2_base_window8_256.dbv4-full` — deletes stored pending rows the fixed pipeline no longer implies. Verified safe: the remap delete step is scoped to `status='pending'` + this model_version, and it never re-creates a row for any tag that has an existing row of any status (rejected stays rejected).
3. Confirm the ansible-rendered prod `.env` (iac repo, user's WIP) does not pin `ML_PARENT_SUPERSEDE_MIN_CONFIDENCE` to the old default.
