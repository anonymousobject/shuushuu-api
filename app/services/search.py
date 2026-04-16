"""Search service for Meilisearch integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from meilisearch_python_sdk import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.tag import Tags
from app.models.tag_external_link import TagExternalLinks

logger = get_logger(__name__)

TAGS_INDEX_NAME = "tags"

# Module-level reference set during app lifespan
_search_service: SearchService | None = None


def set_search_service(service: SearchService | None) -> None:
    """Set the module-level search service instance (called from lifespan)."""
    global _search_service
    _search_service = service


async def _get_parent_usage_count(db: AsyncSession, alias_of: int) -> int | None:
    """Look up a parent tag's usage_count. Returns None if not found."""
    result = await db.execute(
        select(Tags.usage_count).where(Tags.tag_id == alias_of)  # type: ignore[call-overload]
    )
    return result.scalar_one_or_none()


async def _get_parent_usage_counts(db: AsyncSession, tags: list[Tags]) -> dict[int, int]:
    """Look up parent usage_counts for all alias tags in a batch.

    Returns a mapping of alias tag_id -> parent usage_count.
    """
    alias_tags = {tag.tag_id: tag.alias_of for tag in tags if tag.alias_of is not None}
    if not alias_tags:
        return {}
    parent_ids = set(alias_tags.values())
    result = await db.execute(
        select(Tags.tag_id, Tags.usage_count).where(  # type: ignore[call-overload]
            Tags.tag_id.in_(parent_ids)  # type: ignore[union-attr]
        )
    )
    parent_counts: dict[int, int] = {row.tag_id: row.usage_count for row in result.all()}
    return {
        alias_id: parent_counts[parent_id]  # type: ignore[misc]
        for alias_id, parent_id in alias_tags.items()
        if parent_id in parent_counts
    }


async def _get_external_urls(db: AsyncSession, tag_id: int) -> list[str]:
    """Fetch external URLs for a single tag."""
    result = await db.execute(
        select(TagExternalLinks.url).where(  # type: ignore[call-overload]
            TagExternalLinks.tag_id == tag_id
        )
    )
    return list(result.scalars().all())


async def _get_external_urls_batch(db: AsyncSession, tag_ids: list[int]) -> dict[int, list[str]]:
    """Fetch external URLs for multiple tags in a single query.

    Returns a mapping of tag_id -> list of URLs.
    """
    if not tag_ids:
        return {}
    result = await db.execute(
        select(TagExternalLinks.tag_id, TagExternalLinks.url).where(  # type: ignore[call-overload]
            TagExternalLinks.tag_id.in_(tag_ids)  # type: ignore[attr-defined]
        )
    )
    urls_map: dict[int, list[str]] = {}
    for row in result.all():
        urls_map.setdefault(row.tag_id, []).append(row.url)
    return urls_map


async def sync_tag_to_search(
    tag: Tags,
    *,
    db: AsyncSession | None = None,
    service: SearchService | None = None,
) -> None:
    """Sync a tag to Meilisearch. Best-effort -- never raises.

    Awaits the Meilisearch call but runs after the MySQL commit, so the
    write path has already succeeded. Typically completes in <10ms.

    Args:
        tag: The tag to sync
        db: Optional DB session for looking up parent usage_count on alias tags
        service: SearchService instance, or None to use module-level default
    """
    svc = service or _search_service
    if svc is None:
        return
    try:
        parent_usage_count = None
        external_urls = None
        if db is not None:
            if tag.alias_of is not None:
                parent_usage_count = await _get_parent_usage_count(db, tag.alias_of)
            external_urls = await _get_external_urls(db, tag.tag_id)  # type: ignore[arg-type]
        await svc.index_tag(tag, parent_usage_count=parent_usage_count, external_urls=external_urls)
    except Exception:
        logger.warning("meilisearch_sync_failed", tag_id=tag.tag_id, exc_info=True)


async def sync_tags_to_search(
    tags: list[Tags],
    *,
    db: AsyncSession | None = None,
    service: SearchService | None = None,
) -> None:
    """Sync multiple tags to Meilisearch in a single call. Best-effort -- never raises.

    Awaits the Meilisearch call but runs after the MySQL commit, so the
    write path has already succeeded.

    Args:
        tags: The tags to sync
        db: Optional DB session for looking up parent usage_counts on alias tags
        service: SearchService instance, or None to use module-level default
    """
    if not tags:
        return
    svc = service or _search_service
    if svc is None:
        return
    try:
        parent_counts = None
        external_urls_map = None
        if db is not None:
            parent_counts = await _get_parent_usage_counts(db, tags)
            external_urls_map = await _get_external_urls_batch(
                db,
                [tag.tag_id for tag in tags],  # type: ignore[misc]
            )
        await svc.index_tags(
            tags,
            parent_usage_counts=parent_counts,
            external_urls_map=external_urls_map,
        )
    except Exception:
        logger.warning("meilisearch_bulk_sync_failed", count=len(tags), exc_info=True)


async def sync_tag_delete_to_search(tag_id: int, *, service: SearchService | None = None) -> None:
    """Remove a tag from Meilisearch. Best-effort -- never raises.

    Awaits the Meilisearch call but runs after the MySQL commit, so the
    write path has already succeeded. Typically completes in <10ms.

    Args:
        tag_id: ID of the tag to remove
        service: SearchService instance, or None to use module-level default
    """
    svc = service or _search_service
    if svc is None:
        return
    try:
        await svc.delete_tag(tag_id)
    except Exception:
        logger.warning("meilisearch_sync_delete_failed", tag_id=tag_id, exc_info=True)


@dataclass
class TagSearchResult:
    """Result from a tag search operation."""

    tag_ids: list[int]
    total: int


def _tag_to_document(
    tag: Tags,
    *,
    parent_usage_count: int | None = None,
    external_urls: list[str] | None = None,
) -> dict[str, Any]:
    """Convert a Tags model to a Meilisearch document.

    Args:
        tag: The tag to convert.
        parent_usage_count: If provided and the tag is an alias, use this
            instead of the tag's own usage_count for ranking purposes.
        external_urls: URLs associated with this tag (artist sites, etc.).
            Searchable at lower priority than title/desc.
    """
    usage_count = tag.usage_count
    if tag.alias_of is not None and parent_usage_count is not None:
        usage_count = parent_usage_count
    return {
        "tag_id": tag.tag_id,
        "title": tag.title,
        "desc": tag.desc,
        "type": tag.type,
        "usage_count": usage_count,
        "alias_of": tag.alias_of,
        "external_urls": external_urls or [],
    }


async def configure_tags_index(client: AsyncClient) -> None:
    """Create and configure the tags index in Meilisearch.

    Sets ranking rules, filterable attributes, and searchable attributes.
    Idempotent — safe to call on every startup.
    """
    try:
        await client.create_index(TAGS_INDEX_NAME, primary_key="tag_id")
    except Exception:
        # Index may already exist; log for observability in case of real failures.
        logger.debug("meilisearch_create_tags_index_failed", exc_info=True)

    index = client.index(TAGS_INDEX_NAME)
    await index.update_ranking_rules(
        [
            "words",
            "typo",
            "proximity",
            "attribute",
            "exactness",
            "usage_count:desc",
        ]
    )
    await index.update_filterable_attributes(["type", "alias_of"])
    await index.update_searchable_attributes(["title", "desc", "external_urls"])
    await index.update_sortable_attributes(["usage_count"])

    logger.info("meilisearch_tags_index_configured")


class SearchService:
    """Service for indexing and searching via Meilisearch."""

    def __init__(self, client: AsyncClient) -> None:
        self.client = client

    async def index_tag(
        self,
        tag: Tags,
        *,
        parent_usage_count: int | None = None,
        external_urls: list[str] | None = None,
    ) -> None:
        """Index or update a single tag in Meilisearch."""
        doc = _tag_to_document(
            tag, parent_usage_count=parent_usage_count, external_urls=external_urls
        )
        index = self.client.index(TAGS_INDEX_NAME)
        await index.add_documents([doc])
        logger.debug("meilisearch_tag_indexed", tag_id=tag.tag_id)

    async def index_tags(
        self,
        tags: list[Tags],
        *,
        parent_usage_counts: dict[int, int] | None = None,
        external_urls_map: dict[int, list[str]] | None = None,
    ) -> None:
        """Bulk index multiple tags in Meilisearch.

        Args:
            tags: Tags to index.
            parent_usage_counts: Optional mapping of tag_id -> parent usage_count
                for alias tags, so they rank by parent popularity.
            external_urls_map: Optional mapping of tag_id -> list of external URLs.
        """
        if not tags:
            return
        counts = parent_usage_counts or {}
        urls_map = external_urls_map or {}
        docs = [
            _tag_to_document(
                tag,
                parent_usage_count=counts.get(tag.tag_id),  # type: ignore[arg-type]
                external_urls=urls_map.get(tag.tag_id),  # type: ignore[arg-type]
            )
            for tag in tags
        ]
        index = self.client.index(TAGS_INDEX_NAME)
        await index.add_documents(docs)
        logger.debug("meilisearch_tags_indexed", count=len(docs))

    async def delete_tag(self, tag_id: int) -> None:
        """Remove a tag from the Meilisearch index."""
        index = self.client.index(TAGS_INDEX_NAME)
        await index.delete_document(str(tag_id))
        logger.debug("meilisearch_tag_deleted", tag_id=tag_id)

    async def search_tags(
        self,
        query: str,
        *,
        limit: int = 20,
        offset: int = 0,
        type_filter: int | None = None,
        exclude_aliases: bool = False,
    ) -> TagSearchResult:
        """Search tags via Meilisearch.

        Args:
            query: Search text
            limit: Max results to return
            offset: Number of results to skip
            type_filter: Filter by tag type (TagType constant)
            exclude_aliases: If True, exclude tags that are aliases

        Returns:
            TagSearchResult with ordered tag IDs and total count
        """
        filters: list[str] = []
        if type_filter is not None:
            filters.append(f"type = {type_filter}")
        if exclude_aliases:
            filters.append("alias_of IS NULL")

        filter_str = " AND ".join(filters) if filters else None

        index = self.client.index(TAGS_INDEX_NAME)
        results = await index.search(
            query,
            limit=limit,
            offset=offset,
            filter=filter_str,
        )

        tag_ids = [hit["tag_id"] for hit in results.hits]
        logger.debug(
            "meilisearch_tag_search",
            query=query,
            hits=len(tag_ids),
            total=results.estimated_total_hits,
        )
        return TagSearchResult(tag_ids=tag_ids, total=results.estimated_total_hits or 0)
