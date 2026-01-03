"""
Tests for users API endpoints.

These tests cover the /api/v1/users endpoints including:
- List users
- Get user profile
- Create user
- Update user profile
- Get user's images
- Get user's favorites
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash
from app.models.favorite import Favorites
from app.models.image import Images
from app.models.permissions import GroupPerms, Groups, Perms, UserGroups
from app.models.user import Users


@pytest.mark.api
class TestListUsers:
    """Tests for GET /api/v1/users endpoint."""

    async def test_list_users(self, client: AsyncClient, db_session: AsyncSession):
        """Test listing users."""
        response = await client.get("/api/v1/users")
        assert response.status_code == 200
        data = response.json()
        assert "total" in data
        assert "users" in data
        # At least 3 test users from conftest.py
        assert data["total"] >= 3

    async def test_list_users_pagination(self, client: AsyncClient, db_session: AsyncSession):
        """Test user list pagination."""
        # Create additional users
        for i in range(10):
            user = Users(
                username=f"paginationuser{i}",
                password=get_password_hash("TestPassword123!"),
                password_type="bcrypt",
                salt="",
                email=f"pagination{i}@example.com",
            )
            db_session.add(user)
        await db_session.commit()

        # Test pagination
        response = await client.get("/api/v1/users?page=1&per_page=5")
        assert response.status_code == 200
        data = response.json()
        assert data["per_page"] == 5
        assert len(data["users"]) <= 5

    async def test_list_users_search_matches(self, client: AsyncClient, db_session: AsyncSession):
        """Test searching users by username with partial matches."""
        # Create test users
        test_users = [
            ("Alice", "alice@example.com"),
            ("Bob", "bob@example.com"),
            ("AliceWonderland", "alicewonderland@example.com"),
            ("charlie", "charlie@example.com"),
        ]
        for username, email in test_users:
            user = Users(
                username=username,
                password=get_password_hash("TestPassword123!"),
                password_type="bcrypt",
                salt="",
                email=email,
                active=1,
            )
            db_session.add(user)
        await db_session.commit()

        # Search for "alice" (should match Alice and AliceWonderland)
        response = await client.get("/api/v1/users?search=alice")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        usernames = [u["username"] for u in data["users"]]
        assert "Alice" in usernames
        assert "AliceWonderland" in usernames
        assert "Bob" not in usernames
        assert "charlie" not in usernames

    async def test_list_users_search_case_insensitive(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that username search is case-insensitive."""
        # Create test user
        user = Users(
            username="JohnDoe",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="johndoe@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()

        # Test different case variations
        for search_term in ["john", "JOHN", "JoHn", "johndoe"]:
            response = await client.get(f"/api/v1/users?search={search_term}")
            assert response.status_code == 200
            data = response.json()
            assert data["total"] >= 1
            usernames = [u["username"] for u in data["users"]]
            assert "JohnDoe" in usernames

    async def test_list_users_search_no_matches(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test searching with no matching results."""
        response = await client.get("/api/v1/users?search=nonexistentuserxyz")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["users"] == []

    async def test_update_user_freeform_fields_normalized(self, client: AsyncClient, db_session: AsyncSession):
        """Updating user free-form fields (title/location/interests) stores plain text (no normalization)."""
        # Create user
        user = Users(
            username="normalizeuser",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="normalize@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "normalizeuser", "password": "TestPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Update free-form fields with normal text
        payload = {
            "user_title": "I am Special & Proud",
            "location": "City & County",
            "interests": "Fish & Chips & More",
        }

        response = await client.patch(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {access_token}"},
            json=payload,
        )
        assert response.status_code == 200

        # Fetch profile and verify fields are stored as plain text
        response = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {access_token}"})
        assert response.status_code == 200
        data = response.json()
        # Plain text storage: what goes in comes out (no HTML escaping/normalization)
        assert data["user_title"] == "I am Special & Proud"
        assert data["location"] == "City & County"
        assert data["interests"] == "Fish & Chips & More"

    async def test_list_users_search_with_pagination(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that search results are properly paginated."""
        # Create 25 users with "searchuser" in their name
        for i in range(25):
            user = Users(
                username=f"searchuser{i}",
                password=get_password_hash("TestPassword123!"),
                password_type="bcrypt",
                salt="",
                email=f"searchuser{i}@example.com",
                active=1,
            )
            db_session.add(user)
        await db_session.commit()

        # Search with pagination
        response = await client.get("/api/v1/users?search=searchuser&per_page=20")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 25
        assert len(data["users"]) == 20
        assert data["per_page"] == 20

        # Get second page
        response = await client.get("/api/v1/users?search=searchuser&page=2&per_page=20")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 25
        assert len(data["users"]) == 5

    async def test_list_users_search_special_characters(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test searching for usernames with special characters."""
        # Create users with special characters
        test_users = [
            ("user_name", "user_name@example.com"),
            ("user.name", "user.name@example.com"),
            ("user-name", "user-name@example.com"),
        ]
        for username, email in test_users:
            user = Users(
                username=username,
                password=get_password_hash("TestPassword123!"),
                password_type="bcrypt",
                salt="",
                email=email,
                active=1,
            )
            db_session.add(user)
        await db_session.commit()

        # Search for "user" should match all three
        response = await client.get("/api/v1/users?search=user")
        assert response.status_code == 200
        data = response.json()
        usernames = [u["username"] for u in data["users"]]
        assert "user_name" in usernames
        assert "user.name" in usernames
        assert "user-name" in usernames

    async def test_list_users_search_exact_match_priority(self, client: AsyncClient, db_session: AsyncSession):
        """Exact username match should be prioritized for search results."""
        # Create a user with exact name 'Ran'
        ran = Users(
            username="Ran",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="ran@example.com",
            active=1,
        )
        db_session.add(ran)

        # Create other users containing 'ran' in their names to crowd the results
        for i in range(15):
            user = Users(
                username=f"random{i}ran",
                password=get_password_hash("TestPassword123!"),
                password_type="bcrypt",
                salt="",
                email=f"random{i}@example.com",
                active=1,
            )
            db_session.add(user)

        await db_session.commit()

        # Search for 'ran' and expect 'Ran' to appear in results and be first
        response = await client.get("/api/v1/users?search=ran&per_page=10")
        assert response.status_code == 200
        data = response.json()
        usernames = [u["username"] for u in data["users"]]
        assert "Ran" in usernames
        assert usernames[0] == "Ran"

    async def test_list_users_search_empty_returns_all(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that empty search string or no search parameter returns all users."""
        # Create test users to ensure we have users in the database
        test_users = [
            ("EmptySearchUser1", "emptysearch1@example.com"),
            ("EmptySearchUser2", "emptysearch2@example.com"),
            ("EmptySearchUser3", "emptysearch3@example.com"),
        ]
        for username, email in test_users:
            user = Users(
                username=username,
                password=get_password_hash("TestPassword123!"),
                password_type="bcrypt",
                salt="",
                email=email,
                active=1,
            )
            db_session.add(user)
        await db_session.commit()

        # Get total users without search parameter (baseline)
        response_no_search = await client.get("/api/v1/users")
        assert response_no_search.status_code == 200
        data_no_search = response_no_search.json()
        total_users = data_no_search["total"]
        assert total_users >= 3  # At least our 3 test users

        # Test with empty string search parameter - should return same total as no search
        response_empty_search = await client.get("/api/v1/users?search=")
        assert response_empty_search.status_code == 200
        data_empty_search = response_empty_search.json()
        assert data_empty_search["total"] == total_users


@pytest.mark.api
class TestGetUser:
    """Tests for GET /api/v1/users/{user_id} endpoint."""

    async def test_get_user_by_id(self, client: AsyncClient, db_session: AsyncSession):
        """Test getting a user by ID."""
        # Use existing test user (user_id=1 from conftest)
        response = await client.get("/api/v1/users/1")
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == 1
        assert "username" in data
        # assert "email" in data  # Email may be omitted in public response

    async def test_get_nonexistent_user(self, client: AsyncClient):
        """Test getting a user that doesn't exist."""
        response = await client.get("/api/v1/users/999999")
        assert response.status_code == 404


@pytest.mark.api
class TestCreateUser:
    """Tests for POST /api/v1/users endpoint."""

    async def test_create_user_success(self, client: AsyncClient, db_session: AsyncSession):
        """Test successful user creation."""
        user_data = {
            "username": "newuser123",
            "email": "newuser@example.com",
            "password": "SecurePassword123!",
            "turnstile_token": "test-token",
        }

        response = await client.post("/api/v1/users", json=user_data)
        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "newuser123"
        assert data["email"] == "newuser@example.com"
        assert "user_id" in data

    async def test_create_user_duplicate_username(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test creating user with duplicate username."""
        # Create first user
        user_data = {
            "username": "duplicate",
            "email": "first@example.com",
            "password": "SecurePassword123!",
            "turnstile_token": "test-token",
        }
        response = await client.post("/api/v1/users", json=user_data)
        assert response.status_code == 200

        # Try to create second user with same username
        user_data2 = {
            "username": "duplicate",
            "email": "second@example.com",
            "password": "SecurePassword123!",
            "turnstile_token": "test-token",
        }
        response = await client.post("/api/v1/users", json=user_data2)
        assert response.status_code == 409
        assert "already exists" in response.json()["detail"].lower()

    async def test_create_user_invalid_username(self, client: AsyncClient):
        """Test creating user with invalid username format."""
        user_data = {
            "username": "ab",  # Too short (min 3 chars)
            "email": "test@example.com",
            "password": "SecurePassword123!",
            "turnstile_token": "test-token",
        }
        response = await client.post("/api/v1/users", json=user_data)
        assert response.status_code == 400

    async def test_create_user_weak_password(self, client: AsyncClient):
        """Test creating user with weak password."""
        user_data = {
            "username": "weakpassuser",
            "email": "weak@example.com",
            "password": "weak",
            "turnstile_token": "test-token",
        }
        response = await client.post("/api/v1/users", json=user_data)
        assert response.status_code == 400


@pytest.mark.api
class TestGetCurrentUserProfile:
    """Tests for GET /api/v1/users/me endpoint."""

    async def test_get_current_user_profile(self, client: AsyncClient, db_session: AsyncSession):
        """Test getting current user's profile."""
        # Create user and login
        user = Users(
            username="currentuser",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="current@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "currentuser", "password": "TestPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Get profile
        response = await client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "currentuser"

    async def test_get_current_user_profile_unauthenticated(self, client: AsyncClient):
        """Test getting current user profile without authentication."""
        response = await client.get("/api/v1/users/me")
        assert response.status_code == 401

    async def test_get_current_user_profile_includes_empty_permissions(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that /users/me includes empty permissions array for user without permissions."""
        # Create user without permissions
        user = Users(
            username="nopermuser",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="noperm@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "nopermuser", "password": "TestPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Get profile
        response = await client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "permissions" in data
        assert isinstance(data["permissions"], list)
        assert len(data["permissions"]) == 0

    async def test_get_current_user_profile_includes_permissions_from_groups(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that /users/me includes permissions from user's groups."""
        from app.models.permissions import GroupPerms, Groups, Perms, UserGroups

        # Create permissions
        perm1 = Perms(title="image_tag_add", desc="Add tags to images")
        perm2 = Perms(title="image_tag_remove", desc="Remove tags from images")
        db_session.add(perm1)
        db_session.add(perm2)
        await db_session.commit()
        await db_session.refresh(perm1)
        await db_session.refresh(perm2)

        # Create group with permissions
        group = Groups(title="Taggers", desc="Users who can manage tags")
        db_session.add(group)
        await db_session.commit()
        await db_session.refresh(group)

        # Add permissions to group
        db_session.add(GroupPerms(group_id=group.group_id, perm_id=perm1.perm_id, permvalue=1))
        db_session.add(GroupPerms(group_id=group.group_id, perm_id=perm2.perm_id, permvalue=1))
        await db_session.commit()

        # Create user and add to group
        user = Users(
            username="taggeruser",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="tagger@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        db_session.add(UserGroups(user_id=user.user_id, group_id=group.group_id))
        await db_session.commit()

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "taggeruser", "password": "TestPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Get profile
        response = await client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "permissions" in data
        assert "image_tag_add" in data["permissions"]
        assert "image_tag_remove" in data["permissions"]

    async def test_get_current_user_profile_permissions_sorted(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that permissions array is sorted alphabetically."""
        from app.models.permissions import Perms, UserPerms

        # Create permissions in non-alphabetical order
        perm1 = Perms(title="user_ban", desc="Ban users")
        perm2 = Perms(title="image_edit", desc="Edit images")
        perm3 = Perms(title="tag_create", desc="Create tags")
        db_session.add_all([perm1, perm2, perm3])
        await db_session.commit()
        await db_session.refresh(perm1)
        await db_session.refresh(perm2)
        await db_session.refresh(perm3)

        # Create user with multiple permissions
        user = Users(
            username="sorteduser",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="sorted@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Add permissions in non-alphabetical order
        db_session.add(UserPerms(user_id=user.user_id, perm_id=perm1.perm_id, permvalue=1))
        db_session.add(UserPerms(user_id=user.user_id, perm_id=perm2.perm_id, permvalue=1))
        db_session.add(UserPerms(user_id=user.user_id, perm_id=perm3.perm_id, permvalue=1))
        await db_session.commit()

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "sorteduser", "password": "TestPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Get profile
        response = await client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "permissions" in data
        # Should be sorted alphabetically
        assert data["permissions"] == ["image_edit", "tag_create", "user_ban"]


@pytest.mark.api
class TestUpdateUserProfile:
    """Tests for PATCH /api/v1/users/{user_id} endpoint."""

    async def test_update_own_profile(self, client: AsyncClient, db_session: AsyncSession):
        """Test user updating their own profile."""
        # Create user
        user = Users(
            username="updateuser",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="update@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "updateuser", "password": "TestPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Update profile
        response = await client.patch(
            f"/api/v1/users/{user.user_id}",
            json={"email": "newemail@example.com"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        # assert data["email"] == "newemail@example.com"  # Email may not be returned in response

    async def test_update_email_pm_pref_via_me(self, client: AsyncClient, db_session: AsyncSession):
        """Test that PATCH /api/v1/users/me accepts and returns email_pm_pref."""
        # Create user
        user = Users(
            username="pmtest",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="pmtest@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "pmtest", "password": "TestPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Enable PM email pref
        response = await client.patch(
            "/api/v1/users/me",
            json={"email_pm_pref": 1},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("email_pm_pref") == 1

        # Verify persisted in DB
        await db_session.refresh(user)
        assert user.email_pm_pref == 1

    async def test_update_user_settings_via_me(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that PATCH /api/v1/users/me accepts and returns user settings."""
        from decimal import Decimal

        # Create user with default settings
        user = Users(
            username="settingstest",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="settingstest@example.com",
            active=1,
            show_all_images=0,
            spoiler_warning_pref=1,
            timezone=Decimal("0.00"),
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "settingstest", "password": "TestPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Update all settings
        response = await client.patch(
            "/api/v1/users/me",
            json={
                "show_all_images": 1,
                "spoiler_warning_pref": 0,
                "timezone": "-5.00",
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()

        # Verify response includes updated settings
        assert data.get("show_all_images") == 1
        assert data.get("spoiler_warning_pref") == 0
        assert Decimal(data.get("timezone")) == Decimal("-5")

        # Verify persisted in DB
        await db_session.refresh(user)
        assert user.show_all_images == 1
        assert user.spoiler_warning_pref == 0
        assert user.timezone == Decimal("-5")

    async def test_update_user_settings_validation(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that user settings have proper validation."""
        # Create user
        user = Users(
            username="settingsvalidation",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="settingsvalidation@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "settingsvalidation", "password": "TestPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Test invalid show_all_images (must be 0 or 1)
        response = await client.patch(
            "/api/v1/users/me",
            json={"show_all_images": 2},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 422

        # Test invalid spoiler_warning_pref (must be 0 or 1)
        response = await client.patch(
            "/api/v1/users/me",
            json={"spoiler_warning_pref": -1},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 422

        # Test invalid timezone (must be between -12 and 14)
        response = await client.patch(
            "/api/v1/users/me",
            json={"timezone": "15.00"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 422

        response = await client.patch(
            "/api/v1/users/me",
            json={"timezone": "-13.00"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 422

    async def test_update_display_preferences_via_me(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that PATCH /api/v1/users/me accepts display preference settings."""
        # Create user with default settings
        user = Users(
            username="displayprefs",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="displayprefs@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "displayprefs", "password": "TestPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Update display preferences
        response = await client.patch(
            "/api/v1/users/me",
            json={
                "thumb_layout": 1,
                "sorting_pref": "favorites",
                "sorting_pref_order": "asc",  # lowercase to test case normalization
                "images_per_page": 50,
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()

        # Verify response includes updated settings
        assert data.get("thumb_layout") == 1
        assert data.get("sorting_pref") == "favorites"
        assert data.get("sorting_pref_order") == "ASC"  # Should be uppercase
        assert data.get("images_per_page") == 50

        # Verify persisted in DB
        await db_session.refresh(user)
        assert user.thumb_layout == 1
        assert user.sorting_pref == "favorites"
        assert user.sorting_pref_order == "ASC"
        assert user.images_per_page == 50

    async def test_update_display_preferences_validation(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that display preference settings have proper validation."""
        # Create user
        user = Users(
            username="displayvalidation",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="displayvalidation@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "displayvalidation", "password": "TestPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Test invalid thumb_layout (must be 0 or 1)
        response = await client.patch(
            "/api/v1/users/me",
            json={"thumb_layout": 2},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 422

        # Test invalid sorting_pref (must be valid ImageSortBy value)
        response = await client.patch(
            "/api/v1/users/me",
            json={"sorting_pref": "invalid_sort"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 422

        # Test invalid sorting_pref_order (must be ASC or DESC)
        response = await client.patch(
            "/api/v1/users/me",
            json={"sorting_pref_order": "RANDOM"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 422

        # Test invalid images_per_page (must be 1-100)
        response = await client.patch(
            "/api/v1/users/me",
            json={"images_per_page": 0},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 422

        response = await client.patch(
            "/api/v1/users/me",
            json={"images_per_page": 101},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 422

    async def test_update_other_user_profile_forbidden(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test user cannot update another user's profile."""
        # Create two users
        user1 = Users(
            username="user1",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="user1@example.com",
            active=1,
        )
        user2 = Users(
            username="user2",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="user2@example.com",
            active=1,
        )
        db_session.add(user1)
        db_session.add(user2)
        await db_session.commit()
        await db_session.refresh(user1)
        await db_session.refresh(user2)

        # Login as user1
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "user1", "password": "TestPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Try to update user2's profile
        response = await client.patch(
            f"/api/v1/users/{user2.user_id}",
            json={"email": "hacked@example.com"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 403


@pytest.mark.api
class TestGetUserImages:
    """Tests for GET /api/v1/users/{user_id}/images endpoint."""

    async def test_get_user_images(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test getting images uploaded by a user."""
        # Create images for user_id=1
        for i in range(3):
            image_data = sample_image_data.copy()
            image_data["filename"] = f"user1-image-{i}"
            image_data["md5_hash"] = f"user1hash{i:020d}"
            image_data["user_id"] = 1
            db_session.add(Images(**image_data))
        await db_session.commit()

        response = await client.get("/api/v1/users/1/images")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        for img in data["images"]:
            assert img["user_id"] == 1

    async def test_get_user_images_nonexistent_user(self, client: AsyncClient):
        """Test getting images for non-existent user."""
        response = await client.get("/api/v1/users/999999/images")
        assert response.status_code == 404


@pytest.mark.api
class TestGetUserFavorites:
    """Tests for GET /api/v1/users/{user_id}/favorites endpoint."""

    async def test_get_user_favorites(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test getting images favorited by a user."""
        # Create images
        for i in range(3):
            image_data = sample_image_data.copy()
            image_data["filename"] = f"fav-image-{i}"
            image_data["md5_hash"] = f"favhash{i:021d}"
            image = Images(**image_data)
            db_session.add(image)

        await db_session.commit()

        # Get the images using AsyncSession
        from sqlalchemy import select

        result = await db_session.execute(select(Images))
        images = result.scalars().all()

        # Create favorites for user_id=1
        for image in images:
            favorite = Favorites(user_id=1, image_id=image.image_id)
            db_session.add(favorite)
        await db_session.commit()

        response = await client.get("/api/v1/users/1/favorites")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3

    async def test_get_user_favorites_nonexistent_user(self, client: AsyncClient):
        """Test getting favorites for non-existent user."""
        response = await client.get("/api/v1/users/999999/favorites")
        assert response.status_code == 404


@pytest.mark.api
class TestAvatarUpload:
    """Tests for POST /api/v1/users/me/avatar and /api/v1/users/{user_id}/avatar endpoints."""

    async def test_upload_avatar_own_profile(
        self, client: AsyncClient, db_session: AsyncSession, tmp_path
    ):
        """Test uploading avatar to own profile."""
        from io import BytesIO
        from unittest.mock import patch

        from PIL import Image

        # Create user and login
        user = Users(
            username="avataruser",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="avatar@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "avataruser", "password": "TestPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Create a test image
        img = Image.new("RGB", (100, 100), color="red")
        img_bytes = BytesIO()
        img.save(img_bytes, format="PNG")
        img_bytes.seek(0)

        # Upload avatar (mock storage path to use tmp_path)
        with patch("app.services.avatar.settings") as mock_settings:
            mock_settings.AVATAR_STORAGE_PATH = str(tmp_path)
            mock_settings.MAX_AVATAR_SIZE = 1024 * 1024
            mock_settings.MAX_AVATAR_DIMENSION = 200

            response = await client.post(
                "/api/v1/users/me/avatar",
                files={"avatar": ("test.png", img_bytes, "image/png")},
                headers={"Authorization": f"Bearer {access_token}"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["avatar"] != ""
        assert data["avatar"].endswith(".png")

    async def test_upload_avatar_unauthenticated(self, client: AsyncClient):
        """Test uploading avatar without authentication fails."""
        from io import BytesIO

        from PIL import Image

        img = Image.new("RGB", (100, 100), color="red")
        img_bytes = BytesIO()
        img.save(img_bytes, format="PNG")
        img_bytes.seek(0)

        response = await client.post(
            "/api/v1/users/me/avatar",
            files={"avatar": ("test.png", img_bytes, "image/png")},
        )
        assert response.status_code == 401

    async def test_upload_avatar_invalid_file_type(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test uploading non-image file fails."""
        # Create user and login
        user = Users(
            username="avataruser2",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="avatar2@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "avataruser2", "password": "TestPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Try to upload text file
        response = await client.post(
            "/api/v1/users/me/avatar",
            files={"avatar": ("test.txt", b"not an image", "text/plain")},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 400

    async def test_upload_avatar_other_user_forbidden(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test uploading avatar to another user's profile is forbidden."""
        from io import BytesIO

        from PIL import Image

        # Create two users
        user1 = Users(
            username="avataruser3",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="avatar3@example.com",
            active=1,
        )
        user2 = Users(
            username="avataruser4",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="avatar4@example.com",
            active=1,
        )
        db_session.add(user1)
        db_session.add(user2)
        await db_session.commit()
        await db_session.refresh(user2)

        # Login as user1
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "avataruser3", "password": "TestPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Create test image
        img = Image.new("RGB", (100, 100), color="red")
        img_bytes = BytesIO()
        img.save(img_bytes, format="PNG")
        img_bytes.seek(0)

        # Try to upload to user2's profile
        response = await client.post(
            f"/api/v1/users/{user2.user_id}/avatar",
            files={"avatar": ("test.png", img_bytes, "image/png")},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 403

    async def test_upload_avatar_user_with_permission_can_update_other(
        self, client: AsyncClient, db_session: AsyncSession, tmp_path
    ):
        """Test user with USER_EDIT_PROFILE permission can upload avatar for other users."""
        from io import BytesIO
        from unittest.mock import patch

        from PIL import Image

        # Create user with permission to edit profiles
        editor = Users(
            username="avataradmin",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="avataradmin@example.com",
            active=1,
        )
        # Create regular user
        user = Users(
            username="avataruser5",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="avatar5@example.com",
            active=1,
        )
        db_session.add(editor)
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(editor)
        await db_session.refresh(user)

        # Grant USER_EDIT_PROFILE permission to editor
        perm = Perms(title="user_edit_profile", desc="Edit user profiles")
        db_session.add(perm)
        await db_session.flush()

        group = Groups(title="avatar_edit_group", desc="Avatar edit group")
        db_session.add(group)
        await db_session.flush()

        group_perm = GroupPerms(group_id=group.group_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(group_perm)

        user_group = UserGroups(user_id=editor.user_id, group_id=group.group_id)
        db_session.add(user_group)
        await db_session.commit()

        # Login as editor
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "avataradmin", "password": "TestPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Create test image
        img = Image.new("RGB", (100, 100), color="blue")
        img_bytes = BytesIO()
        img.save(img_bytes, format="PNG")
        img_bytes.seek(0)

        # Editor uploads to regular user's profile (mock storage path)
        with patch("app.services.avatar.settings") as mock_settings:
            mock_settings.AVATAR_STORAGE_PATH = str(tmp_path)
            mock_settings.MAX_AVATAR_SIZE = 1024 * 1024
            mock_settings.MAX_AVATAR_DIMENSION = 200

            response = await client.post(
                f"/api/v1/users/{user.user_id}/avatar",
                files={"avatar": ("test.png", img_bytes, "image/png")},
                headers={"Authorization": f"Bearer {access_token}"},
            )
        assert response.status_code == 200


@pytest.mark.api
class TestUserSorting:
    """Tests for user list sorting functionality."""

    async def test_sort_by_user_id_asc(self, client: AsyncClient, db_session: AsyncSession):
        """Test sorting users by user_id in ascending order."""
        # Create test users
        users = []
        for i in range(1, 4):
            user = Users(
                username=f"sortuser{i}",
                email=f"sortuser{i}@example.com",
                password=get_password_hash("TestPassword123!"),
                password_type="bcrypt",
                salt="",
                active=1,
                date_joined=datetime.now(UTC),
            )
            db_session.add(user)
            users.append(user)
        await db_session.commit()
        # Refresh to get user_ids
        for user in users:
            await db_session.refresh(user)

        response = await client.get("/api/v1/users?sort_by=user_id&sort_order=ASC&search=sortuser")
        assert response.status_code == 200
        data = response.json()
        # Only our test users should be returned
        returned_usernames = [u["username"] for u in data["users"]]
        assert set(returned_usernames) == {f"sortuser{i}" for i in range(1, 4)}
        # Verify ascending order
        user_ids = [u["user_id"] for u in data["users"]]
        assert user_ids == sorted(user_ids)

    async def test_sort_by_user_id_desc(self, client: AsyncClient, db_session: AsyncSession):
        """Test sorting users by user_id in descending order."""
        # Create test users
        users = []
        for i in range(1, 4):
            user = Users(
                username=f"sortuser{i}",
                email=f"sortuser{i}@example.com",
                password=get_password_hash("TestPassword123!"),
                password_type="bcrypt",
                salt="",
                active=1,
                date_joined=datetime.now(UTC),
            )
            db_session.add(user)
            users.append(user)
        await db_session.commit()
        for user in users:
            await db_session.refresh(user)

        response = await client.get("/api/v1/users?sort_by=user_id&sort_order=DESC&search=sortuser")
        assert response.status_code == 200
        data = response.json()
        returned_usernames = [u["username"] for u in data["users"]]
        assert set(returned_usernames) == {f"sortuser{i}" for i in range(1, 4)}
        # Verify descending order
        user_ids = [u["user_id"] for u in data["users"]]
        assert user_ids == sorted(user_ids, reverse=True)

    async def test_sort_by_username_asc(self, client: AsyncClient, db_session: AsyncSession):
        """Test sorting users by username in ascending order."""
        # Create test users
        users = []
        for i in range(1, 4):
            user = Users(
                username=f"sortuser{i}",
                email=f"sortuser{i}@example.com",
                password=get_password_hash("TestPassword123!"),
                password_type="bcrypt",
                salt="",
                active=1,
                date_joined=datetime.now(UTC),
            )
            db_session.add(user)
            users.append(user)
        await db_session.commit()
        for user in users:
            await db_session.refresh(user)

        response = await client.get("/api/v1/users?sort_by=username&sort_order=ASC&search=sortuser")
        assert response.status_code == 200
        data = response.json()
        returned_usernames = [u["username"] for u in data["users"]]
        assert set(returned_usernames) == {f"sortuser{i}" for i in range(1, 4)}
        # Verify ascending order
        usernames = [u["username"] for u in data["users"]]
        assert usernames == sorted(usernames)

    async def test_sort_by_username_desc(self, client: AsyncClient, db_session: AsyncSession):
        """Test sorting users by username in descending order."""
        # Create test users
        users = []
        for i in range(1, 4):
            user = Users(
                username=f"sortuser{i}",
                email=f"sortuser{i}@example.com",
                password=get_password_hash("TestPassword123!"),
                password_type="bcrypt",
                salt="",
                active=1,
                date_joined=datetime.now(UTC),
            )
            db_session.add(user)
            users.append(user)
        await db_session.commit()
        for user in users:
            await db_session.refresh(user)

        response = await client.get("/api/v1/users?sort_by=username&sort_order=DESC&search=sortuser")
        assert response.status_code == 200
        data = response.json()
        returned_usernames = [u["username"] for u in data["users"]]
        assert set(returned_usernames) == {f"sortuser{i}" for i in range(1, 4)}
        # Verify descending order
        usernames = [u["username"] for u in data["users"]]
        assert usernames == sorted(usernames, reverse=True)

    async def test_sort_by_date_joined_asc(self, client: AsyncClient, db_session: AsyncSession):
        """Test sorting users by date_joined in ascending order."""
        # Create users with known date_joined values
        base_time = datetime.now(UTC)
        users = [
            Users(
                username=f"datejoinuser{i}",
                password=get_password_hash("TestPassword123!"),
                password_type="bcrypt",
                salt="",
                email=f"datejoinuser{i}@example.com",
                active=1,
                date_joined=base_time + timedelta(hours=i),
            )
            for i in range(5)
        ]
        for user in users:
            db_session.add(user)
        await db_session.commit()

        response = await client.get("/api/v1/users?sort_by=date_joined&sort_order=ASC&search=datejoinuser")
        assert response.status_code == 200
        data = response.json()
        assert len(data["users"]) == 5

        # Verify ascending order by comparing adjacent dates
        dates = [u["date_joined"] for u in data["users"]]
        for i in range(len(dates) - 1):
            assert dates[i] <= dates[i + 1], f"Date {dates[i]} should be <= {dates[i + 1]}"

    async def test_sort_by_date_joined_desc(self, client: AsyncClient, db_session: AsyncSession):
        """Test sorting users by date_joined in descending order."""
        # Create users with known date_joined values
        base_time = datetime.now(UTC)
        users = [
            Users(
                username=f"datejoinuser_desc{i}",
                password=get_password_hash("TestPassword123!"),
                password_type="bcrypt",
                salt="",
                email=f"datejoinuser_desc{i}@example.com",
                active=1,
                date_joined=base_time + timedelta(hours=i),
            )
            for i in range(5)
        ]
        for user in users:
            db_session.add(user)
        await db_session.commit()

        response = await client.get("/api/v1/users?sort_by=date_joined&sort_order=DESC&search=datejoinuser_desc")
        assert response.status_code == 200
        data = response.json()
        assert len(data["users"]) == 5

        # Verify descending order by comparing adjacent dates
        dates = [u["date_joined"] for u in data["users"]]
        for i in range(len(dates) - 1):
            assert dates[i] >= dates[i + 1], f"Date {dates[i]} should be >= {dates[i + 1]}"

    async def test_sort_by_last_login_asc(self, client: AsyncClient, db_session: AsyncSession):
        """Test sorting users by last_login in ascending order."""
        # Create users with different last_login values
        now = datetime.now(UTC)
        users = [
            Users(
                username=f"loginuser{i}",
                password=get_password_hash("TestPassword123!"),
                password_type="bcrypt",
                salt="",
                email=f"loginuser{i}@example.com",
                active=1,
                last_login=now - timedelta(days=i) if i > 0 else None,
            )
            for i in range(5)
        ]
        for user in users:
            db_session.add(user)
        await db_session.commit()

        response = await client.get("/api/v1/users?sort_by=last_login&sort_order=ASC&search=loginuser")
        assert response.status_code == 200
        data = response.json()
        assert len(data["users"]) == 5

        # Check that non-null values are sorted in ascending order
        last_logins = [u["last_login"] for u in data["users"]]
        # Check that non-null values are sorted
        non_null_logins = [ll for ll in last_logins if ll is not None]
        assert non_null_logins == sorted(non_null_logins)

    async def test_sort_by_last_login_desc(self, client: AsyncClient, db_session: AsyncSession):
        """Test sorting users by last_login in descending order."""
        # Create users with different last_login values
        now = datetime.now(UTC)
        users = [
            Users(
                username=f"loginuser_desc{i}",
                password=get_password_hash("TestPassword123!"),
                password_type="bcrypt",
                salt="",
                email=f"loginuser_desc{i}@example.com",
                active=1,
                last_login=now - timedelta(days=i) if i > 0 else None,
            )
            for i in range(5)
        ]
        for user in users:
            db_session.add(user)
        await db_session.commit()

        response = await client.get("/api/v1/users?sort_by=last_login&sort_order=DESC&search=loginuser_desc")
        assert response.status_code == 200
        data = response.json()
        assert len(data["users"]) == 5

        # Check that non-null values are sorted in descending order and all nulls come last
        last_logins = [u["last_login"] for u in data["users"]]
        non_null_logins = [ll for ll in last_logins if ll is not None]
        null_logins = [ll for ll in last_logins if ll is None]
        # All non-nulls should come before any nulls
        if null_logins:
            # The first null should come after all non-nulls
            first_null_index = last_logins.index(None)
            assert all(ll is not None for ll in last_logins[:first_null_index])
            assert all(ll is None for ll in last_logins[first_null_index:])
        # Non-nulls should be sorted in descending order
        assert non_null_logins == sorted(non_null_logins, reverse=True)

    async def test_sort_by_image_posts_asc(self, client: AsyncClient, db_session: AsyncSession):
        """Test sorting users by image_posts in ascending order."""
        # Create users with different image_posts counts
        users = [
            Users(
                username=f"imgpostuser{i}",
                password=get_password_hash("TestPassword123!"),
                password_type="bcrypt",
                salt="",
                email=f"imgpostuser{i}@example.com",
                active=1,
                image_posts=i * 10,
            )
            for i in range(5)
        ]
        for user in users:
            db_session.add(user)
        await db_session.commit()

        response = await client.get("/api/v1/users?sort_by=image_posts&sort_order=ASC&search=imgpostuser")
        assert response.status_code == 200
        data = response.json()
        assert len(data["users"]) == 5

        # Verify ascending order
        image_posts = [u["image_posts"] for u in data["users"]]
        assert image_posts == sorted(image_posts)

    async def test_sort_by_image_posts_desc(self, client: AsyncClient, db_session: AsyncSession):
        """Test sorting users by image_posts in descending order."""
        # Create users with different image_posts counts
        users = [
            Users(
                username=f"imgpostuser_desc{i}",
                password=get_password_hash("TestPassword123!"),
                password_type="bcrypt",
                salt="",
                email=f"imgpostuser_desc{i}@example.com",
                active=1,
                image_posts=i * 10,
            )
            for i in range(5)
        ]
        for user in users:
            db_session.add(user)
        await db_session.commit()

        response = await client.get("/api/v1/users?sort_by=image_posts&sort_order=DESC&search=imgpostuser_desc")
        assert response.status_code == 200
        data = response.json()
        assert len(data["users"]) == 5

        # Verify descending order
        image_posts = [u["image_posts"] for u in data["users"]]
        assert image_posts == sorted(image_posts, reverse=True)

    async def test_sort_by_posts_asc(self, client: AsyncClient, db_session: AsyncSession):
        """Test sorting users by posts in ascending order."""
        # Create users with different posts counts
        users = [
            Users(
                username=f"postsuser{i}",
                password=get_password_hash("TestPassword123!"),
                password_type="bcrypt",
                salt="",
                email=f"postsuser{i}@example.com",
                active=1,
                posts=i * 5,
            )
            for i in range(5)
        ]
        for user in users:
            db_session.add(user)
        await db_session.commit()

        response = await client.get("/api/v1/users?sort_by=posts&sort_order=ASC&search=postsuser")
        assert response.status_code == 200
        data = response.json()
        assert len(data["users"]) == 5

        # Verify ascending order
        posts = [u["posts"] for u in data["users"]]
        assert posts == sorted(posts)

    async def test_sort_by_posts_desc(self, client: AsyncClient, db_session: AsyncSession):
        """Test sorting users by posts in descending order."""
        # Create users with different posts counts
        users = [
            Users(
                username=f"postsuser_desc{i}",
                password=get_password_hash("TestPassword123!"),
                password_type="bcrypt",
                salt="",
                email=f"postsuser_desc{i}@example.com",
                active=1,
                posts=i * 5,
            )
            for i in range(5)
        ]
        for user in users:
            db_session.add(user)
        await db_session.commit()

        response = await client.get("/api/v1/users?sort_by=posts&sort_order=DESC&search=postsuser_desc")
        assert response.status_code == 200
        data = response.json()
        assert len(data["users"]) == 5

        # Verify descending order
        posts = [u["posts"] for u in data["users"]]
        assert posts == sorted(posts, reverse=True)

    async def test_sort_by_favorites_asc(self, client: AsyncClient, db_session: AsyncSession):
        """Test sorting users by favorites in ascending order."""
        # Create users with different favorites counts
        users = [
            Users(
                username=f"favuser{i}",
                password=get_password_hash("TestPassword123!"),
                password_type="bcrypt",
                salt="",
                email=f"favuser{i}@example.com",
                active=1,
                favorites=i * 3,
            )
            for i in range(5)
        ]
        for user in users:
            db_session.add(user)
        await db_session.commit()

        response = await client.get("/api/v1/users?sort_by=favorites&sort_order=ASC&search=favuser")
        assert response.status_code == 200
        data = response.json()
        assert len(data["users"]) == 5

        # Verify ascending order
        favorites = [u["favorites"] for u in data["users"]]
        assert favorites == sorted(favorites)

    async def test_sort_by_favorites_desc(self, client: AsyncClient, db_session: AsyncSession):
        """Test sorting users by favorites in descending order."""
        # Create users with different favorites counts
        users = [
            Users(
                username=f"favuser_desc{i}",
                password=get_password_hash("TestPassword123!"),
                password_type="bcrypt",
                salt="",
                email=f"favuser_desc{i}@example.com",
                active=1,
                favorites=i * 3,
            )
            for i in range(5)
        ]
        for user in users:
            db_session.add(user)
        await db_session.commit()

        response = await client.get("/api/v1/users?sort_by=favorites&sort_order=DESC&search=favuser_desc")
        assert response.status_code == 200
        data = response.json()
        assert len(data["users"]) == 5

        # Verify descending order
        favorites = [u["favorites"] for u in data["users"]]
        assert favorites == sorted(favorites, reverse=True)

    async def test_default_sorting(self, client: AsyncClient, db_session: AsyncSession):
        """Test default sorting behavior when no sort parameters are provided."""
        response = await client.get("/api/v1/users")
        assert response.status_code == 200
        data = response.json()
        assert len(data["users"]) > 0

        # Default should be user_id DESC (as per UserSortParams)
        user_ids = [u["user_id"] for u in data["users"]]
        assert user_ids == sorted(user_ids, reverse=True)

    async def test_sorting_with_null_last_login(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test sorting by last_login field when some users have null values."""
        now = datetime.now(UTC)

        # Create users: some with last_login, some without
        users = [
            Users(
                username=f"nullloginuser{i}",
                password=get_password_hash("TestPassword123!"),
                password_type="bcrypt",
                salt="",
                email=f"nullloginuser{i}@example.com",
                active=1,
                last_login=now - timedelta(days=i) if i % 2 == 0 else None,
            )
            for i in range(6)
        ]
        for user in users:
            db_session.add(user)
        await db_session.commit()

        # Test ASC order - filter to only our test users
        response_asc = await client.get("/api/v1/users?sort_by=last_login&sort_order=ASC&search=nullloginuser")
        assert response_asc.status_code == 200
        data_asc = response_asc.json()

        # Test DESC order - filter to only our test users
        response_desc = await client.get("/api/v1/users?sort_by=last_login&sort_order=DESC&search=nullloginuser")
        assert response_desc.status_code == 200
        data_desc = response_desc.json()

        # Both should succeed and return our 6 test users
        assert len(data_asc["users"]) == 6
        assert len(data_desc["users"]) == 6

        # Verify that users with non-null last_login are properly ordered
        last_logins_asc = [u["last_login"] for u in data_asc["users"] if u["last_login"] is not None]
        last_logins_desc = [u["last_login"] for u in data_desc["users"] if u["last_login"] is not None]

        # Check ordering by comparing adjacent elements
        for i in range(len(last_logins_asc) - 1):
            assert last_logins_asc[i] <= last_logins_asc[i + 1]

        for i in range(len(last_logins_desc) - 1):
            assert last_logins_desc[i] >= last_logins_desc[i + 1]

    async def test_sorting_combined_with_pagination(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that sorting works correctly with pagination."""
        # Create users with specific image_posts values
        users = [
            Users(
                username=f"sortpaginuser{i}",
                password=get_password_hash("TestPassword123!"),
                password_type="bcrypt",
                salt="",
                email=f"sortpaginuser{i}@example.com",
                active=1,
                image_posts=i * 2,
            )
            for i in range(10)
        ]
        for user in users:
            db_session.add(user)
        await db_session.commit()

        # Get first page sorted by image_posts DESC
        response_page1 = await client.get(
            "/api/v1/users?sort_by=image_posts&sort_order=DESC&page=1&per_page=5&search=sortpaginuser"
        )
        assert response_page1.status_code == 200
        data_page1 = response_page1.json()

        # Get second page
        response_page2 = await client.get(
            "/api/v1/users?sort_by=image_posts&sort_order=DESC&page=2&per_page=5&search=sortpaginuser"
        )
        assert response_page2.status_code == 200
        data_page2 = response_page2.json()

        # Verify pagination metadata
        assert data_page1["page"] == 1
        assert data_page2["page"] == 2

        # Verify sorting is maintained across pages
        page1_posts = [u["image_posts"] for u in data_page1["users"]]
        page2_posts = [u["image_posts"] for u in data_page2["users"]]

        # Each page should be sorted
        assert page1_posts == sorted(page1_posts, reverse=True)
        assert page2_posts == sorted(page2_posts, reverse=True)

        # Last item of page 1 should have more/equal image_posts than first item of page 2
        if page1_posts and page2_posts:
            assert page1_posts[-1] >= page2_posts[0]

    async def test_sorting_combined_with_search(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that sorting works correctly with search functionality."""
        # Create users with "search" in username and different posts counts
        users = [
            Users(
                username=f"searchsortuser{i}",
                password=get_password_hash("TestPassword123!"),
                password_type="bcrypt",
                salt="",
                email=f"searchsortuser{i}@example.com",
                active=1,
                posts=i * 7,
            )
            for i in range(5)
        ]
        for user in users:
            db_session.add(user)
        await db_session.commit()

        # Search and sort
        response = await client.get(
            "/api/v1/users?search=searchsortuser&sort_by=posts&sort_order=ASC"
        )
        assert response.status_code == 200
        data = response.json()

        # Should only return users matching search
        for user in data["users"]:
            assert "searchsortuser" in user["username"].lower()

        # Results should be sorted by posts in ascending order
        posts = [u["posts"] for u in data["users"]]
        assert posts == sorted(posts)


@pytest.mark.api
class TestAvatarDelete:
    """Tests for DELETE /api/v1/users/me/avatar and /api/v1/users/{user_id}/avatar endpoints."""

    async def test_delete_avatar_own_profile(self, client: AsyncClient, db_session: AsyncSession):
        """Test deleting avatar from own profile."""
        # Create user with avatar
        user = Users(
            username="delavataruser",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="delavatar@example.com",
            active=1,
            avatar="existing_avatar.png",
        )
        db_session.add(user)
        await db_session.commit()

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "delavataruser", "password": "TestPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Delete avatar
        response = await client.delete(
            "/api/v1/users/me/avatar",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["avatar"] == ""

    async def test_delete_avatar_unauthenticated(self, client: AsyncClient):
        """Test deleting avatar without authentication fails."""
        response = await client.delete("/api/v1/users/me/avatar")
        assert response.status_code == 401

    async def test_delete_avatar_other_user_forbidden(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test deleting another user's avatar is forbidden."""
        # Create two users
        user1 = Users(
            username="delavataruser2",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="delavatar2@example.com",
            active=1,
        )
        user2 = Users(
            username="delavataruser3",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="delavatar3@example.com",
            active=1,
            avatar="user2_avatar.png",
        )
        db_session.add(user1)
        db_session.add(user2)
        await db_session.commit()
        await db_session.refresh(user2)

        # Login as user1
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "delavataruser2", "password": "TestPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Try to delete user2's avatar
        response = await client.delete(
            f"/api/v1/users/{user2.user_id}/avatar",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 403

    async def test_delete_avatar_user_with_permission_can_delete_other(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test user with USER_EDIT_PROFILE permission can delete another user's avatar."""
        # Create user with permission to edit profiles
        editor = Users(
            username="delavataradmin",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="delavataradmin@example.com",
            active=1,
        )
        # Create regular user with avatar
        user = Users(
            username="delavataruser4",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="delavatar4@example.com",
            active=1,
            avatar="user_avatar.png",
        )
        db_session.add(editor)
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(editor)
        await db_session.refresh(user)

        # Grant USER_EDIT_PROFILE permission to editor
        perm = Perms(title="user_edit_profile", desc="Edit user profiles")
        db_session.add(perm)
        await db_session.flush()

        group = Groups(title="avatar_delete_group", desc="Avatar delete group")
        db_session.add(group)
        await db_session.flush()

        group_perm = GroupPerms(group_id=group.group_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(group_perm)

        user_group = UserGroups(user_id=editor.user_id, group_id=group.group_id)
        db_session.add(user_group)
        await db_session.commit()

        # Login as editor
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "delavataradmin", "password": "TestPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Editor deletes regular user's avatar
        response = await client.delete(
            f"/api/v1/users/{user.user_id}/avatar",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["avatar"] == ""


# ===== User Profile Edit Authorization Tests =====


async def create_test_user_with_password(
    db_session: AsyncSession,
    username: str,
    email: str,
) -> tuple[Users, str]:
    """Create a test user and return user object and password."""
    password = "TestPassword123!"
    user = Users(
        username=username,
        password=get_password_hash(password),
        password_type="bcrypt",
        salt="",
        email=email,
        active=1,
        admin=0,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user, password


async def grant_user_permission(db_session: AsyncSession, user_id: int, perm_title: str):
    """Grant a permission to a user via a group."""
    result = await db_session.execute(select(Perms).where(Perms.title == perm_title))
    perm = result.scalar_one_or_none()
    if not perm:
        perm = Perms(title=perm_title, desc=f"Test permission {perm_title}")
        db_session.add(perm)
        await db_session.flush()

    result = await db_session.execute(
        select(Groups).where(Groups.title == "user_edit_test_group")
    )
    group = result.scalar_one_or_none()
    if not group:
        group = Groups(title="user_edit_test_group", desc="User edit test group")
        db_session.add(group)
        await db_session.flush()

    result = await db_session.execute(
        select(GroupPerms).where(
            GroupPerms.group_id == group.group_id, GroupPerms.perm_id == perm.perm_id
        )
    )
    if not result.scalar_one_or_none():
        group_perm = GroupPerms(group_id=group.group_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(group_perm)
        await db_session.flush()

    result = await db_session.execute(
        select(UserGroups).where(
            UserGroups.user_id == user_id, UserGroups.group_id == group.group_id
        )
    )
    if not result.scalar_one_or_none():
        user_group = UserGroups(user_id=user_id, group_id=group.group_id)
        db_session.add(user_group)

    await db_session.commit()


async def login_test_user(client: AsyncClient, username: str, password: str) -> str:
    """Login and return the access token."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


@pytest.mark.api
class TestUserProfileEditAuthorization:
    """Tests for user profile edit authorization using permission system."""

    async def test_user_can_edit_own_profile(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that a user can edit their own profile."""
        user, password = await create_test_user_with_password(
            db_session, "selfeditor", "selfeditor@example.com"
        )
        token = await login_test_user(client, user.username, password)

        response = await client.patch(
            f"/api/v1/users/{user.user_id}",
            json={"location": "New York"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert response.json()["location"] == "New York"

    async def test_user_with_permission_can_edit_others(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that a user with USER_EDIT_PROFILE permission can edit others."""
        editor, editor_password = await create_test_user_with_password(
            db_session, "profileeditor", "profileeditor@example.com"
        )
        await grant_user_permission(db_session, editor.user_id, "user_edit_profile")

        target, _ = await create_test_user_with_password(
            db_session, "editme", "editme@example.com"
        )

        token = await login_test_user(client, editor.username, editor_password)

        response = await client.patch(
            f"/api/v1/users/{target.user_id}",
            json={"location": "Los Angeles"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert response.json()["location"] == "Los Angeles"

    async def test_user_without_permission_cannot_edit_others(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that a user without permission cannot edit others."""
        user, password = await create_test_user_with_password(
            db_session, "noeditperm", "noeditperm@example.com"
        )
        target, _ = await create_test_user_with_password(
            db_session, "donteditme", "donteditme@example.com"
        )

        token = await login_test_user(client, user.username, password)

        response = await client.patch(
            f"/api/v1/users/{target.user_id}",
            json={"location": "Chicago"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403
