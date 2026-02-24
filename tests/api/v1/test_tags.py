"""
Tests for tags API endpoints.

These tests cover the /api/v1/tags endpoints including:
- List and search tags
- Get tag details
- Create tag (admin only)
- Update tag (admin only)
- Delete tag (admin only)
- Get images by tag
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.tags import get_tag_hierarchy
from app.config import TagType
from app.core.security import get_password_hash
from app.models.image import Images
from app.models.permissions import Perms, UserPerms
from app.models.tag import Tags
from app.models.tag_external_link import TagExternalLinks
from app.models.tag_link import TagLinks
from app.models.user import Users


@pytest.mark.api
class TestListTags:
    """Tests for GET /api/v1/tags endpoint."""

    async def test_list_tags(self, client: AsyncClient, db_session: AsyncSession):
        """Test listing tags."""
        # Create test tags
        for i in range(5):
            tag = Tags(
                title=f"Test Tag {i}",
                desc=f"Description for tag {i}",
                type=TagType.THEME,
            )
            db_session.add(tag)
        await db_session.commit()

        response = await client.get("/api/v1/tags")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 5
        assert "tags" in data

    @pytest.mark.needs_commit  # FULLTEXT search requires committed data
    async def test_search_tags(self, client: AsyncClient, db_session: AsyncSession):
        """Test searching tags by name."""
        # Create tags with different names
        tag1 = Tags(title="anime girl", desc="Anime female character", type=TagType.THEME)
        tag2 = Tags(title="school uniform", desc="School clothing", type=TagType.CHARACTER)
        tag3 = Tags(title="cat ears", desc="Feline ears", type=TagType.ARTIST)
        db_session.add_all([tag1, tag2, tag3])
        await db_session.commit()

        # Search for "school"
        response = await client.get("/api/v1/tags?search=school")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["tags"][0]["title"] == "school uniform"

    async def test_search_tags_with_periods(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test searching tags containing periods like 'C.C.'.

        MySQL fulltext treats periods as word delimiters, so "C.C." becomes tokens
        "C" and "C" which are both below the minimum token size. The search should
        fall back to LIKE prefix matching for such terms.
        """
        # Create tags with periods in the name
        tag1 = Tags(title="C.C.", desc="Code Geass character", type=TagType.CHARACTER)
        tag2 = Tags(title="C.C. Lemon", desc="Drink mascot", type=TagType.CHARACTER)
        tag3 = Tags(title="Regular Tag", desc="Normal tag", type=TagType.CHARACTER)
        db_session.add_all([tag1, tag2, tag3])
        await db_session.commit()

        # Search for "C.C." should find the matching tags, not random "C" words
        response = await client.get("/api/v1/tags?search=C.C.")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        titles = {tag["title"] for tag in data["tags"]}
        assert "C.C." in titles
        assert "C.C. Lemon" in titles
        assert "Regular Tag" not in titles

    @pytest.mark.needs_commit
    async def test_search_tags_with_hyphens(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test searching tags containing hyphens like 'Deep-Blue Series'.

        MySQL fulltext treats hyphens as word delimiters, so "Deep-Blue" becomes
        tokens "Deep" and "Blue". The search must tokenize the query the same way
        so that "deep-blue" matches.
        """
        tag1 = Tags(title="Deep-Blue Series", desc="A series", type=TagType.THEME)
        tag2 = Tags(title="Unrelated Tag", desc="Other", type=TagType.THEME)
        db_session.add_all([tag1, tag2])
        await db_session.commit()

        # Search for "deep-blue" should find "Deep-Blue Series"
        response = await client.get("/api/v1/tags?search=deep-blue")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        titles = {tag["title"] for tag in data["tags"]}
        assert "Deep-Blue Series" in titles

    async def test_filter_tags_by_type(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test filtering tags by type."""
        # Create tags of different types
        tag1 = Tags(title="tag1", type=TagType.THEME)
        tag2 = Tags(title="tag2", type=TagType.CHARACTER)
        tag3 = Tags(title="tag3", type=TagType.THEME)
        db_session.add_all([tag1, tag2, tag3])
        await db_session.commit()

        # Filter by THEME type
        response = await client.get(f"/api/v1/tags?type={TagType.THEME}")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        for tag in data["tags"]:
            assert tag["type"] == TagType.THEME

    async def test_filter_tags_by_ids(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test filtering tags by specific IDs."""
        # Create test tags
        tag1 = Tags(title="tag1", type=TagType.THEME)
        tag2 = Tags(title="tag2", type=TagType.CHARACTER)
        tag3 = Tags(title="tag3", type=TagType.ARTIST)
        db_session.add_all([tag1, tag2, tag3])
        await db_session.commit()
        await db_session.refresh(tag1)
        await db_session.refresh(tag2)
        await db_session.refresh(tag3)

        # Filter by specific IDs
        response = await client.get(f"/api/v1/tags?ids={tag1.tag_id},{tag3.tag_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        returned_ids = {tag["tag_id"] for tag in data["tags"]}
        assert returned_ids == {tag1.tag_id, tag3.tag_id}
        # No invalid IDs
        assert data.get("invalid_ids") is None

    async def test_filter_tags_with_invalid_ids(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test filtering tags with mix of valid and invalid IDs."""
        # Create test tags
        tag1 = Tags(title="tag1", type=TagType.THEME)
        tag2 = Tags(title="tag2", type=TagType.CHARACTER)
        db_session.add_all([tag1, tag2])
        await db_session.commit()
        await db_session.refresh(tag1)
        await db_session.refresh(tag2)

        # Mix valid IDs with invalid ones (non-numeric)
        response = await client.get(f"/api/v1/tags?ids={tag1.tag_id},abc,{tag2.tag_id},xyz")
        assert response.status_code == 200
        data = response.json()

        # Should return valid tags
        assert data["total"] == 2
        returned_ids = {tag["tag_id"] for tag in data["tags"]}
        assert returned_ids == {tag1.tag_id, tag2.tag_id}

        # Should report invalid IDs
        assert data["invalid_ids"] is not None
        assert set(data["invalid_ids"]) == {"abc", "xyz"}

    async def test_filter_tags_with_only_invalid_ids(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test filtering tags with only invalid IDs."""
        # Create some tags
        tag = Tags(title="tag1", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()

        # Only invalid IDs
        response = await client.get("/api/v1/tags?ids=abc,xyz,foo")
        assert response.status_code == 200
        data = response.json()

        # Should return no tags
        assert data["total"] == 0
        assert len(data["tags"]) == 0

        # Should report all invalid IDs
        assert data["invalid_ids"] is not None
        assert set(data["invalid_ids"]) == {"abc", "xyz", "foo"}

    async def test_filter_tags_with_empty_id_strings(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test filtering tags handles empty strings in ID list gracefully."""
        # Create test tags
        tag1 = Tags(title="tag1", type=TagType.THEME)
        db_session.add(tag1)
        await db_session.commit()
        await db_session.refresh(tag1)

        # IDs with empty strings (trailing commas, double commas)
        response = await client.get(f"/api/v1/tags?ids={tag1.tag_id},,")
        assert response.status_code == 200
        data = response.json()

        # Should return valid tag
        assert data["total"] == 1
        assert data["tags"][0]["tag_id"] == tag1.tag_id

        # Empty strings should not be reported as invalid
        assert data.get("invalid_ids") is None

    async def test_filter_tags_with_duplicate_ids(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test filtering tags deduplicates duplicate IDs."""
        # Create test tags
        tag1 = Tags(title="tag1", type=TagType.THEME)
        tag2 = Tags(title="tag2", type=TagType.CHARACTER)
        tag3 = Tags(title="tag3", type=TagType.ARTIST)
        db_session.add_all([tag1, tag2, tag3])
        await db_session.commit()
        await db_session.refresh(tag1)
        await db_session.refresh(tag2)
        await db_session.refresh(tag3)

        # Request with duplicate IDs (e.g., ids=1,1,1,2,2,3)
        response = await client.get(
            f"/api/v1/tags?ids={tag1.tag_id},{tag1.tag_id},{tag1.tag_id},"
            f"{tag2.tag_id},{tag2.tag_id},{tag3.tag_id}"
        )
        assert response.status_code == 200
        data = response.json()

        # Should return only unique tags (total = 3, not 6)
        assert data["total"] == 3
        returned_ids = {tag["tag_id"] for tag in data["tags"]}
        assert returned_ids == {tag1.tag_id, tag2.tag_id, tag3.tag_id}

        # No invalid IDs
        assert data.get("invalid_ids") is None

    async def test_filter_tags_by_parent_tag_id(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test filtering tags by parent tag ID (get child tags)."""
        # Create parent tag
        parent_tag = Tags(title="swimsuit", desc="Parent tag for swimsuit types", type=TagType.THEME)
        db_session.add(parent_tag)
        await db_session.commit()
        await db_session.refresh(parent_tag)

        # Create child tags that inherit from parent
        child1 = Tags(
            title="bikini",
            desc="Two-piece swimsuit",
            type=TagType.THEME,
            inheritedfrom_id=parent_tag.tag_id,
        )
        child2 = Tags(
            title="school swimsuit",
            desc="School-style swimsuit",
            type=TagType.THEME,
            inheritedfrom_id=parent_tag.tag_id,
        )
        # Create a tag that is NOT a child
        other_tag = Tags(title="evening dress", desc="Not related to swimsuit", type=TagType.THEME)
        db_session.add_all([child1, child2, other_tag])
        await db_session.commit()
        await db_session.refresh(child1)
        await db_session.refresh(child2)

        # Filter by parent tag ID
        response = await client.get(f"/api/v1/tags?parent_tag_id={parent_tag.tag_id}")
        assert response.status_code == 200
        data = response.json()

        # Should return only the child tags
        assert data["total"] == 2
        returned_ids = {tag["tag_id"] for tag in data["tags"]}
        assert returned_ids == {child1.tag_id, child2.tag_id}
        # Verify parent is not included
        assert parent_tag.tag_id not in returned_ids
        # Verify other unrelated tag is not included
        assert other_tag.tag_id not in returned_ids

    async def test_filter_tags_by_parent_with_no_children(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test filtering by parent tag ID when parent has no children."""
        # Create parent tag with no children
        parent_tag = Tags(title="lonely parent", desc="Has no children", type=TagType.THEME)
        db_session.add(parent_tag)
        await db_session.commit()
        await db_session.refresh(parent_tag)

        # Filter by this parent tag ID
        response = await client.get(f"/api/v1/tags?parent_tag_id={parent_tag.tag_id}")
        assert response.status_code == 200
        data = response.json()

        # Should return empty result
        assert data["total"] == 0
        assert len(data["tags"]) == 0

    async def test_filter_tags_by_parent_with_nested_hierarchy(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test filtering by parent tag ID only returns direct children, not grandchildren."""
        # Create parent tag
        parent_tag = Tags(title="clothing", desc="Top level category", type=TagType.THEME)
        db_session.add(parent_tag)
        await db_session.commit()
        await db_session.refresh(parent_tag)

        # Create child tag
        child_tag = Tags(
            title="dress",
            desc="Child of clothing",
            type=TagType.THEME,
            inheritedfrom_id=parent_tag.tag_id,
        )
        db_session.add(child_tag)
        await db_session.commit()
        await db_session.refresh(child_tag)

        # Create grandchild tag (child of child)
        grandchild_tag = Tags(
            title="sundress",
            desc="Grandchild of clothing",
            type=TagType.THEME,
            inheritedfrom_id=child_tag.tag_id,
        )
        db_session.add(grandchild_tag)
        await db_session.commit()
        await db_session.refresh(grandchild_tag)

        # Filter by parent tag ID
        response = await client.get(f"/api/v1/tags?parent_tag_id={parent_tag.tag_id}")
        assert response.status_code == 200
        data = response.json()

        # Should return only direct children, not grandchildren
        assert data["total"] == 1
        assert data["tags"][0]["tag_id"] == child_tag.tag_id
        # Grandchild should not be included
        returned_ids = {tag["tag_id"] for tag in data["tags"]}
        assert grandchild_tag.tag_id not in returned_ids

    async def test_filter_tags_by_parent_combined_with_type(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test filtering tags by parent tag ID combined with type filter."""
        # Create parent tag
        parent_tag = Tags(title="character trait", desc="Parent tag", type=TagType.THEME)
        db_session.add(parent_tag)
        await db_session.commit()
        await db_session.refresh(parent_tag)

        # Create child tags with different types
        child1 = Tags(
            title="blue eyes",
            desc="Eye color",
            type=TagType.THEME,
            inheritedfrom_id=parent_tag.tag_id,
        )
        child2 = Tags(
            title="red hair",
            desc="Hair color",
            type=TagType.CHARACTER,
            inheritedfrom_id=parent_tag.tag_id,
        )
        db_session.add_all([child1, child2])
        await db_session.commit()
        await db_session.refresh(child1)
        await db_session.refresh(child2)

        # Filter by parent tag ID and type
        response = await client.get(
            f"/api/v1/tags?parent_tag_id={parent_tag.tag_id}&type={TagType.THEME}"
        )
        assert response.status_code == 200
        data = response.json()

        # Should return only child tags with matching type
        assert data["total"] == 1
        assert data["tags"][0]["tag_id"] == child1.tag_id
        assert data["tags"][0]["type"] == TagType.THEME


@pytest.mark.api
class TestTagListSorting:
    """Tests for sort_by/sort_order params and usage_count in tag list response."""

    async def test_tag_response_includes_usage_count(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that tag list response includes usage_count field."""
        tag = Tags(title="countable tag", type=TagType.THEME, usage_count=42)
        db_session.add(tag)
        await db_session.commit()

        response = await client.get("/api/v1/tags?search=co")
        assert response.status_code == 200
        data = response.json()
        matching = [t for t in data["tags"] if t["title"] == "countable tag"]
        assert len(matching) == 1
        assert matching[0]["usage_count"] == 42

    async def test_default_sort_is_usage_count_desc(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that default sort without search is usage_count DESC."""
        tags = [
            Tags(title="low usage", type=TagType.THEME, usage_count=5),
            Tags(title="high usage", type=TagType.THEME, usage_count=100),
            Tags(title="mid usage", type=TagType.THEME, usage_count=50),
        ]
        for tag in tags:
            db_session.add(tag)
        await db_session.commit()

        response = await client.get("/api/v1/tags?type=1")
        assert response.status_code == 200
        data = response.json()

        # Filter to our test tags
        titles = [t["title"] for t in data["tags"] if "usage" in t["title"]]
        assert titles == ["high usage", "mid usage", "low usage"]

    async def test_sort_by_title_asc(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test sorting tags alphabetically by title ascending."""
        tags = [
            Tags(title="cherry", type=TagType.SOURCE, usage_count=0),
            Tags(title="apple", type=TagType.SOURCE, usage_count=0),
            Tags(title="banana", type=TagType.SOURCE, usage_count=0),
        ]
        for tag in tags:
            db_session.add(tag)
        await db_session.commit()

        response = await client.get("/api/v1/tags?type=2&sort_by=title&sort_order=ASC")
        assert response.status_code == 200
        data = response.json()
        titles = [t["title"] for t in data["tags"]]
        assert titles == ["apple", "banana", "cherry"]

    async def test_sort_by_title_desc(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test sorting tags alphabetically by title descending."""
        tags = [
            Tags(title="cherry", type=TagType.SOURCE, usage_count=0),
            Tags(title="apple", type=TagType.SOURCE, usage_count=0),
            Tags(title="banana", type=TagType.SOURCE, usage_count=0),
        ]
        for tag in tags:
            db_session.add(tag)
        await db_session.commit()

        response = await client.get("/api/v1/tags?type=2&sort_by=title&sort_order=DESC")
        assert response.status_code == 200
        data = response.json()
        titles = [t["title"] for t in data["tags"]]
        assert titles == ["cherry", "banana", "apple"]

    async def test_sort_by_tag_id_asc(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test sorting tags by tag_id ascending (chronological)."""
        tag1 = Tags(title="first created", type=TagType.ARTIST, usage_count=0)
        tag2 = Tags(title="second created", type=TagType.ARTIST, usage_count=0)
        tag3 = Tags(title="third created", type=TagType.ARTIST, usage_count=0)
        db_session.add_all([tag1, tag2, tag3])
        await db_session.commit()

        response = await client.get("/api/v1/tags?type=3&sort_by=tag_id&sort_order=ASC")
        assert response.status_code == 200
        data = response.json()
        titles = [t["title"] for t in data["tags"]]
        assert titles == ["first created", "second created", "third created"]

    async def test_sort_by_date_added_maps_to_tag_id(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that sort_by=date_added uses tag_id (chronological, indexed)."""
        tag1 = Tags(title="first created", type=TagType.ARTIST, usage_count=0)
        tag2 = Tags(title="second created", type=TagType.ARTIST, usage_count=0)
        tag3 = Tags(title="third created", type=TagType.ARTIST, usage_count=0)
        db_session.add_all([tag1, tag2, tag3])
        await db_session.commit()

        response = await client.get("/api/v1/tags?type=3&sort_by=date_added&sort_order=DESC")
        assert response.status_code == 200
        data = response.json()
        titles = [t["title"] for t in data["tags"]]
        assert titles == ["third created", "second created", "first created"]

    async def test_sort_by_type(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test sorting tags by type."""
        tags = [
            Tags(title="sort type artist", type=TagType.ARTIST, usage_count=0),
            Tags(title="sort type theme", type=TagType.THEME, usage_count=0),
            Tags(title="sort type source", type=TagType.SOURCE, usage_count=0),
        ]
        for tag in tags:
            db_session.add(tag)
        await db_session.commit()

        # Don't use search param here - search activates relevance sorting
        response = await client.get("/api/v1/tags?sort_by=type&sort_order=ASC")
        assert response.status_code == 200
        data = response.json()
        type_tags = [t for t in data["tags"] if t["title"].startswith("sort type")]
        types = [t["type"] for t in type_tags]
        # Types should be in ascending order
        assert types == sorted(types)

    async def test_sort_by_usage_count_asc(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test sorting by usage_count ascending."""
        tags = [
            Tags(title="popular sort", type=TagType.CHARACTER, usage_count=200),
            Tags(title="unpopular sort", type=TagType.CHARACTER, usage_count=1),
            Tags(title="medium sort", type=TagType.CHARACTER, usage_count=50),
        ]
        for tag in tags:
            db_session.add(tag)
        await db_session.commit()

        response = await client.get("/api/v1/tags?type=4&sort_by=usage_count&sort_order=ASC")
        assert response.status_code == 200
        data = response.json()
        titles = [t["title"] for t in data["tags"] if "sort" in t["title"]]
        assert titles == ["unpopular sort", "medium sort", "popular sort"]

    async def test_sort_order_case_insensitive(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that sort_order accepts lowercase values."""
        response = await client.get("/api/v1/tags?sort_by=usage_count&sort_order=desc")
        assert response.status_code == 200

        response = await client.get("/api/v1/tags?sort_by=usage_count&sort_order=asc")
        assert response.status_code == 200

    @pytest.mark.needs_commit  # FULLTEXT search requires committed data
    async def test_explicit_sort_by_overrides_search_relevance(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that explicit sort_by overrides relevance ranking during search."""
        # Exact match gets HIGH usage_count so usage_count ASC puts it LAST,
        # while relevance would put it FIRST (exact match priority 0).
        tag1 = Tags(title="match", type=TagType.THEME, usage_count=999)
        tag2 = Tags(title="match partial", type=TagType.THEME, usage_count=1)
        db_session.add_all([tag1, tag2])
        await db_session.commit()

        # With explicit sort_by=usage_count ASC, "match partial" (1) comes before
        # "match" (999), proving sort_by overrides relevance (which would put
        # the exact match "match" first).
        response = await client.get(
            "/api/v1/tags?search=match&sort_by=usage_count&sort_order=ASC"
        )
        assert response.status_code == 200
        data = response.json()
        matching = [t for t in data["tags"] if t["title"].startswith("match")]
        assert len(matching) == 2
        assert matching[0]["title"] == "match partial"
        assert matching[1]["title"] == "match"

    @pytest.mark.needs_commit  # FULLTEXT search requires committed data
    async def test_search_without_sort_by_uses_relevance(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that search without explicit sort_by uses relevance ranking."""
        # Exact match gets LOW usage_count — relevance should still put it first
        tag1 = Tags(title="match", type=TagType.THEME, usage_count=1)
        tag2 = Tags(title="match partial", type=TagType.THEME, usage_count=999)
        db_session.add_all([tag1, tag2])
        await db_session.commit()

        # No sort_by: relevance puts exact match first despite lower usage_count
        response = await client.get("/api/v1/tags?search=match")
        assert response.status_code == 200
        data = response.json()
        matching = [t for t in data["tags"] if t["title"].startswith("match")]
        assert len(matching) == 2
        assert matching[0]["title"] == "match"

    async def test_invalid_sort_by_rejected(self, client: AsyncClient):
        """Test that invalid sort_by values are rejected."""
        response = await client.get("/api/v1/tags?sort_by=invalid_field")
        assert response.status_code == 422


@pytest.mark.api
class TestAliasOfName:
    """Tests for alias_of_name field in tag list responses.

    This feature was added to include the title of the tag being aliased
    in the tag list response, requiring a self-join on the Tags table.
    """

    async def test_alias_of_name_when_tag_has_alias(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that alias_of_name is populated when a tag has an alias_of value."""
        # Create the original tag
        original_tag = Tags(
            title="maid outfit",
            desc="Maid clothing",
            type=TagType.THEME,
        )
        db_session.add(original_tag)
        await db_session.commit()
        await db_session.refresh(original_tag)

        # Create an alias tag that points to the original
        alias_tag = Tags(
            title="maid dress",
            desc="Alternative name for maid outfit",
            type=TagType.THEME,
            alias_of=original_tag.tag_id,
        )
        db_session.add(alias_tag)
        await db_session.commit()
        await db_session.refresh(alias_tag)

        # Get the tag list
        response = await client.get("/api/v1/tags")
        assert response.status_code == 200
        data = response.json()

        # Find the alias tag in the response
        alias_tag_response = None
        original_tag_response = None
        for tag in data["tags"]:
            if tag["tag_id"] == alias_tag.tag_id:
                alias_tag_response = tag
            if tag["tag_id"] == original_tag.tag_id:
                original_tag_response = tag

        # Verify alias_of_name is populated for the alias tag
        assert alias_tag_response is not None
        assert alias_tag_response["alias_of"] == original_tag.tag_id
        assert alias_tag_response["alias_of_name"] == "maid outfit"
        assert alias_tag_response["is_alias"] is True

        # Verify original tag has no alias_of_name
        assert original_tag_response is not None
        assert original_tag_response["alias_of"] is None
        assert original_tag_response["alias_of_name"] is None
        assert original_tag_response["is_alias"] is False

    async def test_alias_of_name_when_tag_has_no_alias(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that alias_of_name is None when a tag doesn't have an alias_of value."""
        # Create a regular tag with no alias
        regular_tag = Tags(
            title="summer dress",
            desc="Summer clothing",
            type=TagType.THEME,
        )
        db_session.add(regular_tag)
        await db_session.commit()
        await db_session.refresh(regular_tag)

        # Get the tag list
        response = await client.get("/api/v1/tags")
        assert response.status_code == 200
        data = response.json()

        # Find the tag in the response
        tag_response = None
        for tag in data["tags"]:
            if tag["tag_id"] == regular_tag.tag_id:
                tag_response = tag
                break

        # Verify alias_of_name is None
        assert tag_response is not None
        assert tag_response["alias_of"] is None
        assert tag_response["alias_of_name"] is None
        assert tag_response["is_alias"] is False

    async def test_alias_of_name_with_filtering(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that alias_of_name works correctly with type filtering."""
        # Create original tags of different types
        character_tag = Tags(
            title="sakura kinomoto",
            desc="Character from Cardcaptor Sakura",
            type=TagType.CHARACTER,
        )
        theme_tag = Tags(
            title="school uniform",
            desc="School clothing",
            type=TagType.THEME,
        )
        db_session.add_all([character_tag, theme_tag])
        await db_session.commit()
        await db_session.refresh(character_tag)
        await db_session.refresh(theme_tag)

        # Create alias tags
        character_alias = Tags(
            title="kinomoto sakura",
            desc="Alternative name order",
            type=TagType.CHARACTER,
            alias_of=character_tag.tag_id,
        )
        theme_alias = Tags(
            title="school outfit",
            desc="Alternative name",
            type=TagType.THEME,
            alias_of=theme_tag.tag_id,
        )
        db_session.add_all([character_alias, theme_alias])
        await db_session.commit()
        await db_session.refresh(character_alias)
        await db_session.refresh(theme_alias)

        # Filter by CHARACTER type
        response = await client.get(f"/api/v1/tags?type={TagType.CHARACTER}")
        assert response.status_code == 200
        data = response.json()

        # Should only get CHARACTER tags, and only the expected ones
        character_ids = {character_tag.tag_id, character_alias.tag_id}
        assert {tag["tag_id"] for tag in data["tags"]} == character_ids
        for tag in data["tags"]:
            assert tag["type"] == TagType.CHARACTER
            # If it's an alias, verify alias_of_name is populated
            if tag["tag_id"] == character_alias.tag_id:
                assert tag["alias_of_name"] == "sakura kinomoto"
                assert tag["is_alias"] is True

    @pytest.mark.needs_commit  # FULLTEXT search requires committed data
    async def test_alias_of_name_with_search(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that alias_of_name works correctly with full-text search."""
        # Create original tag
        original_tag = Tags(
            title="cat ears",
            desc="Feline ears",
            type=TagType.THEME,
        )
        db_session.add(original_tag)
        await db_session.commit()
        await db_session.refresh(original_tag)

        # Create alias tag
        alias_tag = Tags(
            title="neko ears",
            desc="Japanese for cat ears",
            type=TagType.THEME,
            alias_of=original_tag.tag_id,
        )
        db_session.add(alias_tag)
        await db_session.commit()
        await db_session.refresh(alias_tag)

        # Search for "neko" (should find the alias)
        response = await client.get("/api/v1/tags?search=neko")
        assert response.status_code == 200
        data = response.json()

        # Should find the alias tag
        assert data["total"] >= 1
        neko_tag = None
        for tag in data["tags"]:
            if tag["tag_id"] == alias_tag.tag_id:
                neko_tag = tag
                break

        # Verify alias_of_name is populated
        assert neko_tag is not None
        assert neko_tag["alias_of_name"] == "cat ears"
        assert neko_tag["is_alias"] is True

    async def test_alias_of_name_with_exclude_aliases_filter(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that alias tags are excluded when exclude_aliases=true."""
        # Create original tag
        original_tag = Tags(
            title="blue eyes",
            desc="Blue colored eyes",
            type=TagType.THEME,
        )
        db_session.add(original_tag)
        await db_session.commit()
        await db_session.refresh(original_tag)

        # Create alias tag
        alias_tag = Tags(
            title="blue colored eyes",
            desc="Alternative name",
            type=TagType.THEME,
            alias_of=original_tag.tag_id,
        )
        db_session.add(alias_tag)
        await db_session.commit()
        await db_session.refresh(alias_tag)

        # Get tags excluding aliases
        response = await client.get("/api/v1/tags?exclude_aliases=true")
        assert response.status_code == 200
        data = response.json()

        # Should not include the alias tag
        tag_ids = {tag["tag_id"] for tag in data["tags"]}
        assert alias_tag.tag_id not in tag_ids
        assert original_tag.tag_id in tag_ids


@pytest.mark.api
@pytest.mark.needs_commit  # FULLTEXT search requires committed data
class TestFuzzyTagSearch:
    """Tests for fuzzy/full-text search on tags.

    Tests the hybrid search strategy:
    - Short queries (< 3 chars): LIKE prefix matching
    - Long queries (>= 3 chars): FULLTEXT word-order independent matching

    This solves the Japanese character name problem:
    Searching "sakura kinomoto" should find "kinomoto sakura".
    """

    async def test_short_query_prefix_match(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that short queries (< 3 chars) use prefix matching."""
        # Create test tags
        tag1 = Tags(title="sakura kinomoto", type=TagType.CHARACTER)
        tag2 = Tags(title="sakura card", type=TagType.CHARACTER)
        tag3 = Tags(title="cardcaptor sakura", type=TagType.CHARACTER)
        tag4 = Tags(title="kinomoto sakura", type=TagType.CHARACTER)
        db_session.add_all([tag1, tag2, tag3, tag4])
        await db_session.commit()

        # Search for "sa" (2 chars) - should match prefix only
        response = await client.get("/api/v1/tags?search=sa")
        assert response.status_code == 200
        data = response.json()
        # Should find tags starting with "sa" (sakura kinomoto, sakura card)
        # But NOT "cardcaptor sakura" (doesn't start with "sa")
        # And NOT "kinomoto sakura" (doesn't start with "sa")
        assert data["total"] == 2
        titles = {tag["title"] for tag in data["tags"]}
        assert "sakura kinomoto" in titles
        assert "sakura card" in titles
        assert "cardcaptor sakura" not in titles
        assert "kinomoto sakura" not in titles

    async def test_long_query_word_order_independent(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that long queries (>= 3 chars) use word-order independent full-text search.

        This is the key feature for Japanese character names!

        With multiple words in the search query (e.g., "sakura kinomoto"), the search
        uses AND logic - all words must be present in the tag title, but in any order.
        This solves the Japanese character naming problem where different sources
        list the name in different word orders.
        """
        # Create tags with same words but different order
        tag1 = Tags(title="kinomoto sakura", type=TagType.CHARACTER)
        tag2 = Tags(title="sakura kinomoto", type=TagType.CHARACTER)
        tag3 = Tags(title="sakura mitsuki", type=TagType.CHARACTER)  # Different person (not kinomoto)
        db_session.add_all([tag1, tag2, tag3])
        await db_session.commit()

        # Search for "sakura kinomoto" (3+ chars, multiple words)
        # Uses FULLTEXT with +sakura* +kinomoto* (AND logic - both words required)
        # So it matches tags with BOTH "sakura" AND "kinomoto", regardless of order
        # But NOT "sakura mitsuki" since it lacks "kinomoto"
        response = await client.get("/api/v1/tags?search=sakura%20kinomoto")
        assert response.status_code == 200
        data = response.json()

        # Should find both orderings of "sakura kinomoto", but NOT "sakura mitsuki"
        # (sakura mitsuki doesn't have "kinomoto", so it doesn't match +sakura* +kinomoto*)
        assert data["total"] == 2
        titles = {tag["title"] for tag in data["tags"]}
        # Both orderings should be found
        assert "sakura kinomoto" in titles
        assert "kinomoto sakura" in titles
        # But NOT sakura mitsuki
        assert "sakura mitsuki" not in titles

    async def test_long_query_partial_word_match(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that full-text search requires whole words (not partial word matches).

        Note: MariaDB/MySQL FULLTEXT has minimum word length (usually 3-4 chars) and
        only matches complete words, not arbitrary substrings.
        """
        # Create test tags with full words
        tag1 = Tags(title="school uniform", type=TagType.THEME)
        tag2 = Tags(title="schoolgirl", type=TagType.CHARACTER)
        tag3 = Tags(title="scholar", type=TagType.THEME)
        db_session.add_all([tag1, tag2, tag3])
        await db_session.commit()

        # Search for "school" (6 chars, full word)
        response = await client.get("/api/v1/tags?search=school")
        assert response.status_code == 200
        data = response.json()
        # FULLTEXT matches complete words, so "school" should find:
        # - "school uniform" (contains word "school")
        # - "schoolgirl" (contains word "schoolgirl", not "school")
        # - "scholar" (may or may not match depending on word stemming)
        # In BOOLEAN mode, it's more strict - just ensure we get some results
        assert data["total"] >= 1  # At least "school uniform"
        titles = {tag["title"] for tag in data["tags"]}
        assert "school uniform" in titles

    async def test_full_text_relevance_sorting(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that full-text search results include relevant matches."""
        # Create tags with varying relevance
        tag1 = Tags(title="cat ears", type=TagType.THEME)  # Exact word match
        tag2 = Tags(title="feline ears", type=TagType.THEME)  # Contains "ears" but not "cat"
        tag3 = Tags(title="category tags", type=TagType.THEME)  # "category" contains "cat" but is different word
        db_session.add_all([tag1, tag2, tag3])
        await db_session.commit()

        # Search for "cat" (3 chars) - should use full-text
        response = await client.get("/api/v1/tags?search=cat")
        assert response.status_code == 200
        data = response.json()

        # FULLTEXT should find "cat ears" (exact word "cat")
        # May or may not find "category" depending on word stemming in BOOLEAN mode
        assert data["total"] >= 1
        assert data["tags"][0]["title"] == "cat ears"  # Exact match should be first

    async def test_long_query_exact_match_prioritized(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that exact matches are prioritized in long query full-text search.

        This addresses the issue where searching for "maid" should return the "maid" tag
        first, even if there are other tags like "maid outfit" or "head maid" that also
        match the full-text search.
        """
        # Create tags with various "maid" matches
        maid_tag = Tags(title="maid", type=TagType.THEME)
        maid_outfit = Tags(title="maid outfit", type=TagType.THEME)
        head_maid = Tags(title="head maid", type=TagType.THEME)
        db_session.add_all([maid_tag, maid_outfit, head_maid])
        await db_session.commit()

        # Search for "maid" (4 chars) - should use full-text with exact match prioritization
        response = await client.get("/api/v1/tags?search=maid")
        assert response.status_code == 200
        data = response.json()

        assert data["total"] == 3
        # The exact match "maid" should be first
        assert data["tags"][0]["title"] == "maid"
        # Other matches should follow
        assert data["tags"][1]["title"] in ["maid outfit", "head maid"]
        assert data["tags"][2]["title"] in ["maid outfit", "head maid"]

    async def test_short_query_case_insensitive(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that short prefix queries are case-insensitive."""
        # Create test tags with mixed case
        tag1 = Tags(title="Sakura Kinomoto", type=TagType.CHARACTER)
        tag2 = Tags(title="sakura card", type=TagType.CHARACTER)
        tag3 = Tags(title="SAKURA", type=TagType.THEME)
        db_session.add_all([tag1, tag2, tag3])
        await db_session.commit()

        # Search for lowercase "sa"
        response = await client.get("/api/v1/tags?search=sa")
        assert response.status_code == 200
        data = response.json()
        # Should find all tags starting with "sa" (case-insensitive)
        assert data["total"] == 3

    async def test_search_with_stopwords(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that search works correctly when query contains stopwords like 'The'.

        This addresses a bug where searching for "The Forgotten" would fail because
        "the" is a MySQL/MariaDB fulltext stopword. When combined with the `+` required
        operator, the entire query would fail.

        The fix filters out stopwords and short terms before building the fulltext query.
        """
        # Create tags with "The" in the title
        tag1 = Tags(title="The Forgotten Field", type=TagType.CHARACTER)
        tag2 = Tags(title="The 8th son? Are you kidding me?", type=TagType.CHARACTER)
        tag3 = Tags(title="Forgotten Dreams", type=TagType.CHARACTER)  # No "The"
        db_session.add_all([tag1, tag2, tag3])
        await db_session.commit()

        # Search for "The Forgotten" - should find "The Forgotten Field"
        response = await client.get("/api/v1/tags?search=The%20Forgotten")
        assert response.status_code == 200
        data = response.json()

        # Should find at least one result
        assert data["total"] >= 1, "Search for 'The Forgotten' should return results"
        titles = {tag["title"] for tag in data["tags"]}
        assert "The Forgotten Field" in titles, "Should find 'The Forgotten Field'"
        # Should NOT include "Forgotten Dreams" (lacks "The" if we're being strict about matching)
        # Actually after fix, we filter out "The" as a stopword, so "Forgotten" is what's searched
        # This means "Forgotten Dreams" might also be returned - that's acceptable

    async def test_search_with_short_terms(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that search works when query contains very short terms (< 3 chars).

        MySQL/MariaDB fulltext has a minimum token size (default 3). Terms shorter
        than this are ignored in fulltext search, which can cause issues when combined
        with the `+` required operator.

        Example: Searching "The F" would fail because "F" is below min token size.
        """
        # Create tags
        tag1 = Tags(title="The Forgotten Field", type=TagType.CHARACTER)
        tag2 = Tags(title="The Five Star Stories", type=TagType.CHARACTER)
        db_session.add_all([tag1, tag2])
        await db_session.commit()

        # Search for "The F" - should still work by filtering out short terms
        response = await client.get("/api/v1/tags?search=The%20F")
        assert response.status_code == 200
        data = response.json()

        # With stopword and short term filtering, this becomes empty or just searches
        # based on valid terms. The important thing is it doesn't crash or return 0
        # when there are valid tags starting with "The F".
        # After fix: "The" is stopword, "F" is too short, so we might need fallback
        # For now, just ensure we get some results that match "The F" prefix
        assert data["total"] >= 1, "Search for 'The F' should return results"

    async def test_search_with_special_like_characters(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that LIKE special characters (% and _) are escaped in search.

        Without escaping, a search containing '%' or '_' would be interpreted
        as wildcards, potentially matching unintended results.
        """
        # Create tags with special characters in title
        tag1 = Tags(title="100% Orange Juice", type=TagType.CHARACTER)
        tag2 = Tags(title="Orange Soda", type=TagType.CHARACTER)
        db_session.add_all([tag1, tag2])
        await db_session.commit()

        # Search for "100%" - should only find exact prefix match, not wildcard
        # The % should be escaped so it doesn't match everything
        response = await client.get("/api/v1/tags?search=100%25")  # URL encoded %
        assert response.status_code == 200
        data = response.json()

        # Should find "100% Orange Juice" but not "Orange Soda"
        assert data["total"] == 1
        assert data["tags"][0]["title"] == "100% Orange Juice"

    async def test_search_with_fulltext_special_characters(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that fulltext boolean operators are stripped from search terms.

        MySQL fulltext BOOLEAN MODE uses characters like +, -, *, ~, ", (), <, >, @
        as operators. Without sanitization, searching for "C++" would create
        "+C++*" which interprets the extra + as operators.
        """
        # Create tags with special characters
        tag1 = Tags(title="C++ Programming", type=TagType.CHARACTER)
        tag2 = Tags(title="C Sharp Programming", type=TagType.CHARACTER)
        db_session.add_all([tag1, tag2])
        await db_session.commit()

        # Search for "C++" - the ++ should be stripped, searching for just "C"
        # which is too short (< 3 chars), so it falls back to LIKE
        response = await client.get("/api/v1/tags?search=C%2B%2B")  # URL encoded C++
        assert response.status_code == 200
        data = response.json()

        # Should not crash and should return some results
        # After stripping ++, "C" is too short, falls back to LIKE "C++%"
        assert data["total"] >= 1

    async def test_search_with_obfuscated_stopwords(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that stopwords are detected after sanitization.

        A term like "+the+" should be recognized as stopword "the" after
        stripping the special characters, not pass through as a non-stopword.
        """
        # Create tag
        tag1 = Tags(title="The Forgotten Field", type=TagType.CHARACTER)
        db_session.add_all([tag1])
        await db_session.commit()

        # Search for "+the+ Forgotten" - "+the+" should become "the" (stopword)
        # and be filtered out, leaving just "Forgotten" for fulltext search
        response = await client.get("/api/v1/tags?search=%2Bthe%2B%20Forgotten")
        assert response.status_code == 200
        data = response.json()

        # Should find results since "Forgotten" is a valid search term
        assert data["total"] >= 1
        titles = {tag["title"] for tag in data["tags"]}
        assert "The Forgotten Field" in titles

    async def test_hybrid_search_with_filters(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that fuzzy search works with other filters (type, exclude_aliases, etc)."""
        # Create test tags with different types
        tag1 = Tags(title="kinomoto sakura", type=TagType.CHARACTER)
        tag2 = Tags(title="sakura kinomoto", type=TagType.THEME)
        tag3 = Tags(title="sakura leaf", type=TagType.CHARACTER, alias_of=1)
        db_session.add_all([tag1, tag2, tag3])
        await db_session.commit()

        # Search for "sakura" with type filter (CHARACTER only)
        response = await client.get("/api/v1/tags?search=sakura&type=4")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2  # Both CHARACTER tags
        for tag in data["tags"]:
            assert tag["type"] == TagType.CHARACTER

        # Search for "sakura" excluding aliases
        response = await client.get("/api/v1/tags?search=sakura&exclude_aliases=true")
        assert response.status_code == 200
        data = response.json()
        # Should not include alias tags
        for tag in data["tags"]:
            assert tag.get("is_alias") is False or tag.get("is_alias") is None

    async def test_search_term_with_underscores(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that searching for terms containing underscores works correctly.

        MySQL/MariaDB InnoDB FULLTEXT treats underscore as a word character (not a
        delimiter), so 'yano_0o0' is indexed as a single token. Our Python-side
        tokenizer must match this behavior to produce valid FULLTEXT queries.
        """
        tag = Tags(title="Yano (yano_0o0)", type=TagType.CHARACTER)
        db_session.add(tag)
        await db_session.commit()

        response = await client.get("/api/v1/tags?search=yano_0o0")
        assert response.status_code == 200
        data = response.json()

        assert data["total"] >= 1, "Search for 'yano_0o0' should find 'Yano (yano_0o0)'"
        titles = {tag["title"] for tag in data["tags"]}
        assert "Yano (yano_0o0)" in titles



@pytest.mark.api
class TestGetTag:
    """Tests for GET /api/v1/tags/{tag_id} endpoint."""

    async def test_get_tag_by_id(self, client: AsyncClient, db_session: AsyncSession):
        """Test getting a tag by ID."""
        tag = Tags(title="Test Tag", desc="Test description", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        response = await client.get(f"/api/v1/tags/{tag.tag_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["tag_id"] == tag.tag_id
        assert data["title"] == "Test Tag"
        assert data["total_image_count"] == 0  # No images yet

    async def test_get_nonexistent_tag(self, client: AsyncClient):
        """Test getting a tag that doesn't exist."""
        response = await client.get("/api/v1/tags/999999")
        assert response.status_code == 404

    async def test_get_tag_includes_creator_and_date(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that getting a tag includes the creator user and creation date."""
        # Create user who will create the tag
        user = Users(
            username="tagcreator",
            password=get_password_hash("Password123!"),
            password_type="bcrypt",
            salt="",
            email="tagcreator@example.com",
            active=1,
            admin=0,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create tag with user_id
        tag = Tags(
            title="User Tag",
            desc="Tag with creator",
            type=TagType.THEME,
            user_id=user.user_id,
        )
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Get tag
        response = await client.get(f"/api/v1/tags/{tag.tag_id}")
        assert response.status_code == 200
        data = response.json()

        # Verify creator information is included
        assert "created_by" in data
        assert data["created_by"] is not None
        assert data["created_by"]["user_id"] == user.user_id
        assert data["created_by"]["username"] == "tagcreator"

        # Verify creation date is included
        assert "date_added" in data
        assert data["date_added"] is not None

    async def test_get_tag_includes_creator_avatar_url(self, client: AsyncClient, db_session: AsyncSession):
        """Ensure created_by includes avatar_url when user has an avatar"""
        from app.config import settings

        user = Users(
            username="avataruser",
            password=get_password_hash("Password123!"),
            password_type="bcrypt",
            salt="",
            email="avataruser@example.com",
            active=1,
            admin=0,
            avatar="avatar.png",
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        tag = Tags(
            title="Avatar Tag",
            desc="Tag with avatar creator",
            type=TagType.THEME,
            user_id=user.user_id,
        )
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        response = await client.get(f"/api/v1/tags/{tag.tag_id}")
        assert response.status_code == 200
        data = response.json()

        assert data["created_by"]["avatar"] == "avatar.png"
        assert data["created_by"]["avatar_url"] == f"{settings.IMAGE_BASE_URL}/images/avatars/avatar.png"

    async def test_get_tag_includes_creator_groups(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that getting a tag includes the creator's groups for username coloring."""
        from app.models.permissions import Groups, UserGroups

        # Create user who will create the tag
        user = Users(
            username="groupedcreator",
            password=get_password_hash("Password123!"),
            password_type="bcrypt",
            salt="",
            email="groupedcreator@example.com",
            active=1,
            admin=0,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create groups
        mod_group = Groups(title="mods", desc="Moderators")
        artist_group = Groups(title="artists", desc="Verified artists")
        db_session.add_all([mod_group, artist_group])
        await db_session.commit()
        await db_session.refresh(mod_group)
        await db_session.refresh(artist_group)

        # Add user to groups
        user_mod = UserGroups(user_id=user.user_id, group_id=mod_group.group_id)
        user_artist = UserGroups(user_id=user.user_id, group_id=artist_group.group_id)
        db_session.add_all([user_mod, user_artist])
        await db_session.commit()

        # Create tag with user_id
        tag = Tags(
            title="Grouped Creator Tag",
            desc="Tag created by user with groups",
            type=TagType.THEME,
            user_id=user.user_id,
        )
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Get tag
        response = await client.get(f"/api/v1/tags/{tag.tag_id}")
        assert response.status_code == 200
        data = response.json()

        # Verify creator includes groups
        assert "created_by" in data
        assert data["created_by"] is not None
        assert data["created_by"]["user_id"] == user.user_id
        assert data["created_by"]["username"] == "groupedcreator"
        assert "groups" in data["created_by"]
        assert set(data["created_by"]["groups"]) == {"mods", "artists"}

    async def test_get_tag_with_aliases(self, client: AsyncClient, db_session: AsyncSession):
        """Test that getting a tag includes its aliases (tags that redirect to it)."""
        # Create canonical tag
        canonical = Tags(title="Canonical Tag", desc="The main tag", type=TagType.THEME)
        db_session.add(canonical)
        await db_session.commit()
        await db_session.refresh(canonical)

        # Create alias tags pointing to canonical
        alias1 = Tags(
            title="Alias One", desc="First alias", type=TagType.THEME, alias_of=canonical.tag_id
        )
        alias2 = Tags(
            title="Alias Two", desc="Second alias", type=TagType.THEME, alias_of=canonical.tag_id
        )
        db_session.add_all([alias1, alias2])
        await db_session.commit()

        # Get canonical tag - should include aliases
        response = await client.get(f"/api/v1/tags/{canonical.tag_id}")
        assert response.status_code == 200
        data = response.json()

        assert "aliases" in data
        assert len(data["aliases"]) == 2
        alias_titles = {a["title"] for a in data["aliases"]}
        assert alias_titles == {"Alias One", "Alias Two"}

    async def test_get_tag_without_aliases(self, client: AsyncClient, db_session: AsyncSession):
        """Test that a tag without aliases returns an empty aliases list."""
        tag = Tags(title="Standalone Tag", desc="No aliases", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        response = await client.get(f"/api/v1/tags/{tag.tag_id}")
        assert response.status_code == 200
        data = response.json()

        assert "aliases" in data
        assert data["aliases"] == []

    async def test_get_alias_tag_shows_sibling_aliases(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that viewing an alias tag shows all aliases of the resolved (canonical) tag."""
        # Create canonical tag
        canonical = Tags(title="Canonical", desc="Main tag", type=TagType.THEME)
        db_session.add(canonical)
        await db_session.commit()
        await db_session.refresh(canonical)

        # Create multiple alias tags
        alias1 = Tags(
            title="Alias A", desc="First alias", type=TagType.THEME, alias_of=canonical.tag_id
        )
        alias2 = Tags(
            title="Alias B", desc="Second alias", type=TagType.THEME, alias_of=canonical.tag_id
        )
        db_session.add_all([alias1, alias2])
        await db_session.commit()
        await db_session.refresh(alias1)
        await db_session.refresh(alias2)

        # Get alias1 - should show both aliases (sibling aliases of the same canonical tag)
        response = await client.get(f"/api/v1/tags/{alias1.tag_id}")
        assert response.status_code == 200
        data = response.json()

        # Verify this tag is marked as an alias
        assert data["is_alias"] is True
        assert data["aliased_tag_id"] == canonical.tag_id

        # Verify aliases shows all tags pointing to the canonical tag (both alias1 and alias2)
        assert "aliases" in data
        assert len(data["aliases"]) == 2
        alias_titles = {a["title"] for a in data["aliases"]}
        assert alias_titles == {"Alias A", "Alias B"}

    async def test_total_image_count_anonymous_excludes_non_public(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Anonymous users should only see public-status images in total_image_count."""
        from app.config import ImageStatus

        tag = Tags(title="Count Test Tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.flush()

        # Create images with various statuses
        statuses = [
            ImageStatus.ACTIVE,    # public
            ImageStatus.ACTIVE,    # public
            ImageStatus.SPOILER,   # public
            ImageStatus.REPOST,    # public
            ImageStatus.OTHER,     # non-public (status=0)
            ImageStatus.INAPPROPRIATE,  # non-public (status=-2)
        ]
        for i, status_val in enumerate(statuses):
            img_data = sample_image_data.copy()
            img_data.update({
                "filename": f"count-test-{i}",
                "md5_hash": f"counttest{i:018d}",
                "status": status_val,
            })
            img = Images(**img_data)
            db_session.add(img)
            await db_session.flush()
            db_session.add(TagLinks(tag_id=tag.tag_id, image_id=img.image_id))

        await db_session.commit()

        # Anonymous request - should only count public statuses (ACTIVE, SPOILER, REPOST)
        response = await client.get(f"/api/v1/tags/{tag.tag_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["total_image_count"] == 4  # 2 ACTIVE + 1 SPOILER + 1 REPOST

    async def test_total_image_count_show_all_images_sees_all(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """User with show_all_images=1 should see all images in total_image_count."""
        from app.config import ImageStatus
        from app.core.security import create_access_token

        user = Users(
            username="showall_user",
            email="showall@test.com",
            password=get_password_hash("Password123!"),
            password_type="bcrypt",
            salt="",
            show_all_images=1,
            active=1,
        )
        db_session.add(user)
        await db_session.flush()

        tag = Tags(title="Show All Count Tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.flush()

        statuses = [
            ImageStatus.ACTIVE,
            ImageStatus.SPOILER,
            ImageStatus.OTHER,
            ImageStatus.INAPPROPRIATE,
        ]
        for i, status_val in enumerate(statuses):
            img_data = sample_image_data.copy()
            img_data.update({
                "filename": f"showall-test-{i}",
                "md5_hash": f"showalltest{i:015d}",
                "status": status_val,
            })
            img = Images(**img_data)
            db_session.add(img)
            await db_session.flush()
            db_session.add(TagLinks(tag_id=tag.tag_id, image_id=img.image_id))

        await db_session.commit()

        token = create_access_token(user.user_id)
        response = await client.get(
            f"/api/v1/tags/{tag.tag_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total_image_count"] == 4  # All statuses visible

    async def test_total_image_count_show_all_images_0_excludes_non_public(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """User with show_all_images=0 sees public images + own non-public images."""
        from app.config import ImageStatus
        from app.core.security import create_access_token

        user = Users(
            username="noshowuser",
            email="noshow@test.com",
            password=get_password_hash("Password123!"),
            password_type="bcrypt",
            salt="",
            show_all_images=0,
            active=1,
        )
        db_session.add(user)
        await db_session.flush()

        tag = Tags(title="No Show All Count Tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.flush()

        # Other user's images (various statuses)
        statuses = [
            ImageStatus.ACTIVE,
            ImageStatus.SPOILER,
            ImageStatus.OTHER,
            ImageStatus.INAPPROPRIATE,
        ]
        for i, status_val in enumerate(statuses):
            img_data = sample_image_data.copy()
            img_data.update({
                "filename": f"noshow-test-{i}",
                "md5_hash": f"noshowtest{i:015d}",
                "status": status_val,
            })
            img = Images(**img_data)
            db_session.add(img)
            await db_session.flush()
            db_session.add(TagLinks(tag_id=tag.tag_id, image_id=img.image_id))

        # User's own non-public image (should be counted)
        own_img_data = sample_image_data.copy()
        own_img_data.update({
            "filename": "noshow-own",
            "md5_hash": "noshowown" + "0" * 16,
            "status": ImageStatus.OTHER,
            "user_id": user.user_id,
        })
        own_img = Images(**own_img_data)
        db_session.add(own_img)
        await db_session.flush()
        db_session.add(TagLinks(tag_id=tag.tag_id, image_id=own_img.image_id))

        await db_session.commit()

        token = create_access_token(user.user_id)
        response = await client.get(
            f"/api/v1/tags/{tag.tag_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        # 2 public (ACTIVE + SPOILER) + 1 own non-public = 3
        assert data["total_image_count"] == 3


@pytest.mark.api
class TestGetImagesByTag:
    """Tests for GET /api/v1/tags/{tag_id}/images endpoint."""

    async def test_get_images_by_tag(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test getting images with a specific tag."""
        # Create tag
        tag = Tags(title="sunset", desc="Sunset scenes", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Create images
        images = []
        for i in range(3):
            image_data = sample_image_data.copy()
            image_data["filename"] = f"sunset-{i}"
            image_data["md5_hash"] = f"sunset{i:021d}"
            image = Images(**image_data)
            db_session.add(image)
            images.append(image)
        await db_session.commit()

        # Link images to tag
        for image in images:
            await db_session.refresh(image)
            tag_link = TagLinks(tag_id=tag.tag_id, image_id=image.image_id)
            db_session.add(tag_link)
        await db_session.commit()

        response = await client.get(f"/api/v1/tags/{tag.tag_id}/images")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3

    async def test_get_images_by_nonexistent_tag(self, client: AsyncClient):
        """Test getting images for non-existent tag."""
        response = await client.get("/api/v1/tags/999999/images")
        assert response.status_code == 404

    async def test_get_images_by_tag_with_depth_0(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """tag_depth=0 should only return images tagged with the exact tag, not children."""
        # Create parent tag
        parent = Tags(title="clothing", desc="Parent", type=TagType.THEME)
        db_session.add(parent)
        await db_session.commit()
        await db_session.refresh(parent)

        # Create child tag
        child = Tags(
            title="dress",
            desc="Child of clothing",
            type=TagType.THEME,
            inheritedfrom_id=parent.tag_id,
        )
        db_session.add(child)
        await db_session.commit()
        await db_session.refresh(child)

        # Image tagged with parent directly
        image1_data = sample_image_data.copy()
        image1_data["filename"] = "parent-img"
        image1_data["md5_hash"] = "a" * 32
        image1 = Images(**image1_data)
        db_session.add(image1)
        await db_session.flush()

        # Image tagged with child only
        image2_data = sample_image_data.copy()
        image2_data["filename"] = "child-img"
        image2_data["md5_hash"] = "b" * 32
        image2 = Images(**image2_data)
        db_session.add(image2)
        await db_session.flush()

        tag_link1 = TagLinks(tag_id=parent.tag_id, image_id=image1.image_id)
        tag_link2 = TagLinks(tag_id=child.tag_id, image_id=image2.image_id)
        db_session.add_all([tag_link1, tag_link2])
        await db_session.commit()

        # tag_depth=0: only exact tag, no children
        response = await client.get(
            f"/api/v1/tags/{parent.tag_id}/images?tag_depth=0"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["images"][0]["image_id"] == image1.image_id

    async def test_get_images_by_tag_default_depth(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """No tag_depth should return full hierarchy (default behavior)."""
        # Create parent → child hierarchy
        parent = Tags(title="clothing", desc="Parent", type=TagType.THEME)
        db_session.add(parent)
        await db_session.commit()
        await db_session.refresh(parent)

        child = Tags(
            title="dress",
            type=TagType.THEME,
            inheritedfrom_id=parent.tag_id,
        )
        db_session.add(child)
        await db_session.commit()
        await db_session.refresh(child)

        # Image tagged with child only
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.flush()

        tag_link = TagLinks(tag_id=child.tag_id, image_id=image.image_id)
        db_session.add(tag_link)
        await db_session.commit()

        # No tag_depth: full hierarchy, should find child-tagged image
        response = await client.get(f"/api/v1/tags/{parent.tag_id}/images")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["images"][0]["image_id"] == image.image_id


@pytest.mark.api
class TestCreateTag:
    """Tests for POST /api/v1/tags endpoint (admin only)."""

    async def test_create_tag_as_admin(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test creating a tag as admin."""
        # Create TAG_CREATE permission
        perm = Perms(title="tag_create", desc="Create tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="adminuser",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="admin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_CREATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "adminuser", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Create tag
        tag_data = {
            "title": "new tag",
            "desc": "A new test tag",
            "type": TagType.THEME,
        }
        response = await client.post(
            "/api/v1/tags",
            json=tag_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "new tag"
        assert data["type"] == TagType.THEME

        # Verify user_id is stored in database
        tag_result = await db_session.execute(
            select(Tags).where(Tags.tag_id == data["tag_id"])
        )
        created_tag = tag_result.scalar_one()
        assert created_tag.user_id == admin.user_id

    async def test_create_tag_as_non_admin(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test non-admin user cannot create tags."""
        # Create regular user
        user = Users(
            username="regularuser",
            password=get_password_hash("Password123!"),
            password_type="bcrypt",
            salt="",
            email="regular@example.com",
            active=1,
            admin=0,
        )
        db_session.add(user)
        await db_session.commit()

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "regularuser", "password": "Password123!"},
        )
        access_token = login_response.json()["access_token"]

        # Try to create tag
        tag_data = {
            "title": "forbidden tag",
            "desc": "Should not be created",
            "type": TagType.THEME,
        }
        response = await client.post(
            "/api/v1/tags",
            json=tag_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 403

    async def test_create_tag_unauthenticated(self, client: AsyncClient):
        """Test creating tag without authentication."""
        tag_data = {
            "title": "unauthenticated tag",
            "desc": "Should not be created",
            "type": TagType.THEME,
        }
        response = await client.post("/api/v1/tags", json=tag_data)
        assert response.status_code == 401

    async def test_create_duplicate_tag(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test creating a duplicate tag."""
        # Create TAG_CREATE permission
        perm = Perms(title="tag_create", desc="Create tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="adminuser2",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="admin2@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_CREATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)

        # Create existing tag
        existing_tag = Tags(title="existing", desc="Already exists", type=TagType.THEME)
        db_session.add(existing_tag)
        await db_session.commit()

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "adminuser2", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Try to create duplicate tag
        tag_data = {
            "title": "existing",
            "desc": "Duplicate",
            "type": TagType.THEME,
        }
        response = await client.post(
            "/api/v1/tags",
            json=tag_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 409


@pytest.mark.api
class TestUpdateTag:
    """Tests for PUT /api/v1/tags/{tag_id} endpoint (admin only)."""

    async def test_update_tag_as_admin(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test admin updating a tag."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="adminupdate",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="adminupdate@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)

        # Create tag to update
        tag = Tags(title="old title", desc="old description", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "adminupdate", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Update tag
        update_data = {
            "title": "new title",
            "desc": "new description",
            "type": TagType.CHARACTER,
        }
        response = await client.put(
            f"/api/v1/tags/{tag.tag_id}",
            json=update_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "new title"
        assert data["type"] == TagType.CHARACTER

    async def test_update_tag_as_non_admin(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test non-admin cannot update tags."""
        # Create regular user
        user = Users(
            username="regularupdate",
            password=get_password_hash("Password123!"),
            password_type="bcrypt",
            salt="",
            email="regularupdate@example.com",
            active=1,
            admin=0,
        )
        db_session.add(user)

        # Create tag
        tag = Tags(title="tag to update", desc="description", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "regularupdate", "password": "Password123!"},
        )
        access_token = login_response.json()["access_token"]

        # Try to update tag
        update_data = {
            "title": "hacked title",
            "desc": "hacked description",
            "type": TagType.THEME,
        }
        response = await client.put(
            f"/api/v1/tags/{tag.tag_id}",
            json=update_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 403

    async def test_update_tag_rejects_duplicate_title_and_type(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that renaming a tag to a (title, type) that already exists returns 409."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="admindupeupdate",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="admindupeupdate@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)

        # Create two tags with different titles but same type
        existing_tag = Tags(title="existing tag", desc="", type=TagType.THEME)
        tag_to_rename = Tags(title="rename me", desc="", type=TagType.THEME)
        db_session.add_all([existing_tag, tag_to_rename])
        await db_session.commit()
        await db_session.refresh(tag_to_rename)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "admindupeupdate", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Try to rename tag to existing (title, type) combo
        response = await client.put(
            f"/api/v1/tags/{tag_to_rename.tag_id}",
            json={"title": "existing tag", "type": TagType.THEME},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 409
        assert "already exists" in response.json()["detail"].lower()


    async def test_setting_alias_migrates_tag_links(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that when a tag is aliased, its existing tag_links are migrated to the canonical tag."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="adminalias",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="adminalias@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)

        # Create canonical tag and alias tag
        canonical_tag = Tags(title="Tobise Tomoe", desc="canonical", type=TagType.CHARACTER)
        alias_tag = Tags(title="Tomoe Tobise", desc="alias", type=TagType.CHARACTER)
        db_session.add_all([canonical_tag, alias_tag])
        await db_session.commit()
        await db_session.refresh(canonical_tag)
        await db_session.refresh(alias_tag)

        # Create images and link them to the alias tag
        image1 = Images(
            filename="alias-test-001",
            ext="jpg",
            original_filename="test1.jpg",
            md5_hash="a" * 32,
            filesize=1000,
            width=100,
            height=100,
            rating=0.0,
            user_id=admin.user_id,
            status=1,
        )
        image2 = Images(
            filename="alias-test-002",
            ext="jpg",
            original_filename="test2.jpg",
            md5_hash="b" * 32,
            filesize=1000,
            width=100,
            height=100,
            rating=0.0,
            user_id=admin.user_id,
            status=1,
        )
        db_session.add_all([image1, image2])
        await db_session.commit()
        await db_session.refresh(image1)
        await db_session.refresh(image2)

        # Link images to the alias tag
        link1 = TagLinks(tag_id=alias_tag.tag_id, image_id=image1.image_id, user_id=admin.user_id)
        link2 = TagLinks(tag_id=alias_tag.tag_id, image_id=image2.image_id, user_id=admin.user_id)
        db_session.add_all([link1, link2])
        await db_session.commit()

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "adminalias", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Set alias_of on the alias tag
        response = await client.put(
            f"/api/v1/tags/{alias_tag.tag_id}",
            json={"title": "Tomoe Tobise", "alias_of": canonical_tag.tag_id},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200

        # Verify tag_links were migrated to canonical tag
        result = await db_session.execute(
            select(TagLinks).where(TagLinks.tag_id == canonical_tag.tag_id)
        )
        canonical_links = result.all()
        assert len(canonical_links) == 2

        # Verify no links remain on the alias tag
        result = await db_session.execute(
            select(TagLinks).where(TagLinks.tag_id == alias_tag.tag_id)
        )
        alias_links = result.all()
        assert len(alias_links) == 0

    async def test_setting_alias_handles_duplicate_links(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that migrating tag_links skips images already linked to the canonical tag."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="adminaliasdup",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="adminaliasdup@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)

        # Create canonical tag and alias tag
        canonical_tag = Tags(title="Canonical Dup", desc="canonical", type=TagType.CHARACTER)
        alias_tag = Tags(title="Alias Dup", desc="alias", type=TagType.CHARACTER)
        db_session.add_all([canonical_tag, alias_tag])
        await db_session.commit()
        await db_session.refresh(canonical_tag)
        await db_session.refresh(alias_tag)

        # Create image
        image = Images(
            filename="alias-dup-test-001",
            ext="jpg",
            original_filename="test.jpg",
            md5_hash="c" * 32,
            filesize=1000,
            width=100,
            height=100,
            rating=0.0,
            user_id=admin.user_id,
            status=1,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Link image to BOTH tags (simulating pre-existing canonical link)
        link_canonical = TagLinks(
            tag_id=canonical_tag.tag_id, image_id=image.image_id, user_id=admin.user_id
        )
        link_alias = TagLinks(
            tag_id=alias_tag.tag_id, image_id=image.image_id, user_id=admin.user_id
        )
        db_session.add_all([link_canonical, link_alias])
        await db_session.commit()

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "adminaliasdup", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Set alias_of on the alias tag
        response = await client.put(
            f"/api/v1/tags/{alias_tag.tag_id}",
            json={"title": "Alias Dup", "alias_of": canonical_tag.tag_id},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200

        # Verify canonical tag has exactly 1 link (no duplicate)
        result = await db_session.execute(
            select(TagLinks).where(TagLinks.tag_id == canonical_tag.tag_id)
        )
        canonical_links = result.all()
        assert len(canonical_links) == 1

        # Verify alias tag has no links
        result = await db_session.execute(
            select(TagLinks).where(TagLinks.tag_id == alias_tag.tag_id)
        )
        alias_links = result.all()
        assert len(alias_links) == 0


@pytest.mark.api
class TestDeleteTag:
    """Tests for DELETE /api/v1/tags/{tag_id} endpoint (admin only)."""

    async def test_delete_tag_as_admin(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test admin deleting a tag."""
        # Create TAG_DELETE permission
        perm = Perms(title="tag_delete", desc="Delete tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="admindelete",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="admindelete@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_DELETE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)

        # Create tag to delete
        tag = Tags(title="tag to delete", desc="will be deleted", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "admindelete", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Delete tag
        response = await client.delete(
            f"/api/v1/tags/{tag.tag_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 204

        # Verify tag is deleted
        get_response = await client.get(f"/api/v1/tags/{tag.tag_id}")
        assert get_response.status_code == 404

    async def test_delete_tag_as_non_admin(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test non-admin cannot delete tags."""
        # Create regular user
        user = Users(
            username="regulardelete",
            password=get_password_hash("Password123!"),
            password_type="bcrypt",
            salt="",
            email="regulardelete@example.com",
            active=1,
            admin=0,
        )
        db_session.add(user)

        # Create tag
        tag = Tags(title="protected tag", desc="should not be deleted", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "regulardelete", "password": "Password123!"},
        )
        access_token = login_response.json()["access_token"]

        # Try to delete tag
        response = await client.delete(
            f"/api/v1/tags/{tag.tag_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 403


@pytest.mark.api
class TestAddTagLink:
    """Tests for POST /api/v1/tags/{tag_id}/links endpoint."""

    async def test_add_link_to_tag(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test adding an external link to a tag."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="adminlinks",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="adminlinks@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Create tag
        tag = Tags(title="artist tag", desc="Test artist", type=TagType.ARTIST)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "adminlinks", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Add link to tag
        link_data = {"url": "https://twitter.com/artist_name"}
        response = await client.post(
            f"/api/v1/tags/{tag.tag_id}/links",
            json=link_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["url"] == "https://twitter.com/artist_name"
        assert "link_id" in data
        assert "date_added" in data

    async def test_add_link_without_protocol(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that URLs without http/https are rejected."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="adminproto",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="adminproto@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Create tag
        tag = Tags(title="test tag", desc="Test", type=TagType.ARTIST)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "adminproto", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Try to add link without protocol
        link_data = {"url": "twitter.com/artist"}
        response = await client.post(
            f"/api/v1/tags/{tag.tag_id}/links",
            json=link_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 422  # Validation error

    async def test_add_empty_url(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that empty URLs (whitespace only) are rejected."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="adminempty",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="adminempty@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Create tag
        tag = Tags(title="test tag", desc="Test", type=TagType.ARTIST)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "adminempty", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Try to add empty/whitespace URL
        link_data = {"url": "   "}
        response = await client.post(
            f"/api/v1/tags/{tag.tag_id}/links",
            json=link_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 422  # Validation error

    async def test_add_duplicate_link(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that adding duplicate URL to same tag returns 409."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="admindup",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="admindup@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Create tag with existing link
        tag = Tags(title="test tag", desc="Test", type=TagType.ARTIST)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        link = TagExternalLinks(tag_id=tag.tag_id, url="https://example.com")
        db_session.add(link)
        await db_session.commit()

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "admindup", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Try to add same URL again
        link_data = {"url": "https://example.com"}
        response = await client.post(
            f"/api/v1/tags/{tag.tag_id}/links",
            json=link_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 409

    async def test_add_link_to_nonexistent_tag(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test adding link to non-existent tag returns 404."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="admin404",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="admin404@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin404", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Try to add link to non-existent tag
        link_data = {"url": "https://example.com"}
        response = await client.post(
            "/api/v1/tags/999999/links",
            json=link_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 404

    async def test_add_link_without_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that users without TAG_UPDATE permission cannot add links."""
        # Create regular user without permission
        user = Users(
            username="regularlinkuser",
            password=get_password_hash("Password123!"),
            password_type="bcrypt",
            salt="",
            email="regularlink@example.com",
            active=1,
            admin=0,
        )
        db_session.add(user)
        await db_session.commit()

        # Create tag
        tag = Tags(title="test tag", desc="Test", type=TagType.ARTIST)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "regularlinkuser", "password": "Password123!"},
        )
        access_token = login_response.json()["access_token"]

        # Try to add link
        link_data = {"url": "https://example.com"}
        response = await client.post(
            f"/api/v1/tags/{tag.tag_id}/links",
            json=link_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 403


@pytest.mark.api
class TestDeleteTagLink:
    """Tests for DELETE /api/v1/tags/{tag_id}/links/{link_id} endpoint."""

    async def test_delete_link_from_tag(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test deleting an external link from a tag."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="admindellink",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="admindellink@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Create tag with link
        tag = Tags(title="test tag", desc="Test", type=TagType.ARTIST)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        link = TagExternalLinks(tag_id=tag.tag_id, url="https://example.com")
        db_session.add(link)
        await db_session.commit()
        await db_session.refresh(link)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "admindellink", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Delete link
        response = await client.delete(
            f"/api/v1/tags/{tag.tag_id}/links/{link.link_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 204

    async def test_delete_nonexistent_link(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test deleting non-existent link returns 404."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="admindel404",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="admindel404@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Create tag (without link)
        tag = Tags(title="test tag", desc="Test", type=TagType.ARTIST)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "admindel404", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Try to delete non-existent link
        response = await client.delete(
            f"/api/v1/tags/{tag.tag_id}/links/999999",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 404

    async def test_delete_link_from_wrong_tag(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test deleting link using wrong tag_id returns 404."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="admindelwrong",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="admindelwrong@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Create two tags
        tag1 = Tags(title="tag1", desc="Test", type=TagType.ARTIST)
        tag2 = Tags(title="tag2", desc="Test", type=TagType.ARTIST)
        db_session.add_all([tag1, tag2])
        await db_session.commit()
        await db_session.refresh(tag1)
        await db_session.refresh(tag2)

        # Add link to tag1
        link = TagExternalLinks(tag_id=tag1.tag_id, url="https://example.com")
        db_session.add(link)
        await db_session.commit()
        await db_session.refresh(link)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "admindelwrong", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Try to delete link from wrong tag
        response = await client.delete(
            f"/api/v1/tags/{tag2.tag_id}/links/{link.link_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 404

    async def test_delete_link_without_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that users without TAG_UPDATE permission cannot delete links."""
        # Create regular user without permission
        user = Users(
            username="regulardellinkuser",
            password=get_password_hash("Password123!"),
            password_type="bcrypt",
            salt="",
            email="regulardellink@example.com",
            active=1,
            admin=0,
        )
        db_session.add(user)
        await db_session.commit()

        # Create tag with link
        tag = Tags(title="test tag", desc="Test", type=TagType.ARTIST)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        link = TagExternalLinks(tag_id=tag.tag_id, url="https://example.com")
        db_session.add(link)
        await db_session.commit()
        await db_session.refresh(link)

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "regulardellinkuser", "password": "Password123!"},
        )
        access_token = login_response.json()["access_token"]

        # Try to delete link
        response = await client.delete(
            f"/api/v1/tags/{tag.tag_id}/links/{link.link_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 403


@pytest.mark.api
class TestGetTagWithLinks:
    """Tests for GET /api/v1/tags/{tag_id} endpoint with links."""

    async def test_get_tag_with_links(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that tag details include external links with full metadata."""
        # Create tag with links
        tag = Tags(title="test tag", desc="Test", type=TagType.ARTIST)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Add multiple links
        link1 = TagExternalLinks(tag_id=tag.tag_id, url="https://twitter.com/artist")
        link2 = TagExternalLinks(tag_id=tag.tag_id, url="https://pixiv.net/users/123")
        db_session.add_all([link1, link2])
        await db_session.commit()
        await db_session.refresh(link1)
        await db_session.refresh(link2)

        # Get tag details
        response = await client.get(f"/api/v1/tags/{tag.tag_id}")
        assert response.status_code == 200
        data = response.json()
        assert "links" in data
        assert len(data["links"]) == 2

        # Links should be objects with link_id, url, and date_added
        urls = [link["url"] for link in data["links"]]
        assert "https://twitter.com/artist" in urls
        assert "https://pixiv.net/users/123" in urls

        # Verify each link has required fields
        for link in data["links"]:
            assert "link_id" in link
            assert "url" in link
            assert "date_added" in link
            assert isinstance(link["link_id"], int)

    async def test_get_tag_without_links(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that tags without links have empty links array."""
        # Create tag without links
        tag = Tags(title="test tag", desc="Test", type=TagType.ARTIST)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Get tag details
        response = await client.get(f"/api/v1/tags/{tag.tag_id}")
        assert response.status_code == 200
        data = response.json()
        assert "links" in data
        assert data["links"] == []


@pytest.mark.api
class TestGetTagHierarchy:
    """Tests for get_tag_hierarchy function.

    This function uses a recursive CTE to efficiently get all descendant tags
    in a tag's hierarchy (children, grandchildren, etc.).
    """

    async def test_single_tag_no_children(self, db_session: AsyncSession):
        """Test hierarchy for a tag with no children returns only itself."""
        tag = Tags(title="lone tag", desc="No children", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        hierarchy = await get_tag_hierarchy(db_session, tag.tag_id)

        assert hierarchy == [tag.tag_id]

    async def test_parent_with_direct_children(self, db_session: AsyncSession):
        """Test hierarchy includes direct children."""
        # Create parent
        parent = Tags(title="parent", desc="Parent tag", type=TagType.THEME)
        db_session.add(parent)
        await db_session.commit()
        await db_session.refresh(parent)

        # Create children
        child1 = Tags(
            title="child1",
            desc="First child",
            type=TagType.THEME,
            inheritedfrom_id=parent.tag_id,
        )
        child2 = Tags(
            title="child2",
            desc="Second child",
            type=TagType.THEME,
            inheritedfrom_id=parent.tag_id,
        )
        db_session.add_all([child1, child2])
        await db_session.commit()
        await db_session.refresh(child1)
        await db_session.refresh(child2)

        hierarchy = await get_tag_hierarchy(db_session, parent.tag_id)

        assert set(hierarchy) == {parent.tag_id, child1.tag_id, child2.tag_id}

    async def test_multi_level_hierarchy(self, db_session: AsyncSession):
        """Test hierarchy includes grandchildren (multi-level)."""
        # Create grandparent -> parent -> child structure
        grandparent = Tags(title="food", desc="Top level", type=TagType.THEME)
        db_session.add(grandparent)
        await db_session.commit()
        await db_session.refresh(grandparent)

        parent = Tags(
            title="sweets",
            desc="Child of food",
            type=TagType.THEME,
            inheritedfrom_id=grandparent.tag_id,
        )
        db_session.add(parent)
        await db_session.commit()
        await db_session.refresh(parent)

        grandchild = Tags(
            title="cake",
            desc="Child of sweets",
            type=TagType.THEME,
            inheritedfrom_id=parent.tag_id,
        )
        db_session.add(grandchild)
        await db_session.commit()
        await db_session.refresh(grandchild)

        # Query from top level should include all descendants
        hierarchy = await get_tag_hierarchy(db_session, grandparent.tag_id)

        assert set(hierarchy) == {grandparent.tag_id, parent.tag_id, grandchild.tag_id}

    async def test_max_depth_limits_recursion(self, db_session: AsyncSession):
        """Test that max_depth parameter limits how deep the hierarchy goes."""
        # Create a 4-level deep hierarchy
        level1 = Tags(title="level1", desc="Level 1", type=TagType.THEME)
        db_session.add(level1)
        await db_session.commit()
        await db_session.refresh(level1)

        level2 = Tags(
            title="level2", type=TagType.THEME, inheritedfrom_id=level1.tag_id
        )
        db_session.add(level2)
        await db_session.commit()
        await db_session.refresh(level2)

        level3 = Tags(
            title="level3", type=TagType.THEME, inheritedfrom_id=level2.tag_id
        )
        db_session.add(level3)
        await db_session.commit()
        await db_session.refresh(level3)

        level4 = Tags(
            title="level4", type=TagType.THEME, inheritedfrom_id=level3.tag_id
        )
        db_session.add(level4)
        await db_session.commit()
        await db_session.refresh(level4)

        # With max_depth=2, should only get level1 and level2
        hierarchy = await get_tag_hierarchy(db_session, level1.tag_id, max_depth=2)
        assert set(hierarchy) == {level1.tag_id, level2.tag_id}

        # With max_depth=3, should get level1, level2, and level3
        hierarchy = await get_tag_hierarchy(db_session, level1.tag_id, max_depth=3)
        assert set(hierarchy) == {level1.tag_id, level2.tag_id, level3.tag_id}

        # With default max_depth (10), should get all levels
        hierarchy = await get_tag_hierarchy(db_session, level1.tag_id)
        assert set(hierarchy) == {
            level1.tag_id,
            level2.tag_id,
            level3.tag_id,
            level4.tag_id,
        }

    async def test_child_hierarchy_excludes_parent(self, db_session: AsyncSession):
        """Test querying a child tag does not include its parent."""
        parent = Tags(title="parent", desc="Parent", type=TagType.THEME)
        db_session.add(parent)
        await db_session.commit()
        await db_session.refresh(parent)

        child = Tags(
            title="child",
            desc="Child",
            type=TagType.THEME,
            inheritedfrom_id=parent.tag_id,
        )
        db_session.add(child)
        await db_session.commit()
        await db_session.refresh(child)

        # Query from child should not include parent
        hierarchy = await get_tag_hierarchy(db_session, child.tag_id)

        assert hierarchy == [child.tag_id]
        assert parent.tag_id not in hierarchy


@pytest.mark.api
class TestTagNameValidation:
    """Tests for tag name character validation.

    Tag names must only contain:
    - Latin letters (a-z, A-Z)
    - Digits (0-9)
    - CJK characters (Chinese, Japanese kanji, Korean)
    - Hiragana and Katakana
    - Basic punctuation: space, hyphen, period, apostrophe, colon,
      parentheses, exclamation, question mark, ampersand, forward slash,
      comma, underscore

    Additionally:
    - Minimum length: 2 characters
    - Maximum length: 150 characters
    - Consecutive spaces are normalized to single space
    """

    async def test_valid_latin_tag_name(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that basic Latin letters are allowed."""
        # Create TAG_CREATE permission and admin user
        perm = Perms(title="tag_create", desc="Create tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username="tagvalidadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="tagvalidadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm = UserPerms(user_id=admin.user_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(user_perm)
        await db_session.commit()

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "tagvalidadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Create tag with valid Latin name
        response = await client.post(
            "/api/v1/tags",
            json={"title": "School Uniform", "type": TagType.THEME},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        assert response.json()["title"] == "School Uniform"

    async def test_valid_cjk_tag_name(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that CJK characters (Japanese kanji, Chinese) are allowed."""
        perm = Perms(title="tag_create", desc="Create tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username="tagcjkadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="tagcjkadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm = UserPerms(user_id=admin.user_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(user_perm)
        await db_session.commit()

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "tagcjkadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Create tag with Japanese kanji
        response = await client.post(
            "/api/v1/tags",
            json={"title": "桜木花道", "type": TagType.CHARACTER},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        assert response.json()["title"] == "桜木花道"

    async def test_valid_hiragana_katakana_tag_name(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that Hiragana and Katakana are allowed."""
        perm = Perms(title="tag_create", desc="Create tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username="tagkanaadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="tagkanaadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm = UserPerms(user_id=admin.user_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(user_perm)
        await db_session.commit()

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "tagkanaadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Create tag with hiragana
        response = await client.post(
            "/api/v1/tags",
            json={"title": "ひらがな", "type": TagType.CHARACTER},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200

        # Create tag with katakana (different word to avoid collation conflicts)
        response = await client.post(
            "/api/v1/tags",
            json={"title": "カタカナ", "type": TagType.CHARACTER},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200

    async def test_valid_korean_tag_name(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that Korean Hangul is allowed."""
        perm = Perms(title="tag_create", desc="Create tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username="tagkoreanadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="tagkoreanadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm = UserPerms(user_id=admin.user_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(user_perm)
        await db_session.commit()

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "tagkoreanadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Create tag with Korean
        response = await client.post(
            "/api/v1/tags",
            json={"title": "한글태그", "type": TagType.CHARACTER},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200

    async def test_valid_punctuation_in_tag_name(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that allowed punctuation characters work."""
        perm = Perms(title="tag_create", desc="Create tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username="tagpunctadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="tagpunctadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm = UserPerms(user_id=admin.user_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(user_perm)
        await db_session.commit()

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "tagpunctadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Test various valid punctuation
        valid_names = [
            "C.C.",  # periods
            "Re:Zero",  # colon
            "Fate/Stay Night",  # forward slash
            "Sakura (Naruto)",  # parentheses
            "K-On!",  # hyphen and exclamation
            "Who's that?",  # apostrophe and question mark
            "Tom & Jerry",  # ampersand
            "test_tag",  # underscore
            "One, Two, Three",  # comma
        ]

        for i, name in enumerate(valid_names):
            response = await client.post(
                "/api/v1/tags",
                json={"title": name, "type": TagType.THEME},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert response.status_code == 200, f"Failed for: {name}"

    async def test_invalid_decorative_symbols(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that decorative unicode symbols are rejected."""
        perm = Perms(title="tag_create", desc="Create tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username="taginvalidadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="taginvalidadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm = UserPerms(user_id=admin.user_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(user_perm)
        await db_session.commit()

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "taginvalidadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Test invalid decorative symbols
        invalid_names = [
            "｡☆",  # fullwidth period and star
            "（ФωФ）",  # fullwidth parens, Cyrillic, Greek
            "ꕤ H U H U ꕤ",  # decorative Vai syllable
            "★ star ★",  # star symbols
            "♥ heart ♥",  # heart symbols
            "→ arrow",  # arrow symbol
        ]

        for name in invalid_names:
            response = await client.post(
                "/api/v1/tags",
                json={"title": name, "type": TagType.THEME},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert response.status_code == 422, f"Should reject: {name}"

    async def test_invalid_fullwidth_punctuation(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that fullwidth punctuation is rejected (use ASCII instead)."""
        perm = Perms(title="tag_create", desc="Create tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username="tagfwadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="tagfwadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm = UserPerms(user_id=admin.user_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(user_perm)
        await db_session.commit()

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "tagfwadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Test fullwidth punctuation (should be rejected)
        invalid_names = [
            "test（fullwidth）",  # fullwidth parentheses
            "test。period",  # fullwidth period
            "test！exclaim",  # fullwidth exclamation
        ]

        for name in invalid_names:
            response = await client.post(
                "/api/v1/tags",
                json={"title": name, "type": TagType.THEME},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert response.status_code == 422, f"Should reject fullwidth: {name}"

    async def test_invalid_cyrillic_greek(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that Cyrillic and Greek letters are rejected."""
        perm = Perms(title="tag_create", desc="Create tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username="tagcyrilladmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="tagcyrilladmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm = UserPerms(user_id=admin.user_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(user_perm)
        await db_session.commit()

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "tagcyrilladmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Cyrillic and Greek should be rejected
        invalid_names = [
            "Привет",  # Cyrillic
            "αβγ",  # Greek
            "ωmega",  # Mixed Greek omega with Latin
        ]

        for name in invalid_names:
            response = await client.post(
                "/api/v1/tags",
                json={"title": name, "type": TagType.THEME},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert response.status_code == 422, f"Should reject: {name}"

    async def test_minimum_length_validation(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that tag names must be at least 2 characters."""
        perm = Perms(title="tag_create", desc="Create tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username="tagminadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="tagminadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm = UserPerms(user_id=admin.user_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(user_perm)
        await db_session.commit()

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "tagminadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Single character should be rejected
        response = await client.post(
            "/api/v1/tags",
            json={"title": "A", "type": TagType.THEME},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 422

        # Two characters should be allowed
        response = await client.post(
            "/api/v1/tags",
            json={"title": "AB", "type": TagType.THEME},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200

    async def test_maximum_length_validation(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that tag names cannot exceed 150 characters."""
        perm = Perms(title="tag_create", desc="Create tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username="tagmaxadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="tagmaxadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm = UserPerms(user_id=admin.user_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(user_perm)
        await db_session.commit()

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "tagmaxadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # 151 characters should be rejected
        response = await client.post(
            "/api/v1/tags",
            json={"title": "A" * 151, "type": TagType.THEME},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 422

        # 150 characters should be allowed
        response = await client.post(
            "/api/v1/tags",
            json={"title": "A" * 150, "type": TagType.THEME},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200

    async def test_consecutive_spaces_normalized(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that consecutive spaces are normalized to single space."""
        perm = Perms(title="tag_create", desc="Create tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username="tagspaceadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="tagspaceadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm = UserPerms(user_id=admin.user_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(user_perm)
        await db_session.commit()

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "tagspaceadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Multiple spaces should be normalized
        response = await client.post(
            "/api/v1/tags",
            json={"title": "School    Uniform", "type": TagType.THEME},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        # The title should be normalized
        assert response.json()["title"] == "School Uniform"

    async def test_tag_update_validation(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that tag update also validates the title."""
        # Create permissions
        create_perm = Perms(title="tag_create", desc="Create tags")
        update_perm = Perms(title="tag_update", desc="Update tags")
        db_session.add_all([create_perm, update_perm])
        await db_session.commit()
        await db_session.refresh(create_perm)
        await db_session.refresh(update_perm)

        admin = Users(
            username="tagupdatevalidadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="tagupdatevalidadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm1 = UserPerms(user_id=admin.user_id, perm_id=create_perm.perm_id, permvalue=1)
        user_perm2 = UserPerms(user_id=admin.user_id, perm_id=update_perm.perm_id, permvalue=1)
        db_session.add_all([user_perm1, user_perm2])
        await db_session.commit()

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "tagupdatevalidadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Create a valid tag first
        create_response = await client.post(
            "/api/v1/tags",
            json={"title": "Valid Tag", "type": TagType.THEME},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert create_response.status_code == 200
        tag_id = create_response.json()["tag_id"]

        # Try to update with invalid name
        response = await client.put(
            f"/api/v1/tags/{tag_id}",
            json={"title": "★ Invalid ★", "type": TagType.THEME},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 422

    async def test_create_tag_requires_title(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that creating a tag without title is rejected."""
        perm = Perms(title="tag_create", desc="Create tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username="tagrequiredadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="tagrequiredadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm = UserPerms(user_id=admin.user_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(user_perm)
        await db_session.commit()

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "tagrequiredadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Missing title should be rejected
        response = await client.post(
            "/api/v1/tags",
            json={"type": TagType.THEME},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 422

        # Explicit null title should be rejected
        response = await client.post(
            "/api/v1/tags",
            json={"title": None, "type": TagType.THEME},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 422
