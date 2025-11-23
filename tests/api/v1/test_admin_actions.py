"""
API tests for admin action audit logging.

Tests verify that each admin action creates the correct audit log entry
with appropriate foreign keys and details JSON.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import (
    AdminActionType,
    ImageStatus,
    ReportStatus,
    ReviewOutcome,
    ReviewStatus,
)
from app.core.security import get_password_hash
from app.models.admin_action import AdminActions
from app.models.image import Images
from app.models.image_report import ImageReports
from app.models.image_review import ImageReviews
from app.models.permissions import GroupPerms, Groups, Perms, UserGroups
from app.models.user import Users


async def create_admin_user(
    db_session: AsyncSession,
    username: str = "auditadmin",
    email: str = "auditadmin@example.com",
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
        admin=1,
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
        filename="test-audit-image",
        ext="jpg",
        original_filename="test.jpg",
        md5_hash=f"audittesthash{user_id:08d}",
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


async def grant_permissions(
    db_session: AsyncSession, user_id: int, perm_titles: list[str]
):
    """Grant multiple permissions to a user via a group."""
    # Get or create a test group
    result = await db_session.execute(select(Groups).where(Groups.title == "audit_test_admin"))
    group = result.scalar_one_or_none()
    if not group:
        group = Groups(title="audit_test_admin", desc="Audit test admin group")
        db_session.add(group)
        await db_session.flush()

    for perm_title in perm_titles:
        # Get or create the permission
        result = await db_session.execute(select(Perms).where(Perms.title == perm_title))
        perm = result.scalar_one_or_none()
        if not perm:
            perm = Perms(title=perm_title, desc=f"Test permission {perm_title}")
            db_session.add(perm)
            await db_session.flush()

        # Add permission to group if not already
        result = await db_session.execute(
            select(GroupPerms).where(
                GroupPerms.group_id == group.group_id,
                GroupPerms.perm_id == perm.perm_id,
            )
        )
        if not result.scalar_one_or_none():
            group_perm = GroupPerms(group_id=group.group_id, perm_id=perm.perm_id, permvalue=1)
            db_session.add(group_perm)

    # Add user to group if not already
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
class TestReportDismissAuditLog:
    """Tests for audit logging when dismissing reports."""

    async def test_dismiss_creates_audit_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Dismissing a report creates REPORT_DISMISS audit entry."""
        admin, password = await create_admin_user(db_session, "dismissadmin1")
        await grant_permissions(db_session, admin.user_id, ["report_manage"])
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)
        report = ImageReports(
            image_id=image.image_id,
            user_id=admin.user_id,
            category=1,
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.commit()
        await db_session.refresh(report)

        response = await client.post(
            f"/api/v1/admin/reports/{report.report_id}/dismiss",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

        # Verify audit log entry
        stmt = select(AdminActions).where(
            AdminActions.report_id == report.report_id,
            AdminActions.action_type == AdminActionType.REPORT_DISMISS,
        )
        result = await db_session.execute(stmt)
        action = result.scalar_one_or_none()

        assert action is not None
        assert action.user_id == admin.user_id
        assert action.report_id == report.report_id
        assert action.image_id == image.image_id
        # Details may be empty dict or contain previous_status
        assert action.details is not None or action.details == {}


@pytest.mark.api
class TestReportActionAuditLog:
    """Tests for audit logging when taking action on reports."""

    async def test_action_creates_audit_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Taking action on a report creates REPORT_ACTION audit entry."""
        admin, password = await create_admin_user(db_session, "actionadmin1")
        await grant_permissions(db_session, admin.user_id, ["report_manage"])
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)
        report = ImageReports(
            image_id=image.image_id,
            user_id=admin.user_id,
            category=2,
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.commit()
        await db_session.refresh(report)

        response = await client.post(
            f"/api/v1/admin/reports/{report.report_id}/action",
            json={"new_status": ImageStatus.INAPPROPRIATE},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

        # Verify audit log entry
        stmt = select(AdminActions).where(
            AdminActions.report_id == report.report_id,
            AdminActions.action_type == AdminActionType.REPORT_ACTION,
        )
        result = await db_session.execute(stmt)
        action = result.scalar_one_or_none()

        assert action is not None
        assert action.user_id == admin.user_id
        assert action.report_id == report.report_id
        assert action.image_id == image.image_id
        assert action.details is not None
        assert action.details.get("new_status") == ImageStatus.INAPPROPRIATE
        assert "previous_status" in action.details


@pytest.mark.api
class TestReviewStartAuditLog:
    """Tests for audit logging when starting reviews."""

    async def test_create_review_creates_audit_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Creating a review creates REVIEW_START audit entry."""
        admin, password = await create_admin_user(db_session, "startadmin1")
        await grant_permissions(db_session, admin.user_id, ["review_start"])
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)

        response = await client.post(
            f"/api/v1/admin/images/{image.image_id}/review",
            json={"deadline_days": 7},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 201  # Created
        review_data = response.json()

        # Verify audit log entry
        stmt = select(AdminActions).where(
            AdminActions.review_id == review_data["review_id"],
            AdminActions.action_type == AdminActionType.REVIEW_START,
        )
        result = await db_session.execute(stmt)
        action = result.scalar_one_or_none()

        assert action is not None
        assert action.user_id == admin.user_id
        assert action.review_id == review_data["review_id"]
        assert action.image_id == image.image_id

    async def test_escalate_report_creates_audit_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Escalating a report creates REVIEW_START audit entry."""
        admin, password = await create_admin_user(db_session, "escadmin1")
        await grant_permissions(
            db_session, admin.user_id, ["report_manage", "review_start"]
        )
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)
        report = ImageReports(
            image_id=image.image_id,
            user_id=admin.user_id,
            category=2,
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.commit()
        await db_session.refresh(report)

        response = await client.post(
            f"/api/v1/admin/reports/{report.report_id}/escalate",
            json={"deadline_days": 7},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        review_data = response.json()

        # Verify audit log entry
        stmt = select(AdminActions).where(
            AdminActions.review_id == review_data["review_id"],
            AdminActions.action_type == AdminActionType.REVIEW_START,
        )
        result = await db_session.execute(stmt)
        action = result.scalar_one_or_none()

        assert action is not None
        assert action.user_id == admin.user_id
        assert action.report_id == report.report_id
        assert action.review_id == review_data["review_id"]


@pytest.mark.api
class TestReviewVoteAuditLog:
    """Tests for audit logging when voting on reviews."""

    async def test_vote_creates_audit_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Casting a vote creates REVIEW_VOTE audit entry."""
        admin, password = await create_admin_user(db_session, "voteadmin1")
        await grant_permissions(db_session, admin.user_id, ["review_vote"])
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)
        from datetime import UTC, datetime, timedelta

        review = ImageReviews(
            image_id=image.image_id,
            initiated_by=admin.user_id,
            deadline=datetime.now(UTC) + timedelta(days=7),
            status=ReviewStatus.OPEN,
            outcome=ReviewOutcome.PENDING,
            review_type=1,
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

        # Verify audit log entry
        stmt = select(AdminActions).where(
            AdminActions.review_id == review.review_id,
            AdminActions.action_type == AdminActionType.REVIEW_VOTE,
        )
        result = await db_session.execute(stmt)
        action = result.scalar_one_or_none()

        assert action is not None
        assert action.user_id == admin.user_id
        assert action.review_id == review.review_id
        assert action.image_id == image.image_id
        assert action.details is not None
        assert action.details.get("vote") == 1


@pytest.mark.api
class TestReviewCloseAuditLog:
    """Tests for audit logging when closing reviews."""

    async def test_close_creates_audit_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Closing a review creates REVIEW_CLOSE audit entry."""
        admin, password = await create_admin_user(db_session, "closeadmin1")
        await grant_permissions(db_session, admin.user_id, ["review_close_early"])
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)
        from datetime import UTC, datetime, timedelta

        review = ImageReviews(
            image_id=image.image_id,
            initiated_by=admin.user_id,
            deadline=datetime.now(UTC) + timedelta(days=7),
            status=ReviewStatus.OPEN,
            outcome=ReviewOutcome.PENDING,
            review_type=1,
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

        # Verify audit log entry
        stmt = select(AdminActions).where(
            AdminActions.review_id == review.review_id,
            AdminActions.action_type == AdminActionType.REVIEW_CLOSE,
        )
        result = await db_session.execute(stmt)
        action = result.scalar_one_or_none()

        assert action is not None
        assert action.user_id == admin.user_id
        assert action.review_id == review.review_id
        assert action.image_id == image.image_id
        assert action.details is not None
        assert action.details.get("outcome") == ReviewOutcome.KEEP


@pytest.mark.api
class TestReviewExtendAuditLog:
    """Tests for audit logging when extending reviews."""

    async def test_extend_creates_audit_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Extending a review creates REVIEW_EXTEND audit entry."""
        admin, password = await create_admin_user(db_session, "extendadmin1")
        await grant_permissions(db_session, admin.user_id, ["review_start"])
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)
        from datetime import UTC, datetime, timedelta

        review = ImageReviews(
            image_id=image.image_id,
            initiated_by=admin.user_id,
            deadline=datetime.now(UTC) + timedelta(days=7),
            status=ReviewStatus.OPEN,
            outcome=ReviewOutcome.PENDING,
            extension_used=0,
            review_type=1,
        )
        db_session.add(review)
        await db_session.commit()
        await db_session.refresh(review)

        response = await client.post(
            f"/api/v1/admin/reviews/{review.review_id}/extend",
            json={"days": 3},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

        # Verify audit log entry
        stmt = select(AdminActions).where(
            AdminActions.review_id == review.review_id,
            AdminActions.action_type == AdminActionType.REVIEW_EXTEND,
        )
        result = await db_session.execute(stmt)
        action = result.scalar_one_or_none()

        assert action is not None
        assert action.user_id == admin.user_id
        assert action.review_id == review.review_id
        assert action.image_id == image.image_id
        assert action.details is not None
        assert "extension_days" in action.details
