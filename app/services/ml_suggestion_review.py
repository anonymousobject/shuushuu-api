"""Apply human review decisions to ML-generated tag suggestions.

Approving a suggestion mirrors the canonical tag-add path (images.py /
batch_tag.py / the admin report-suggestion approval flow): it creates a
TagLink on the canonical tag, records a TagHistory add row, refreshes the
image's denormalized tag-type flags, and syncs the affected tags to
Meilisearch after commit. Rejecting only updates the suggestion row.
"""

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db_retry import retry_on_snapshot_conflict
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.tag import Tags
from app.models.tag_history import TagHistory
from app.models.tag_link import TagLinks
from app.schemas.ml_tag_suggestion import (
    ReviewSuggestionRequest,
    ReviewSuggestionsRequest,
    ReviewSuggestionsResponse,
)
from app.services.ml_suggestion_pipeline import fetch_parent_map
from app.services.search import sync_tags_to_search
from app.services.tag_type_flags import refresh_image_tag_type_flags


async def approve_pending_suggestions_for_links(
    db: AsyncSession,
    links: Iterable[tuple[int, int]],
    user_id: int | None,
) -> None:
    """Mark pending ML suggestions approved when their tag is applied out of band.

    ``links`` is the (image_id, tag_id) pairs for TagLinks the caller just
    created outside the ML review flow (manual tag add, batch tagging, report
    resolution). Without this, those suggestion rows stay 'pending' forever
    and inflate the review-queue worklist counts. The tagger is recorded as
    the reviewer: applying the tag is an implicit approval.

    Matches on the applied (canonical) tag_id only — a pending suggestion
    whose tag has since become an alias of the applied tag is not matched,
    consistent with the exact-tag_id semantics of suggestion generation and
    the queue's grid filter.

    Flushes only; the caller owns the transaction and commit.
    """
    pairs = list(links)
    if not pairs:
        return

    await db.execute(
        update(MlTagSuggestions)
        .where(
            MlTagSuggestions.status == "pending",  # type: ignore[arg-type]
            tuple_(MlTagSuggestions.image_id, MlTagSuggestions.tag_id).in_(pairs),  # type: ignore[arg-type]
        )
        .values(
            status="approved",
            reviewed_at=datetime.now(UTC),
            reviewed_by_user_id=user_id,
        )
    )

    await delete_pending_ancestor_suggestions(db, pairs)


async def delete_pending_ancestor_suggestions(
    db: AsyncSession,
    links: Iterable[tuple[int, int]],
) -> list[int]:
    """Delete pending suggestions made redundant by a newly applied descendant.

    ``links`` is the (image_id, tag_id) pairs for TagLinks the caller just
    created. Each applied tag's Tags.inheritedfrom_id chain is walked and any
    PENDING suggestion rows for those ancestors on that image are deleted —
    once the more specific tag is on the image, suggesting the generic one is
    redundant (generation applies the same rule via
    filter_redundant_suggestions). Approved/rejected rows are never touched,
    and only ancestors are affected: a pending suggestion for a DESCENDANT of
    the applied tag keeps its own review.

    Flush-only; the caller owns the transaction and commit. Returns the
    suggestion_ids of the deleted rows so callers can report them to clients.
    """
    pairs = list(links)
    if not pairs:
        return []

    # Flush first: the session is autoflush=False and callers in this module
    # (_apply_reviews_for_image) set suggestion.status as unflushed ORM
    # attribute mutations before reaching here. Without this, the pending-only
    # DELETE below reads stale pre-flush status and can delete a row the
    # same-batch review just marked approved/rejected.
    await db.flush()

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
        return []

    doomed_ids_result = await db.execute(
        select(MlTagSuggestions.suggestion_id).where(  # type: ignore[call-overload]
            MlTagSuggestions.status == "pending",
            tuple_(MlTagSuggestions.image_id, MlTagSuggestions.tag_id).in_(doomed),  # type: ignore[arg-type]
        )
    )
    doomed_ids = list(doomed_ids_result.scalars().all())

    await db.execute(
        delete(MlTagSuggestions).where(
            MlTagSuggestions.status == "pending",  # type: ignore[arg-type]
            tuple_(MlTagSuggestions.image_id, MlTagSuggestions.tag_id).in_(doomed),  # type: ignore[arg-type]
        )
    )

    return doomed_ids


async def _apply_reviews_for_image(
    db: AsyncSession,
    image_id: int,
    items: list[ReviewSuggestionRequest],
    user_id: int,
) -> tuple[set[int], list[int]]:
    """Apply approve/reject decisions for all suggestions on a single image.

    Performs:
    - alias-resolve → create TagLink + TagHistory (approve path)
    - set status / reviewed_at / reviewed_by_user_id on each suggestion row
    - refresh_image_tag_type_flags(db, image_id) when any TagLink was created

    Does NOT call db.commit() and does NOT call sync_tags_to_search.
    Returns (created_link_tag_ids, removed_suggestion_ids): the set of
    canonical tag_ids for which a new TagLink was created, and the
    suggestion_ids of any PENDING ancestor suggestions cascade-deleted as a
    result (empty when no TagLink was created).
    """
    suggestion_ids = [item.suggestion_id for item in items]
    suggestions_result = await db.execute(
        select(MlTagSuggestions).where(
            MlTagSuggestions.suggestion_id.in_(suggestion_ids),  # type: ignore[union-attr]
            MlTagSuggestions.image_id == image_id,  # type: ignore[arg-type]
        )
    )
    suggestions_by_id = {sugg.suggestion_id: sugg for sugg in suggestions_result.scalars().all()}

    # Resolve each suggestion's tag to its canonical tag (alias-aware).
    tag_ids = {sugg.tag_id for sugg in suggestions_by_id.values()}
    resolved_tag_ids: dict[int, int] = {}
    if tag_ids:
        tags_result = await db.execute(
            select(Tags).where(Tags.tag_id.in_(tag_ids))  # type: ignore[union-attr]
        )
        for tag in tags_result.scalars().all():
            canonical_id = tag.alias_of if tag.alias_of else tag.tag_id
            resolved_tag_ids[tag.tag_id] = canonical_id  # type: ignore[index, assignment]

    # Batch fetch existing TagLinks on the canonical tags to avoid duplicates.
    canonical_tag_ids = set(resolved_tag_ids.values())
    existing_links: set[tuple[int, int]] = set()
    if canonical_tag_ids:
        links_result = await db.execute(
            select(TagLinks).where(
                TagLinks.image_id == image_id,  # type: ignore[arg-type]
                TagLinks.tag_id.in_(canonical_tag_ids),  # type: ignore[attr-defined]
            )
        )
        existing_links = {(link.image_id, link.tag_id) for link in links_result.scalars().all()}

    created_link_tag_ids: set[int] = set()
    review_time = datetime.now(UTC)

    for review_item in items:
        suggestion = suggestions_by_id.get(review_item.suggestion_id)
        if not suggestion:
            # Caller is responsible for error tracking; we just skip missing ones.
            continue

        if review_item.action == "approve":
            # Resolve alias at apply time; the suggestion row keeps its tag_id.
            resolved_tag_id = resolved_tag_ids.get(suggestion.tag_id, suggestion.tag_id)

            # Create TagLink + history only if the canonical tag isn't linked yet.
            if (image_id, resolved_tag_id) not in existing_links:
                db.add(
                    TagLinks(
                        image_id=image_id,
                        tag_id=resolved_tag_id,
                        user_id=user_id,
                    )
                )
                db.add(
                    TagHistory(
                        image_id=image_id,
                        tag_id=resolved_tag_id,
                        action="a",
                        user_id=user_id,
                    )
                )
                existing_links.add((image_id, resolved_tag_id))
                created_link_tag_ids.add(resolved_tag_id)

            suggestion.status = "approved"
            suggestion.reviewed_at = review_time
            suggestion.reviewed_by_user_id = user_id

        elif review_item.action == "reject":
            suggestion.status = "rejected"
            suggestion.reviewed_at = review_time
            suggestion.reviewed_by_user_id = user_id

    removed_suggestion_ids: list[int] = []
    if created_link_tag_ids:
        removed_suggestion_ids = await delete_pending_ancestor_suggestions(
            db, [(image_id, tag_id) for tag_id in created_link_tag_ids]
        )

    # Refresh denormalized tag-type flags (per-image; flush only, no commit).
    if created_link_tag_ids:
        await refresh_image_tag_type_flags(db, image_id)

    return created_link_tag_ids, removed_suggestion_ids


async def review_ml_tag_suggestions(
    image_id: int,
    request: ReviewSuggestionsRequest,
    user_id: int,
    db: AsyncSession,
) -> ReviewSuggestionsResponse:
    """Approve or reject ML tag suggestions in batch.

    Approving applies the suggestion's tag to the image (creating a TagLink on
    the canonical tag if the suggestion's tag has since become an alias) and
    records the add in tag history. Rejecting only marks the suggestion. The
    suggestion row keeps its original tag_id regardless of alias resolution.
    """
    suggestion_ids = [item.suggestion_id for item in request.suggestions]

    # A concurrent ml_remap run (or another reviewer) can rewrite these same
    # suggestion rows between our fetch and our commit, tripping MariaDB
    # ER_CHECKREAD (1020) under innodb_snapshot_isolation (see
    # app/core/db_retry.py). Retry the whole fetch-through-commit unit on a
    # fresh snapshot. Re-running _apply() after a rollback is idempotent-safe
    # by construction: the fresh fetch re-reads current suggestion statuses,
    # so changes already committed by the other writer are visible under the
    # new snapshot (no double-apply), and a suggestion the other writer
    # removed simply falls through to the missing-suggestion errors path.
    async def _apply() -> tuple[int, int, list[str], set[int], list[int]]:
        suggestions_result = await db.execute(
            select(MlTagSuggestions).where(
                MlTagSuggestions.suggestion_id.in_(suggestion_ids),  # type: ignore[union-attr]
                MlTagSuggestions.image_id == image_id,  # type: ignore[arg-type]
            )
        )
        suggestions_by_id = {
            sugg.suggestion_id: sugg for sugg in suggestions_result.scalars().all()
        }

        approved_count = 0
        rejected_count = 0
        errors: list[str] = []

        # Separate items into found (processed by helper) and missing (errors).
        found_items: list[ReviewSuggestionRequest] = []
        for review_item in request.suggestions:
            if review_item.suggestion_id not in suggestions_by_id:
                errors.append(f"Suggestion {review_item.suggestion_id} not found")
            else:
                found_items.append(review_item)
                if review_item.action == "approve":
                    approved_count += 1
                elif review_item.action == "reject":
                    rejected_count += 1

        created, removed_suggestion_ids = await _apply_reviews_for_image(
            db, image_id, found_items, user_id
        )

        await db.commit()

        return approved_count, rejected_count, errors, created, removed_suggestion_ids

    (
        approved_count,
        rejected_count,
        errors,
        created,
        removed_suggestion_ids,
    ) = await retry_on_snapshot_conflict(db, _apply, what="ml_review_apply")

    # Sync affected tags to Meilisearch (usage_count updated by DB trigger).
    # Non-DB side effect: stays outside the retried unit so it never repeats.
    if created:
        tag_results = await db.execute(
            select(Tags).where(Tags.tag_id.in_(created))  # type: ignore[union-attr]
        )
        await sync_tags_to_search(list(tag_results.scalars().all()), db=db)

    return ReviewSuggestionsResponse(
        approved=approved_count,
        rejected=rejected_count,
        errors=errors,
        removed_suggestion_ids=removed_suggestion_ids,
    )


async def bulk_review_suggestions(
    db: AsyncSession,
    reviews: list[dict[str, Any]],
    user_id: int,
) -> ReviewSuggestionsResponse:
    """Approve or reject ML tag suggestions across multiple images in one transaction.

    Fetches suggestions by suggestion_id only (no image_id filter), groups them
    by image_id, then calls _apply_reviews_for_image once per distinct image.
    Missing suggestion_ids go to errors without aborting valid ones.

    Emits a single db.commit() and a single batched sync_tags_to_search over
    all created TagLinks — never N commits or N syncs.
    """
    suggestion_ids = [r["suggestion_id"] for r in reviews]

    # Same snapshot-conflict exposure as review_ml_tag_suggestions (a
    # concurrent ml_remap run or another reviewer rewriting these rows), but
    # spanning every image in the batch. Retry the whole fetch-through-commit
    # unit on a fresh snapshot (see app/core/db_retry.py). Re-running _apply()
    # after a rollback is idempotent-safe by construction: the fresh fetch
    # re-reads current suggestion statuses, so changes already committed by
    # the other writer are visible under the new snapshot (no double-apply),
    # and rows the other writer removed simply fall through to the
    # missing-suggestion errors path.
    async def _apply() -> tuple[int, int, list[str], set[int], list[int]]:
        suggestions_result = await db.execute(
            select(MlTagSuggestions).where(
                MlTagSuggestions.suggestion_id.in_(suggestion_ids)  # type: ignore[union-attr]
            )
        )
        suggestions_by_id = {
            sugg.suggestion_id: sugg for sugg in suggestions_result.scalars().all()
        }

        approved_count = 0
        rejected_count = 0
        errors: list[str] = []

        # Group found suggestions by image_id; record errors for missing ids.
        items_by_image: dict[int, list[ReviewSuggestionRequest]] = defaultdict(list)
        for r in reviews:
            sid = r["suggestion_id"]
            action = r["action"]
            if sid not in suggestions_by_id:
                errors.append(f"Suggestion {sid} not found")
                continue
            sugg = suggestions_by_id[sid]
            items_by_image[sugg.image_id].append(
                ReviewSuggestionRequest(suggestion_id=sid, action=action)
            )
            if action == "approve":
                approved_count += 1
            elif action == "reject":
                rejected_count += 1

        # Process each image's suggestions; accumulate created tag_ids and
        # cascade-deleted ancestor suggestion_ids.
        all_created_tag_ids: set[int] = set()
        all_removed_suggestion_ids: list[int] = []
        for image_id, items in items_by_image.items():
            created, removed_suggestion_ids = await _apply_reviews_for_image(
                db, image_id, items, user_id
            )
            all_created_tag_ids |= created
            all_removed_suggestion_ids.extend(removed_suggestion_ids)

        # Single commit spanning all images.
        await db.commit()

        return (
            approved_count,
            rejected_count,
            errors,
            all_created_tag_ids,
            all_removed_suggestion_ids,
        )

    (
        approved_count,
        rejected_count,
        errors,
        all_created_tag_ids,
        all_removed_suggestion_ids,
    ) = await retry_on_snapshot_conflict(db, _apply, what="ml_review_bulk_apply")

    # Single batched search-sync over the union of created tag_ids.
    # Non-DB side effect: stays outside the retried unit so it never repeats.
    if all_created_tag_ids:
        tag_results = await db.execute(
            select(Tags).where(Tags.tag_id.in_(all_created_tag_ids))  # type: ignore[union-attr]
        )
        await sync_tags_to_search(list(tag_results.scalars().all()), db=db)

    return ReviewSuggestionsResponse(
        approved=approved_count,
        rejected=rejected_count,
        errors=errors,
        removed_suggestion_ids=all_removed_suggestion_ids,
    )
