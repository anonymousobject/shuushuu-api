"""
API tests for the review (voting) system.

Tests cover:
- Review management endpoints (list, get, create, vote, close, extend)
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, ReviewOutcome, ReviewStatus
from app.core.security import get_password_hash
from app.models.image import Images
from app.models.image_review import ImageReviews
from app.models.permissions import GroupPerms, Groups, Perms, UserGroups
from app.models.review_vote import ReviewVotes
from app.models.user import Users


async def create_auth_user(
    db_session: AsyncSession,
    username: str = "authuser",
    email: str = "auth@example.com",
    admin: bool = False,
) -> tuple[Users, str]:
    """Create a user and return the user object and password."""
    password = "TestPassword123!"
    user = Users(
        username=username,
        password=get_password_hash(password),
        password_type="bcrypt",
        salt="",
        email=email,
        active=1,
        admin=1 if admin else 0,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user, password


async def login_user(client: AsyncClient, username: str, password: str) -> str:
    """Login and return the access token."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


async def create_test_image(db_session: AsyncSession, user_id: int) -> Images:
    """Create a test image."""
    image = Images(
        filename="test-review-image",
        ext="jpg",
        original_filename="test.jpg",
        md5_hash=f"reviewtesthash{user_id:010d}",
        filesize=123456,
        width=1920,
        height=1080,
        user_id=user_id,
        status=ImageStatus.ACTIVE,
        locked=0,
    )
    db_session.add(image)
    await db_session.commit()
    await db_session.refresh(image)
    return image


async def grant_permission(db_session: AsyncSession, user_id: int, perm_title: str):
    """Grant a permission to a user via a group."""
    result = await db_session.execute(select(Perms).where(Perms.title == perm_title))
    perm = result.scalar_one_or_none()
    if not perm:
        perm = Perms(title=perm_title, desc=f"Test permission {perm_title}")
        db_session.add(perm)
        await db_session.flush()

    result = await db_session.execute(select(Groups).where(Groups.title == "test_admin"))
    group = result.scalar_one_or_none()
    if not group:
        group = Groups(title="test_admin", desc="Test admin group")
        db_session.add(group)
        await db_session.flush()

    result = await db_session.execute(
        select(GroupPerms).where(
            GroupPerms.group_id == group.group_id,
            GroupPerms.perm_id == perm.perm_id,
        )
    )
    if not result.scalar_one_or_none():
        group_perm = GroupPerms(group_id=group.group_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(group_perm)

    result = await db_session.execute(
        select(UserGroups).where(
            UserGroups.user_id == user_id,
            UserGroups.group_id == group.group_id,
        )
    )
    if not result.scalar_one_or_none():
        user_group = UserGroups(user_id=user_id, group_id=group.group_id)
        db_session.add(user_group)

    await db_session.commit()


@pytest.mark.api
class TestReviewsList:
    """Tests for GET /api/v1/admin/reviews endpoint."""

    async def test_list_reviews_with_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test admin with REVIEW_VIEW can list reviews."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "review_view")
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)
        review = ImageReviews(
            image_id=image.image_id,
            initiated_by=admin.user_id,
            deadline=datetime.now(UTC) + timedelta(days=7),
            status=ReviewStatus.OPEN,
        )
        db_session.add(review)
        await db_session.commit()

        response = await client.get(
            "/api/v1/admin/reviews",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1

    async def test_list_reviews_filter_by_status(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test filtering reviews by status."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "review_view")
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)

        # Create open and closed reviews
        for status in [ReviewStatus.OPEN, ReviewStatus.CLOSED]:
            review = ImageReviews(
                image_id=image.image_id,
                initiated_by=admin.user_id,
                deadline=datetime.now(UTC) + timedelta(days=7),
                status=status,
            )
            db_session.add(review)
        await db_session.commit()

        # Filter by open
        response = await client.get(
            "/api/v1/admin/reviews?status=0",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json()["total"] == 1

    async def test_list_reviews_without_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test user without REVIEW_VIEW permission is denied."""
        user, password = await create_auth_user(db_session)
        token = await login_user(client, user.username, password)

        response = await client.get(
            "/api/v1/admin/reviews",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403


@pytest.mark.api
class TestCreateReview:
    """Tests for POST /api/v1/admin/images/{image_id}/review endpoint."""

    async def test_create_review_success(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test creating a review directly on an image."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "review_start")
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)

        response = await client.post(
            f"/api/v1/admin/images/{image.image_id}/review",
            json={"deadline_days": 10},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["image_id"] == image.image_id
        assert data["initiated_by"] == admin.user_id
        assert data["status"] == ReviewStatus.OPEN
        assert data["status_label"] == "Open"

        # Verify image status changed to REVIEW
        await db_session.refresh(image)
        assert image.status == ImageStatus.REVIEW

    async def test_create_review_with_existing_open_review_fails(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test creating review when image already has open review fails."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "review_start")
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)

        # Create existing open review
        review = ImageReviews(
            image_id=image.image_id,
            initiated_by=admin.user_id,
            deadline=datetime.now(UTC) + timedelta(days=7),
            status=ReviewStatus.OPEN,
        )
        db_session.add(review)
        await db_session.commit()

        response = await client.post(
            f"/api/v1/admin/images/{image.image_id}/review",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 409
        assert "already has an open review" in response.json()["detail"]


@pytest.mark.api
class TestReviewVote:
    """Tests for POST /api/v1/admin/reviews/{review_id}/vote endpoint."""

    async def test_cast_vote_keep(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test casting a 'keep' vote on a review."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "review_vote")
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)
        review = ImageReviews(
            image_id=image.image_id,
            initiated_by=admin.user_id,
            deadline=datetime.now(UTC) + timedelta(days=7),
            status=ReviewStatus.OPEN,
        )
        db_session.add(review)
        await db_session.commit()
        await db_session.refresh(review)

        response = await client.post(
            f"/api/v1/admin/reviews/{review.review_id}/vote",
            json={"vote": 1, "comment": "Looks fine to me"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["vote"] == 1
        assert data["vote_label"] == "Keep"
        assert data["comment"] == "Looks fine to me"

    async def test_cast_vote_remove(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test casting a 'remove' vote on a review."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "review_vote")
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)
        review = ImageReviews(
            image_id=image.image_id,
            initiated_by=admin.user_id,
            deadline=datetime.now(UTC) + timedelta(days=7),
            status=ReviewStatus.OPEN,
        )
        db_session.add(review)
        await db_session.commit()
        await db_session.refresh(review)

        response = await client.post(
            f"/api/v1/admin/reviews/{review.review_id}/vote",
            json={"vote": 0},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["vote"] == 0
        assert data["vote_label"] == "Remove"

    async def test_update_existing_vote(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test updating an existing vote."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "review_vote")
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)
        review = ImageReviews(
            image_id=image.image_id,
            initiated_by=admin.user_id,
            deadline=datetime.now(UTC) + timedelta(days=7),
            status=ReviewStatus.OPEN,
        )
        db_session.add(review)
        await db_session.commit()
        await db_session.refresh(review)

        # First vote: keep
        response = await client.post(
            f"/api/v1/admin/reviews/{review.review_id}/vote",
            json={"vote": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json()["vote"] == 1

        # Change to remove
        response = await client.post(
            f"/api/v1/admin/reviews/{review.review_id}/vote",
            json={"vote": 0, "comment": "Changed my mind"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json()["vote"] == 0
        assert response.json()["comment"] == "Changed my mind"

    async def test_vote_on_second_review_for_same_image(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test voting on a second review for an image that was previously reviewed."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "review_vote")
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)

        # First review: create, vote, close
        review1 = ImageReviews(
            image_id=image.image_id,
            initiated_by=admin.user_id,
            deadline=datetime.now(UTC) + timedelta(days=7),
            status=ReviewStatus.OPEN,
        )
        db_session.add(review1)
        await db_session.commit()
        await db_session.refresh(review1)

        response = await client.post(
            f"/api/v1/admin/reviews/{review1.review_id}/vote",
            json={"vote": 1, "comment": "Keep it"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

        # Close the first review
        review1.status = ReviewStatus.CLOSED
        review1.outcome = ReviewOutcome.KEEP
        await db_session.commit()

        # Second review on the same image
        review2 = ImageReviews(
            image_id=image.image_id,
            initiated_by=admin.user_id,
            deadline=datetime.now(UTC) + timedelta(days=7),
            status=ReviewStatus.OPEN,
        )
        db_session.add(review2)
        await db_session.commit()
        await db_session.refresh(review2)

        # Vote on the second review - should succeed, not 500
        response = await client.post(
            f"/api/v1/admin/reviews/{review2.review_id}/vote",
            json={"vote": 0, "comment": "Remove this time"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["vote"] == 0
        assert data["review_id"] == review2.review_id

    async def test_vote_on_closed_review_fails(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test voting on closed review fails."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "review_vote")
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)
        review = ImageReviews(
            image_id=image.image_id,
            initiated_by=admin.user_id,
            deadline=datetime.now(UTC) + timedelta(days=7),
            status=ReviewStatus.CLOSED,  # Closed
        )
        db_session.add(review)
        await db_session.commit()
        await db_session.refresh(review)

        response = await client.post(
            f"/api/v1/admin/reviews/{review.review_id}/vote",
            json={"vote": 1},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400
        assert "closed" in response.json()["detail"].lower()


@pytest.mark.api
class TestReviewClose:
    """Tests for POST /api/v1/admin/reviews/{review_id}/close endpoint."""

    async def test_close_review_keep(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test closing review with keep outcome."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "review_close_early")
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)
        image.status = ImageStatus.REVIEW
        review = ImageReviews(
            image_id=image.image_id,
            initiated_by=admin.user_id,
            deadline=datetime.now(UTC) + timedelta(days=7),
            status=ReviewStatus.OPEN,
        )
        db_session.add(review)
        await db_session.commit()
        await db_session.refresh(review)

        response = await client.post(
            f"/api/v1/admin/reviews/{review.review_id}/close",
            json={"outcome": ReviewOutcome.KEEP},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == ReviewStatus.CLOSED
        assert data["outcome"] == ReviewOutcome.KEEP
        assert data["outcome_label"] == "Keep"
        assert data["closed_by"] == admin.user_id
        assert data["closed_by_username"] == admin.username

        # Verify image status changed to ACTIVE
        await db_session.refresh(image)
        assert image.status == ImageStatus.ACTIVE

    async def test_close_review_remove(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test closing review with remove outcome."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "review_close_early")
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)
        image.status = ImageStatus.REVIEW
        review = ImageReviews(
            image_id=image.image_id,
            initiated_by=admin.user_id,
            deadline=datetime.now(UTC) + timedelta(days=7),
            status=ReviewStatus.OPEN,
        )
        db_session.add(review)
        await db_session.commit()
        await db_session.refresh(review)

        response = await client.post(
            f"/api/v1/admin/reviews/{review.review_id}/close",
            json={"outcome": ReviewOutcome.REMOVE},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["outcome"] == ReviewOutcome.REMOVE

        # Verify image status changed to INAPPROPRIATE
        await db_session.refresh(image)
        assert image.status == ImageStatus.INAPPROPRIATE

    async def test_close_review_automatic_has_null_closed_by(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that reviews closed by the deadline job have closed_by=null."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "review_view")
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)
        # Simulate a review closed by the background job (closed_by=None)
        review = ImageReviews(
            image_id=image.image_id,
            initiated_by=admin.user_id,
            deadline=datetime.now(UTC) - timedelta(days=1),
            status=ReviewStatus.CLOSED,
            outcome=ReviewOutcome.KEEP,
            closed_at=datetime.now(UTC),
        )
        db_session.add(review)
        await db_session.commit()
        await db_session.refresh(review)

        response = await client.get(
            f"/api/v1/admin/reviews/{review.review_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["closed_by"] is None
        assert data["closed_by_username"] is None

    async def test_close_review_shows_closed_by_in_list(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that list endpoint shows closed_by_username for early-closed reviews."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "review_close_early")
        await grant_permission(db_session, admin.user_id, "review_view")
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)
        image.status = ImageStatus.REVIEW
        review = ImageReviews(
            image_id=image.image_id,
            initiated_by=admin.user_id,
            deadline=datetime.now(UTC) + timedelta(days=7),
            status=ReviewStatus.OPEN,
        )
        db_session.add(review)
        await db_session.commit()
        await db_session.refresh(review)

        # Close the review
        await client.post(
            f"/api/v1/admin/reviews/{review.review_id}/close",
            json={"outcome": ReviewOutcome.KEEP},
            headers={"Authorization": f"Bearer {token}"},
        )

        # Verify in list endpoint
        response = await client.get(
            "/api/v1/admin/reviews?status=1",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) >= 1
        closed_review = next(i for i in items if i["review_id"] == review.review_id)
        assert closed_review["closed_by"] == admin.user_id
        assert closed_review["closed_by_username"] == admin.username

    async def test_close_already_closed_review_fails(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test closing already closed review fails."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "review_close_early")
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)
        review = ImageReviews(
            image_id=image.image_id,
            initiated_by=admin.user_id,
            deadline=datetime.now(UTC) + timedelta(days=7),
            status=ReviewStatus.CLOSED,
        )
        db_session.add(review)
        await db_session.commit()
        await db_session.refresh(review)

        response = await client.post(
            f"/api/v1/admin/reviews/{review.review_id}/close",
            json={"outcome": ReviewOutcome.KEEP},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400


@pytest.mark.api
class TestReviewExtend:
    """Tests for POST /api/v1/admin/reviews/{review_id}/extend endpoint."""

    async def test_extend_review_success(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test extending a review deadline."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "review_start")
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)
        review = ImageReviews(
            image_id=image.image_id,
            initiated_by=admin.user_id,
            deadline=datetime.now(UTC) + timedelta(days=1),
            status=ReviewStatus.OPEN,
            extension_used=0,
        )
        db_session.add(review)
        await db_session.commit()
        await db_session.refresh(review)

        response = await client.post(
            f"/api/v1/admin/reviews/{review.review_id}/extend",
            json={"days": 5},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["extension_used"] == 1

    async def test_extend_review_already_extended_fails(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test extending review when extension already used fails."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "review_start")
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)
        review = ImageReviews(
            image_id=image.image_id,
            initiated_by=admin.user_id,
            deadline=datetime.now(UTC) + timedelta(days=1),
            status=ReviewStatus.OPEN,
            extension_used=1,  # Already extended
        )
        db_session.add(review)
        await db_session.commit()
        await db_session.refresh(review)

        response = await client.post(
            f"/api/v1/admin/reviews/{review.review_id}/extend",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400
        assert "already been used" in response.json()["detail"]

    async def test_extend_closed_review_fails(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test extending closed review fails."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "review_start")
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)
        review = ImageReviews(
            image_id=image.image_id,
            initiated_by=admin.user_id,
            deadline=datetime.now(UTC) + timedelta(days=1),
            status=ReviewStatus.CLOSED,
        )
        db_session.add(review)
        await db_session.commit()
        await db_session.refresh(review)

        response = await client.post(
            f"/api/v1/admin/reviews/{review.review_id}/extend",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400


@pytest.mark.api
class TestReviewDetail:
    """Tests for GET /api/v1/admin/reviews/{review_id} endpoint."""

    async def test_get_review_with_votes(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test getting review details with all votes."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "review_view")
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)
        review = ImageReviews(
            image_id=image.image_id,
            initiated_by=admin.user_id,
            deadline=datetime.now(UTC) + timedelta(days=7),
            status=ReviewStatus.OPEN,
        )
        db_session.add(review)
        await db_session.commit()
        await db_session.refresh(review)

        # Add some votes
        vote1 = ReviewVotes(
            review_id=review.review_id,
            image_id=image.image_id,
            user_id=admin.user_id,
            vote=1,
            comment="Keep it",
        )
        db_session.add(vote1)
        await db_session.commit()

        response = await client.get(
            f"/api/v1/admin/reviews/{review.review_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["review_id"] == review.review_id
        assert data["vote_count"] == 1
        assert data["keep_votes"] == 1
        assert data["remove_votes"] == 0
        assert len(data["votes"]) == 1
        assert data["votes"][0]["vote"] == 1
        assert data["votes"][0]["comment"] == "Keep it"

    async def test_get_nonexistent_review(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test getting non-existent review returns 404."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "review_view")
        token = await login_user(client, admin.username, password)

        response = await client.get(
            "/api/v1/admin/reviews/999999",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404


@pytest.mark.api
class TestReviewPermissionDenials:
    """Tests for permission denial scenarios (403)."""

    async def test_create_review_without_review_start_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test creating review without REVIEW_START permission fails."""
        user, password = await create_auth_user(db_session, username="noperm1")
        await grant_permission(db_session, user.user_id, "review_view")  # Only view, not start
        token = await login_user(client, user.username, password)

        image = await create_test_image(db_session, user.user_id)

        response = await client.post(
            f"/api/v1/admin/images/{image.image_id}/review",
            json={"deadline_days": 7},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403

    async def test_vote_without_review_vote_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test voting without REVIEW_VOTE permission fails."""
        user, password = await create_auth_user(db_session, username="noperm2")
        await grant_permission(db_session, user.user_id, "review_view")  # Only view, not vote
        token = await login_user(client, user.username, password)

        image = await create_test_image(db_session, user.user_id)
        review = ImageReviews(
            image_id=image.image_id,
            initiated_by=user.user_id,
            deadline=datetime.now(UTC) + timedelta(days=7),
            status=ReviewStatus.OPEN,
        )
        db_session.add(review)
        await db_session.commit()
        await db_session.refresh(review)

        response = await client.post(
            f"/api/v1/admin/reviews/{review.review_id}/vote",
            json={"vote": 1},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403

    async def test_close_without_review_close_early_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test closing review without REVIEW_CLOSE_EARLY permission fails."""
        user, password = await create_auth_user(db_session, username="noperm3")
        await grant_permission(db_session, user.user_id, "review_vote")  # Has vote, not close_early
        token = await login_user(client, user.username, password)

        image = await create_test_image(db_session, user.user_id)
        review = ImageReviews(
            image_id=image.image_id,
            initiated_by=user.user_id,
            deadline=datetime.now(UTC) + timedelta(days=7),
            status=ReviewStatus.OPEN,
        )
        db_session.add(review)
        await db_session.commit()
        await db_session.refresh(review)

        response = await client.post(
            f"/api/v1/admin/reviews/{review.review_id}/close",
            json={"outcome": ReviewOutcome.KEEP},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403

    async def test_extend_without_review_start_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test extending review without REVIEW_START permission fails."""
        user, password = await create_auth_user(db_session, username="noperm4")
        await grant_permission(db_session, user.user_id, "review_vote")  # Has vote, not start
        token = await login_user(client, user.username, password)

        image = await create_test_image(db_session, user.user_id)
        review = ImageReviews(
            image_id=image.image_id,
            initiated_by=user.user_id,
            deadline=datetime.now(UTC) + timedelta(days=7),
            status=ReviewStatus.OPEN,
            extension_used=0,
        )
        db_session.add(review)
        await db_session.commit()
        await db_session.refresh(review)

        response = await client.post(
            f"/api/v1/admin/reviews/{review.review_id}/extend",
            json={"days": 3},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403

    async def test_list_reviews_without_review_view_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test listing reviews without REVIEW_VIEW permission fails."""
        user, password = await create_auth_user(db_session, username="noperm5")
        # No review permissions at all
        token = await login_user(client, user.username, password)

        response = await client.get(
            "/api/v1/admin/reviews",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403

    async def test_get_review_detail_without_review_view_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test getting review details without REVIEW_VIEW permission fails."""
        user, password = await create_auth_user(db_session, username="noperm6")
        # No review permissions at all
        token = await login_user(client, user.username, password)

        image = await create_test_image(db_session, user.user_id)
        review = ImageReviews(
            image_id=image.image_id,
            initiated_by=user.user_id,
            deadline=datetime.now(UTC) + timedelta(days=7),
            status=ReviewStatus.OPEN,
        )
        db_session.add(review)
        await db_session.commit()
        await db_session.refresh(review)

        response = await client.get(
            f"/api/v1/admin/reviews/{review.review_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403
