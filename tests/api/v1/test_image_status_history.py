"""Tests for image status history tracking."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, ReviewOutcome, ReviewStatus
from app.core.security import get_password_hash
from app.models.image import Images
from app.models.image_review import ImageReviews
from app.models.image_status_history import ImageStatusHistory
from app.models.permissions import GroupPerms, Groups, Perms, UserGroups
from app.models.user import Users
from app.services.review_jobs import check_review_deadlines


async def create_admin_user(
    db_session: AsyncSession,
    username: str = "statushistoryadmin",
    email: str = "statushistoryadmin@example.com",
) -> tuple[Users, str]:
    """Create an admin user and return the user object and password."""
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


async def grant_permission(db_session: AsyncSession, user_id: int, perm_title: str):
    """Grant a permission to a user via a group."""
    result = await db_session.execute(select(Perms).where(Perms.title == perm_title))
    perm = result.scalar_one_or_none()
    if not perm:
        perm = Perms(title=perm_title, desc=f"Test permission {perm_title}")
        db_session.add(perm)
        await db_session.flush()

    result = await db_session.execute(
        select(Groups).where(Groups.title == "status_history_test_admin")
    )
    group = result.scalar_one_or_none()
    if not group:
        group = Groups(title="status_history_test_admin", desc="Status history test admin group")
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


async def login_user(client: AsyncClient, username: str, password: str) -> str:
    """Login and return the access token."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


async def create_test_image(
    db_session: AsyncSession,
    user_id: int,
    status: int = ImageStatus.ACTIVE,
) -> Images:
    """Create a test image."""
    image = Images(
        user_id=user_id,
        filename=f"test_status_history_{datetime.now(UTC).timestamp()}",
        ext="jpg",
        md5_hash=f"statushist{datetime.now(UTC).timestamp():.0f}hash",
        status=status,
    )
    db_session.add(image)
    await db_session.commit()
    await db_session.refresh(image)
    return image


@pytest.mark.api
class TestImageStatusHistoryOnStatusChange:
    """Tests that ImageStatusHistory is written on admin status changes."""

    async def test_status_change_creates_history_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Changing image status via admin endpoint should create ImageStatusHistory entry."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "image_edit")
        image = await create_test_image(db_session, admin.user_id)

        # Capture IDs before API call
        image_id = image.image_id
        admin_user_id = admin.user_id

        token = await login_user(client, admin.username, admin_password)

        response = await client.patch(
            f"/api/v1/admin/images/{image_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": ImageStatus.SPOILER},
        )
        assert response.status_code == 200

        # Expire cached objects to see changes from the API request
        db_session.expire_all()

        # Verify history entry was created
        result = await db_session.execute(
            select(ImageStatusHistory).where(
                ImageStatusHistory.image_id == image_id,
                ImageStatusHistory.new_status == ImageStatus.SPOILER,
            )
        )
        history = result.scalar_one_or_none()
        assert history is not None
        assert history.old_status == ImageStatus.ACTIVE
        assert history.user_id == admin_user_id

    async def test_repost_status_change_creates_history_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Marking an image as repost should create ImageStatusHistory entry."""
        admin, admin_password = await create_admin_user(
            db_session, username="repostadmin", email="repostadmin@example.com"
        )
        await grant_permission(db_session, admin.user_id, "image_edit")

        original_image = await create_test_image(db_session, admin.user_id)
        repost_image = await create_test_image(db_session, admin.user_id)

        # Capture IDs before API call
        repost_image_id = repost_image.image_id
        original_image_id = original_image.image_id
        admin_user_id = admin.user_id

        token = await login_user(client, admin.username, admin_password)

        response = await client.patch(
            f"/api/v1/admin/images/{repost_image_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "status": ImageStatus.REPOST,
                "replacement_id": original_image_id,
            },
        )
        assert response.status_code == 200

        db_session.expire_all()

        # Verify history entry was created
        result = await db_session.execute(
            select(ImageStatusHistory).where(
                ImageStatusHistory.image_id == repost_image_id,
                ImageStatusHistory.new_status == ImageStatus.REPOST,
            )
        )
        history = result.scalar_one_or_none()
        assert history is not None
        assert history.old_status == ImageStatus.ACTIVE
        assert history.user_id == admin_user_id

    async def test_no_history_entry_when_status_unchanged(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """No ImageStatusHistory entry when only locked changes (not status)."""
        admin, admin_password = await create_admin_user(
            db_session, username="lockonlyadmin", email="lockonlyadmin@example.com"
        )
        await grant_permission(db_session, admin.user_id, "image_edit")
        image = await create_test_image(db_session, admin.user_id)

        image_id = image.image_id

        token = await login_user(client, admin.username, admin_password)

        # Only change locked, not status
        response = await client.patch(
            f"/api/v1/admin/images/{image_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"locked": True},
        )
        assert response.status_code == 200

        db_session.expire_all()

        # Verify NO history entry was created
        result = await db_session.execute(
            select(ImageStatusHistory).where(
                ImageStatusHistory.image_id == image_id,
            )
        )
        history = result.scalar_one_or_none()
        assert history is None


@pytest.mark.asyncio
class TestImageStatusHistoryOnReviewClose:
    """Tests that ImageStatusHistory is written when reviews are closed."""

    async def test_review_close_creates_history_entry_keep(
        self, db_session: AsyncSession
    ) -> None:
        """Closing a review with KEEP outcome should create ImageStatusHistory entry."""
        from app.models.review_vote import ReviewVotes

        # Create test user for voting
        user_ids = []
        for i in range(3):
            user = Users(
                user_id=100 + i,
                username=f"voter_keep_{i}",
                password="testpassword",
                password_type="bcrypt",
                salt=f"saltkeep{i:010d}"[:16],
                email=f"voterkeep{i}@example.com",
            )
            db_session.add(user)
            user_ids.append(100 + i)
        await db_session.commit()

        # Create image in REVIEW status
        image = Images(
            filename=f"test_review_close_keep_{datetime.now(UTC).timestamp()}",
            ext="jpg",
            md5_hash=f"reviewkeep{datetime.now(UTC).timestamp():.0f}",
            filesize=123456,
            width=1920,
            height=1080,
            user_id=user_ids[0],
            status=ImageStatus.REVIEW,
            locked=0,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create expired review
        review = ImageReviews(
            image_id=image.image_id,
            initiated_by=user_ids[0],
            deadline=datetime.now(UTC) - timedelta(days=1),
            extension_used=0,
            status=ReviewStatus.OPEN,
            outcome=ReviewOutcome.PENDING,
            review_type=1,
            created_at=datetime.now(UTC),
        )
        db_session.add(review)
        await db_session.commit()
        await db_session.refresh(review)

        # Add 3 keep votes (quorum met)
        for uid in user_ids:
            vote = ReviewVotes(
                review_id=review.review_id,
                user_id=uid,
                vote=1,  # keep
                created_at=datetime.now(UTC),
            )
            db_session.add(vote)
        await db_session.commit()

        image_id = image.image_id

        # Run the deadline check job
        await check_review_deadlines(db_session)

        # Verify history entry was created
        result = await db_session.execute(
            select(ImageStatusHistory).where(
                ImageStatusHistory.image_id == image_id,
                ImageStatusHistory.new_status == ImageStatus.ACTIVE,
            )
        )
        history = result.scalar_one_or_none()
        assert history is not None
        assert history.old_status == ImageStatus.REVIEW
        assert history.user_id is None  # System action

    async def test_review_close_creates_history_entry_remove(
        self, db_session: AsyncSession
    ) -> None:
        """Closing a review with REMOVE outcome should create ImageStatusHistory entry."""
        from app.models.review_vote import ReviewVotes

        # Create test users for voting
        user_ids = []
        for i in range(3):
            user = Users(
                user_id=200 + i,
                username=f"voter_remove_{i}",
                password="testpassword",
                password_type="bcrypt",
                salt=f"saltremv{i:010d}"[:16],
                email=f"voterremove{i}@example.com",
            )
            db_session.add(user)
            user_ids.append(200 + i)
        await db_session.commit()

        # Create image in REVIEW status
        image = Images(
            filename=f"test_review_close_remove_{datetime.now(UTC).timestamp()}",
            ext="jpg",
            md5_hash=f"reviewremv{datetime.now(UTC).timestamp():.0f}",
            filesize=123456,
            width=1920,
            height=1080,
            user_id=user_ids[0],
            status=ImageStatus.REVIEW,
            locked=0,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        # Create expired review
        review = ImageReviews(
            image_id=image.image_id,
            initiated_by=user_ids[0],
            deadline=datetime.now(UTC) - timedelta(days=1),
            extension_used=0,
            status=ReviewStatus.OPEN,
            outcome=ReviewOutcome.PENDING,
            review_type=1,
            created_at=datetime.now(UTC),
        )
        db_session.add(review)
        await db_session.commit()
        await db_session.refresh(review)

        # Add 3 remove votes (quorum met)
        for uid in user_ids:
            vote = ReviewVotes(
                review_id=review.review_id,
                user_id=uid,
                vote=0,  # remove
                created_at=datetime.now(UTC),
            )
            db_session.add(vote)
        await db_session.commit()

        image_id = image.image_id

        # Run the deadline check job
        await check_review_deadlines(db_session)

        # Verify history entry was created
        result = await db_session.execute(
            select(ImageStatusHistory).where(
                ImageStatusHistory.image_id == image_id,
                ImageStatusHistory.new_status == ImageStatus.INAPPROPRIATE,
            )
        )
        history = result.scalar_one_or_none()
        assert history is not None
        assert history.old_status == ImageStatus.REVIEW
        assert history.user_id is None  # System action
