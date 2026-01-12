"""Tests for groups in comment API responses."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.comment import Comments
from app.models.image import Images
from app.models.permissions import Groups, UserGroups


@pytest.mark.api
class TestCommentUserGroups:
    """Tests for user groups in comment API responses."""

    async def test_list_comments_includes_user_groups(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Comments list endpoint returns user's groups."""
        # Create a group and add user 1 to it
        group = Groups(title="commenters", desc="Commenters")
        db_session.add(group)
        await db_session.flush()

        user_group = UserGroups(user_id=1, group_id=group.group_id)
        db_session.add(user_group)

        # Create an image
        image = Images(
            filename="test-comment-groups-001",
            ext="jpg",
            original_filename="test.jpg",
            md5_hash="cgroups001hash",
            filesize=1000,
            width=100,
            height=100,
            user_id=1,
            status=1,
            locked=0,
        )
        db_session.add(image)
        await db_session.flush()

        # Create a comment
        comment = Comments(
            image_id=image.image_id,
            user_id=1,
            post_text="Test comment with groups",
        )
        db_session.add(comment)
        await db_session.commit()

        response = await client.get("/api/v1/comments")
        assert response.status_code == 200

        data = response.json()
        assert len(data["comments"]) >= 1

        # Find our comment
        test_comment = next(
            (c for c in data["comments"] if c["post_text"] == "Test comment with groups"),
            None,
        )
        assert test_comment is not None
        assert test_comment["user"]["groups"] == ["commenters"]

    async def test_get_comment_includes_user_groups(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Single comment endpoint returns user's groups."""
        # Create a group and add user 1 to it
        group = Groups(title="single_commenters", desc="Single Commenters")
        db_session.add(group)
        await db_session.flush()

        user_group = UserGroups(user_id=1, group_id=group.group_id)
        db_session.add(user_group)

        # Create an image
        image = Images(
            filename="test-comment-groups-002",
            ext="jpg",
            original_filename="test.jpg",
            md5_hash="cgroups002hash",
            filesize=1000,
            width=100,
            height=100,
            user_id=1,
            status=1,
            locked=0,
        )
        db_session.add(image)
        await db_session.flush()

        # Create a comment
        comment = Comments(
            image_id=image.image_id,
            user_id=1,
            post_text="Single comment with groups",
        )
        db_session.add(comment)
        await db_session.commit()
        await db_session.refresh(comment)

        response = await client.get(f"/api/v1/comments/{comment.post_id}")
        assert response.status_code == 200

        data = response.json()
        assert data["user"]["groups"] == ["single_commenters"]

    async def test_get_image_comments_includes_user_groups(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Image comments endpoint returns user's groups."""
        # Create a group and add user 1 to it
        group = Groups(title="image_commenters", desc="Image Commenters")
        db_session.add(group)
        await db_session.flush()

        user_group = UserGroups(user_id=1, group_id=group.group_id)
        db_session.add(user_group)

        # Create an image
        image = Images(
            filename="test-comment-groups-003",
            ext="jpg",
            original_filename="test.jpg",
            md5_hash="cgroups003hash",
            filesize=1000,
            width=100,
            height=100,
            user_id=1,
            status=1,
            locked=0,
        )
        db_session.add(image)
        await db_session.flush()

        # Create a comment
        comment = Comments(
            image_id=image.image_id,
            user_id=1,
            post_text="Image comment with groups",
        )
        db_session.add(comment)
        await db_session.commit()
        await db_session.refresh(image)

        response = await client.get(f"/api/v1/comments/image/{image.image_id}")
        assert response.status_code == 200

        data = response.json()
        assert len(data["comments"]) >= 1

        # Find our comment
        test_comment = next(
            (c for c in data["comments"] if c["post_text"] == "Image comment with groups"),
            None,
        )
        assert test_comment is not None
        assert test_comment["user"]["groups"] == ["image_commenters"]

    async def test_get_user_comments_includes_user_groups(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """User comments endpoint returns user's groups."""
        # Create a group and add user 1 to it
        group = Groups(title="user_commenters", desc="User Commenters")
        db_session.add(group)
        await db_session.flush()

        user_group = UserGroups(user_id=1, group_id=group.group_id)
        db_session.add(user_group)

        # Create an image
        image = Images(
            filename="test-comment-groups-004",
            ext="jpg",
            original_filename="test.jpg",
            md5_hash="cgroups004hash",
            filesize=1000,
            width=100,
            height=100,
            user_id=1,
            status=1,
            locked=0,
        )
        db_session.add(image)
        await db_session.flush()

        # Create a comment
        comment = Comments(
            image_id=image.image_id,
            user_id=1,
            post_text="User comment with groups",
        )
        db_session.add(comment)
        await db_session.commit()

        response = await client.get("/api/v1/comments/user/1")
        assert response.status_code == 200

        data = response.json()
        assert len(data["comments"]) >= 1

        # Find our comment
        test_comment = next(
            (c for c in data["comments"] if c["post_text"] == "User comment with groups"),
            None,
        )
        assert test_comment is not None
        assert test_comment["user"]["groups"] == ["user_commenters"]

    async def test_user_with_no_groups_returns_empty_list(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Comment by user without groups returns empty groups list."""
        # Create an image (user 1 has no groups in this test)
        image = Images(
            filename="test-comment-groups-005",
            ext="jpg",
            original_filename="test.jpg",
            md5_hash="cgroups005hash",
            filesize=1000,
            width=100,
            height=100,
            user_id=1,
            status=1,
            locked=0,
        )
        db_session.add(image)
        await db_session.flush()

        # Create a comment (user has no groups)
        comment = Comments(
            image_id=image.image_id,
            user_id=1,
            post_text="No groups comment",
        )
        db_session.add(comment)
        await db_session.commit()
        await db_session.refresh(comment)

        response = await client.get(f"/api/v1/comments/{comment.post_id}")
        assert response.status_code == 200

        data = response.json()
        assert data["user"]["groups"] == []
