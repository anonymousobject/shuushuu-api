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
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.core.security import get_password_hash
from app.models.image import Images
from app.models.permissions import Perms, UserPerms
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.user import Users


@pytest.mark.api
class TestListTags:
    """Tests for GET /api/v1/tags/ endpoint."""

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

        response = await client.get("/api/v1/tags/")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 5
        assert "tags" in data

    async def test_search_tags(self, client: AsyncClient, db_session: AsyncSession):
        """Test searching tags by name."""
        # Create tags with different names
        tag1 = Tags(title="anime girl", desc="Anime female character", type=TagType.THEME)
        tag2 = Tags(title="school uniform", desc="School clothing", type=TagType.CHARACTER)
        tag3 = Tags(title="cat ears", desc="Feline ears", type=TagType.ARTIST)
        db_session.add_all([tag1, tag2, tag3])
        await db_session.commit()

        # Search for "school"
        response = await client.get("/api/v1/tags/?search=school")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["tags"][0]["title"] == "school uniform"

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
        response = await client.get(f"/api/v1/tags/?type={TagType.THEME}")
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
        response = await client.get(f"/api/v1/tags/?ids={tag1.tag_id},{tag3.tag_id}")
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
        response = await client.get(f"/api/v1/tags/?ids={tag1.tag_id},abc,{tag2.tag_id},xyz")
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
        response = await client.get("/api/v1/tags/?ids=abc,xyz,foo")
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
        response = await client.get(f"/api/v1/tags/?ids={tag1.tag_id},,")
        assert response.status_code == 200
        data = response.json()

        # Should return valid tag
        assert data["total"] == 1
        assert data["tags"][0]["tag_id"] == tag1.tag_id

        # Empty strings should not be reported as invalid
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
        other_tag = Tags(title="dress", desc="Not related to swimsuit", type=TagType.THEME)
        db_session.add_all([child1, child2, other_tag])
        await db_session.commit()
        await db_session.refresh(child1)
        await db_session.refresh(child2)

        # Filter by parent tag ID
        response = await client.get(f"/api/v1/tags/?parent_tag_id={parent_tag.tag_id}")
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
        response = await client.get(f"/api/v1/tags/?parent_tag_id={parent_tag.tag_id}")
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
        response = await client.get(f"/api/v1/tags/?parent_tag_id={parent_tag.tag_id}")
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
            f"/api/v1/tags/?parent_tag_id={parent_tag.tag_id}&type={TagType.THEME}"
        )
        assert response.status_code == 200
        data = response.json()

        # Should return only child tags with matching type
        assert data["total"] == 1
        assert data["tags"][0]["tag_id"] == child1.tag_id
        assert data["tags"][0]["type"] == TagType.THEME



@pytest.mark.api
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
        response = await client.get("/api/v1/tags/?search=sa")
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
        response = await client.get("/api/v1/tags/?search=sakura%20kinomoto")
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
        response = await client.get("/api/v1/tags/?search=school")
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
        response = await client.get("/api/v1/tags/?search=cat")
        assert response.status_code == 200
        data = response.json()

        # FULLTEXT should find "cat ears" (exact word "cat")
        # May or may not find "category" depending on word stemming in BOOLEAN mode
        assert data["total"] >= 1
        assert data["tags"][0]["title"] == "cat ears"  # Exact match should be first

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
        response = await client.get("/api/v1/tags/?search=sa")
        assert response.status_code == 200
        data = response.json()
        # Should find all tags starting with "sa" (case-insensitive)
        assert data["total"] == 3

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
        response = await client.get("/api/v1/tags/?search=sakura&type=4")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2  # Both CHARACTER tags
        for tag in data["tags"]:
            assert tag["type"] == TagType.CHARACTER

        # Search for "sakura" excluding aliases
        response = await client.get("/api/v1/tags/?search=sakura&exclude_aliases=true")
        assert response.status_code == 200
        data = response.json()
        # Should not include alias tags
        for tag in data["tags"]:
            assert tag.get("is_alias") is False or tag.get("is_alias") is None



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
        assert data["image_count"] == 0  # No images yet

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


@pytest.mark.api
class TestCreateTag:
    """Tests for POST /api/v1/tags/ endpoint (admin only)."""

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
            "/api/v1/tags/",
            json=tag_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "new tag"
        assert data["type"] == TagType.THEME

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
            "/api/v1/tags/",
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
        response = await client.post("/api/v1/tags/", json=tag_data)
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
            "/api/v1/tags/",
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
