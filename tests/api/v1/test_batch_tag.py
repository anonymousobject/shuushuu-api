"""Tests for POST /api/v1/tags/batch endpoint."""

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.core.security import create_access_token
from app.models.image import Images
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.permissions import Perms, UserPerms
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.user import Users
from app.services.tag_type_flags import refresh_image_tag_type_flags


async def _create_user_with_tag_permission(
    db_session: AsyncSession, perms: list[str] | None = None
) -> Users:
    """Create a user with specified tag permissions (defaults to IMAGE_TAG_ADD)."""
    if perms is None:
        perms = ["image_tag_add"]
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

    perm_descs = {
        "image_tag_add": "Add tags to images",
        "image_tag_remove": "Remove tags from images",
    }
    for perm_title in perms:
        perm = Perms(title=perm_title, desc=perm_descs.get(perm_title, perm_title))
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
        """Only 'add' and 'remove' actions are supported."""
        user = await _create_user_with_tag_permission(db_session)
        token = create_access_token(user.id)
        response = await client.post(
            "/api/v1/tags/batch",
            json={"action": "replace", "tag_ids": [1], "image_ids": [1]},
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


@pytest.mark.api
class TestBatchTagRemove:
    """Tests for batch tag removal."""

    async def test_remove_tags_from_images(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Successfully remove multiple tags from multiple images."""
        user = await _create_user_with_tag_permission(db_session, ["image_tag_remove"])
        token = create_access_token(user.id)
        images = await _create_test_images(db_session, user, 2)
        tags = await _create_test_tags(db_session, 2)

        # Pre-link all tags to all images
        for img in images:
            for tag in tags:
                db_session.add(
                    TagLinks(image_id=img.image_id, tag_id=tag.tag_id, user_id=user.user_id)
                )
        await db_session.commit()

        response = await client.post(
            "/api/v1/tags/batch",
            json={
                "action": "remove",
                "tag_ids": [t.tag_id for t in tags],
                "image_ids": [img.image_id for img in images],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["removed"]) == 4  # 2 images * 2 tags
        assert len(data["skipped"]) == 0

    async def test_skips_not_tagged(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Tags not linked to images are reported as skipped."""
        user = await _create_user_with_tag_permission(db_session, ["image_tag_remove"])
        token = create_access_token(user.id)
        images = await _create_test_images(db_session, user, 1)
        tags = await _create_test_tags(db_session, 1)

        response = await client.post(
            "/api/v1/tags/batch",
            json={
                "action": "remove",
                "tag_ids": [tags[0].tag_id],
                "image_ids": [images[0].image_id],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["removed"]) == 0
        assert len(data["skipped"]) == 1
        assert data["skipped"][0]["reason"] == "not_tagged"

    async def test_skips_nonexistent_images(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Missing image IDs are reported as skipped."""
        user = await _create_user_with_tag_permission(db_session, ["image_tag_remove"])
        token = create_access_token(user.id)
        tags = await _create_test_tags(db_session, 1)

        response = await client.post(
            "/api/v1/tags/batch",
            json={
                "action": "remove",
                "tag_ids": [tags[0].tag_id],
                "image_ids": [999999],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["removed"]) == 0
        assert len(data["skipped"]) == 1
        assert data["skipped"][0]["reason"] == "image_not_found"

    async def test_skips_nonexistent_tags(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Missing tag IDs are reported as skipped."""
        user = await _create_user_with_tag_permission(db_session, ["image_tag_remove"])
        token = create_access_token(user.id)
        images = await _create_test_images(db_session, user, 1)

        response = await client.post(
            "/api/v1/tags/batch",
            json={
                "action": "remove",
                "tag_ids": [999999],
                "image_ids": [images[0].image_id],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["removed"]) == 0
        assert len(data["skipped"]) == 1
        assert data["skipped"][0]["reason"] == "tag_not_found"

    async def test_creates_removal_history(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Each removed tag link creates a tag history entry with action 'r'."""
        from app.models.tag_history import TagHistory

        user = await _create_user_with_tag_permission(db_session, ["image_tag_remove"])
        token = create_access_token(user.id)
        images = await _create_test_images(db_session, user, 1)
        tags = await _create_test_tags(db_session, 1)

        # Pre-link
        db_session.add(
            TagLinks(image_id=images[0].image_id, tag_id=tags[0].tag_id, user_id=user.user_id)
        )
        await db_session.commit()

        response = await client.post(
            "/api/v1/tags/batch",
            json={
                "action": "remove",
                "tag_ids": [tags[0].tag_id],
                "image_ids": [images[0].image_id],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

        result = await db_session.execute(
            select(func.count(TagHistory.tag_history_id)).where(
                TagHistory.tag_id == tags[0].tag_id,
                TagHistory.action == "r",
            )
        )
        assert result.scalar() == 1

    async def test_resolves_alias_tags(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Alias tags resolve to their canonical tag for removal."""
        user = await _create_user_with_tag_permission(db_session, ["image_tag_remove"])
        token = create_access_token(user.id)
        images = await _create_test_images(db_session, user, 1)

        # Create canonical tag and alias
        canonical = Tags(title="Canonical Remove Tag", type=TagType.THEME)
        db_session.add(canonical)
        await db_session.commit()
        await db_session.refresh(canonical)

        alias = Tags(title="Alias Remove Tag", type=TagType.THEME, alias_of=canonical.tag_id)
        db_session.add(alias)
        await db_session.commit()
        await db_session.refresh(alias)

        # Link canonical tag to image
        db_session.add(
            TagLinks(
                image_id=images[0].image_id, tag_id=canonical.tag_id, user_id=user.user_id
            )
        )
        await db_session.commit()

        # Remove using alias tag ID
        response = await client.post(
            "/api/v1/tags/batch",
            json={
                "action": "remove",
                "tag_ids": [alias.tag_id],
                "image_ids": [images[0].image_id],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["removed"]) == 1
        assert data["removed"][0]["tag_id"] == canonical.tag_id

        # Verify the link was actually deleted
        link_result = await db_session.execute(
            select(TagLinks).where(
                TagLinks.image_id == images[0].image_id,
                TagLinks.tag_id == canonical.tag_id,
            )
        )
        assert link_result.scalar_one_or_none() is None

        # Verify history was written for canonical tag
        from app.models.tag_history import TagHistory

        history_result = await db_session.execute(
            select(TagHistory).where(
                TagHistory.image_id == images[0].image_id,
                TagHistory.tag_id == canonical.tag_id,
                TagHistory.action == "r",
            )
        )
        assert history_result.scalar_one_or_none() is not None

    async def test_requires_remove_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Users without IMAGE_TAG_REMOVE permission get 403."""
        # User with only ADD permission, not REMOVE
        user = await _create_user_with_tag_permission(db_session, ["image_tag_add"])
        token = create_access_token(user.id)
        response = await client.post(
            "/api/v1/tags/batch",
            json={"action": "remove", "tag_ids": [1], "image_ids": [1]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403


@pytest.mark.api
class TestBatchTagTypeFlags:
    """Tests that batch add/remove keep has_* tag-type flags correct."""

    async def test_batch_add_sets_has_artist_flag(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Batch-adding an ARTIST tag sets has_artist=True on all affected images."""
        user = await _create_user_with_tag_permission(db_session, ["image_tag_add"])
        token = create_access_token(user.id)
        images = await _create_test_images(db_session, user, 2)

        artist_tag = Tags(title="Test Artist", type=TagType.ARTIST)
        db_session.add(artist_tag)
        await db_session.commit()
        await db_session.refresh(artist_tag)

        response = await client.post(
            "/api/v1/tags/batch",
            json={
                "action": "add",
                "tag_ids": [artist_tag.tag_id],
                "image_ids": [img.image_id for img in images],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert len(response.json()["added"]) == 2

        # Fresh reads — bypass identity map
        for img in images:
            result = await db_session.execute(
                select(Images)
                .where(Images.image_id == img.image_id)
                .execution_options(populate_existing=True)
            )
            fresh = result.scalar_one()
            assert fresh.has_artist is True, (
                f"image {img.image_id} has_artist should be True after batch add"
            )

    async def test_batch_remove_clears_has_artist_flag(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Batch-removing the only ARTIST tag clears has_artist=False on all affected images."""
        user = await _create_user_with_tag_permission(
            db_session, ["image_tag_add", "image_tag_remove"]
        )
        token = create_access_token(user.id)
        images = await _create_test_images(db_session, user, 2)

        artist_tag = Tags(title="Test Artist Remove", type=TagType.ARTIST)
        db_session.add(artist_tag)
        await db_session.commit()
        await db_session.refresh(artist_tag)

        # Pre-link the artist tag to both images
        for img in images:
            db_session.add(
                TagLinks(
                    image_id=img.image_id,
                    tag_id=artist_tag.tag_id,
                    user_id=user.user_id,
                )
            )
        await db_session.commit()

        # Establish the real pre-state: recompute flags so has_artist=True
        for img in images:
            await refresh_image_tag_type_flags(db_session, img.image_id)
        await db_session.commit()

        # Precondition: has_artist must be True before the remove
        for img in images:
            result = await db_session.execute(
                select(Images)
                .where(Images.image_id == img.image_id)
                .execution_options(populate_existing=True)
            )
            pre = result.scalar_one()
            assert pre.has_artist is True, (
                f"image {img.image_id} has_artist should be True before batch remove"
            )

        response = await client.post(
            "/api/v1/tags/batch",
            json={
                "action": "remove",
                "tag_ids": [artist_tag.tag_id],
                "image_ids": [img.image_id for img in images],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert len(response.json()["removed"]) == 2

        # Fresh reads — bypass identity map
        for img in images:
            result = await db_session.execute(
                select(Images)
                .where(Images.image_id == img.image_id)
                .execution_options(populate_existing=True)
            )
            fresh = result.scalar_one()
            assert fresh.has_artist is False, (
                f"image {img.image_id} has_artist should be False after batch remove"
            )


@pytest.mark.api
class TestBatchAddApprovesMlSuggestions:
    """Batch-adding tags must resolve matching pending ML suggestions."""

    async def test_batch_add_approves_matching_pending_suggestions(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Pending suggestions on all batch-tagged images are marked approved."""
        user = await _create_user_with_tag_permission(db_session)
        images = await _create_test_images(db_session, user, 2)
        tag = Tags(title="batch_ml_sugg_tag", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        suggestions = []
        for img in images:
            s = MlTagSuggestions(
                image_id=img.image_id,
                tag_id=tag.tag_id,
                confidence=0.9,
                model_version="v3",
                status="pending",
            )
            db_session.add(s)
            suggestions.append(s)
        await db_session.commit()
        for s in suggestions:
            await db_session.refresh(s)

        token = create_access_token(user.id)
        response = await client.post(
            "/api/v1/tags/batch",
            json={
                "action": "add",
                "tag_ids": [tag.tag_id],
                "image_ids": [img.image_id for img in images],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert len(response.json()["added"]) == 2

        for s in suggestions:
            await db_session.refresh(s)
            assert s.status == "approved"
            assert s.reviewed_by_user_id == user.user_id
