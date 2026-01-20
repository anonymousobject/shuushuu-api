"""
Tests for GET /images/{image_id}/reviews endpoint.

Tests that completed image reviews (closed review sessions) can be retrieved
with proper pagination and labels, while hiding internal fields.

Fields shown: review_id, review_type, review_type_label, outcome, outcome_label, created_at, closed_at
Fields hidden: initiated_by, deadline, status (always CLOSED), votes
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ReviewOutcome, ReviewStatus, ReviewType
from app.models.image import Images
from app.models.image_review import ImageReviews
from app.models.user import Users


@pytest.mark.api
class TestGetImageReviews:
    """Tests for GET /images/{image_id}/reviews endpoint."""

    async def test_returns_closed_reviews_for_image(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Should return only closed reviews for the specified image."""
        # Create a user
        user = Users(
            username="reviewsuser1",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="reviewsuser1@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="reviews1",
            ext="jpg",
            md5_hash="reviewsmd5111111111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
            status=1,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create a closed review
        review = ImageReviews(
            image_id=image.image_id,
            review_type=ReviewType.APPROPRIATENESS,
            status=ReviewStatus.CLOSED,
            outcome=ReviewOutcome.KEEP,
            initiated_by=user.user_id,
        )
        db_session.add(review)
        await db_session.commit()
        await db_session.refresh(review)

        # GET image reviews
        response = await client.get(f"/api/v1/images/{image.image_id}/reviews")
        assert response.status_code == 200

        data = response.json()
        assert "total" in data
        assert "page" in data
        assert "per_page" in data
        assert "items" in data
        assert data["total"] == 1
        assert len(data["items"]) == 1

        # Verify item contains expected fields
        item = data["items"][0]
        assert item["review_id"] == review.review_id
        assert item["review_type"] == ReviewType.APPROPRIATENESS
        assert item["outcome"] == ReviewOutcome.KEEP
        assert "created_at" in item

    async def test_excludes_open_reviews(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Should NOT return open (in-progress) reviews."""
        # Create a user
        user = Users(
            username="openreviewuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="openreviewuser@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="openreview1",
            ext="jpg",
            md5_hash="openreviewmd511111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
            status=1,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create an OPEN review (should be excluded)
        open_review = ImageReviews(
            image_id=image.image_id,
            review_type=ReviewType.APPROPRIATENESS,
            status=ReviewStatus.OPEN,
            outcome=ReviewOutcome.PENDING,
            initiated_by=user.user_id,
        )
        # Create a CLOSED review (should be included)
        closed_review = ImageReviews(
            image_id=image.image_id,
            review_type=ReviewType.APPROPRIATENESS,
            status=ReviewStatus.CLOSED,
            outcome=ReviewOutcome.REMOVE,
            initiated_by=user.user_id,
        )
        db_session.add_all([open_review, closed_review])
        await db_session.commit()

        # GET image reviews
        response = await client.get(f"/api/v1/images/{image.image_id}/reviews")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 1  # Only closed review
        assert len(data["items"]) == 1
        assert data["items"][0]["outcome"] == ReviewOutcome.REMOVE

    async def test_returns_404_for_nonexistent_image(self, client: AsyncClient) -> None:
        """Should return 404 for nonexistent image."""
        response = await client.get("/api/v1/images/99999999/reviews")
        assert response.status_code == 404

    async def test_returns_empty_list_if_no_closed_reviews(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Should return empty list when image has no closed reviews."""
        # Create a user
        user = Users(
            username="noreviewsuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="noreviewsuser@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image with no reviews
        image = Images(
            filename="noreviews1",
            ext="jpg",
            md5_hash="noreviewsmd51111111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
            status=1,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # GET image reviews
        response = await client.get(f"/api/v1/images/{image.image_id}/reviews")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []

    async def test_includes_correct_labels(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Should include human-readable labels for review_type and outcome."""
        # Create a user
        user = Users(
            username="labeluser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="labeluser@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="labels1",
            ext="jpg",
            md5_hash="labelsmd511111111111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
            status=1,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create reviews with different outcomes
        review_keep = ImageReviews(
            image_id=image.image_id,
            review_type=ReviewType.APPROPRIATENESS,
            status=ReviewStatus.CLOSED,
            outcome=ReviewOutcome.KEEP,
            initiated_by=user.user_id,
        )
        review_remove = ImageReviews(
            image_id=image.image_id,
            review_type=ReviewType.APPROPRIATENESS,
            status=ReviewStatus.CLOSED,
            outcome=ReviewOutcome.REMOVE,
            initiated_by=user.user_id,
        )
        db_session.add_all([review_keep, review_remove])
        await db_session.commit()

        # GET image reviews
        response = await client.get(f"/api/v1/images/{image.image_id}/reviews")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 2

        # Build map by outcome for verification
        items_by_outcome = {item["outcome"]: item for item in data["items"]}

        # Check review_type_label
        assert items_by_outcome[ReviewOutcome.KEEP]["review_type_label"] == "appropriateness"
        assert items_by_outcome[ReviewOutcome.REMOVE]["review_type_label"] == "appropriateness"

        # Check outcome_label
        assert items_by_outcome[ReviewOutcome.KEEP]["outcome_label"] == "keep"
        assert items_by_outcome[ReviewOutcome.REMOVE]["outcome_label"] == "remove"

    async def test_does_not_include_hidden_fields(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Should NOT include initiated_by, deadline, or status fields in response."""
        # Create a user
        user = Users(
            username="hiddenfieldsuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="hiddenfieldsuser@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="hiddenfields1",
            ext="jpg",
            md5_hash="hiddenfieldsmd5111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
            status=1,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create a closed review with initiated_by set
        review = ImageReviews(
            image_id=image.image_id,
            review_type=ReviewType.APPROPRIATENESS,
            status=ReviewStatus.CLOSED,
            outcome=ReviewOutcome.KEEP,
            initiated_by=user.user_id,  # This should be hidden
        )
        db_session.add(review)
        await db_session.commit()

        # GET image reviews
        response = await client.get(f"/api/v1/images/{image.image_id}/reviews")
        assert response.status_code == 200

        data = response.json()
        assert len(data["items"]) == 1

        item = data["items"][0]
        # These fields should NOT be present
        assert "initiated_by" not in item
        assert "deadline" not in item
        assert "status" not in item
        assert "extension_used" not in item

        # These fields SHOULD be present
        assert "review_id" in item
        assert "review_type" in item
        assert "review_type_label" in item
        assert "outcome" in item
        assert "outcome_label" in item
        assert "created_at" in item
        assert "closed_at" in item

    async def test_pagination_works(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Should support pagination."""
        # Create a user
        user = Users(
            username="reviewpageuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="reviewpageuser@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="reviewpage1",
            ext="jpg",
            md5_hash="reviewpagemd511111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
            status=1,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create 5 closed reviews
        for i in range(5):
            review = ImageReviews(
                image_id=image.image_id,
                review_type=ReviewType.APPROPRIATENESS,
                status=ReviewStatus.CLOSED,
                outcome=ReviewOutcome.KEEP,
                initiated_by=user.user_id,
            )
            db_session.add(review)
        await db_session.commit()

        # Get first page with per_page=2
        response = await client.get(
            f"/api/v1/images/{image.image_id}/reviews?page=1&per_page=2"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 1
        assert data["per_page"] == 2
        assert len(data["items"]) == 2
        assert data["total"] == 5

        # Get second page
        response = await client.get(
            f"/api/v1/images/{image.image_id}/reviews?page=2&per_page=2"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 2
        assert len(data["items"]) == 2

        # Get third page
        response = await client.get(
            f"/api/v1/images/{image.image_id}/reviews?page=3&per_page=2"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 3
        assert len(data["items"]) == 1

    async def test_ordered_by_most_recent_first(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Reviews should be ordered by most recent (created_at DESC) first."""
        # Create a user
        user = Users(
            username="revieworderuser",
            password="hashed",
            password_type="bcrypt",
            salt="",
            email="revieworderuser@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create an image
        image = Images(
            filename="revieworder1",
            ext="jpg",
            md5_hash="reviewordermd5111111111111111111",
            user_id=user.user_id,
            width=100,
            height=100,
            filesize=1000,
            status=1,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create reviews in order (first, second, third)
        # review_id is auto-increment, so higher ID = more recent
        review1 = ImageReviews(
            image_id=image.image_id,
            review_type=ReviewType.APPROPRIATENESS,
            status=ReviewStatus.CLOSED,
            outcome=ReviewOutcome.KEEP,
            initiated_by=user.user_id,
        )
        db_session.add(review1)
        await db_session.commit()
        await db_session.refresh(review1)

        review2 = ImageReviews(
            image_id=image.image_id,
            review_type=ReviewType.APPROPRIATENESS,
            status=ReviewStatus.CLOSED,
            outcome=ReviewOutcome.REMOVE,
            initiated_by=user.user_id,
        )
        db_session.add(review2)
        await db_session.commit()
        await db_session.refresh(review2)

        review3 = ImageReviews(
            image_id=image.image_id,
            review_type=ReviewType.APPROPRIATENESS,
            status=ReviewStatus.CLOSED,
            outcome=ReviewOutcome.KEEP,
            initiated_by=user.user_id,
        )
        db_session.add(review3)
        await db_session.commit()
        await db_session.refresh(review3)

        # GET image reviews
        response = await client.get(f"/api/v1/images/{image.image_id}/reviews")
        assert response.status_code == 200

        data = response.json()
        assert len(data["items"]) == 3

        # Most recent (highest ID) should be first
        assert data["items"][0]["review_id"] == review3.review_id
        assert data["items"][1]["review_id"] == review2.review_id
        assert data["items"][2]["review_id"] == review1.review_id
