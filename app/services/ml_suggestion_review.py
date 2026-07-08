"""Apply human review decisions to ML-generated tag suggestions.

Approving a suggestion mirrors the canonical tag-add path (images.py /
batch_tag.py / the admin report-suggestion approval flow): it creates a
TagLink on the canonical tag, records a TagHistory add row, refreshes the
image's denormalized tag-type flags, and syncs the affected tags to
Meilisearch after commit. Rejecting only updates the suggestion row.
"""

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.tag import Tags
from app.models.tag_history import TagHistory
from app.models.tag_link import TagLinks
from app.schemas.ml_tag_suggestion import (
    ReviewSuggestionRequest,
    ReviewSuggestionsRequest,
    ReviewSuggestionsResponse,
)
from app.services.search import sync_tags_to_search
from app.services.tag_type_flags import refresh_image_tag_type_flags


async def _apply_reviews_for_image(
    db: AsyncSession,
    image_id: int,
    items: list[ReviewSuggestionRequest],
    user_id: int,
) -> set[int]:
    """Apply approve/reject decisions for all suggestions on a single image.

    Performs:
    - alias-resolve → create TagLink + TagHistory (approve path)
    - set status / reviewed_at / reviewed_by_user_id on each suggestion row
    - refresh_image_tag_type_flags(db, image_id) when any TagLink was created

    Does NOT call db.commit() and does NOT call sync_tags_to_search.
    Returns the set of canonical tag_ids for which a new TagLink was created.
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

    # Refresh denormalized tag-type flags (per-image; flush only, no commit).
    if created_link_tag_ids:
        await refresh_image_tag_type_flags(db, image_id)

    return created_link_tag_ids


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
    suggestions_result = await db.execute(
        select(MlTagSuggestions).where(
            MlTagSuggestions.suggestion_id.in_(suggestion_ids),  # type: ignore[union-attr]
            MlTagSuggestions.image_id == image_id,  # type: ignore[arg-type]
        )
    )
    suggestions_by_id = {sugg.suggestion_id: sugg for sugg in suggestions_result.scalars().all()}

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

    created = await _apply_reviews_for_image(db, image_id, found_items, user_id)

    await db.commit()

    # Sync affected tags to Meilisearch (usage_count updated by DB trigger).
    if created:
        tag_results = await db.execute(
            select(Tags).where(Tags.tag_id.in_(created))  # type: ignore[union-attr]
        )
        await sync_tags_to_search(list(tag_results.scalars().all()), db=db)

    return ReviewSuggestionsResponse(
        approved=approved_count,
        rejected=rejected_count,
        errors=errors,
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
    suggestions_result = await db.execute(
        select(MlTagSuggestions).where(
            MlTagSuggestions.suggestion_id.in_(suggestion_ids)  # type: ignore[union-attr]
        )
    )
    suggestions_by_id = {sugg.suggestion_id: sugg for sugg in suggestions_result.scalars().all()}

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

    # Process each image's suggestions; accumulate created tag_ids.
    all_created_tag_ids: set[int] = set()
    for image_id, items in items_by_image.items():
        created = await _apply_reviews_for_image(db, image_id, items, user_id)
        all_created_tag_ids |= created

    # Single commit spanning all images.
    await db.commit()

    # Single batched search-sync over the union of created tag_ids.
    if all_created_tag_ids:
        tag_results = await db.execute(
            select(Tags).where(Tags.tag_id.in_(all_created_tag_ids))  # type: ignore[union-attr]
        )
        await sync_tags_to_search(list(tag_results.scalars().all()), db=db)

    return ReviewSuggestionsResponse(
        approved=approved_count,
        rejected=rejected_count,
        errors=errors,
    )
