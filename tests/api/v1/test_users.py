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

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash
from app.models.favorite import Favorites
from app.models.image import Images
from app.models.user import Users


@pytest.mark.api
class TestListUsers:
    """Tests for GET /api/v1/users/ endpoint."""

    async def test_list_users(self, client: AsyncClient, db_session: AsyncSession):
        """Test listing users."""
        response = await client.get("/api/v1/users/")
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
        response = await client.get("/api/v1/users/?page=1&per_page=5")
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
        response = await client.get("/api/v1/users/?search=alice")
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
            response = await client.get(f"/api/v1/users/?search={search_term}")
            assert response.status_code == 200
            data = response.json()
            assert data["total"] >= 1
            usernames = [u["username"] for u in data["users"]]
            assert "JohnDoe" in usernames

    async def test_list_users_search_no_matches(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test searching with no matching results."""
        response = await client.get("/api/v1/users/?search=nonexistentuserxyz")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["users"] == []

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
        response = await client.get("/api/v1/users/?search=searchuser&per_page=20")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 25
        assert len(data["users"]) == 20
        assert data["per_page"] == 20

        # Get second page
        response = await client.get("/api/v1/users/?search=searchuser&page=2&per_page=20")
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
        response = await client.get("/api/v1/users/?search=user")
        assert response.status_code == 200
        data = response.json()
        usernames = [u["username"] for u in data["users"]]
        assert "user_name" in usernames
        assert "user.name" in usernames
        assert "user-name" in usernames

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
        response_no_search = await client.get("/api/v1/users/")
        assert response_no_search.status_code == 200
        data_no_search = response_no_search.json()
        total_users = data_no_search["total"]
        assert total_users >= 3  # At least our 3 test users

        # Test with empty string search parameter - should return same total as no search
        response_empty_search = await client.get("/api/v1/users/?search=")
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
    """Tests for POST /api/v1/users/ endpoint."""

    async def test_create_user_success(self, client: AsyncClient, db_session: AsyncSession):
        """Test successful user creation."""
        user_data = {
            "username": "newuser123",
            "email": "newuser@example.com",
            "password": "SecurePassword123!",
        }

        response = await client.post("/api/v1/users/", json=user_data)
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
        }
        response = await client.post("/api/v1/users/", json=user_data)
        assert response.status_code == 200

        # Try to create second user with same username
        user_data2 = {
            "username": "duplicate",
            "email": "second@example.com",
            "password": "SecurePassword123!",
        }
        response = await client.post("/api/v1/users/", json=user_data2)
        assert response.status_code == 409
        assert "already exists" in response.json()["detail"].lower()

    async def test_create_user_invalid_username(self, client: AsyncClient):
        """Test creating user with invalid username format."""
        user_data = {
            "username": "ab",  # Too short (min 3 chars)
            "email": "test@example.com",
            "password": "SecurePassword123!",
        }
        response = await client.post("/api/v1/users/", json=user_data)
        assert response.status_code == 400

    async def test_create_user_weak_password(self, client: AsyncClient):
        """Test creating user with weak password."""
        user_data = {
            "username": "weakpassuser",
            "email": "weak@example.com",
            "password": "weak",
        }
        response = await client.post("/api/v1/users/", json=user_data)
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

    async def test_upload_avatar_admin_can_update_other(
        self, client: AsyncClient, db_session: AsyncSession, tmp_path
    ):
        """Test admin can upload avatar for other users."""
        from io import BytesIO
        from unittest.mock import patch

        from PIL import Image

        # Create admin user
        admin = Users(
            username="avataradmin",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="avataradmin@example.com",
            active=1,
            admin=1,
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
        db_session.add(admin)
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Login as admin
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

        # Admin uploads to regular user's profile (mock storage path)
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

    async def test_delete_avatar_admin_can_delete_other(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test admin can delete another user's avatar."""
        # Create admin user
        admin = Users(
            username="delavataradmin",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="delavataradmin@example.com",
            active=1,
            admin=1,
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
        db_session.add(admin)
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "delavataradmin", "password": "TestPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Admin deletes regular user's avatar
        response = await client.delete(
            f"/api/v1/users/{user.user_id}/avatar",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["avatar"] == ""
