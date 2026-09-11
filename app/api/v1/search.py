"""Search endpoint, answered from Postgres (app/services/tag_search.py)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.api.dependencies import SortOrder, TagSortBy
from app.core.database import get_db
from app.core.logging import get_logger
from app.models.tag import Tags
from app.schemas.search import SearchResponse, TagSearchHit
from app.services.artist_identity import parse_identity_query, resolve_identity, site_display_name
from app.services.tag_search import search_tags

logger = get_logger(__name__)

router = APIRouter(prefix="/search", tags=["search"])


async def _identity_hit_already_on_first_page(
    db: AsyncSession,
    tag_id: int,
    q: str,
    *,
    limit: int,
    type_id: int | None,
    exclude_aliases: bool,
    sort: list[str] | None,
) -> bool:
    """Whether the first page (offset=0) for this query already has tag_id.

    Used only when the caller requested a later page, so the exact-identity
    layer's "already found" check has a stable, page-independent answer —
    see the comment in `search()`.
    """
    first_page = await search_tags(
        db,
        q,
        limit=limit,
        offset=0,
        type_filter=type_id,
        exclude_aliases=exclude_aliases,
        sort=sort,
    )
    return tag_id in first_page.tag_ids


@router.get("", response_model=SearchResponse)
async def search(
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[
        str,
        Query(
            max_length=200, description="Search query (empty = list all, filter+sort still apply)"
        ),
    ] = "",
    type_id: Annotated[int | None, Query(description="Filter by tag type", alias="type")] = None,
    exclude_aliases: Annotated[bool, Query(description="Exclude alias tags")] = False,
    limit: Annotated[int, Query(ge=1, le=100, description="Max results")] = 20,
    offset: Annotated[int, Query(ge=0, le=500_000, description="Results to skip")] = 0,
    sort_by: Annotated[
        TagSortBy | None,
        Query(description="Sort field (omit for relevance ranking)"),
    ] = None,
    sort_order: Annotated[SortOrder, Query(description="Sort order")] = "DESC",
) -> SearchResponse:
    """Search tags. Relevance order unless sort_by is given, in which case the sort dominates."""
    sort = [f"{sort_by}:{sort_order.lower()}"] if sort_by is not None else None

    result = await search_tags(
        db,
        q,
        limit=limit,
        offset=offset,
        type_filter=type_id,
        exclude_aliases=exclude_aliases,
        sort=sort,
    )

    # Fetch full tag records from Postgres, preserving the engine's rank order.
    # Outerjoin a self-aliased Tags so alias hits include the parent's title
    # as alias_of_name — same pattern as list_tags in app/api/v1/tags.py.
    hits: list[TagSearchHit] = []
    if result.tag_ids:
        AliasedTag = aliased(Tags)
        query = (
            select(
                Tags,
                AliasedTag.title.label("alias_of_name"),  # type: ignore[union-attr]
                AliasedTag.usage_count.label("alias_of_usage_count"),  # type: ignore[attr-defined]
            )
            .outerjoin(AliasedTag, Tags.alias_of == AliasedTag.tag_id)  # type: ignore[arg-type]
            .where(Tags.tag_id.in_(result.tag_ids))  # type: ignore[union-attr]
        )
        db_result = await db.execute(query)
        rows_by_id = {
            tag.tag_id: (tag, alias_of_name, alias_of_usage_count)
            for tag, alias_of_name, alias_of_usage_count in db_result.all()
        }

        for tag_id in result.tag_ids:
            row = rows_by_id.get(tag_id)
            if row:
                tag, alias_of_name, alias_of_usage_count = row
                hit = TagSearchHit.model_validate(tag)
                hit.alias_of_name = alias_of_name
                hit.alias_of_usage_count = alias_of_usage_count
                hits.append(hit)

    total = result.total

    # Exact artist-identity layer: if the query names a specific external
    # identity (bare ID, "pixiv <id>", or a profile URL), prepend the tag that
    # owns it — even if the text search missed it or ranked it lower.
    # Runs after the engine call above; the owning tag must still satisfy the
    # request's own filters.
    #
    # The owning tag must still satisfy the request's own `type`/
    # `exclude_aliases` filters before it's surfaced — this layer supplements
    # the engine's ranking, it doesn't bypass the filters the engine itself
    # enforced. Skipping this check would leak the tag into type-filtered or
    # alias-excluded result sets it doesn't belong in.
    #
    # Pagination semantics: the hit is only ever injected/reflagged into the
    # FIRST page (offset == 0). Doing this on later pages too would make the
    # tag appear to "jump" into view on every page as a user paginates, and
    # — the actual bug this comment replaces — checking for it against a
    # later page's own (unrelated) `hits` almost never finds it there, so
    # every page would independently decide it's "new" and re-increment
    # `total`, giving each page a different, ever-growing total for the same
    # query. To keep `total` identical no matter which page is requested,
    # "already found by the engine" is always resolved against the engine's
    # first page for this query — reusing this request's own result when
    # offset is already 0, otherwise issuing one extra lookup at offset=0.
    identity = parse_identity_query(q) if q else None
    if identity is not None:
        exact_tag = await resolve_identity(db, identity)
        if (
            exact_tag is not None
            and (type_id is None or exact_tag.type == type_id)
            and not (exclude_aliases and exact_tag.alias_of is not None)
        ):
            label = f"{site_display_name(identity.site)} {identity.external_id}"

            # Alias rows of the matched canonical are redundant with the
            # flagged canonical hit (shown first) -- during the
            # alias-coexistence period, the engine can independently match
            # both the canonical and a legacy alias tag (e.g. one titled
            # "Pixiv 21412050") for the same identity query, rendering as
            # two visually-identical suggestion rows. Drop them from THIS
            # page's hits regardless of offset -- an alias row can land on
            # a later page even when the canonical itself is only ever
            # injected/flagged on the first page (see below). `total` is
            # adjusted down by exactly what's dropped from this page, so a
            # query whose alias rows straddle a page boundary reports a
            # slightly different total per page -- tolerated because these
            # rows disappear entirely once aliases are fully retired.
            alias_rows = [h for h in hits if h.alias_of == exact_tag.tag_id]
            if alias_rows:
                hits = [h for h in hits if h.alias_of != exact_tag.tag_id]
                total -= len(alias_rows)

            if offset == 0:
                already_found = exact_tag.tag_id in result.tag_ids
            else:
                already_found = await _identity_hit_already_on_first_page(
                    db,
                    exact_tag.tag_id,  # type: ignore[arg-type]
                    q,
                    limit=limit,
                    type_id=type_id,
                    exclude_aliases=exclude_aliases,
                    sort=sort,
                )
            if not already_found:
                total += 1

            if offset == 0:
                existing = next((h for h in hits if h.tag_id == exact_tag.tag_id), None)
                if existing is not None:
                    hits.remove(existing)
                    existing.matched_identity = label
                    hits.insert(0, existing)
                else:
                    exact_hit = TagSearchHit.model_validate(exact_tag)
                    exact_hit.matched_identity = label
                    hits.insert(0, exact_hit)
                    # Prepending onto an already-full page would exceed the
                    # requested page size; drop the lowest-ranked engine hit
                    # to keep the limit contract.
                    hits = hits[:limit]

    return SearchResponse(
        query=q,
        entity="tags",
        hits=hits,
        total=total,
        limit=limit,
        offset=offset,
    )
