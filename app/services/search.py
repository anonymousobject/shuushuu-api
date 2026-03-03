"""Search service for Meilisearch integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from meilisearch_python_sdk import AsyncClient

from app.core.logging import get_logger
from app.models.tag import Tags

logger = get_logger(__name__)

TAGS_INDEX_NAME = "tags"

# Module-level reference set during app lifespan
_search_service: SearchService | None = None


def set_search_service(service: SearchService | None) -> None:
    """Set the module-level search service instance (called from lifespan)."""
    global _search_service
    _search_service = service


def get_search_service_instance() -> SearchService | None:
    """Get the current search service, or None if not initialized."""
    return _search_service


async def sync_tag_to_search(tag: Tags, *, service: SearchService | None = None) -> None:
    """Sync a tag to Meilisearch. Best-effort -- never raises.

    Awaits the Meilisearch call but runs after the MySQL commit, so the
    write path has already succeeded. Typically completes in <10ms.

    Args:
        tag: The tag to sync
        service: SearchService instance, or None to use module-level default
    """
    svc = service or _search_service
    if svc is None:
        return
    try:
        await svc.index_tag(tag)
    except Exception:
        logger.warning("meilisearch_sync_failed", tag_id=tag.tag_id, exc_info=True)


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


def _tag_to_document(tag: Tags) -> dict[str, Any]:
    """Convert a Tags model to a Meilisearch document."""
    return {
        "tag_id": tag.tag_id,
        "title": tag.title,
        "desc": tag.desc,
        "type": tag.type,
        "usage_count": tag.usage_count,
        "alias_of": tag.alias_of,
    }


async def configure_tags_index(client: AsyncClient) -> None:
    """Create and configure the tags index in Meilisearch.

    Sets ranking rules, filterable attributes, and searchable attributes.
    Idempotent — safe to call on every startup.
    """
    try:
        await client.create_index(TAGS_INDEX_NAME, primary_key="tag_id")
    except Exception:
        pass  # Index may already exist

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
    await index.update_searchable_attributes(["title", "desc"])
    await index.update_sortable_attributes(["usage_count"])

    logger.info("meilisearch_tags_index_configured")


class SearchService:
    """Service for indexing and searching via Meilisearch."""

    def __init__(self, client: AsyncClient) -> None:
        self.client = client

    async def index_tag(self, tag: Tags) -> None:
        """Index or update a single tag in Meilisearch."""
        doc = _tag_to_document(tag)
        index = self.client.index(TAGS_INDEX_NAME)
        await index.add_documents([doc])
        logger.debug("meilisearch_tag_indexed", tag_id=tag.tag_id)

    async def index_tags(self, tags: list[Tags]) -> None:
        """Bulk index multiple tags in Meilisearch."""
        if not tags:
            return
        docs = [_tag_to_document(tag) for tag in tags]
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
