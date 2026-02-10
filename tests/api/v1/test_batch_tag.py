"""Tests for POST /api/v1/tags/batch endpoint."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.core.security import create_access_token
from app.models.image import Images
from app.models.permissions import Perms, UserPerms
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.user import Users


async def _create_user_with_tag_permission(db_session: AsyncSession) -> Users:
    """Create a user with IMAGE_TAG_ADD permission."""
    user = Users(
        username="batch_tagger",
        password="hashed_password_here",
        password_type="bcrypt",
        salt="saltsalt12345678",
        email="tagger@example.com",
        active=1,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    perm = Perms(title="image_tag_add", desc="Add tags to images")
    db_session.add(perm)
    await db_session.commit()
    await db_session.refresh(perm)

    user_perm = UserPerms(
        user_id=user.user_id,
        perm_id=perm.perm_id,
        permvalue=1,
    )
    db_session.add(user_perm)
    await db_session.commit()

    return user


async def _create_test_images(db_session: AsyncSession, user: Users, count: int) -> list[Images]:
    """Create test images owned by user."""
    images = []
    for i in range(count):
        image = Images(
            filename=f"batch-test-{i:03d}",
            ext="jpg",
            original_filename=f"batch{i}.jpg",
            md5_hash=f"batch{i:027x}",
            filesize=100000,
            width=800,
            height=600,
            caption=f"Batch test image {i}",
            rating=0.0,
            user_id=user.user_id,
            status=1,
            locked=False,
        )
        db_session.add(image)
        images.append(image)
    await db_session.commit()
    for img in images:
        await db_session.refresh(img)
    return images


async def _create_test_tags(db_session: AsyncSession, count: int) -> list[Tags]:
    """Create test tags."""
    tags = []
    for i in range(count):
        tag = Tags(title=f"Batch Tag {i}", type=TagType.THEME)
        db_session.add(tag)
        tags.append(tag)
    await db_session.commit()
    for t in tags:
        await db_session.refresh(t)
    return tags


@pytest.mark.api
class TestBatchTagValidation:
    """Tests for request validation on POST /api/v1/tags/batch."""

    async def test_rejects_unauthenticated(self, client: AsyncClient):
        """Batch tag endpoint requires authentication."""
        response = await client.post(
            "/api/v1/tags/batch",
            json={"action": "add", "tag_ids": [1], "image_ids": [1]},
        )
        assert response.status_code == 401

    async def test_rejects_empty_tag_ids(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """tag_ids must have at least 1 item."""
        user = await _create_user_with_tag_permission(db_session)
        token = create_access_token(user.id)
        response = await client.post(
            "/api/v1/tags/batch",
            json={"action": "add", "tag_ids": [], "image_ids": [1]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422

    async def test_rejects_too_many_tag_ids(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """tag_ids must have at most 5 items."""
        user = await _create_user_with_tag_permission(db_session)
        token = create_access_token(user.id)
        response = await client.post(
            "/api/v1/tags/batch",
            json={"action": "add", "tag_ids": [1, 2, 3, 4, 5, 6], "image_ids": [1]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422

    async def test_rejects_empty_image_ids(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """image_ids must have at least 1 item."""
        user = await _create_user_with_tag_permission(db_session)
        token = create_access_token(user.id)
        response = await client.post(
            "/api/v1/tags/batch",
            json={"action": "add", "tag_ids": [1], "image_ids": []},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422

    async def test_rejects_too_many_image_ids(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """image_ids must have at most 100 items."""
        user = await _create_user_with_tag_permission(db_session)
        token = create_access_token(user.id)
        response = await client.post(
            "/api/v1/tags/batch",
            json={"action": "add", "tag_ids": [1], "image_ids": list(range(1, 102))},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422

    async def test_rejects_invalid_action(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Only 'add' action is supported."""
        user = await _create_user_with_tag_permission(db_session)
        token = create_access_token(user.id)
        response = await client.post(
            "/api/v1/tags/batch",
            json={"action": "remove", "tag_ids": [1], "image_ids": [1]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422


@pytest.mark.api
class TestBatchTagAdd:
    """Tests for the happy path and skip-and-report behavior."""

    async def test_add_tags_to_images(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Successfully add multiple tags to multiple images."""
        user = await _create_user_with_tag_permission(db_session)
        token = create_access_token(user.id)
        images = await _create_test_images(db_session, user, 3)
        tags = await _create_test_tags(db_session, 2)

        response = await client.post(
            "/api/v1/tags/batch",
            json={
                "action": "add",
                "tag_ids": [t.tag_id for t in tags],
                "image_ids": [img.image_id for img in images],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["added"]) == 6  # 3 images * 2 tags
        assert len(data["skipped"]) == 0

    async def test_skips_nonexistent_images(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Missing image IDs are reported as skipped."""
        user = await _create_user_with_tag_permission(db_session)
        token = create_access_token(user.id)
        images = await _create_test_images(db_session, user, 1)
        tags = await _create_test_tags(db_session, 1)

        response = await client.post(
            "/api/v1/tags/batch",
            json={
                "action": "add",
                "tag_ids": [tags[0].tag_id],
                "image_ids": [images[0].image_id, 999999],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["added"]) == 1
        skipped = data["skipped"]
        assert len(skipped) == 1
        assert skipped[0]["image_id"] == 999999
        assert skipped[0]["reason"] == "image_not_found"

    async def test_skips_nonexistent_tags(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Missing tag IDs are reported as skipped."""
        user = await _create_user_with_tag_permission(db_session)
        token = create_access_token(user.id)
        images = await _create_test_images(db_session, user, 1)

        response = await client.post(
            "/api/v1/tags/batch",
            json={
                "action": "add",
                "tag_ids": [999999],
                "image_ids": [images[0].image_id],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["added"]) == 0
        skipped = data["skipped"]
        assert len(skipped) == 1
        assert skipped[0]["reason"] == "tag_not_found"

    async def test_skips_already_tagged(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Already-existing tag links are reported as skipped."""
        user = await _create_user_with_tag_permission(db_session)
        token = create_access_token(user.id)
        images = await _create_test_images(db_session, user, 1)
        tags = await _create_test_tags(db_session, 1)

        # Pre-link the tag
        existing_link = TagLinks(
            image_id=images[0].image_id,
            tag_id=tags[0].tag_id,
            user_id=user.user_id,
        )
        db_session.add(existing_link)
        await db_session.commit()

        response = await client.post(
            "/api/v1/tags/batch",
            json={
                "action": "add",
                "tag_ids": [tags[0].tag_id],
                "image_ids": [images[0].image_id],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["added"]) == 0
        assert len(data["skipped"]) == 1
        assert data["skipped"][0]["reason"] == "already_tagged"

    async def test_resolves_alias_tags(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Alias tags resolve to their canonical tag."""
        user = await _create_user_with_tag_permission(db_session)
        token = create_access_token(user.id)
        images = await _create_test_images(db_session, user, 1)

        # Create canonical tag and alias
        canonical = Tags(title="Canonical Tag", type=TagType.THEME)
        db_session.add(canonical)
        await db_session.commit()
        await db_session.refresh(canonical)

        alias = Tags(title="Alias Tag", type=TagType.THEME, alias_of=canonical.tag_id)
        db_session.add(alias)
        await db_session.commit()
        await db_session.refresh(alias)

        response = await client.post(
            "/api/v1/tags/batch",
            json={
                "action": "add",
                "tag_ids": [alias.tag_id],
                "image_ids": [images[0].image_id],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["added"]) == 1
        # The added pair should use the canonical tag_id
        assert data["added"][0]["tag_id"] == canonical.tag_id

    async def test_creates_tag_history(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Each new tag link creates a tag history entry."""
        from sqlalchemy import func, select

        from app.models.tag_history import TagHistory

        user = await _create_user_with_tag_permission(db_session)
        token = create_access_token(user.id)
        images = await _create_test_images(db_session, user, 2)
        tags = await _create_test_tags(db_session, 1)

        response = await client.post(
            "/api/v1/tags/batch",
            json={
                "action": "add",
                "tag_ids": [tags[0].tag_id],
                "image_ids": [img.image_id for img in images],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

        # Verify history entries were created
        result = await db_session.execute(
            select(func.count(TagHistory.tag_history_id)).where(
                TagHistory.tag_id == tags[0].tag_id,
                TagHistory.action == "a",
            )
        )
        count = result.scalar()
        assert count == 2

    async def test_requires_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Users without IMAGE_TAG_ADD permission get 403."""
        # Create user WITHOUT the permission
        user = Users(
            username="no_perms_user",
            password="hashed_password_here",
            password_type="bcrypt",
            salt="saltsalt12345678",
            email="noperms@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        token = create_access_token(user.id)
        response = await client.post(
            "/api/v1/tags/batch",
            json={"action": "add", "tag_ids": [1], "image_ids": [1]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403
