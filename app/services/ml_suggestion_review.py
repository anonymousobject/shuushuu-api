"""Apply human review decisions to ML-generated tag suggestions.

Approving a suggestion mirrors the canonical tag-add path (images.py /
batch_tag.py / the admin report-suggestion approval flow): it creates a
TagLink on the canonical tag, records a TagHistory add row, refreshes the
image's denormalized tag-type flags, and syncs the affected tags to
Meilisearch after commit. Rejecting only updates the suggestion row.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.tag import Tags
from app.models.tag_history import TagHistory
from app.models.tag_link import TagLinks
from app.schemas.ml_tag_suggestion import (
    ReviewSuggestionsRequest,
    ReviewSuggestionsResponse,
)
from app.services.search import sync_tags_to_search
from app.services.tag_type_flags import refresh_image_tag_type_flags


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
    # Batch fetch all suggestions in one query to avoid N+1.
    suggestion_ids = [item.suggestion_id for item in request.suggestions]
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

    approved_count = 0
    rejected_count = 0
    errors: list[str] = []
    created_link_tag_ids: set[int] = set()

    review_time = datetime.now(UTC)

    for review_item in request.suggestions:
        suggestion = suggestions_by_id.get(review_item.suggestion_id)
        if not suggestion:
            errors.append(f"Suggestion {review_item.suggestion_id} not found")
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
            approved_count += 1

        elif review_item.action == "reject":
            suggestion.status = "rejected"
            suggestion.reviewed_at = review_time
            suggestion.reviewed_by_user_id = user_id
            rejected_count += 1

    # Refresh denormalized tag-type flags once when any TagLink was created.
    if created_link_tag_ids:
        await refresh_image_tag_type_flags(db, image_id)

    await db.commit()

    # Sync affected tags to Meilisearch (usage_count updated by DB trigger).
    if created_link_tag_ids:
        tag_results = await db.execute(
            select(Tags).where(Tags.tag_id.in_(created_link_tag_ids))  # type: ignore[union-attr]
        )
        await sync_tags_to_search(list(tag_results.scalars().all()), db=db)

    return ReviewSuggestionsResponse(
        approved=approved_count,
        rejected=rejected_count,
        errors=errors,
    )
