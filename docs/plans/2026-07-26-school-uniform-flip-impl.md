# Seifuku → School Uniform Flip & Reparent Rollout Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make "school uniform" (tag 16) the canonical tag with "seifuku" (3661) as its alias, reparent it from `skirt` (159) to `uniform` (142), and backfill the explicit `skirt` tag on affected images using stored ML predictions.

**Architecture:** The flip is a title swap between the two existing tag rows (tag 16 keeps its ~153k tag_links, history, and ML mappings — nothing moves). The reparent is a one-field change on tag 16. The skirt backfill runs entirely from the `ml_raw_predictions` store via `scripts/ml_remap.py` (no GPU), followed by a bulk-approve of high-confidence suggestions through the existing reviewed-suggestion pipeline.

**Tech Stack:** Admin tag API (audited), `scripts/import_tag_mappings.py`, `scripts/ml_remap.py`, `app/services/ml_suggestion_review.bulk_review_suggestions`, MariaDB, Meilisearch.

## Global Constraints

- ML model name everywhere: `swinv2_base_window8_256.dbv4-full` (never the `wd-swinv2-tagger-v3` code default).
- Always run scripts with `uv run python …` from the repo root.
- Before any script run, confirm the target DB: `uv run python -c "from app.config import settings; print(settings.DATABASE_URL.split('@')[-1])"`.
- Tag changes go through the admin API/UI (writes `tag_audit_log`), never raw SQL.
- `ML_MIN_CONFIDENCE=0.35` (suggestion floor; raw store floor is ~0.49 so it's the effective floor), `ML_PARENT_SUPERSEDE_MIN_CONFIDENCE=0.9`.
- Bulk auto-approve is scoped to images already tagged `school uniform` (16) — no blanket auto-tagging.

## Tag/data reference

| tag_id | title (after flip) | role |
|--------|-------------------|------|
| 16 | school uniform | canonical; parent = 142; holds all links |
| 3661 | seifuku | alias of 16 |
| 159 | skirt | former parent; unchanged |
| 142 | uniform | new parent; unchanged |

Dev measurements (2026-07-26, dev = recent prod restore):

- 153,636 images tagged 16; 144,685 of them lack an explicit `skirt` tag. Before the reparent, `skirt` searches returned skirt ∪ seifuku ≈ 312k images; after it, 167,754 — the backfill closes most of that gap.
- Of the 144,685: **90,137** have a stored raw `skirt` prediction (26,760 ≥ 0.9; 44,977 in 0.7–0.9; 18,400 in 0.49–0.7).
- Danbooru `serafuku` has 65,307 raw predictions and **no `tag_mappings` row** — added in Task 2.
- Existing dev suggestion counts before remap: skirt pending 58,144; school-uniform pending 26,410.
- 2,527 images carry both `uniform` (142) and 16 — redundant after the reparent (Task 5 decision).

---

## Task 0: Tag flip in dev — DONE 2026-07-26

Recorded for the prod re-run (Task 6 repeats these steps). All via admin UI; `tag_audit_log` ids 5442–5446:

- [x] Rename 3661: `school uniform` → `school uniform (alias-swap)` (frees the title)
- [x] Rename 16: `seifuku` → `school uniform`
- [x] Rename 3661: `school uniform (alias-swap)` → `seifuku`
- [x] Reparent 16: parent 159 → 142
- [x] Meilisearch reflects new titles automatically (tag updates sync on write — verified via `GET /api/v1/search?q=school+uniform`; `scripts/reindex_search.py` was not needed)

Verification (already passing in dev, reuse on prod):

```sql
SELECT tag_id, title, alias_of, inheritedfrom_id, usage_count
FROM tags WHERE tag_id IN (16, 3661, 159, 142);
-- expect: 16 'school uniform' parent=142, usage 153636 ; 3661 'seifuku' alias_of=16, usage 0 ;
--         159 and 142 unchanged
```

---

### Task 1: Fix tag descriptions (16 and 142)

Two descs state the *old* hierarchy:

- Tag 16: `school and sailor uniforms, does not require a "skirt" tag` — skirt is no longer implied, so the guidance is backwards.
- Tag 142 (`uniform`): `police officers, firemen, etc. Not school uniforms` — school uniforms are now exactly what it parents.

**Files:** none (data change via admin UI/API, audited).

- [x] **Step 1: Update the desc on tag 16** via the admin tag edit UI (or `PATCH /api/v1/tags/16`):

  > School and sailor uniforms (seifuku). Skirt is no longer implied — add the skirt tag when a skirt is visible.

- [x] **Step 2: Update the desc on tag 142** likewise:

  > Uniforms of any kind — police, military, work, etc. For school/sailor uniforms use the school uniform tag.

- [x] **Step 3: Verify**

```sql
SELECT tag_id, `desc` FROM tags WHERE tag_id IN (16, 142);
```

Expected: the new texts; and `tag_audit_log` rows for both tags with `old_desc`/`new_desc` set.

---

### Task 2: Update `data/tag_mappings.csv` and import

Two changes: point `school_uniform` at 16 directly (its current target 3661 still resolves via the alias, but the CSV title column now mismatches 3661's title and the indirection buys nothing), and add the missing `serafuku` mapping (65,307 stored predictions currently ignored).

`data/tag_mappings.csv` is deliberately git-ignored (`.gitignore:113-115`; commit `2932deb chore(ml): stop tracking environment-bound tag-mapping CSVs (DB is source of truth)`): it carries environment-bound `internal_tag_id`s, and the `tag_mappings` DB table — not the file — is the source of truth. The edits below happen in each environment's own working copy of the file and are never added, staged, or committed via git; dev's edit stays on the dev workstation, and Task 6 applies the equivalent two-row edit directly to prod's own copy.

**Files:**
- Modify: `data/tag_mappings.csv` (line ~194) — working copy only, not tracked in git (see note above)

**Interfaces:**
- Produces: `tag_mappings` rows `school_uniform → 16`, `serafuku → 16` (consumed by Task 3's remap).

- [x] **Step 1: Edit the CSV.** Change the `school_uniform` row and insert `serafuku` after it (file is alphabetical by `danbooru_tag`; `scythe` sits between them):

```csv
school_uniform,school uniform,16,map
scythe,scythe,41317,map
serafuku,school uniform,16,map
```

- [x] **Step 2: Import into dev** (upsert keyed by `danbooru_tag`; existing rows with changed targets update in place):

```bash
uv run python scripts/import_tag_mappings.py data/tag_mappings.csv
```

Expected output: `Created: 1` (serafuku), `Updated: 1` (school_uniform), `Errors: 0`.

- [x] **Step 3: Verify**

```sql
SELECT external_tag, internal_tag_id FROM tag_mappings
WHERE external_tag IN ('school_uniform','serafuku','skirt');
-- expect: school_uniform→16, serafuku→16, skirt→159
```

- [x] **Step 4: No commit** — the CSV is git-ignored by design (see note above); the plan doc and script are committed separately.

---

### Task 3: Scoped remaps in dev

Regenerate suggestions for every image that has raw predictions mapping to tag 159 or tag 16. `remap_image_from_store` rebuilds each image's pending set under the *current* hierarchy: skirt is no longer superseded by school-uniform predictions (skirt now has no children), so skirt suggestions materialize; pending `uniform` (142) suggestions superseded by a ≥0.9 school-uniform prediction are culled. Approved/rejected rows are never touched, so dismissed tags stay dismissed.

**Prerequisite:** Tasks 1–2 (mappings must include serafuku before the remap).

- [x] **Step 1: Record before-counts** (for the delta check in Step 3):

```sql
SELECT tag_id, status, COUNT(*) FROM ml_tag_suggestions
WHERE tag_id IN (159, 16, 142) GROUP BY tag_id, status;
```

- [x] **Step 2: Remap images with skirt predictions (~275k images), then school_uniform/serafuku predictions (~180k distinct images; overlap with the first pass is re-processed idempotently)** — per-image commit; chained in a single tmux session so the two remaps never run concurrently (they touch overlapping images with per-image commits, and concurrency risks lock contention):

```bash
tmux new -d -s remap_flip '
  cd /home/dtaylor/shuu/shuushuu-api
  uv run python scripts/ml_remap.py --model swinv2_base_window8_256.dbv4-full --tag 159 > /tmp/remap_tag159.log 2>&1
  uv run python scripts/ml_remap.py --model swinv2_base_window8_256.dbv4-full --tag 16 > /tmp/remap_tag16.log 2>&1
'
```

Dev actuals: both runs together took ~63 minutes (275,544 then 153,783 images); each log ends with `done: images_remapped=N`.

- [x] **Step 3: Verify deltas**

```sql
-- skirt pendings should grow by roughly +90k (58,144 → ~148k)
SELECT status, COUNT(*) FROM ml_tag_suggestions WHERE tag_id = 159 GROUP BY status;

-- new skirt pendings on school-uniform images specifically (~90,137)
SELECT COUNT(*) FROM ml_tag_suggestions s
JOIN tag_links tl ON tl.image_id = s.image_id AND tl.tag_id = 16
WHERE s.tag_id = 159 AND s.status = 'pending';

-- no pending 'uniform' suggestion may survive on an image with a >=0.9 school-uniform pending
SELECT COUNT(*) FROM ml_tag_suggestions u
JOIN ml_tag_suggestions su ON su.image_id = u.image_id
  AND su.tag_id = 16 AND su.status = 'pending' AND su.confidence >= 0.9
WHERE u.tag_id = 142 AND u.status = 'pending';
-- expect: 0
```

Dev actuals: 159 pendings 58,144 → 152,676 (+94,532; 90,130 on tag-16 images); 16 pendings 26,410 → 26,702; approved/rejected untouched; 0 errors.

Also scan the logs: `grep -cE "error" /tmp/remap_tag159.log /tmp/remap_tag16.log` — deleted images are logged-and-skipped, anything else needs a look before proceeding.

---

### Task 4: Bulk-approve script for high-confidence skirt suggestions

Approve pending `skirt` suggestions with confidence ≥ 0.9 on images already tagged `school uniform` (~26,760 in dev). All review logic (tag_links creation, usage_count triggers, ancestor-suggestion cascade, Meilisearch sync, snapshot-conflict retry) lives in the already-tested `bulk_review_suggestions`; the script only selects IDs and batches — same thin-wrapper convention as `scripts/ml_remap.py`, so no separate test file.

**Files:**
- Create: `scripts/ml_bulk_approve_skirt.py`

**Interfaces:**
- Consumes: `bulk_review_suggestions(db, reviews: list[dict], user_id: int)` from `app/services/ml_suggestion_review.py` (reviews dicts: `{"suggestion_id": int, "action": "approve"}`).

- [x] **Step 1: Write the script**

```python
#!/usr/bin/env python3
"""Bulk-approve pending skirt (159) ML suggestions on school-uniform (16) images.

One-off for the 2026-07 seifuku→school-uniform reparent backfill
(docs/plans/2026-07-26-school-uniform-flip-impl.md). Selection here, all
review side effects in the tested bulk_review_suggestions service.

Usage:
    uv run python scripts/ml_bulk_approve_skirt.py --user-id 123 --dry-run
    uv run python scripts/ml_bulk_approve_skirt.py --user-id 123
"""

import argparse
import asyncio

from sqlalchemy import select

from app.core.database import get_async_session
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.tag_link import TagLinks
from app.services.ml_suggestion_review import bulk_review_suggestions

SKIRT_TAG_ID = 159
SCOPE_TAG_ID = 16  # school uniform


async def run(args: argparse.Namespace) -> None:
    async with get_async_session() as db:
        query = (
            select(MlTagSuggestions.suggestion_id)
            .join(TagLinks, TagLinks.image_id == MlTagSuggestions.image_id)
            .where(
                MlTagSuggestions.tag_id == SKIRT_TAG_ID,
                MlTagSuggestions.status == "pending",
                MlTagSuggestions.confidence >= args.min_confidence,
                TagLinks.tag_id == SCOPE_TAG_ID,
            )
            .order_by(MlTagSuggestions.suggestion_id)
        )
        suggestion_ids = list((await db.execute(query)).scalars())
        print(f"matched pending suggestions: {len(suggestion_ids)}")

        if args.dry_run:
            print("dry run — nothing approved")
            return

        approved = 0
        for start in range(0, len(suggestion_ids), args.batch_size):
            batch = suggestion_ids[start : start + args.batch_size]
            reviews = [{"suggestion_id": sid, "action": "approve"} for sid in batch]
            response = await bulk_review_suggestions(db, reviews, args.user_id)
            approved += response.approved
            if response.errors:
                for err in response.errors[:10]:
                    print(f"  error: {err}")
            print(f"  approved {approved}/{len(suggestion_ids)}")

        print(f"done: approved={approved}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", type=int, required=True,
                        help="Reviewer user_id recorded on the approvals")
    parser.add_argument("--min-confidence", type=float, default=0.9)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
```

- [x] **Step 2: Dry run against dev**

```bash
uv run python scripts/ml_bulk_approve_skirt.py --user-id <your admin user_id> --dry-run
```

Expected: `matched pending suggestions: ~26760` (must be within a few hundred of the Task 3 verification numbers; if it's wildly off, stop and investigate).

- [x] **Step 3: Real run against dev**

```bash
uv run python scripts/ml_bulk_approve_skirt.py --user-id <your admin user_id>
```

- [x] **Step 4: Verify**

```sql
-- approvals landed as tag_links: this count should now be ~8947 + ~26760
SELECT COUNT(*) FROM tag_links s
JOIN tag_links k ON k.image_id = s.image_id AND k.tag_id = 159
WHERE s.tag_id = 16;

-- usage_count trigger kept up (compare to before: 167,754 + approved count)
SELECT usage_count FROM tags WHERE tag_id = 159;

-- no >=0.9 pending skirt left on school-uniform images
-- (rerun the dry-run command; expect: matched pending suggestions: 0)
```

Dev actuals: dry-run 26,757; approved 26,757; tag_links both-count 8,947 → 35,704; usage_count 167,754 → 194,511; re-dry-run 0.

Dev actuals, second pass (2026-07-27): after sampling the 0.8–0.9 band (12/12 showed skirts), a second run with `--min-confidence 0.8` approved 28,640 more — both-count → 64,344, usage_count → 223,151, zero ≥0.8 pending left, 0 errors. Prod runs a single pass at 0.8 (Task 6 Step 5).

Spot-check a handful of the newly tagged images in the UI — the tag should render and the ML panel should show skirt as approved.

- [x] **Step 5: Commit**

```bash
git add scripts/ml_bulk_approve_skirt.py
git commit -m "feat(scripts): bulk-approve high-confidence skirt suggestions for reparent backfill"
```

---

### Task 5: DECISION — strip redundant `uniform` links

2,527 images carry both `uniform` (142) and `school uniform` (16). After the reparent the explicit `uniform` is redundant (ancestor of an applied tag) — this is the both-tags redundancy WhiteKitten raised. Removing it deletes user-applied tags, so **get mod-team sign-off in the Discord thread before running**.

- [ ] **Step 0 (gate): mod sign-off recorded** — if declined, skip this task; redundant links are harmless to search.
- [ ] **Step 1: Remove via the batch service** (records history/audit; do NOT raw-DELETE). Fetch the image list with SQL, then call the service in batches:

```bash
docker exec shuushuu-mariadb-dev mariadb -ushuushuu_dev -p<pw> shuushuu_dev -N -e "
  SELECT u.image_id FROM tag_links u
  JOIN tag_links s ON s.image_id = u.image_id AND s.tag_id = 16
  WHERE u.tag_id = 142;" > /tmp/redundant_uniform_images.txt

uv run python - <<'EOF'
import asyncio
from pathlib import Path
from app.core.database import get_async_session
from app.services.batch_tag import batch_remove_tags

USER_ID = 0  # <-- set to the operating admin user_id before running
image_ids = [int(line) for line in Path("/tmp/redundant_uniform_images.txt").read_text().split()]

async def main():
    async with get_async_session() as db:
        for start in range(0, len(image_ids), 500):
            batch = image_ids[start:start + 500]
            resp = await batch_remove_tags(tag_ids=[142], image_ids=batch, user_id=USER_ID, db=db)
            print(f"{start + len(batch)}/{len(image_ids)} removed={len(resp.removed)} skipped={len(resp.skipped)}")

asyncio.run(main())
EOF
```

- [ ] **Step 2: Verify** — the both-tags count drops to 0 and `uniform`'s usage_count fell by the removed amount:

```sql
SELECT COUNT(*) FROM tag_links u
JOIN tag_links s ON s.image_id = u.image_id AND s.tag_id = 16
WHERE u.tag_id = 142;  -- expect 0
SELECT usage_count FROM tags WHERE tag_id = 142;  -- expect ~39370 - 2527
```

---

### Task 6: Prod rollout

Prod's ML store is seeded and live generation is on (confirmed 2026-07-26), so the full sequence below applies. Sanity-check anyway before starting:

```sql
SELECT COUNT(*) FROM ml_raw_predictions;  -- on PROD; expect ~25M, NOT 0
```

Two timing notes:

- The reparent (Step 1) immediately shrinks `skirt` searches from ~312k to ~168k results until Step 5 restores the high-confidence portion. Schedule Steps 1–5 close together (same day), off-peak.
- Live generation runs against the hierarchy at upload time, so uploads between Step 1 and Step 4 already get correct (new-hierarchy) suggestions; the scoped remaps only rebuild the pre-flip backlog. Concurrent mod reviewing during a remap is safe (snapshot-conflict retry in the review service).

- [ ] **Step 0: Deploying this branch to prod is safe w.r.t. the CSV.** The branch carries no data files — `data/tag_mappings.csv` is git-ignored (Task 2) and was never committed. Checking out or deploying `chore/school-uniform-flip` on prod does not touch, overwrite, or otherwise interact with prod's own untracked `data/tag_mappings.csv` working copy; there's nothing to back up or move aside first.
- [ ] **Step 1: Tag flip on prod** via admin UI, same sequence as Task 0 (rename 3661 → temp, rename 16 → `school uniform`, rename 3661 → `seifuku`, reparent 16 to 142) plus the Task 1 desc update. Run the Task 0 verification SQL on prod (expect 16 canonical/parent=142, 3661 alias_of=16/usage 0, 159 and 142 unchanged — the SQL comment's "usage 153636" is dev-measured; prod will differ slightly).
- [ ] **Step 2: Verify prod search** — `GET /api/v1/search?q=school+uniform` shows tag 16 canonical / 3661 alias. If titles are stale, run `uv run python scripts/reindex_search.py`.
- [ ] **Step 3: Import mappings on prod.**

  a. **Count first.** `SELECT COUNT(*) FROM tag_mappings;` on prod, and `awk 'END{print NR-1}' data/tag_mappings.csv` on prod's copy — compare both against dev (2,140 rows / 2,140 mappings after import). Investigate any large drift before importing (`docs/ml-tag-suggestions.md` still says "297 entries" — that count is stale; the curated file has grown to 2,140).

  b. **Apply the same two-row edit to prod's own `data/tag_mappings.csv` copy** (`school_uniform` row → id 16; insert `serafuku,school uniform,16,map` after the `scythe` row — see Task 2 Step 1). If (a) showed prod's copy already matches dev's row count, copying dev's file wholesale onto prod's is equivalent.

  c. **Run** `uv run python scripts/import_tag_mappings.py data/tag_mappings.csv` and review the Created/Updated/Unchanged/Errors summary — expect serafuku created and school_uniform updated; any *other* creations/updates reflect pre-existing CSV↔DB drift being applied (normally desired, since the CSV is curated, but eyeball them rather than assuming 1/1).

- [ ] **Step 4: Scoped remaps** — chained in a single tmux session so the two remaps never run concurrently (they touch overlapping images with per-image commits, and concurrency risks lock contention); each log ends with `done: images_remapped=N`. Dev took ~63 minutes combined (275,544 then 153,783 images); expect roughly the same order of magnitude on prod. Off-peak.

```bash
tmux new -d -s remap_flip '
  cd /PATH/TO/shuushuu-api   # deployed app dir (see docs/ml-tag-suggestions-prod-seeding.md)
  uv run python scripts/ml_remap.py --model swinv2_base_window8_256.dbv4-full --tag 159 > /sakura/shuushuu/ml-backfill/remap_tag159.log 2>&1
  uv run python scripts/ml_remap.py --model swinv2_base_window8_256.dbv4-full --tag 16 > /sakura/shuushuu/ml-backfill/remap_tag16.log 2>&1
'
```

- [ ] **Step 5: Bulk-approve on prod** — dry-run then real run of `scripts/ml_bulk_approve_skirt.py --user-id <prod admin id> --min-confidence 0.8` (Task 4 steps; **0.8 floor**, decided from dev sampling — 12/12 of a random 0.8–0.9 band sample showed skirts). Confirm a fresh prod DB backup exists first — this writes ~55k tag_links (dev: 26,757 at ≥0.9 + 28,640 at 0.8–0.9). Run off-peak: each 500-row batch's `usage_count` trigger holds a row lock on `tags.tag_id=159` until commit, so heavy concurrent skirt tagging could hit lock timeouts; a smaller `--batch-size` shortens the lock window if contention shows up. The script ends with a benign `RuntimeError: Event loop is closed` traceback *after* its `done: approved=N` line — this is expected (no `engine.dispose()` in the shared session helper, same as `scripts/ml_remap.py`), not a run failure.
- [ ] **Step 6 (if Task 5 approved): redundant-uniform removal on prod** — Task 5 steps against prod.

---

### Task 7: Post-rollout verification & comms

- [ ] **Step 1: End-state numbers on prod**

```sql
SELECT t.tag_id, t.title, t.usage_count FROM tags t WHERE t.tag_id IN (16, 3661, 159, 142);
SELECT status, COUNT(*) FROM ml_tag_suggestions WHERE tag_id = 159 GROUP BY status;
```

- [ ] **Step 2: Spot-check searches** — `skirt` results contain the bulk-approved images; `seifuku` search resolves to school uniform; a seifuku-tagged image without a skirt no longer appears under `skirt`.
- [ ] **Step 3: Tell the mod team** (Discord thread): the flip is live; the review queue now holds the 0.7–0.8 skirt band (~16k) plus the sub-0.7 band (~18k) — reviewed at normal pace, no deadline; new school-uniform suggestions from serafuku predictions will also appear.
- [ ] **Step 4: Merge the branch** — open a PR for `chore/school-uniform-flip` (script, this plan doc; the CSV is git-ignored and never part of the branch — see Task 2).

## Rollback notes

- Flip/reparent: fully reversible via the admin UI (reverse the renames, set parent back to 159); `tag_audit_log` records every step.
- Remap: regenerated *pending* rows are disposable — the seeding runbook's rollback SQL (delete pending by model, batched) applies unchanged.
- Bulk-approve: reversible while attributable — approved rows carry `reviewed_by_user_id` + `reviewed_at`, so the created tag_links can be identified and removed and the suggestions flipped back to pending. Take the DB backup before Step 5 regardless.

## Open decisions

1. **Task 5** — strip the 2,527 redundant `uniform` links? (Needs mod sign-off.)
2. **RESOLVED (2026-07-27): auto-approve floor lowered to 0.8.** Dev sampling: 12/12 random images from the 0.8–0.9 band showed skirts; second dev pass approved that band (28,640). Remaining manual queue in dev: ~34.7k on school-uniform images (16,333 in 0.7–0.8 + 18,400 below 0.7). Lowering further would need fresh sampling of the 0.7–0.8 band.
