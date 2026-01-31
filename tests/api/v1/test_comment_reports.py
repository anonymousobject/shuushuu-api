"""
API tests for the comment reporting system.

Tests cover:
- User report endpoint (POST /comments/{comment_id}/report)
- Admin triage endpoints (list, dismiss, delete)
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import CommentReportCategory, ImageStatus, ReportCategory, ReportStatus
from app.core.security import get_password_hash
from app.models import Comments, CommentReports, ImageReports, Images, Users
from app.models.permissions import GroupPerms, Groups, Perms, UserGroups


async def create_auth_user(
    db_session: AsyncSession,
    username: str = "reportuser",
    email: str = "report@example.com",
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
        admin=0,
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
        filename="test-comment-report-image",
        ext="jpg",
        original_filename="test.jpg",
        md5_hash="commentreporttest00001",
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


async def create_test_comment(
    db_session: AsyncSession, user_id: int, image_id: int, text: str = "Test comment"
) -> Comments:
    """Create a test comment."""
    comment = Comments(
        user_id=user_id,
        image_id=image_id,
        post_text=text,
        deleted=False,
    )
    db_session.add(comment)
    await db_session.commit()
    await db_session.refresh(comment)
    return comment


async def grant_permission(db_session: AsyncSession, user_id: int, perm_title: str):
    """Grant a permission to a user via a group."""
    result = await db_session.execute(select(Perms).where(Perms.title == perm_title))
    perm = result.scalar_one_or_none()
    if not perm:
        perm = Perms(title=perm_title, desc=f"Test permission {perm_title}")
        db_session.add(perm)
        await db_session.flush()

    result = await db_session.execute(select(Groups).where(Groups.title == "test_mod"))
    group = result.scalar_one_or_none()
    if not group:
        group = Groups(title="test_mod", desc="Test mod group")
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
class TestUserCommentReportEndpoint:
    """Tests for POST /api/v1/comments/{comment_id}/report endpoint."""

    async def test_report_comment_success(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test successfully reporting a comment."""
        user, password = await create_auth_user(db_session)
        image = await create_test_image(db_session, user.user_id)
        comment = await create_test_comment(db_session, user.user_id, image.image_id)
        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/comments/{comment.post_id}/report",
            json={
                "category": CommentReportCategory.SPAM,
                "reason_text": "This is spam",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["comment_id"] == comment.post_id
        assert data["category"] == CommentReportCategory.SPAM
        assert data["status"] == ReportStatus.PENDING

    async def test_report_comment_requires_auth(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that reporting requires authentication."""
        user, _ = await create_auth_user(db_session)
        image = await create_test_image(db_session, user.user_id)
        comment = await create_test_comment(db_session, user.user_id, image.image_id)

        response = await client.post(
            f"/api/v1/comments/{comment.post_id}/report",
            json={"category": CommentReportCategory.SPAM},
        )

        assert response.status_code == 401

    async def test_report_nonexistent_comment(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test reporting a comment that doesn't exist."""
        user, password = await create_auth_user(db_session)
        token = await login_user(client, user.username, password)

        response = await client.post(
            "/api/v1/comments/999999/report",
            json={"category": CommentReportCategory.SPAM},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    async def test_report_deleted_comment(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that deleted comments cannot be reported."""
        user, password = await create_auth_user(db_session)
        image = await create_test_image(db_session, user.user_id)
        comment = await create_test_comment(db_session, user.user_id, image.image_id)
        comment.deleted = True
        await db_session.commit()

        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/comments/{comment.post_id}/report",
            json={"category": CommentReportCategory.SPAM},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400
        assert "deleted" in response.json()["detail"].lower()

    async def test_duplicate_pending_report(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that a user cannot have multiple pending reports on the same comment."""
        user, password = await create_auth_user(db_session)
        image = await create_test_image(db_session, user.user_id)
        comment = await create_test_comment(db_session, user.user_id, image.image_id)
        token = await login_user(client, user.username, password)

        # First report succeeds
        response = await client.post(
            f"/api/v1/comments/{comment.post_id}/report",
            json={"category": CommentReportCategory.SPAM},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 201

        # Second report fails
        response = await client.post(
            f"/api/v1/comments/{comment.post_id}/report",
            json={"category": CommentReportCategory.RULE_VIOLATION},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 409
        assert "pending report" in response.json()["detail"].lower()

    async def test_invalid_category(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that invalid categories are rejected."""
        user, password = await create_auth_user(db_session)
        image = await create_test_image(db_session, user.user_id)
        comment = await create_test_comment(db_session, user.user_id, image.image_id)
        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/comments/{comment.post_id}/report",
            json={"category": 999},  # Invalid category
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 422


@pytest.mark.api
class TestAdminCommentReportEndpoints:
    """Tests for admin comment report endpoints."""

    async def test_list_comment_reports(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test listing comment reports with report_type=comment filter."""
        user, password = await create_auth_user(db_session, "admin1", "admin1@test.com")
        await grant_permission(db_session, user.user_id, "report_view")
        image = await create_test_image(db_session, user.user_id)
        comment = await create_test_comment(db_session, user.user_id, image.image_id)

        # Create a comment report
        report = CommentReports(
            comment_id=comment.post_id,
            user_id=user.user_id,
            category=CommentReportCategory.SPAM,
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.commit()

        token = await login_user(client, user.username, password)

        response = await client.get(
            "/api/v1/admin/reports?report_type=comment",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "comment_reports" in data
        assert len(data["comment_reports"]) == 1

    async def test_list_unified_reports(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test listing combined image and comment reports."""
        user, password = await create_auth_user(db_session, "admin_uni", "admin_uni@test.com")
        await grant_permission(db_session, user.user_id, "report_view")
        image = await create_test_image(db_session, user.user_id)
        comment = await create_test_comment(db_session, user.user_id, image.image_id)

        # Create comment report
        comment_report = CommentReports(
            comment_id=comment.post_id,
            user_id=user.user_id,
            category=CommentReportCategory.SPAM,
            status=ReportStatus.PENDING,
        )
        db_session.add(comment_report)

        # Create image report
        image_report = ImageReports(
            image_id=image.image_id,
            user_id=user.user_id,
            category=ReportCategory.SPAM,
            status=ReportStatus.PENDING,
        )
        db_session.add(image_report)
        await db_session.commit()

        token = await login_user(client, user.username, password)

        # report_type="all" (default)
        response = await client.get(
            "/api/v1/admin/reports?report_type=all",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["image_reports"]) == 1
        assert len(data["comment_reports"]) == 1
        assert data["total"] == 2

    async def test_list_comment_reports_with_category(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test listing comment reports with category filter."""
        user, password = await create_auth_user(db_session, "admin_cat_ok", "admin_cat_ok@test.com")
        await grant_permission(db_session, user.user_id, "report_view")
        image = await create_test_image(db_session, user.user_id)
        comment = await create_test_comment(db_session, user.user_id, image.image_id)

        # Create two reports with different categories
        # Note: We must use different users because a single user cannot have multiple
        # pending reports on the same comment (enforced by application logic)

        report1 = CommentReports(
            comment_id=comment.post_id,
            user_id=user.user_id,
            category=CommentReportCategory.RULE_VIOLATION,
            status=ReportStatus.PENDING,
        )
        db_session.add(report1)

        # Determine another user ID to use (could create one, or just pick an arbitrary ID
        # since we're inserting directly into DB and foreign keys might allow existing users)
        # But to be safe and clean, let's create a second user.
        user2, _ = await create_auth_user(db_session, "admin_cat_ok2", "admin_cat_ok2@test.com")

        report2 = CommentReports(
            comment_id=comment.post_id,
            user_id=user2.user_id,
            status=ReportStatus.PENDING,
        )
        db_session.add(report2)
        await db_session.commit()

        token = await login_user(client, user.username, password)

        # Filter by RULE_VIOLATION
        response = await client.get(
            f"/api/v1/admin/reports?report_type=comment&category={CommentReportCategory.RULE_VIOLATION}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["comment_reports"]) == 1
        assert data["comment_reports"][0]["category"] == CommentReportCategory.RULE_VIOLATION

    async def test_list_report_deleted_comment(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test listing a report for a deleted comment."""
        user, password = await create_auth_user(db_session, "admin_del", "admin_del@test.com")
        await grant_permission(db_session, user.user_id, "report_view")
        image = await create_test_image(db_session, user.user_id)
        comment = await create_test_comment(db_session, user.user_id, image.image_id)

        # Create report
        report = CommentReports(
            comment_id=comment.post_id,
            user_id=user.user_id,
            category=CommentReportCategory.SPAM,
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.commit()

        # Hard delete the comment (to simulate cascade issue, or just manual deletion)
        # But wait, foreign key actions might cascade delete the report too if not configured otherwise.
        # Assuming for this test we simulate a disconnect or data inconsistency,
        # or soft delete if we want to test "deleted" flag.
        # The code checks `row.comment_deleted` which comes from LEFT JOIN NULL or explicit deleted=True.

        # Test 1: Soft deleted
        comment.deleted = True
        await db_session.commit()

        token = await login_user(client, user.username, password)
        response = await client.get(
            "/api/v1/admin/reports?report_type=comment",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        report_item = data["comment_reports"][0]
        assert report_item["comment_deleted"] is True

        # Test 2: Hard deleted (if I can delete it without key violation)
        # Usually reports cascade on delete, so if comment is gone, report is gone.
        # But if we rely on OUTER JOIN, we are prepared for it.
        # I'll just skip hard delete test for now to avoid complexity with DB constraints in test.


    async def test_dismiss_comment_report(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test dismissing a comment report."""
        user, password = await create_auth_user(db_session, "admin2", "admin2@test.com")
        await grant_permission(db_session, user.user_id, "report_manage")
        image = await create_test_image(db_session, user.user_id)
        comment = await create_test_comment(db_session, user.user_id, image.image_id)

        report = CommentReports(
            comment_id=comment.post_id,
            user_id=user.user_id,
            category=CommentReportCategory.SPAM,
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.commit()
        await db_session.refresh(report)

        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/admin/reports/comments/{report.report_id}/dismiss",
            json={"admin_notes": "Not a valid report"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200

        # Verify report was dismissed
        await db_session.refresh(report)
        assert report.status == ReportStatus.DISMISSED

    async def test_delete_comment_via_report(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test deleting a comment via the report action."""
        user, password = await create_auth_user(db_session, "admin3", "admin3@test.com")
        await grant_permission(db_session, user.user_id, "report_manage")
        image = await create_test_image(db_session, user.user_id)
        comment = await create_test_comment(db_session, user.user_id, image.image_id)

        report = CommentReports(
            comment_id=comment.post_id,
            user_id=user.user_id,
            category=CommentReportCategory.RULE_VIOLATION,
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.commit()
        await db_session.refresh(report)

        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/admin/reports/comments/{report.report_id}/delete",
            json={"admin_notes": "Violates rules"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200

        # Verify comment was soft-deleted
        await db_session.refresh(comment)
        assert comment.deleted is True

        # Verify report was marked reviewed
        await db_session.refresh(report)
        assert report.status == ReportStatus.REVIEWED

    async def test_action_on_processed_report(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that actions cannot be taken on already processed reports."""
        user, password = await create_auth_user(db_session, "admin4", "admin4@test.com")
        await grant_permission(db_session, user.user_id, "report_manage")
        image = await create_test_image(db_session, user.user_id)
        comment = await create_test_comment(db_session, user.user_id, image.image_id)

        # Create a report that is already reviewed
        report = CommentReports(
            comment_id=comment.post_id,
            user_id=user.user_id,
            category=CommentReportCategory.RULE_VIOLATION,
            status=ReportStatus.REVIEWED,
        )
        db_session.add(report)
        await db_session.commit()

        token = await login_user(client, user.username, password)

        # Try to dismiss
        response = await client.post(
            f"/api/v1/admin/reports/comments/{report.report_id}/dismiss",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400
        assert "already been processed" in response.json()["detail"]

        # Try to delete
        response = await client.post(
            f"/api/v1/admin/reports/comments/{report.report_id}/delete",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400
        assert "already been processed" in response.json()["detail"]

    async def test_dismiss_requires_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that dismiss requires REPORT_MANAGE permission."""
        user, password = await create_auth_user(db_session, "nonadmin", "nonadmin@test.com")
        # No permission granted
        image = await create_test_image(db_session, user.user_id)
        comment = await create_test_comment(db_session, user.user_id, image.image_id)

        report = CommentReports(
            comment_id=comment.post_id,
            user_id=user.user_id,
            category=CommentReportCategory.SPAM,
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.commit()
        await db_session.refresh(report)

        token = await login_user(client, user.username, password)

        response = await client.post(
            f"/api/v1/admin/reports/comments/{report.report_id}/dismiss",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403
