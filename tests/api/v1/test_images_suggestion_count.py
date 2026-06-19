"""
Tests for ml_suggestion_count on the image list endpoint.

Verifies that GET /api/v1/images returns ml_suggestion_count on each
ImageDetailedResponse item, but ONLY for users who hold IMAGE_TAG_ADD or are
admins. Anonymous users and plain (no-perm, non-admin) users always see None.
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
from app.models.user import Users


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db: AsyncSession, suffix: str, admin: bool = False) -> Users:
    user = Users(
        username=f"imgcount_{suffix}",
        email=f"imgcount_{suffix}@example.com",
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
        filename=f"2024-01-01-imgcount-{suffix}",
        ext="jpg",
        user_id=user.user_id,
        md5_hash=f"imgcount_hash_{suffix}",
        filesize=1024,
        width=800,
        height=600,
    )
    db.add(image)
    await db.flush()
    return image


async def _make_tag(db: AsyncSession, user: Users, suffix: str) -> Tags:
    tag = Tags(title=f"imgcount tag {suffix}", type=TagType.THEME, user_id=user.user_id)
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


async def _list_images(client: AsyncClient, headers: dict | None = None) -> list[dict]:
    """Call GET /api/v1/images and return the images list."""
    r = await client.get("/api/v1/images", headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()["images"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.api
class TestMlSuggestionCountOnImageList:
    """ml_suggestion_count is populated for permitted users only."""

    async def test_tagger_sees_pending_count(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """A user with IMAGE_TAG_ADD sees ml_suggestion_count == N for their image."""
        owner = await _make_user(db_session, "tc_tagger_owner")
        tagger = await _make_user(db_session, "tc_tagger")
        await _grant_image_tag_add(db_session, tagger)

        image = await _make_image(db_session, owner, "tc_tagger_img")
        tag1 = await _make_tag(db_session, owner, "tc_tagger_t1")
        tag2 = await _make_tag(db_session, owner, "tc_tagger_t2")
        await _make_suggestion(db_session, image, tag1, status="pending")
        await _make_suggestion(db_session, image, tag2, status="pending")
        # Also add a non-pending suggestion to confirm only 'pending' is counted.
        tag3 = await _make_tag(db_session, owner, "tc_tagger_t3")
        await _make_suggestion(db_session, image, tag3, status="approved")
        await db_session.commit()

        token = create_access_token(user_id=tagger.user_id)
        headers = {"Authorization": f"Bearer {token}"}
        items = await _list_images(client, headers)

        matching = [i for i in items if i["image_id"] == image.image_id]
        assert matching, "Seeded image not found in list"
        assert matching[0]["ml_suggestion_count"] == 2

    async def test_plain_user_sees_none(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """A user without IMAGE_TAG_ADD (and not admin) gets ml_suggestion_count=None."""
        owner = await _make_user(db_session, "tc_plain_owner")
        plain = await _make_user(db_session, "tc_plain")

        image = await _make_image(db_session, owner, "tc_plain_img")
        tag = await _make_tag(db_session, owner, "tc_plain_t")
        await _make_suggestion(db_session, image, tag, status="pending")
        await db_session.commit()

        token = create_access_token(user_id=plain.user_id)
        headers = {"Authorization": f"Bearer {token}"}
        items = await _list_images(client, headers)

        matching = [i for i in items if i["image_id"] == image.image_id]
        assert matching, "Seeded image not found in list"
        assert matching[0]["ml_suggestion_count"] is None

    async def test_admin_without_permission_sees_count(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """An admin (no explicit IMAGE_TAG_ADD grant) gets ml_suggestion_count == N."""
        owner = await _make_user(db_session, "tc_admin_owner")
        admin = await _make_user(db_session, "tc_admin", admin=True)
        # No explicit IMAGE_TAG_ADD grant — the admin flag alone must suffice.

        image = await _make_image(db_session, owner, "tc_admin_img")
        tag1 = await _make_tag(db_session, owner, "tc_admin_t1")
        tag2 = await _make_tag(db_session, owner, "tc_admin_t2")
        tag3 = await _make_tag(db_session, owner, "tc_admin_t3")
        await _make_suggestion(db_session, image, tag1, status="pending")
        await _make_suggestion(db_session, image, tag2, status="pending")
        await _make_suggestion(db_session, image, tag3, status="pending")
        await db_session.commit()

        token = create_access_token(user_id=admin.user_id)
        headers = {"Authorization": f"Bearer {token}"}
        items = await _list_images(client, headers)

        matching = [i for i in items if i["image_id"] == image.image_id]
        assert matching, "Seeded image not found in list"
        assert matching[0]["ml_suggestion_count"] == 3

    async def test_anonymous_sees_none(self, client: AsyncClient, db_session: AsyncSession):
        """An unauthenticated request gets ml_suggestion_count=None on all items."""
        owner = await _make_user(db_session, "tc_anon_owner")
        image = await _make_image(db_session, owner, "tc_anon_img")
        tag = await _make_tag(db_session, owner, "tc_anon_t")
        await _make_suggestion(db_session, image, tag, status="pending")
        await db_session.commit()

        items = await _list_images(client)  # No auth headers.

        matching = [i for i in items if i["image_id"] == image.image_id]
        assert matching, "Seeded image not found in list"
        assert matching[0]["ml_suggestion_count"] is None

    async def test_image_with_no_pending_suggestions_shows_zero_for_tagger(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """For a tagger, an image with no pending suggestions gets ml_suggestion_count=0."""
        owner = await _make_user(db_session, "tc_zero_owner")
        tagger = await _make_user(db_session, "tc_zero_tagger")
        await _grant_image_tag_add(db_session, tagger)

        image = await _make_image(db_session, owner, "tc_zero_img")
        # Only an approved suggestion — no pending ones.
        tag = await _make_tag(db_session, owner, "tc_zero_t")
        await _make_suggestion(db_session, image, tag, status="approved")
        await db_session.commit()

        token = create_access_token(user_id=tagger.user_id)
        headers = {"Authorization": f"Bearer {token}"}
        items = await _list_images(client, headers)

        matching = [i for i in items if i["image_id"] == image.image_id]
        assert matching, "Seeded image not found in list"
        assert matching[0]["ml_suggestion_count"] == 0
