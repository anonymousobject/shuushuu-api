"""Tests for profile favorite tags (grouped GET + own-only mutations)."""

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, TagType
from app.core.security import get_password_hash
from app.models.character_source_link import CharacterSourceLinks
from app.models.character_source_link_picture import CharacterSourceLinkPictures
from app.models.image import Images
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.user import Users
from app.models.user_favorite import UserFavoriteLinks, UserFavoriteTags

CHAR_A = 9801  # linked to SRC_A and SRC_B (two combos)
SRC_A = 9803
SRC_B = 9804
ARTIST_A = 9805
THEME_A = 9806  # never favoritable
SRC_ALIAS = 9807  # alias of SRC_A — rejected on write
IMG_PIC = 9811  # picture source for the (CHAR_A, SRC_A) link


async def _seed(db: AsyncSession) -> dict[str, int]:
    """Tags, two links (one with a picture), one favoriting user. Returns ids."""
    db.add(Tags(tag_id=CHAR_A, type=TagType.CHARACTER, title="fav char"))
    db.add(Tags(tag_id=SRC_A, type=TagType.SOURCE, title="fav source A"))
    db.add(Tags(tag_id=SRC_B, type=TagType.SOURCE, title="fav source B"))
    db.add(Tags(tag_id=ARTIST_A, type=TagType.ARTIST, title="fav artist"))
    db.add(Tags(tag_id=THEME_A, type=TagType.THEME, title="fav theme"))
    db.add(Tags(tag_id=SRC_ALIAS, type=TagType.SOURCE, title="fav source A alias", alias_of=SRC_A))
    db.add(
        Images(
            image_id=IMG_PIC,
            user_id=1,
            ext="jpg",
            status=ImageStatus.ACTIVE,
            width=1000,
            height=1000,
        )
    )
    user = Users(
        username="fav_user",
        password=get_password_hash("Password123!"),
        password_type="bcrypt",
        salt="",
        email="fav_user@example.com",
        active=1,
        admin=0,
    )
    db.add(user)
    await db.flush()
    db.add(TagLinks(tag_id=CHAR_A, image_id=IMG_PIC, user_id=1))
    db.add(TagLinks(tag_id=SRC_A, image_id=IMG_PIC, user_id=1))
    link_a = CharacterSourceLinks(character_tag_id=CHAR_A, source_tag_id=SRC_A)
    link_b = CharacterSourceLinks(character_tag_id=CHAR_A, source_tag_id=SRC_B)
    db.add(link_a)
    db.add(link_b)
    await db.flush()
    assert link_a.id is not None and link_b.id is not None and user.user_id is not None
    db.add(
        CharacterSourceLinkPictures(
            link_id=link_a.id,
            image_id=IMG_PIC,
            crop_x=0.1,
            crop_y=0.1,
            crop_w=0.5,
            crop_h=0.5,
        )
    )
    await db.commit()
    return {"link_a": link_a.id, "link_b": link_b.id, "user_id": user.user_id}


async def _login(
    client: AsyncClient, username: str = "fav_user", password: str = "Password123!"
) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _favorite_all(db: AsyncSession, ids: dict[str, int]) -> None:
    """Directly seed favorites: both combos, one source, one artist."""
    uid = ids["user_id"]
    db.add(UserFavoriteLinks(user_id=uid, link_id=ids["link_a"], position=0))
    db.add(UserFavoriteLinks(user_id=uid, link_id=ids["link_b"], position=1))
    db.add(UserFavoriteTags(user_id=uid, tag_id=SRC_A, position=0))
    db.add(UserFavoriteTags(user_id=uid, tag_id=ARTIST_A, position=0))
    await db.commit()


class TestGetFavoriteTags:
    async def test_grouped_shape_order_and_picture(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        ids = await _seed(db_session)
        await _favorite_all(db_session, ids)
        response = await client.get(f"/api/v1/users/{ids['user_id']}/favorite-tags")
        assert response.status_code == 200, response.text
        body = response.json()

        chars = body["characters"]
        assert [c["link_id"] for c in chars] == [ids["link_a"], ids["link_b"]]
        assert chars[0]["character"]["title"] == "fav char"
        assert chars[0]["source"]["title"] == "fav source A"
        assert chars[0]["picture"]["image_id"] == IMG_PIC
        assert chars[0]["picture"]["thumbnail_url"].endswith(".webp")
        assert chars[1]["picture"] is None  # link_b has no picture

        assert [s["tag"]["tag_id"] for s in body["sources"]] == [SRC_A]
        assert [a["tag"]["tag_id"] for a in body["artists"]] == [ARTIST_A]

    async def test_picture_omitted_when_image_leaves_public_status(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        ids = await _seed(db_session)
        await _favorite_all(db_session, ids)
        image = (
            await db_session.execute(
                select(Images).where(Images.image_id == IMG_PIC)  # type: ignore[arg-type]
            )
        ).scalar_one()
        image.status = ImageStatus.DEACTIVATED
        await db_session.commit()
        response = await client.get(f"/api/v1/users/{ids['user_id']}/favorite-tags")
        assert response.json()["characters"][0]["picture"] is None

    async def test_empty_and_missing_user(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        ids = await _seed(db_session)
        response = await client.get(f"/api/v1/users/{ids['user_id']}/favorite-tags")
        assert response.status_code == 200
        assert response.json() == {"characters": [], "sources": [], "artists": []}
        missing = await client.get("/api/v1/users/999999/favorite-tags")
        assert missing.status_code == 404

    async def test_cross_user_isolation(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        ids = await _seed(db_session)
        await _favorite_all(db_session, ids)
        other = Users(
            username="fav_other",
            password=get_password_hash("Password123!"),
            password_type="bcrypt",
            salt="",
            email="fav_other@example.com",
            active=1,
            admin=0,
        )
        db_session.add(other)
        await db_session.commit()
        await db_session.refresh(other)
        response = await client.get(f"/api/v1/users/{other.user_id}/favorite-tags")
        assert response.json() == {"characters": [], "sources": [], "artists": []}
