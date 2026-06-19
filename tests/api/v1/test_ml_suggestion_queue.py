"""
Tests for the cross-image ML suggestion review queue API endpoints.

These tests cover:
- GET  /api/v1/ml-suggestions/tags    (worklist counts per tag)
- GET  /api/v1/ml-suggestions         (paginated per-tag suggestion grid)
- POST /api/v1/ml-suggestions/review  (cross-image bulk approve/reject)

Plus the security-critical permission gate, which must admit:
- non-admin users holding IMAGE_TAG_ADD, and
- admins WITHOUT IMAGE_TAG_ADD (the standalone admin flag),
and reject everyone else with 403.

All tests use the real test DB and the app test client; the only mocking is
the shared mock_redis fixture (a cache miss falls back to the real DB perm
query), mirroring tests/api/v1/test_ml_tag_suggestions.py.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.core.security import create_access_token
from app.models.image import Images
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.permissions import Perms, UserPerms
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.user import Users


async def _make_user(db: AsyncSession, suffix: str, admin: bool = False) -> Users:
    """Create a plain active user (no permissions, optionally admin)."""
    user = Users(
        username=f"queue_api_{suffix}",
        email=f"queue_api_{suffix}@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="testsalt12345678",
        active=1,
        admin=admin,
    )
    db.add(user)
    await db.flush()
    return user


async def _grant_image_tag_add(db: AsyncSession, user: Users) -> None:
    """Grant IMAGE_TAG_ADD to a user via a direct user-permission override.

    Uses get-or-create on the perm row so multiple users in one test (and the
    enum-seeded perms table) don't collide on the unique perm title.
    """
    perm = (
        await db.execute(select(Perms).where(Perms.title == "image_tag_add"))
    ).scalar_one_or_none()
    if perm is None:
        perm = Perms(title="image_tag_add", desc="Add tags to images")
        db.add(perm)
        await db.flush()
    db.add(UserPerms(user_id=user.user_id, perm_id=perm.perm_id, permvalue=1))
    await db.flush()


async def _make_image(db: AsyncSession, user: Users, suffix: str) -> Images:
    image = Images(
        filename=f"2024-01-01-qapi-{suffix}",
        ext="jpg",
        user_id=user.user_id,
        md5_hash=f"qapi_hash_{suffix}",
        filesize=1024,
        width=800,
        height=600,
    )
    db.add(image)
    await db.flush()
    return image


async def _make_tag(
    db: AsyncSession, user: Users, suffix: str, tag_type: int = TagType.THEME
) -> Tags:
    tag = Tags(title=f"qapi tag {suffix}", type=tag_type, user_id=user.user_id)
    db.add(tag)
    await db.flush()
    return tag


async def _make_suggestion(
    db: AsyncSession,
    image: Images,
    tag: Tags,
    confidence: float = 0.88,
    status: str = "pending",
) -> MlTagSuggestions:
    suggestion = MlTagSuggestions(
        image_id=image.image_id,
        tag_id=tag.tag_id,
        confidence=confidence,
        model_version="v3",
        status=status,
    )
    db.add(suggestion)
    await db.flush()
    return suggestion


@pytest.mark.api
class TestSuggestionWorklistEndpoint:
    """GET /api/v1/ml-suggestions/tags."""

    async def test_returns_worklist_counts(self, client: AsyncClient, db_session: AsyncSession):
        """A tag-edit user gets worklist items with pending counts."""
        user = await _make_user(db_session, "wl1")
        await _grant_image_tag_add(db_session, user)
        image1 = await _make_image(db_session, user, "wl1a")
        image2 = await _make_image(db_session, user, "wl1b")
        tag = await _make_tag(db_session, user, "wl1_tag")
        await _make_suggestion(db_session, image1, tag)
        await _make_suggestion(db_session, image2, tag)
        await db_session.commit()

        token = create_access_token(user_id=user.user_id)
        response = await client.get(
            "/api/v1/ml-suggestions/tags",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        items = response.json()
        row = next((r for r in items if r["tag_id"] == tag.tag_id), None)
        assert row is not None
        assert row["title"] == tag.title
        assert row["type"] == TagType.THEME
        assert row["pending_count"] == 2

    async def test_type_filter_narrows_worklist(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """?type=4 returns only character-type tags."""
        user = await _make_user(db_session, "wl2")
        await _grant_image_tag_add(db_session, user)
        image1 = await _make_image(db_session, user, "wl2a")
        image2 = await _make_image(db_session, user, "wl2b")
        theme_tag = await _make_tag(db_session, user, "wl2_theme", tag_type=TagType.THEME)
        char_tag = await _make_tag(db_session, user, "wl2_char", tag_type=TagType.CHARACTER)
        await _make_suggestion(db_session, image1, theme_tag)
        await _make_suggestion(db_session, image2, char_tag)
        await db_session.commit()

        token = create_access_token(user_id=user.user_id)
        response = await client.get(
            f"/api/v1/ml-suggestions/tags?type={TagType.CHARACTER}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        items = response.json()
        tag_ids = [r["tag_id"] for r in items]
        assert char_tag.tag_id in tag_ids
        assert theme_tag.tag_id not in tag_ids
        for row in items:
            assert row["type"] == TagType.CHARACTER


@pytest.mark.api
class TestSuggestionGridEndpoint:
    """GET /api/v1/ml-suggestions (per-tag paginated grid)."""

    async def test_returns_items_with_computed_thumbnail(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Items carry suggestion_id, confidence, and a serialized image with thumbnail_url."""
        user = await _make_user(db_session, "grid1")
        await _grant_image_tag_add(db_session, user)
        image_hi = await _make_image(db_session, user, "grid1_hi")
        image_lo = await _make_image(db_session, user, "grid1_lo")
        tag = await _make_tag(db_session, user, "grid1_tag")
        # Two pending suggestions on the same tag, different confidence + images.
        await _make_suggestion(db_session, image_hi, tag, confidence=0.95)
        await _make_suggestion(db_session, image_lo, tag, confidence=0.80)
        await db_session.commit()

        token = create_access_token(user_id=user.user_id)
        response = await client.get(
            f"/api/v1/ml-suggestions?tag_id={tag.tag_id}&min_confidence=0.7&page=1&per_page=50",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert data["page"] == 1
        assert len(data["items"]) == 2

        # Sorted by confidence DESC: 0.95 first.
        assert data["items"][0]["confidence"] == 0.95
        assert data["items"][1]["confidence"] == 0.80
        assert data["items"][0]["image"]["image_id"] == image_hi.image_id

        # The image must be serialized via ImageResponse -> computed thumbnail_url
        # (a full URL, NOT a raw filename).
        thumb = data["items"][0]["image"]["thumbnail_url"]
        assert thumb
        assert thumb.endswith(f"/thumbs/{image_hi.filename}.webp")
        assert "/" in thumb  # a URL path, not just the bare filename

    async def test_min_confidence_filters_grid(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """min_confidence excludes suggestions below the threshold."""
        user = await _make_user(db_session, "grid2")
        await _grant_image_tag_add(db_session, user)
        image1 = await _make_image(db_session, user, "grid2a")
        image2 = await _make_image(db_session, user, "grid2b")
        tag = await _make_tag(db_session, user, "grid2_tag")
        await _make_suggestion(db_session, image1, tag, confidence=0.9)
        await _make_suggestion(db_session, image2, tag, confidence=0.5)
        await db_session.commit()

        token = create_access_token(user_id=user.user_id)
        response = await client.get(
            f"/api/v1/ml-suggestions?tag_id={tag.tag_id}&min_confidence=0.7",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["confidence"] == 0.9


@pytest.mark.api
class TestSuggestionReviewEndpoint:
    """POST /api/v1/ml-suggestions/review (cross-image bulk review)."""

    async def test_approve_applies_tag(self, client: AsyncClient, db_session: AsyncSession):
        """Approving a suggestion creates the TagLink and returns counts."""
        user = await _make_user(db_session, "rev1")
        await _grant_image_tag_add(db_session, user)
        image = await _make_image(db_session, user, "rev1_img")
        tag = await _make_tag(db_session, user, "rev1_tag")
        suggestion = await _make_suggestion(db_session, image, tag)
        await db_session.commit()

        token = create_access_token(user_id=user.user_id)
        response = await client.post(
            "/api/v1/ml-suggestions/review",
            json=[{"suggestion_id": suggestion.suggestion_id, "action": "approve"}],
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["approved"] == 1
        assert data["rejected"] == 0
        assert data["errors"] == []

        # Verify the tag was actually applied.
        link = (
            await db_session.execute(
                select(TagLinks).where(
                    TagLinks.image_id == image.image_id,
                    TagLinks.tag_id == tag.tag_id,
                )
            )
        ).scalar_one_or_none()
        assert link is not None
        assert link.user_id == user.user_id

        await db_session.refresh(suggestion)
        assert suggestion.status == "approved"

    async def test_bulk_mixed_across_images(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Approve on one image, reject on another, in a single request."""
        user = await _make_user(db_session, "rev2")
        await _grant_image_tag_add(db_session, user)
        image1 = await _make_image(db_session, user, "rev2a")
        image2 = await _make_image(db_session, user, "rev2b")
        tag1 = await _make_tag(db_session, user, "rev2_t1")
        tag2 = await _make_tag(db_session, user, "rev2_t2")
        s1 = await _make_suggestion(db_session, image1, tag1)
        s2 = await _make_suggestion(db_session, image2, tag2)
        await db_session.commit()

        token = create_access_token(user_id=user.user_id)
        response = await client.post(
            "/api/v1/ml-suggestions/review",
            json=[
                {"suggestion_id": s1.suggestion_id, "action": "approve"},
                {"suggestion_id": s2.suggestion_id, "action": "reject"},
            ],
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["approved"] == 1
        assert data["rejected"] == 1
        assert data["errors"] == []

        # tag1 applied to image1, tag2 NOT applied to image2.
        link1 = (
            await db_session.execute(
                select(TagLinks).where(TagLinks.image_id == image1.image_id)
            )
        ).scalars().all()
        assert {link.tag_id for link in link1} == {tag1.tag_id}

        link2 = (
            await db_session.execute(
                select(TagLinks).where(TagLinks.image_id == image2.image_id)
            )
        ).scalars().all()
        assert link2 == []

    async def test_missing_suggestion_reported_in_errors(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """A non-existent suggestion_id lands in errors; valid ones still process."""
        user = await _make_user(db_session, "rev3")
        await _grant_image_tag_add(db_session, user)
        image = await _make_image(db_session, user, "rev3_img")
        tag = await _make_tag(db_session, user, "rev3_tag")
        suggestion = await _make_suggestion(db_session, image, tag)
        await db_session.commit()

        token = create_access_token(user_id=user.user_id)
        response = await client.post(
            "/api/v1/ml-suggestions/review",
            json=[
                {"suggestion_id": suggestion.suggestion_id, "action": "approve"},
                {"suggestion_id": 99999, "action": "approve"},
            ],
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["approved"] == 1
        assert len(data["errors"]) == 1
        assert "not found" in data["errors"][0].lower()


@pytest.mark.api
class TestSuggestionQueuePermissions:
    """The security-critical admin-OR-permission gate on all three endpoints."""

    async def _seed_one_pending(self, db: AsyncSession, suffix: str):
        """Owner user + image + tag + a pending suggestion; returns (tag, suggestion)."""
        owner = await _make_user(db, f"owner_{suffix}")
        image = await _make_image(db, owner, suffix)
        tag = await _make_tag(db, owner, f"tag_{suffix}")
        suggestion = await _make_suggestion(db, image, tag)
        return tag, suggestion

    async def test_non_perm_non_admin_user_forbidden_on_all_endpoints(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """(a) A user without IMAGE_TAG_ADD and not admin → 403 on all three."""
        tag, suggestion = await self._seed_one_pending(db_session, "perm_a")
        plain = await _make_user(db_session, "plain_a")  # no perms, not admin
        await db_session.commit()

        token = create_access_token(user_id=plain.user_id)
        headers = {"Authorization": f"Bearer {token}"}

        r_tags = await client.get("/api/v1/ml-suggestions/tags", headers=headers)
        assert r_tags.status_code == 403

        r_grid = await client.get(
            f"/api/v1/ml-suggestions?tag_id={tag.tag_id}", headers=headers
        )
        assert r_grid.status_code == 403

        r_review = await client.post(
            "/api/v1/ml-suggestions/review",
            json=[{"suggestion_id": suggestion.suggestion_id, "action": "approve"}],
            headers=headers,
        )
        assert r_review.status_code == 403

    async def test_admin_without_perm_allowed_on_all_endpoints(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """(b) An admin WITHOUT image_tag_add → 200 (NOT 403) on all three.

        This proves the gate is `admin OR has_permission`, not permission alone.
        """
        tag, suggestion = await self._seed_one_pending(db_session, "perm_b")
        admin = await _make_user(db_session, "admin_b", admin=True)  # admin, no perm
        await db_session.commit()

        token = create_access_token(user_id=admin.user_id)
        headers = {"Authorization": f"Bearer {token}"}

        r_tags = await client.get("/api/v1/ml-suggestions/tags", headers=headers)
        assert r_tags.status_code == 200

        r_grid = await client.get(
            f"/api/v1/ml-suggestions?tag_id={tag.tag_id}", headers=headers
        )
        assert r_grid.status_code == 200

        r_review = await client.post(
            "/api/v1/ml-suggestions/review",
            json=[{"suggestion_id": suggestion.suggestion_id, "action": "approve"}],
            headers=headers,
        )
        assert r_review.status_code == 200
        assert r_review.json()["approved"] == 1

    async def test_non_admin_with_perm_allowed_on_all_endpoints(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """(c) A non-admin user WITH IMAGE_TAG_ADD → 200 on all three."""
        tag, suggestion = await self._seed_one_pending(db_session, "perm_c")
        tagger = await _make_user(db_session, "tagger_c")  # not admin
        await _grant_image_tag_add(db_session, tagger)
        await db_session.commit()

        token = create_access_token(user_id=tagger.user_id)
        headers = {"Authorization": f"Bearer {token}"}

        r_tags = await client.get("/api/v1/ml-suggestions/tags", headers=headers)
        assert r_tags.status_code == 200

        r_grid = await client.get(
            f"/api/v1/ml-suggestions?tag_id={tag.tag_id}", headers=headers
        )
        assert r_grid.status_code == 200

        r_review = await client.post(
            "/api/v1/ml-suggestions/review",
            json=[{"suggestion_id": suggestion.suggestion_id, "action": "approve"}],
            headers=headers,
        )
        assert r_review.status_code == 200
        assert r_review.json()["approved"] == 1

    async def test_review_requires_auth(self, client: AsyncClient):
        """Unauthenticated review request → 401."""
        response = await client.post(
            "/api/v1/ml-suggestions/review",
            json=[{"suggestion_id": 1, "action": "approve"}],
        )
        assert response.status_code == 401
