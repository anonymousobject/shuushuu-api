"""Tests for the mod-only has_open_report flag on image responses."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ReportStatus
from app.core.security import get_password_hash
from app.models.image import Images
from app.models.image_report import ImageReports
from app.models.permissions import GroupPerms, Groups, Perms, UserGroups
from app.models.user import Users


async def _make_user(db, username, password="TestPassword123!"):
    user = Users(username=username, password=get_password_hash(password),
                 password_type="bcrypt", salt="", email=f"{username}@example.com", active=1)
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


async def _image(db, owner, md5):
    img = Images(filename="orf", ext="jpg", md5_hash=md5, user_id=owner.user_id,
                 width=10, height=10, filesize=100, status=1)
    db.add(img)
    await db.commit()
    await db.refresh(img)
    return img


@pytest.mark.api
class TestHasOpenReportFlag:
    async def test_detail_flag_visible_to_mod_only(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await _make_user(db_session, "orfowner")
        reported = await _image(db_session, owner, "a" * 32)
        clean = await _image(db_session, owner, "b" * 32)
        db_session.add(ImageReports(image_id=reported.image_id, user_id=owner.user_id,
                                    category=2, status=ReportStatus.PENDING))
        await db_session.commit()

        mod = await _make_user(db_session, "orfmod")
        await _grant(db_session, mod.user_id, "report_view")
        token = await _login(client, mod.username)
        h = {"Authorization": f"Bearer {token}"}

        r = await client.get(f"/api/v1/images/{reported.image_id}", headers=h)
        assert r.status_code == 200
        assert r.json()["has_open_report"] is True

        r = await client.get(f"/api/v1/images/{clean.image_id}", headers=h)
        assert r.json()["has_open_report"] is False

        # Regular user never sees the flag set, even on a reported image.
        plain = await _make_user(db_session, "orfplain")
        token2 = await _login(client, plain.username)
        r = await client.get(
            f"/api/v1/images/{reported.image_id}",
            headers={"Authorization": f"Bearer {token2}"},
        )
        assert r.json()["has_open_report"] is False

    async def test_list_flag_for_mod(self, client: AsyncClient, db_session: AsyncSession):
        owner = await _make_user(db_session, "orflistowner")
        reported = await _image(db_session, owner, "c" * 32)
        db_session.add(ImageReports(image_id=reported.image_id, user_id=owner.user_id,
                                    category=2, status=ReportStatus.PENDING))
        await db_session.commit()

        mod = await _make_user(db_session, "orflistmod")
        await _grant(db_session, mod.user_id, "report_view")
        token = await _login(client, mod.username)

        r = await client.get(
            "/api/v1/images/?per_page=100", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200
        items = r.json()["images"]
        match = [i for i in items if i["image_id"] == reported.image_id]
        assert match and match[0]["has_open_report"] is True
