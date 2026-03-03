"""Search service for Meilisearch integration."""

from meilisearch_python_sdk import AsyncClient

from app.core.logging import get_logger
from app.models.tag import Tags

logger = get_logger(__name__)

TAGS_INDEX_NAME = "tags"


def _tag_to_document(tag: Tags) -> dict:
    """Convert a Tags model to a Meilisearch document."""
    return {
        "tag_id": tag.tag_id,
        "title": tag.title,
        "desc": tag.desc,
        "type": tag.type,
        "usage_count": tag.usage_count,
        "alias_of": tag.alias_of,
    }


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
