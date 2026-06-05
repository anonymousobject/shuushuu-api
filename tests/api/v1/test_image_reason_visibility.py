"""Tests for the owner/mods-only moderation reason on image responses.

Powers the red status band showing *why* a hidden image was taken down without a
comment. The free-text reason and its category must reach the owner and mods
(IMAGE_EDIT / REVIEW_VIEW) but never a normal viewer.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash
from app.models.image import Images
from app.models.permissions import GroupPerms, Groups, Perms, UserGroups
from app.models.user import Users


async def _make_user(db, username, password="TestPassword123!"):
    user = Users(
        username=username,
        password=get_password_hash(password),
        password_type="bcrypt",
        salt="",
        email=f"{username}@example.com",
        active=1,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _grant(db, user_id, perm_title):
    perm = (await db.execute(select(Perms).where(Perms.title == perm_title))).scalar_one_or_none()
    if not perm:
        perm = Perms(title=perm_title, desc=perm_title)
        db.add(perm)
        await db.flush()
    group = Groups(title=f"{perm_title}_{user_id}", desc="g")
    db.add(group)
    await db.flush()
    db.add(GroupPerms(group_id=group.group_id, perm_id=perm.perm_id, permvalue=1))
    db.add(UserGroups(user_id=user_id, group_id=group.group_id))
    await db.commit()


async def _login(client, username, password="TestPassword123!"):
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return r.json()["access_token"]


async def _deactivated_image(db, owner, md5, reason, category):
    img = Images(
        filename="rsn",
        ext="jpg",
        md5_hash=md5,
        user_id=owner.user_id,
        width=10,
        height=10,
        filesize=100,
        status=0,  # DEACTIVATED
        status_reason=reason,
        reason_category=category,
    )
    db.add(img)
    await db.commit()
    await db.refresh(img)
    return img


@pytest.mark.api
class TestImageReasonVisibility:
    async def test_reason_visible_to_owner_and_mod_not_others(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await _make_user(db_session, "rsnowner")
        img = await _deactivated_image(db_session, owner, "d" * 32, "blurry duplicate", 2)

        # Owner sees the reason and category.
        otoken = await _login(client, owner.username)
        r = await client.get(
            f"/api/v1/images/{img.image_id}", headers={"Authorization": f"Bearer {otoken}"}
        )
        assert r.status_code == 200
        assert r.json()["status_reason"] == "blurry duplicate"
        assert r.json()["reason_category"] == 2

        # Mod (IMAGE_EDIT) sees the reason.
        mod = await _make_user(db_session, "rsnmod")
        await _grant(db_session, mod.user_id, "image_edit")
        mtoken = await _login(client, mod.username)
        r = await client.get(
            f"/api/v1/images/{img.image_id}", headers={"Authorization": f"Bearer {mtoken}"}
        )
        assert r.json()["status_reason"] == "blurry duplicate"
        assert r.json()["reason_category"] == 2

        # A plain non-owner user does NOT see the reason (category included).
        plain = await _make_user(db_session, "rsnplain")
        ptoken = await _login(client, plain.username)
        r = await client.get(
            f"/api/v1/images/{img.image_id}", headers={"Authorization": f"Bearer {ptoken}"}
        )
        assert r.json()["status_reason"] is None
        assert r.json()["reason_category"] is None

        # Anonymous does NOT see the reason.
        r = await client.get(f"/api/v1/images/{img.image_id}")
        assert r.json()["status_reason"] is None
        assert r.json()["reason_category"] is None

    async def test_reason_in_list_for_mod_not_plain(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await _make_user(db_session, "rsnlistowner")
        img = await _deactivated_image(db_session, owner, "e" * 32, "low quality", 2)

        mod = await _make_user(db_session, "rsnlistmod")
        await _grant(db_session, mod.user_id, "image_edit")
        mtoken = await _login(client, mod.username)
        r = await client.get(
            "/api/v1/images/?status=0&per_page=100",
            headers={"Authorization": f"Bearer {mtoken}"},
        )
        assert r.status_code == 200
        match = [i for i in r.json()["images"] if i["image_id"] == img.image_id]
        assert match and match[0]["status_reason"] == "low quality"

        plain = await _make_user(db_session, "rsnlistplain")
        ptoken = await _login(client, plain.username)
        r = await client.get(
            "/api/v1/images/?status=0&per_page=100",
            headers={"Authorization": f"Bearer {ptoken}"},
        )
        match = [i for i in r.json()["images"] if i["image_id"] == img.image_id]
        assert match and match[0]["status_reason"] is None
