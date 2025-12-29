"""
API tests for the image reporting system.

Tests cover:
- User report endpoint (POST /images/{image_id}/report)
- Admin triage endpoints (list, dismiss, action, escalate)
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, ReportStatus, ReviewStatus
from app.core.security import get_password_hash
from app.models.image import Images
from app.models.image_report import ImageReports
from app.models.image_report_tag_suggestion import ImageReportTagSuggestions
from app.models.image_review import ImageReviews
from app.models.permissions import GroupPerms, Groups, Perms, UserGroups
from app.models.tag import Tags
from app.models.tag_link import TagLinks
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
        filename="test-report-image",
        ext="jpg",
        original_filename="test.jpg",
        md5_hash="reporttesthash00000001",
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
    # Get or create the permission
    from sqlalchemy import select

    result = await db_session.execute(select(Perms).where(Perms.title == perm_title))
    perm = result.scalar_one_or_none()
    if not perm:
        perm = Perms(title=perm_title, desc=f"Test permission {perm_title}")
        db_session.add(perm)
        await db_session.flush()

    # Get or create a test group
    result = await db_session.execute(select(Groups).where(Groups.title == "test_admin"))
    group = result.scalar_one_or_none()
    if not group:
        group = Groups(title="test_admin", desc="Test admin group")
        db_session.add(group)
        await db_session.flush()

    # Add permission to group
    result = await db_session.execute(
        select(GroupPerms).where(
            GroupPerms.group_id == group.group_id,
            GroupPerms.perm_id == perm.perm_id,
        )
    )
    if not result.scalar_one_or_none():
        group_perm = GroupPerms(group_id=group.group_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(group_perm)

    # Add user to group
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


async def create_test_tags(db_session: AsyncSession, count: int = 3) -> list[Tags]:
    """Create test tags and return them."""
    tags = []
    for i in range(count):
        tag = Tags(
            title=f"test_tag_{i}",
            type=1,  # Theme
        )
        db_session.add(tag)
        tags.append(tag)
    await db_session.commit()
    for tag in tags:
        await db_session.refresh(tag)
    return tags


async def add_tag_to_image(db_session: AsyncSession, image_id: int, tag_id: int) -> None:
    """Add a tag to an image."""
    tag_link = TagLinks(image_id=image_id, tag_id=tag_id)
    db_session.add(tag_link)
    await db_session.commit()


@pytest.mark.api
class TestUserReportEndpoint:
    """Tests for POST /api/v1/images/{image_id}/report endpoint."""

    async def test_report_image_success(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test successful image report by authenticated user."""
        user, password = await create_auth_user(db_session)
        image = await create_test_image(db_session, user.user_id)
        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/images/{image.image_id}/report",
            json={"category": 2, "reason_text": "Inappropriate content"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["image_id"] == image.image_id
        assert data["user_id"] == user.user_id
        assert data["category"] == 2
        assert data["reason_text"] == "Inappropriate content"
        assert data["status"] == ReportStatus.PENDING
        assert data["status_label"] == "Pending"
        assert data["category_label"] == "Inappropriate Image"

    async def test_report_image_category_only(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test report with category only, no reason_text."""
        user, password = await create_auth_user(db_session)
        image = await create_test_image(db_session, user.user_id)
        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/images/{image.image_id}/report",
            json={"category": 1},  # Repost
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["category"] == 1
        assert data["reason_text"] is None

    async def test_report_image_unauthenticated(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test unauthenticated user cannot report."""
        user, _ = await create_auth_user(db_session)
        image = await create_test_image(db_session, user.user_id)

        response = await client.post(
            f"/api/v1/images/{image.image_id}/report",
            json={"category": 1},
        )

        assert response.status_code == 401

    async def test_report_nonexistent_image(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test reporting non-existent image returns 404."""
        user, password = await create_auth_user(db_session)
        token = await login_user(client, user.username, password)

        response = await client.post(
            "/api/v1/images/999999/report",
            json={"category": 1},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404

    async def test_report_duplicate_from_same_user(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test same user cannot report same image twice while pending."""
        user, password = await create_auth_user(db_session)
        image = await create_test_image(db_session, user.user_id)
        token = await login_user(client, user.username, password)

        # First report
        response = await client.post(
            f"/api/v1/images/{image.image_id}/report",
            json={"category": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 201

        # Second report should fail
        response = await client.post(
            f"/api/v1/images/{image.image_id}/report",
            json={"category": 2},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 409
        assert "already have a pending report" in response.json()["detail"]


@pytest.mark.api
class TestAdminReportsList:
    """Tests for GET /api/v1/admin/reports endpoint."""

    async def test_list_reports_with_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test admin with REPORT_VIEW can list reports."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "report_view")
        token = await login_user(client, admin.username, password)

        # Create a report
        image = await create_test_image(db_session, admin.user_id)
        report = ImageReports(
            image_id=image.image_id,
            user_id=admin.user_id,
            category=1,
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.commit()

        response = await client.get(
            "/api/v1/admin/reports",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1

    async def test_list_reports_filter_by_status(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test filtering reports by status."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "report_view")
        token = await login_user(client, admin.username, password)

        # Create reports with different statuses
        image = await create_test_image(db_session, admin.user_id)
        for status in [ReportStatus.PENDING, ReportStatus.REVIEWED, ReportStatus.DISMISSED]:
            report = ImageReports(
                image_id=image.image_id,
                user_id=admin.user_id,
                category=1,
                status=status,
            )
            db_session.add(report)
        await db_session.commit()

        # Filter by pending
        response = await client.get(
            "/api/v1/admin/reports?status=0",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json()["total"] == 1

    async def test_list_reports_default_shows_pending(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Default listing (no status param) should show only pending reports."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "report_view")
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)
        # One pending and one dismissed
        pending = ImageReports(
            image_id=image.image_id,
            user_id=admin.user_id,
            category=1,
            status=ReportStatus.PENDING,
        )
        dismissed = ImageReports(
            image_id=image.image_id,
            user_id=admin.user_id,
            category=1,
            status=ReportStatus.DISMISSED,
        )
        db_session.add(pending)
        db_session.add(dismissed)
        await db_session.commit()

        response = await client.get(
            "/api/v1/admin/reports",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1

    async def test_list_reports_without_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test user without REPORT_VIEW permission is denied."""
        user, password = await create_auth_user(db_session)
        token = await login_user(client, user.username, password)

        response = await client.get(
            "/api/v1/admin/reports",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403

    async def test_list_reports_includes_tag_suggestions(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that list reports includes tag suggestions for MISSING_TAGS reports."""
        admin, password = await create_auth_user(db_session, username="listtest", admin=True)
        await grant_permission(db_session, admin.user_id, "report_view")
        image = await create_test_image(db_session, admin.user_id)
        tags = await create_test_tags(db_session, count=2)
        token = await login_user(client, admin.username, password)

        # Create report with suggestions
        report = ImageReports(
            image_id=image.image_id,
            user_id=admin.user_id,
            category=4,  # MISSING_TAGS
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.flush()

        for tag in tags:
            s = ImageReportTagSuggestions(report_id=report.report_id, tag_id=tag.tag_id)
            db_session.add(s)
        await db_session.commit()

        response = await client.get(
            "/api/v1/admin/reports",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        report_item = data["items"][0]
        assert report_item["suggested_tags"] is not None
        assert len(report_item["suggested_tags"]) == 2


@pytest.mark.api
class TestAdminReportDismiss:
    """Tests for POST /api/v1/admin/reports/{report_id}/dismiss endpoint."""

    async def test_dismiss_report_success(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test dismissing a report."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "report_manage")
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
        assert "dismissed" in response.json()["message"].lower()

    async def test_dismiss_already_processed_report(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test dismissing already-reviewed report fails."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "report_manage")
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)
        report = ImageReports(
            image_id=image.image_id,
            user_id=admin.user_id,
            category=1,
            status=ReportStatus.REVIEWED,  # Already processed
        )
        db_session.add(report)
        await db_session.commit()
        await db_session.refresh(report)

        response = await client.post(
            f"/api/v1/admin/reports/{report.report_id}/dismiss",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400

    async def test_dismiss_report_with_tag_suggestions_marks_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test dismissing report with tag suggestions marks all as rejected."""
        admin, password = await create_auth_user(db_session, username="dismisstest", admin=True)
        await grant_permission(db_session, admin.user_id, "report_manage")
        image = await create_test_image(db_session, admin.user_id)
        tags = await create_test_tags(db_session, count=3)
        token = await login_user(client, admin.username, password)

        report = ImageReports(
            image_id=image.image_id,
            user_id=admin.user_id,
            category=4,  # MISSING_TAGS
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.flush()

        suggestions = []
        for tag in tags:
            s = ImageReportTagSuggestions(report_id=report.report_id, tag_id=tag.tag_id)
            db_session.add(s)
            suggestions.append(s)
        await db_session.commit()
        for s in suggestions:
            await db_session.refresh(s)

        response = await client.post(
            f"/api/v1/admin/reports/{report.report_id}/dismiss",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200

        # Verify all suggestions marked as rejected
        for s in suggestions:
            await db_session.refresh(s)
            assert s.accepted is False

    async def test_dismiss_report_with_admin_notes(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test dismissing report with admin notes."""
        admin, password = await create_auth_user(db_session, username="dismissnotes", admin=True)
        await grant_permission(db_session, admin.user_id, "report_manage")
        image = await create_test_image(db_session, admin.user_id)
        token = await login_user(client, admin.username, password)

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
            json={"admin_notes": "Not a valid report, user misunderstood."},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200

        # Verify admin notes saved
        await db_session.refresh(report)
        assert report.admin_notes == "Not a valid report, user misunderstood."


@pytest.mark.api
class TestAdminReportAction:
    """Tests for POST /api/v1/admin/reports/{report_id}/action endpoint."""

    async def test_action_report_changes_image_status(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test taking action on report changes image status."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "report_manage")
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)
        report = ImageReports(
            image_id=image.image_id,
            user_id=admin.user_id,
            category=2,  # Inappropriate
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

        # Verify image status changed
        await db_session.refresh(image)
        assert image.status == ImageStatus.INAPPROPRIATE


@pytest.mark.api
class TestAdminReportEscalate:
    """Tests for POST /api/v1/admin/reports/{report_id}/escalate endpoint."""

    async def test_escalate_report_creates_review(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test escalating report creates a review session."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "report_manage")
        await grant_permission(db_session, admin.user_id, "review_start")
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
        data = response.json()
        assert data["image_id"] == image.image_id
        assert data["source_report_id"] == report.report_id
        assert data["status"] == ReviewStatus.OPEN

        # Verify image status changed to REVIEW
        await db_session.refresh(image)
        assert image.status == ImageStatus.REVIEW

    async def test_escalate_image_with_existing_review_fails(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test escalating when image already has open review fails."""
        admin, password = await create_auth_user(db_session, username="admin", admin=True)
        await grant_permission(db_session, admin.user_id, "report_manage")
        await grant_permission(db_session, admin.user_id, "review_start")
        token = await login_user(client, admin.username, password)

        image = await create_test_image(db_session, admin.user_id)

        # Create existing open review
        from datetime import UTC, datetime, timedelta

        review = ImageReviews(
            image_id=image.image_id,
            initiated_by=admin.user_id,
            deadline=datetime.now(UTC) + timedelta(days=7),
            status=ReviewStatus.OPEN,
        )
        db_session.add(review)

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
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 409
        assert "already has an open review" in response.json()["detail"]


@pytest.mark.api
class TestReportPermissionDenials:
    """Tests for permission denial scenarios (403)."""

    async def test_dismiss_without_report_manage_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test dismissing report without REPORT_MANAGE permission fails."""
        user, password = await create_auth_user(db_session, username="noperm1")
        await grant_permission(db_session, user.user_id, "report_view")  # Only view, not manage
        token = await login_user(client, user.username, password)

        # Create report to dismiss
        image = await create_test_image(db_session, user.user_id)
        report = ImageReports(
            image_id=image.image_id,
            user_id=user.user_id,
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

        assert response.status_code == 403

    async def test_action_without_report_manage_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test taking action on report without REPORT_MANAGE permission fails."""
        user, password = await create_auth_user(db_session, username="noperm2")
        await grant_permission(db_session, user.user_id, "report_view")
        token = await login_user(client, user.username, password)

        image = await create_test_image(db_session, user.user_id)
        report = ImageReports(
            image_id=image.image_id,
            user_id=user.user_id,
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

        assert response.status_code == 403

    async def test_escalate_without_report_manage_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test escalating report without REPORT_MANAGE permission fails."""
        user, password = await create_auth_user(db_session, username="noperm3")
        await grant_permission(db_session, user.user_id, "review_start")  # Has review_start but not report_manage
        token = await login_user(client, user.username, password)

        image = await create_test_image(db_session, user.user_id)
        report = ImageReports(
            image_id=image.image_id,
            user_id=user.user_id,
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

        assert response.status_code == 403

    async def test_escalate_without_review_start_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test escalating report without REVIEW_START permission fails."""
        user, password = await create_auth_user(db_session, username="noperm4")
        await grant_permission(db_session, user.user_id, "report_manage")  # Has report_manage but not review_start
        token = await login_user(client, user.username, password)

        image = await create_test_image(db_session, user.user_id)
        report = ImageReports(
            image_id=image.image_id,
            user_id=user.user_id,
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

        assert response.status_code == 403


@pytest.mark.api
class TestReportValidation:
    """Tests for validation error scenarios (422).

    Note: Some validation tests are skipped because the API currently
    accepts any integer for category/status. Validation could be added
    in the Pydantic schema or endpoint logic.
    """

    @pytest.mark.skip(
        reason="API currently accepts any integer for category. "
        "Future: Add Literal type or validator to ReportCreate schema."
    )
    async def test_report_with_invalid_category(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test reporting with invalid category fails.

        Note: Currently the API accepts any integer for category.
        To enforce valid values, add a validator to ReportCreate schema.
        """
        pass

    @pytest.mark.skip(
        reason="API currently accepts any integer for new_status. "
        "Future: Add Literal type or validator to ReportActionRequest schema."
    )
    async def test_action_with_invalid_status(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test action with invalid image status fails.

        Note: Currently the API accepts any integer for new_status.
        To enforce valid values, add a validator to ReportActionRequest schema.
        """
        pass


@pytest.mark.api
class TestReportWithTagSuggestions:
    """Tests for MISSING_TAGS reports with tag suggestions."""

    async def test_report_with_valid_tag_suggestions(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test creating MISSING_TAGS report with valid tag suggestions."""
        user, password = await create_auth_user(db_session, username="taguser1")
        image = await create_test_image(db_session, user.user_id)
        tags = await create_test_tags(db_session, count=3)
        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/images/{image.image_id}/report",
            json={
                "category": 4,  # MISSING_TAGS
                "reason_text": "Missing character tags",
                "suggested_tag_ids": [t.tag_id for t in tags],
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["category"] == 4
        assert data["suggested_tags"] is not None
        assert len(data["suggested_tags"]) == 3

    async def test_report_skips_invalid_tag_ids(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that invalid tag IDs are skipped and reported."""
        user, password = await create_auth_user(db_session, username="taguser2")
        image = await create_test_image(db_session, user.user_id)
        tags = await create_test_tags(db_session, count=2)
        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/images/{image.image_id}/report",
            json={
                "category": 4,
                "suggested_tag_ids": [tags[0].tag_id, 999999, tags[1].tag_id],  # 999999 is invalid
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = response.json()
        assert len(data["suggested_tags"]) == 2
        assert data["skipped_tags"]["invalid_tag_ids"] == [999999]

    async def test_report_skips_tags_already_on_image(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that tags already on image are skipped and reported."""
        user, password = await create_auth_user(db_session, username="taguser3")
        image = await create_test_image(db_session, user.user_id)
        tags = await create_test_tags(db_session, count=3)
        # Add first tag to image
        await add_tag_to_image(db_session, image.image_id, tags[0].tag_id)
        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/images/{image.image_id}/report",
            json={
                "category": 4,
                "suggested_tag_ids": [t.tag_id for t in tags],
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = response.json()
        assert len(data["suggested_tags"]) == 2
        assert tags[0].tag_id in data["skipped_tags"]["already_on_image"]

    async def test_report_rejects_tag_suggestions_for_non_missing_tags(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that tag suggestions are rejected for non-MISSING_TAGS categories."""
        user, password = await create_auth_user(db_session, username="taguser4")
        image = await create_test_image(db_session, user.user_id)
        tags = await create_test_tags(db_session, count=2)
        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/images/{image.image_id}/report",
            json={
                "category": 1,  # REPOST, not MISSING_TAGS
                "suggested_tag_ids": [t.tag_id for t in tags],
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 422

    async def test_report_with_duplicate_tag_ids_dedupes(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that duplicate tag IDs are deduplicated."""
        user, password = await create_auth_user(db_session, username="taguser5")
        image = await create_test_image(db_session, user.user_id)
        tags = await create_test_tags(db_session, count=2)
        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/images/{image.image_id}/report",
            json={
                "category": 4,
                "suggested_tag_ids": [tags[0].tag_id, tags[0].tag_id, tags[1].tag_id],  # Duplicate
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = response.json()
        assert len(data["suggested_tags"]) == 2

    async def test_report_missing_tags_without_suggestions_still_works(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test MISSING_TAGS report without suggestions (just reason_text) still works."""
        user, password = await create_auth_user(db_session, username="taguser6")
        image = await create_test_image(db_session, user.user_id)
        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/images/{image.image_id}/report",
            json={
                "category": 4,
                "reason_text": "Missing some tags but I don't know which ones",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["suggested_tags"] is None or len(data["suggested_tags"]) == 0


@pytest.mark.api
class TestAdminApplyTagSuggestions:
    """Tests for POST /api/v1/admin/reports/{report_id}/apply-tag-suggestions endpoint."""

    async def test_apply_all_suggestions(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test applying all tag suggestions to an image."""
        admin, password = await create_auth_user(db_session, username="applytest1", admin=True)
        await grant_permission(db_session, admin.user_id, "report_manage")
        image = await create_test_image(db_session, admin.user_id)
        tags = await create_test_tags(db_session, count=3)
        token = await login_user(client, admin.username, password)

        # Create report with suggestions
        report = ImageReports(
            image_id=image.image_id,
            user_id=admin.user_id,
            category=4,
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.flush()

        suggestions = []
        for tag in tags:
            s = ImageReportTagSuggestions(report_id=report.report_id, tag_id=tag.tag_id)
            db_session.add(s)
            suggestions.append(s)
        await db_session.commit()
        for s in suggestions:
            await db_session.refresh(s)

        response = await client.post(
            f"/api/v1/admin/reports/{report.report_id}/apply-tag-suggestions",
            json={"approved_suggestion_ids": [s.suggestion_id for s in suggestions]},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["applied_tags"]) == 3

        # Verify tags were added to image
        from sqlalchemy import select as sql_select

        tag_links = await db_session.execute(
            sql_select(TagLinks).where(TagLinks.image_id == image.image_id)
        )
        assert len(tag_links.scalars().all()) == 3

    async def test_apply_partial_suggestions(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test applying only some tag suggestions."""
        admin, password = await create_auth_user(db_session, username="applytest2", admin=True)
        await grant_permission(db_session, admin.user_id, "report_manage")
        image = await create_test_image(db_session, admin.user_id)
        tags = await create_test_tags(db_session, count=3)
        token = await login_user(client, admin.username, password)

        report = ImageReports(
            image_id=image.image_id,
            user_id=admin.user_id,
            category=4,
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.flush()

        suggestions = []
        for tag in tags:
            s = ImageReportTagSuggestions(report_id=report.report_id, tag_id=tag.tag_id)
            db_session.add(s)
            suggestions.append(s)
        await db_session.commit()
        for s in suggestions:
            await db_session.refresh(s)

        # Only approve first 2 suggestions
        response = await client.post(
            f"/api/v1/admin/reports/{report.report_id}/apply-tag-suggestions",
            json={
                "approved_suggestion_ids": [
                    suggestions[0].suggestion_id,
                    suggestions[1].suggestion_id,
                ]
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["applied_tags"]) == 2

        # Verify suggestions were marked correctly
        await db_session.refresh(suggestions[0])
        await db_session.refresh(suggestions[1])
        await db_session.refresh(suggestions[2])
        assert suggestions[0].accepted is True
        assert suggestions[1].accepted is True
        assert suggestions[2].accepted is False

    async def test_apply_empty_list_rejects_all(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test applying empty list rejects all suggestions."""
        admin, password = await create_auth_user(db_session, username="applytest3", admin=True)
        await grant_permission(db_session, admin.user_id, "report_manage")
        image = await create_test_image(db_session, admin.user_id)
        tags = await create_test_tags(db_session, count=2)
        token = await login_user(client, admin.username, password)

        report = ImageReports(
            image_id=image.image_id,
            user_id=admin.user_id,
            category=4,
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.flush()

        suggestions = []
        for tag in tags:
            s = ImageReportTagSuggestions(report_id=report.report_id, tag_id=tag.tag_id)
            db_session.add(s)
            suggestions.append(s)
        await db_session.commit()
        for s in suggestions:
            await db_session.refresh(s)

        response = await client.post(
            f"/api/v1/admin/reports/{report.report_id}/apply-tag-suggestions",
            json={"approved_suggestion_ids": []},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200

        # Verify all suggestions rejected
        await db_session.refresh(suggestions[0])
        await db_session.refresh(suggestions[1])
        assert suggestions[0].accepted is False
        assert suggestions[1].accepted is False

    async def test_apply_with_admin_notes(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test applying suggestions with admin notes."""
        admin, password = await create_auth_user(db_session, username="applytest4", admin=True)
        await grant_permission(db_session, admin.user_id, "report_manage")
        image = await create_test_image(db_session, admin.user_id)
        tags = await create_test_tags(db_session, count=1)
        token = await login_user(client, admin.username, password)

        report = ImageReports(
            image_id=image.image_id,
            user_id=admin.user_id,
            category=4,
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.flush()

        s = ImageReportTagSuggestions(report_id=report.report_id, tag_id=tags[0].tag_id)
        db_session.add(s)
        await db_session.commit()
        await db_session.refresh(s)

        response = await client.post(
            f"/api/v1/admin/reports/{report.report_id}/apply-tag-suggestions",
            json={
                "approved_suggestion_ids": [s.suggestion_id],
                "admin_notes": "Good suggestions, approved all.",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200

        # Verify admin notes saved
        await db_session.refresh(report)
        assert report.admin_notes == "Good suggestions, approved all."

    async def test_apply_to_nonexistent_report_fails(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test applying to nonexistent report returns 404."""
        admin, password = await create_auth_user(db_session, username="applytest5", admin=True)
        await grant_permission(db_session, admin.user_id, "report_manage")
        token = await login_user(client, admin.username, password)

        response = await client.post(
            "/api/v1/admin/reports/999999/apply-tag-suggestions",
            json={"approved_suggestion_ids": []},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404

    async def test_apply_without_permission_fails(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test applying without REPORT_MANAGE permission fails."""
        user, password = await create_auth_user(db_session, username="noperm5")
        token = await login_user(client, user.username, password)

        response = await client.post(
            "/api/v1/admin/reports/1/apply-tag-suggestions",
            json={"approved_suggestion_ids": []},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403

    async def test_apply_to_non_missing_tags_report_fails(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test applying tag suggestions to non-MISSING_TAGS report fails."""
        admin, password = await create_auth_user(db_session, username="applytest6", admin=True)
        await grant_permission(db_session, admin.user_id, "report_manage")
        image = await create_test_image(db_session, admin.user_id)
        token = await login_user(client, admin.username, password)

        # Create a REPOST report (category 1), not MISSING_TAGS (category 4)
        report = ImageReports(
            image_id=image.image_id,
            user_id=admin.user_id,
            category=1,  # REPOST
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.commit()
        await db_session.refresh(report)

        response = await client.post(
            f"/api/v1/admin/reports/{report.report_id}/apply-tag-suggestions",
            json={"approved_suggestion_ids": []},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400
        assert "MISSING_TAGS" in response.json()["detail"]

    async def test_tag_already_added_between_report_and_review(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test handling when a suggested tag is already on the image at review time."""
        admin, password = await create_auth_user(db_session, username="applytest7", admin=True)
        await grant_permission(db_session, admin.user_id, "report_manage")
        image = await create_test_image(db_session, admin.user_id)
        tags = await create_test_tags(db_session, count=3)
        token = await login_user(client, admin.username, password)

        # Create MISSING_TAGS report with suggestions
        report = ImageReports(
            image_id=image.image_id,
            user_id=admin.user_id,
            category=4,  # MISSING_TAGS
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.flush()

        # Add suggestions for all three tags
        suggestions = []
        for tag in tags:
            s = ImageReportTagSuggestions(report_id=report.report_id, tag_id=tag.tag_id)
            db_session.add(s)
            suggestions.append(s)
        await db_session.commit()
        for s in suggestions:
            await db_session.refresh(s)

        # Simulate someone already adding the first tag to the image
        # (this happens between report creation and admin review)
        await add_tag_to_image(db_session, image.image_id, tags[0].tag_id)

        # Try to approve all suggestions
        response = await client.post(
            f"/api/v1/admin/reports/{report.report_id}/apply-tag-suggestions",
            json={"approved_suggestion_ids": [s.suggestion_id for s in suggestions]},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        
        # Should have 2 applied tags (tags[1] and tags[2])
        # and 1 already present (tags[0])
        assert len(data["applied_tags"]) == 2
        assert len(data["already_present"]) == 1
        assert tags[0].tag_id in data["already_present"]

        # Verify only 2 new tags were actually added
        from sqlalchemy import select as sql_select

        tag_links = await db_session.execute(
            sql_select(TagLinks).where(TagLinks.image_id == image.image_id)
        )
        all_tag_links = tag_links.scalars().all()
        assert len(all_tag_links) == 3  # 1 already present + 2 newly added
