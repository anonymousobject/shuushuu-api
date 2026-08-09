"""Tests for character-source link picture endpoints (PUT/DELETE .../picture)."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, TagAuditActionType, TagType
from app.core.security import get_password_hash
from app.models.character_source_link import CharacterSourceLinks
from app.models.character_source_link_picture import CharacterSourceLinkPictures
from app.models.image import Images
from app.models.permissions import Perms, UserPerms
from app.models.tag import Tags
from app.models.tag_audit_log import TagAuditLog
from app.models.tag_link import TagLinks
from app.models.user import Users

# Fixture ids far from other test modules' ranges (image_tag_context uses 8xx).
CHAR_ID = 9401
SRC_ID = 9402
OTHER_SRC_ID = 9403
IMG_OK = 9411  # tagged with both, 1000x800
IMG_MISSING_TAG = 9412  # tagged with character only
IMG_HIDDEN = 9413  # both tags but non-public status
IMG_NO_DIMS = 9414  # both tags, width=height=0 (legacy rows)


async def _setup(db: AsyncSession) -> int:
    """Seed tags, images, tag-links and one character-source link; return link id."""
    db.add(Tags(tag_id=CHAR_ID, type=TagType.CHARACTER, title="lp char"))
    db.add(Tags(tag_id=SRC_ID, type=TagType.SOURCE, title="lp source"))
    db.add(Tags(tag_id=OTHER_SRC_ID, type=TagType.SOURCE, title="lp other source"))
    db.add(
        Images(
            image_id=IMG_OK,
            user_id=1,
            ext="jpg",
            status=ImageStatus.ACTIVE,
            width=1000,
            height=800,
        )
    )
    db.add(
        Images(
            image_id=IMG_MISSING_TAG,
            user_id=1,
            ext="jpg",
            status=ImageStatus.ACTIVE,
            width=1000,
            height=800,
        )
    )
    db.add(
        Images(
            image_id=IMG_HIDDEN,
            user_id=1,
            ext="jpg",
            status=ImageStatus.DEACTIVATED,
            width=1000,
            height=800,
        )
    )
    db.add(
        Images(
            image_id=IMG_NO_DIMS,
            user_id=1,
            ext="jpg",
            status=ImageStatus.ACTIVE,
            width=0,
            height=0,
        )
    )
    # Flush so tag/image rows exist before rows that reference them (no ORM
    # relationships -> unit-of-work can't order the inserts itself).
    await db.flush()
    for img in (IMG_OK, IMG_HIDDEN, IMG_NO_DIMS):
        db.add(TagLinks(tag_id=CHAR_ID, image_id=img, user_id=1))
        db.add(TagLinks(tag_id=SRC_ID, image_id=img, user_id=1))
    db.add(TagLinks(tag_id=CHAR_ID, image_id=IMG_MISSING_TAG, user_id=1))
    link = CharacterSourceLinks(character_tag_id=CHAR_ID, source_tag_id=SRC_ID)
    db.add(link)
    await db.commit()
    await db.refresh(link)
    assert link.id is not None
    return link.id


@pytest.fixture
async def tag_create_admin(db_session: AsyncSession) -> Users:
    """Admin user holding TAG_CREATE."""
    perm = Perms(title="tag_create", desc="Create tags and tag links")
    db_session.add(perm)
    await db_session.commit()
    await db_session.refresh(perm)
    admin = Users(
        username="lp_admin",
        password=get_password_hash("AdminPassword123!"),
        password_type="bcrypt",
        salt="",
        email="lp_admin@example.com",
        active=1,
        admin=1,
    )
    db_session.add(admin)
    await db_session.commit()
    await db_session.refresh(admin)
    db_session.add(UserPerms(user_id=admin.user_id, perm_id=perm.perm_id, permvalue=1))
    await db_session.commit()
    return admin


async def _login(client: AsyncClient, username: str, password: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


GOOD_CROP = {"crop_x": 0.1, "crop_y": 0.1, "crop_w": 0.4, "crop_h": 0.5}
# 0.4*1000=400px wide, 0.5*800=400px tall -> exactly square on a 1000x800 image.


class TestSetLinkPicture:
    async def test_set_creates_row_audit_and_response(
        self, client: AsyncClient, db_session: AsyncSession, tag_create_admin: Users
    ) -> None:
        link_id = await _setup(db_session)
        headers = await _login(client, "lp_admin", "AdminPassword123!")
        response = await client.put(
            f"/api/v1/character-source-links/{link_id}/picture",
            json={"image_id": IMG_OK, **GOOD_CROP},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["link_id"] == link_id
        assert body["image_id"] == IMG_OK
        assert body["crop_w"] == GOOD_CROP["crop_w"]
        assert body["set_by_user_id"] == tag_create_admin.user_id

        row = (
            await db_session.execute(
                select(CharacterSourceLinkPictures).where(
                    CharacterSourceLinkPictures.link_id == link_id  # type: ignore[arg-type]
                )
            )
        ).scalar_one()
        assert row.image_id == IMG_OK

        audit = (
            (
                await db_session.execute(
                    select(TagAuditLog).where(
                        TagAuditLog.action_type == TagAuditActionType.PICTURE_SET  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(audit) == 1
        assert audit[0].tag_id == CHAR_ID
        assert audit[0].character_tag_id == CHAR_ID
        assert audit[0].source_tag_id == SRC_ID

    async def test_put_replaces_existing(
        self, client: AsyncClient, db_session: AsyncSession, tag_create_admin: Users
    ) -> None:
        link_id = await _setup(db_session)
        headers = await _login(client, "lp_admin", "AdminPassword123!")
        first = await client.put(
            f"/api/v1/character-source-links/{link_id}/picture",
            json={"image_id": IMG_OK, **GOOD_CROP},
            headers=headers,
        )
        assert first.status_code == 200, first.text
        second = await client.put(
            f"/api/v1/character-source-links/{link_id}/picture",
            json={
                "image_id": IMG_OK,
                "crop_x": 0.2,
                "crop_y": 0.2,
                "crop_w": 0.2,
                "crop_h": 0.25,
            },
            headers=headers,
        )
        assert second.status_code == 200, second.text
        rows = (await db_session.execute(select(CharacterSourceLinkPictures))).scalars().all()
        assert len(rows) == 1
        assert rows[0].crop_x == 0.2

    async def test_requires_auth_and_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        link_id = await _setup(db_session)
        payload = {"image_id": IMG_OK, **GOOD_CROP}
        anon = await client.put(f"/api/v1/character-source-links/{link_id}/picture", json=payload)
        assert anon.status_code == 401
        regular = Users(
            username="lp_regular",
            password=get_password_hash("Password123!"),
            password_type="bcrypt",
            salt="",
            email="lp_regular@example.com",
            active=1,
            admin=0,
        )
        db_session.add(regular)
        await db_session.commit()
        headers = await _login(client, "lp_regular", "Password123!")
        denied = await client.put(
            f"/api/v1/character-source-links/{link_id}/picture",
            json=payload,
            headers=headers,
        )
        assert denied.status_code == 403

    @pytest.mark.parametrize(
        ("image_id", "crop", "status", "detail_fragment"),
        [
            (99999, GOOD_CROP, 404, "Image not found"),
            (IMG_HIDDEN, GOOD_CROP, 400, "not publicly visible"),
            (IMG_MISSING_TAG, GOOD_CROP, 400, "must carry both"),
            (  # x + w > 1
                IMG_OK,
                {"crop_x": 0.8, "crop_y": 0.1, "crop_w": 0.4, "crop_h": 0.5},
                400,
                "beyond the image",
            ),
            (  # 400x160px on 1000x800 — far from square
                IMG_OK,
                {"crop_x": 0.1, "crop_y": 0.1, "crop_w": 0.4, "crop_h": 0.2},
                400,
                "square",
            ),
        ],
    )
    async def test_validation_rejections(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        tag_create_admin: Users,
        image_id: int,
        crop: dict[str, float],
        status: int,
        detail_fragment: str,
    ) -> None:
        link_id = await _setup(db_session)
        headers = await _login(client, "lp_admin", "AdminPassword123!")
        response = await client.put(
            f"/api/v1/character-source-links/{link_id}/picture",
            json={"image_id": image_id, **crop},
            headers=headers,
        )
        assert response.status_code == status, response.text
        assert detail_fragment in response.json()["detail"]

    async def test_per_field_range_is_pydantic_422(
        self, client: AsyncClient, db_session: AsyncSession, tag_create_admin: Users
    ) -> None:
        link_id = await _setup(db_session)
        headers = await _login(client, "lp_admin", "AdminPassword123!")
        # crop_w = 0 violates the schema-level gt=0 -> FastAPI 422, list-shaped
        # detail (distinct from the endpoint's 400 cross-field checks).
        response = await client.put(
            f"/api/v1/character-source-links/{link_id}/picture",
            json={"image_id": IMG_OK, "crop_x": 0.1, "crop_y": 0.1, "crop_w": 0, "crop_h": 0.5},
            headers=headers,
        )
        assert response.status_code == 422

    async def test_missing_link_404(
        self, client: AsyncClient, db_session: AsyncSession, tag_create_admin: Users
    ) -> None:
        await _setup(db_session)
        headers = await _login(client, "lp_admin", "AdminPassword123!")
        response = await client.put(
            "/api/v1/character-source-links/999999/picture",
            json={"image_id": IMG_OK, **GOOD_CROP},
            headers=headers,
        )
        assert response.status_code == 404
        assert "Link not found" in response.json()["detail"]

    async def test_zero_dims_skips_aspect_check(
        self, client: AsyncClient, db_session: AsyncSession, tag_create_admin: Users
    ) -> None:
        link_id = await _setup(db_session)
        headers = await _login(client, "lp_admin", "AdminPassword123!")
        # Wildly non-square crop is accepted when stored dims are 0 (legacy
        # rows) — bounds still apply, aspect is unknowable.
        response = await client.put(
            f"/api/v1/character-source-links/{link_id}/picture",
            json={
                "image_id": IMG_NO_DIMS,
                "crop_x": 0.0,
                "crop_y": 0.0,
                "crop_w": 0.9,
                "crop_h": 0.1,
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text


class TestDeleteLinkPicture:
    async def test_delete_removes_row_and_audits(
        self, client: AsyncClient, db_session: AsyncSession, tag_create_admin: Users
    ) -> None:
        link_id = await _setup(db_session)
        headers = await _login(client, "lp_admin", "AdminPassword123!")
        put = await client.put(
            f"/api/v1/character-source-links/{link_id}/picture",
            json={"image_id": IMG_OK, **GOOD_CROP},
            headers=headers,
        )
        assert put.status_code == 200, put.text
        response = await client.delete(
            f"/api/v1/character-source-links/{link_id}/picture", headers=headers
        )
        assert response.status_code == 204
        rows = (await db_session.execute(select(CharacterSourceLinkPictures))).scalars().all()
        assert rows == []
        audit = (
            (
                await db_session.execute(
                    select(TagAuditLog).where(
                        TagAuditLog.action_type == TagAuditActionType.PICTURE_REMOVED  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(audit) == 1

    async def test_delete_without_picture_404(
        self, client: AsyncClient, db_session: AsyncSession, tag_create_admin: Users
    ) -> None:
        link_id = await _setup(db_session)
        headers = await _login(client, "lp_admin", "AdminPassword123!")
        response = await client.delete(
            f"/api/v1/character-source-links/{link_id}/picture", headers=headers
        )
        assert response.status_code == 404
        assert "Link has no picture" in response.json()["detail"]


class TestTagDetailEmbed:
    async def test_sources_carry_link_id_and_picture(
        self, client: AsyncClient, db_session: AsyncSession, tag_create_admin: Users
    ) -> None:
        link_id = await _setup(db_session)
        headers = await _login(client, "lp_admin", "AdminPassword123!")
        put = await client.put(
            f"/api/v1/character-source-links/{link_id}/picture",
            json={"image_id": IMG_OK, **GOOD_CROP},
            headers=headers,
        )
        assert put.status_code == 200, put.text

        # Character page: sources[] carries link_id + picture
        char_page = await client.get(f"/api/v1/tags/{CHAR_ID}")
        assert char_page.status_code == 200
        sources = char_page.json()["sources"]
        assert len(sources) == 1
        assert sources[0]["link_id"] == link_id
        picture = sources[0]["picture"]
        assert picture is not None
        assert picture["image_id"] == IMG_OK
        assert picture["crop_w"] == GOOD_CROP["crop_w"]
        assert picture["thumbnail_url"].endswith(".webp")
        assert "/thumbs/" in picture["thumbnail_url"]

        # Source page: characters[] carries the same
        src_page = await client.get(f"/api/v1/tags/{SRC_ID}")
        characters = src_page.json()["characters"]
        assert characters[0]["link_id"] == link_id
        assert characters[0]["picture"]["image_id"] == IMG_OK

    async def test_pictureless_link_has_null_picture(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        link_id = await _setup(db_session)
        response = await client.get(f"/api/v1/tags/{CHAR_ID}")
        sources = response.json()["sources"]
        assert sources[0]["link_id"] == link_id
        assert sources[0]["picture"] is None

    async def test_picture_omitted_when_image_leaves_public_status(
        self, client: AsyncClient, db_session: AsyncSession, tag_create_admin: Users
    ) -> None:
        link_id = await _setup(db_session)
        headers = await _login(client, "lp_admin", "AdminPassword123!")
        put = await client.put(
            f"/api/v1/character-source-links/{link_id}/picture",
            json={"image_id": IMG_OK, **GOOD_CROP},
            headers=headers,
        )
        assert put.status_code == 200, put.text
        image = (
            await db_session.execute(
                select(Images).where(Images.image_id == IMG_OK)  # type: ignore[arg-type]
            )
        ).scalar_one()
        image.status = ImageStatus.DEACTIVATED  # deactivated after being chosen
        await db_session.commit()

        response = await client.get(f"/api/v1/tags/{CHAR_ID}")
        assert response.json()["sources"][0]["picture"] is None
