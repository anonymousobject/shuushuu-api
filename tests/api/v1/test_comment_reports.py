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

from app.config import CommentReportCategory, ImageStatus, ReportStatus
from app.core.security import get_password_hash
from app.models import Comments, CommentReports, Images, Users
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
